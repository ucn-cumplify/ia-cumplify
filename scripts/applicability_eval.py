"""Manual evaluation set of applicability-v2: APR-023 to APR-025 of docs/ApplicabilityReasons/test.csv
(step 0.17 of the sprint 3 plan).

It spends real tokens. It sends each case of applicability_eval_set.json to an ia-cumplify that is
already running with applicability-v2, and that service calls OpenAI. It is not part of pytest, which
collects only tests/.

    uv run python scripts/applicability_eval.py [--base-url URL] [--case ID ...] [--out FILE]
                                                [--dry-run] [--timeout SECONDS]

SERVICE_API_KEY and DATABASE_URL come from the environment or the arguments, never from .env. Each
article is looked up in the AI alerts of the local database (source_analysis_id 'ai-suggestion:'), with
this script's own read-only connection, and travels with the activity phrases its alert kept, which are
the actions the backend sends. A failed connection shows only the type of psycopg's error, whose message
can repeat DATABASE_URL with its password. --dry-run stops after the lookup, before any request.
Responses go to --out, outside the repository; the console shows metrics only.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_SET = HERE / "applicability_eval_set.json"
NATURES = ("explicit_obligations", "informative", "referral")
NIL_UUID = "00000000-0000-0000-0000-000000000000"
MAX_MATCHED_VALUES = 10
MAX_ACTIONS = 20
EXPECTED_VERSION_PREFIX = "applicability-v2@"
# ia gives the model APPLICABILITY_REASONS_TIMEOUT_SECONDS (25) and the backend waits 30: a slower request
# leaves the alert with its template and without analysis.
BUDGET_MS = 25_000
NEAR_BUDGET_MS = 20_000
# US$ per million tokens, the rates of the sprint 3 plan (GetChatUsageUseCase.cs:195-197 of the backend).
INPUT_PRICE = 0.20
OUTPUT_PRICE = 1.20

# The newest AI alert that suggested the article. Laws match by their letters and digits ("21.806" and
# "21806", "E2E-1"); articles by the number or word after "Artículo", without accents. The arrays keep the
# order the alert shows.
ARTICLE_QUERY = """
    SELECT ra.legal_body_id::text AS legal_body_id,
           ra.rrll_id::text AS legal_requirement_id,
           s.article_id::text AS article_id,
           coalesce(a.number, '') AS article,
           coalesce(lb.number, '') AS law,
           array(SELECT o.summary FROM regulatory_alert_suggested_obligations o
                 WHERE o.regulatory_alert_suggestion_id = s.id ORDER BY o.sort_order, o.id) AS actions,
           array(SELECT mv.label FROM regulatory_alert_suggestion_matched_values mv
                 WHERE mv.regulatory_alert_suggestion_id = s.id ORDER BY mv.position, mv.id) AS stored_values
    FROM regulatory_alert_suggestions s
    JOIN regulatory_alerts ra ON ra.id = s.regulatory_alert_id
    JOIN legal_bodies lb ON lb.id = ra.legal_body_id
    JOIN articles a ON a.id = s.article_id
    WHERE ra.source_analysis_id LIKE 'ai-suggestion:%%'
      AND lower(regexp_replace(coalesce(lb.number, ''), '[^0-9A-Za-z]', '', 'g')) = %(law)s
      AND unaccent(lower(coalesce(a.number, ''))) ~ %(article)s
      AND (%(legal_body_id)s::uuid IS NULL OR ra.legal_body_id = %(legal_body_id)s::uuid)
    ORDER BY ra.created_at DESC, s.id
    LIMIT 1
"""


class SetError(ValueError):
    """The evaluation set does not have the expected shape."""


class NoData(LookupError):
    """The case cannot be built from this database."""


class DatabaseError(RuntimeError):
    """The database failed. The message never repeats DATABASE_URL."""


def plain(text: str) -> str:
    """Lower case, without accents."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower().strip()


def key(text: str) -> str:
    """Only the letters and digits of plain(), to compare law names and numbers."""
    return re.sub(r"[^0-9a-z]", "", plain(text))


def law_key(law: str) -> str:
    return re.sub(r"[^0-9a-z]", "", law.lower())


def article_pattern(number: str) -> str:
    """A PostgreSQL regex for the article: "74" finds "Artículo 74" but not "Artículo 740"."""
    token = plain(number)
    if not re.fullmatch(r"[0-9a-z]+", token):
        raise SetError(f"article {number!r}: one number or one word, such as 74 or único")
    return rf"^\s*art\D*\m{token}\M"


def load_set(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    seen: set[str] = set()
    for case in cases:
        for name in ("id", "group", "law", "write_reason", "articles"):
            if name not in case:
                raise SetError(f"case {case.get('id')}: missing {name}")
        if case["id"] in seen:
            raise SetError(f"case {case['id']}: repeated id")
        seen.add(case["id"])
        if not isinstance(case["write_reason"], bool):
            raise SetError(f"case {case['id']}: write_reason must be true or false")
        if not case["articles"]:
            raise SetError(f"case {case['id']}: no articles")
        for article in case["articles"]:
            article_pattern(str(article.get("number", "")))
            if article.get("expected", {}).get("nature") not in NATURES:
                raise SetError(f"case {case['id']}: expected nature must be one of {', '.join(NATURES)}")
    return cases


# --- the lookup ------------------------------------------------------------------------------------


@dataclass
class Resolved:
    legal_body_id: str
    legal_requirement_id: str
    rows: list[dict]


class Database:
    """Reads the articles with a read-only connection of its own (never the service's pool)."""

    def __init__(self, database_url: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._error = psycopg.Error
        try:
            # Not autocommit: psycopg opens each transaction with BEGIN READ ONLY, and none is committed.
            self._connection = psycopg.connect(database_url, row_factory=dict_row)
        except psycopg.Error as exc:
            # Only the type: libpq repeats a malformed URL whole, password included.
            raise DatabaseError(
                f"no se pudo conectar con DATABASE_URL (o --database-url): {type(exc).__name__}, sin el detalle "
                "de psycopg, que puede repetir la URL con la contraseña. Revisar que la base esté en marcha y que "
                "la URL tenga la forma postgresql://usuario:contraseña@host:puerto/base, sin comillas y con los "
                "caracteres especiales de la contraseña codificados (@ como %40, / como %2F)"
            ) from None
        self._connection.read_only = True

    def resolve(self, case: dict) -> Resolved:
        legal_body_id: str | None = None
        legal_requirement_id = NIL_UUID
        rows: list[dict] = []
        for spec in case["articles"]:
            params = {"law": law_key(case["law"]), "article": article_pattern(str(spec["number"])), "legal_body_id": legal_body_id}
            try:
                with self._connection.cursor() as cursor:
                    cursor.execute(ARTICLE_QUERY, params)
                    row = cursor.fetchone()
            except self._error as exc:
                raise DatabaseError(
                    f"la consulta falló: {type(exc).__name__}. Necesita las tablas de alertas del backend y la "
                    "extensión unaccent"
                ) from None
            if row is None:
                raise NoData(f"{case['law']}, artículo {spec['number']}: no está en las alertas de la IA de esta base")
            if any(found["article_id"] == row["article_id"] for found in rows):
                raise NoData(f"{case['law']}, artículo {spec['number']}: repite un artículo del mismo caso")
            if legal_body_id is None:
                legal_body_id = row["legal_body_id"]
                legal_requirement_id = row["legal_requirement_id"] or NIL_UUID
            rows.append(row)
        assert legal_body_id is not None
        return Resolved(legal_body_id, legal_requirement_id, rows)

    def close(self) -> None:
        self._connection.close()


def build_request(case: dict, resolved: Resolved) -> dict:
    """The body the backend would send: the case's values, or else the ones the alert kept."""
    articles = []
    for row in resolved.rows:
        values = list(case["matched_values"]) if case.get("matched_values") else list(row["stored_values"])
        if case["write_reason"] and not values:
            raise NoData(f"{row['law']}, {row['article']}: sin valores coincidentes para pedir el motivo")
        articles.append(
            {
                "article_id": row["article_id"],
                "matched_values": values[:MAX_MATCHED_VALUES],
                "actions": [action for action in row["actions"] if action.strip()][:MAX_ACTIONS],
                "write_reason": case["write_reason"],
            }
        )
    return {
        "legal_requirement_id": resolved.legal_requirement_id,
        "legal_body_id": resolved.legal_body_id,
        "articles": articles,
    }


# --- the request -----------------------------------------------------------------------------------


@dataclass
class Result:
    case: dict
    request: dict | None = None
    rows: list[dict] = field(default_factory=list)
    skipped: str | None = None
    status: int | None = None
    detail: str | None = None
    body: dict | None = None
    total_ms: float | None = None
    checks: list[dict] = field(default_factory=list)

    @property
    def usage(self) -> dict:
        return (self.body or {}).get("usage") or {}

    @property
    def passed(self) -> bool:
        return self.skipped is None and self.status == 200 and all(check["passed"] for check in self.checks)

    def cost(self, input_price: float, output_price: float) -> float:
        usage = self.usage
        return (usage.get("prompt_tokens", 0) * input_price + usage.get("completion_tokens", 0) * output_price) / 1e6


def ask(base_url: str, api_key: str, body: dict, timeout: float) -> tuple[int | None, dict | None, str | None, float]:
    url = urlsplit(base_url)
    connection_class = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(url.hostname, url.port, timeout=timeout)
    started = time.perf_counter()
    try:
        connection.request(
            "POST",
            f"{url.path.rstrip('/')}/api/v1/applicability-reasons",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8", "replace")
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        if response.status != 200:
            detail = json.loads(raw).get("detail") if raw.startswith("{") else raw
            return response.status, None, str(detail)[:300], elapsed
        return response.status, json.loads(raw), None, elapsed
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        return None, None, type(exc).__name__, round((time.perf_counter() - started) * 1000, 1)
    finally:
        connection.close()


def check_articles(case: dict, rows: list[dict], body: dict) -> list[dict]:
    """One check per article: nature, obligations, the law it refers to and whether it got a reason."""
    analyses = {item["article_id"]: item for item in body.get("analyses", [])}
    with_reason = {item["article_id"] for item in body.get("reasons", [])}
    checks = []
    for spec, row in zip(case["articles"], rows, strict=True):
        expected = spec["expected"]
        analysis = analyses.get(row["article_id"])
        failures: list[str] = []
        if analysis is None:
            failures.append("sin análisis")
        else:
            obligations = len(analysis.get("obligations") or [])
            if analysis.get("nature") != expected["nature"]:
                failures.append(f"naturaleza {analysis.get('nature')}")
            if expected["nature"] == "explicit_obligations" and obligations < expected.get("min_obligations", 1):
                failures.append(f"{obligations} obligaciones")
            if expected["nature"] != "explicit_obligations" and obligations:
                failures.append(f"{obligations} obligaciones en un artículo sin obligaciones")
            wanted = expected.get("referred")
            if wanted and key(wanted) not in key(analysis.get("referred_legal_body") or ""):
                failures.append("norma a la que remite")
        if case["write_reason"] != (row["article_id"] in with_reason):
            failures.append("motivo de más" if row["article_id"] in with_reason else "sin motivo")
        checks.append(
            {
                "article": row["article"],
                "expected": expected["nature"],
                "nature": analysis.get("nature") if analysis else None,
                "obligations": len(analysis.get("obligations") or []) if analysis else None,
                "referred": analysis.get("referred_legal_body") if analysis else None,
                "reason": row["article_id"] in with_reason,
                "passed": not failures,
                "failures": failures,
            }
        )
    return checks


# --- the report ------------------------------------------------------------------------------------


def print_table(results: list[Result]) -> None:
    header = f"{'Caso':<10} {'Artículo':<22} {'Esperado':<20} {'Obtenido':<20} {'Oblig.':>6} {'Motivo':>6} {'Entrada':>7} {'Salida':>6} {'ms':>8}  Resultado"
    print(header)
    print("-" * len(header))
    for result in results:
        if result.skipped or result.status != 200:
            print(f"{result.case['id']:<10} {result.skipped or f'HTTP {result.status}: {result.detail}'}")
            continue
        usage = result.usage
        for index, check in enumerate(result.checks):
            first = index == 0
            print(
                f"{result.case['id'] if first else '':<10} {check['article'][:22]:<22} {check['expected']:<20} "
                f"{(check['nature'] or '-'):<20} {('-' if check['obligations'] is None else check['obligations']):>6} "
                f"{('sí' if check['reason'] else 'no'):>6} "
                f"{(usage.get('prompt_tokens', '-') if first else ''):>7} {(usage.get('completion_tokens', '-') if first else ''):>6} "
                f"{(result.total_ms if first else ''):>8}  {'ok' if check['passed'] else '; '.join(check['failures'])}"
            )


def summarize(results: list[Result], input_price: float, output_price: float) -> bool:
    print_table(results)
    print()
    answered = [result for result in results if result.status == 200]
    groups: dict[str, list[Result]] = {}
    for result in results:
        groups.setdefault(result.case["group"], []).append(result)
    passed = sum(result.passed for result in results)
    print(
        f"Casos bien: {passed} de {len(results)} ("
        + ", ".join(f"{group}: {sum(r.passed for r in items)} de {len(items)}" for group, items in groups.items())
        + ")."
    )
    versions = {(result.body or {}).get("reason_version", "") for result in answered}
    wrong_version = sorted(version for version in versions if not version.startswith(EXPECTED_VERSION_PREFIX))
    if wrong_version:
        print(
            f"ATENCIÓN: el servicio respondió con {', '.join(wrong_version)}. Hay que ejecutar ia-cumplify con "
            "applicability-v2 (la rama de la ia #31 o dev, una vez integrada)."
        )
    if not answered:
        return False
    slowest = max(answered, key=lambda result: result.total_ms or 0)
    over = [result.case["id"] for result in answered if (result.total_ms or 0) > BUDGET_MS]
    near = [result.case["id"] for result in answered if NEAR_BUDGET_MS < (result.total_ms or 0) <= BUDGET_MS]
    print(
        f"Latencia: el pedido más lento fue {slowest.case['id']}, con {slowest.total_ms:.0f} ms. "
        f"El plazo de ia es de {BUDGET_MS} ms"
        + (f"; lo pasaron {', '.join(over)}" if over else "")
        + (f"; quedaron cerca (más de {NEAR_BUDGET_MS} ms) {', '.join(near)}" if near else "")
        + "."
    )
    prompt = sum(result.usage.get("prompt_tokens", 0) for result in answered)
    cached = sum(result.usage.get("cached_tokens", 0) for result in answered)
    completion = sum(result.usage.get("completion_tokens", 0) for result in answered)
    cost = sum(result.cost(input_price, output_price) for result in answered)
    print(
        f"Tokens: {prompt} de entrada ({cached} desde la caché) y {completion} de salida. Costo estimado: "
        f"US${cost:.4f} en {len(answered)} pedidos (US${cost / len(answered):.4f} por pedido), con "
        f"US${input_price:.2f} y US${output_price:.2f} por millón y sin descontar la caché."
    )
    return passed == len(results) and not over and not wrong_version


def write_out(path: Path, results: list[Result], input_price: float, output_price: float) -> None:
    with path.open("w", encoding="utf-8") as out:
        for result in results:
            out.write(
                json.dumps(
                    {
                        "id": result.case["id"],
                        "group": result.case["group"],
                        "law": result.case["law"],
                        "note": result.case.get("note"),
                        "skipped": result.skipped,
                        "status": result.status,
                        "detail": result.detail,
                        "request": result.request,
                        "response": result.body,
                        "checks": result.checks,
                        "total_ms": result.total_ms,
                        "cost_usd": round(result.cost(input_price, output_price), 6) if result.body else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def default_out() -> Path:
    return Path(tempfile.gettempdir()) / f"applicability_eval-{datetime.now():%Y%m%d-%H%M%S}.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.environ.get("SERVICE_API_KEY", ""), help="defecto: SERVICE_API_KEY")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""), help="defecto: DATABASE_URL")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, dest="case_set")
    parser.add_argument("--case", action="append", default=[], help="solo estos casos (se puede repetir)")
    parser.add_argument("--out", type=Path, default=None, help="respuestas completas; defecto: el directorio temporal")
    parser.add_argument("--dry-run", action="store_true", help="solo busca los casos en la base, sin llamar a ia")
    parser.add_argument("--timeout", type=float, default=60.0, help="segundos por pedido; más que los 25 s de ia")
    parser.add_argument("--input-price", type=float, default=INPUT_PRICE, help="US$ por millón de tokens de entrada")
    parser.add_argument("--output-price", type=float, default=OUTPUT_PRICE, help="US$ por millón de tokens de salida")
    args = parser.parse_args(argv)

    out = (args.out or default_out()).resolve()
    if out == REPO or REPO in out.parents:
        parser.error("--out tiene que quedar fuera del repositorio: guarda los valores coincidentes de las empresas")
    if not args.database_url:
        parser.error("falta DATABASE_URL (o --database-url)")
    if not args.api_key and not args.dry_run:
        parser.error("falta SERVICE_API_KEY (o --api-key)")

    try:
        cases = load_set(args.case_set)
    except SetError as exc:
        parser.error(str(exc))
    if args.case:
        unknown = set(args.case) - {case["id"] for case in cases}
        if unknown:
            parser.error(f"casos desconocidos: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in args.case]

    # Every lookup runs before the first request: a failing one stops the run before it spends tokens.
    results: list[Result] = []
    try:
        database = Database(args.database_url)
    except DatabaseError as exc:
        parser.error(str(exc))
    try:
        for case in cases:
            result = Result(case=case)
            try:
                resolved = database.resolve(case)
                result.rows = resolved.rows
                result.request = build_request(case, resolved)
            except NoData as exc:
                result.skipped = f"sin datos: {exc}"
            results.append(result)
    except DatabaseError as exc:
        parser.error(str(exc))
    finally:
        database.close()

    if args.dry_run:
        for result in results:
            if result.skipped:
                print(f"{result.case['id']}: {result.skipped}")
                continue
            for row, article in zip(result.rows, result.request["articles"], strict=True):
                print(
                    f"{result.case['id']}: {row['law']}, {row['article']} | acciones: {len(article['actions'])} | "
                    f"valores: {len(article['matched_values'])} | write_reason: {str(article['write_reason']).lower()}"
                )
        return 0 if all(result.skipped is None for result in results) else 1

    for result in results:
        if result.skipped:
            print(f"{result.case['id']}: {result.skipped}", file=sys.stderr)
            continue
        result.status, result.body, result.detail, result.total_ms = ask(
            args.base_url, args.api_key, result.request, args.timeout
        )
        if result.status == 200 and result.body is not None:
            result.checks = check_articles(result.case, result.rows, result.body)
        print(f"{result.case['id']}: HTTP {result.status} en {result.total_ms} ms", file=sys.stderr)

    write_out(out, results, args.input_price, args.output_price)
    ok = summarize(results, args.input_price, args.output_price)
    print(f"Respuestas completas en {out} (fuera del repositorio; contienen valores coincidentes de las empresas).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
