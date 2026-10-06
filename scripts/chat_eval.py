"""Manual evaluation set of the chat prompt: CHT-041 to CHT-046 of docs/Chat/test.csv.

It spends real tokens. It sends each case of chat_eval_set.json to an ia-cumplify that is already
running, and that service calls OpenAI. It is not part of pytest, which collects only tests/.

    uv run python scripts/chat_eval.py [--base-url URL] [--case ID ...] [--repeat-case ID]
                                       [--out FILE] [--without-db]

SERVICE_API_KEY and DATABASE_URL come from the environment or the arguments, never from .env. The
passages come from the local database, read with this script's own read-only connection: the
repository keeps only questions and queries. Each request follows the rules the backend mirrors
(point 11 of "Consumo desde el backend" in docs/Chat/api.md), so a 422 is a defect of this script.
Answers and passages go to --out, outside the repository; the console shows metrics only.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import statistics
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
DEFAULT_SET = HERE / "chat_eval_set.json"
KINDS = ("article", "legal_body", "legal_requirement", "vinculation", "obligation")
COVERAGES = ("answered", "not_covered", "uncited", "refused", "no_passages")
FAKE_VERSION_PREFIX = "fake-"

# Unicode White_Space: what ia trims, like .NET Trim(). str.strip() also removes U+001C to U+001F.
WHITE_SPACE = (
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
ONE_LINE_MAX_CHARS = 200
IMAGE = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+=*(?:\s+[A-Za-z0-9+/]{40,}=*)*", re.IGNORECASE
)

# One row per passage, read-only. Laws and articles are matched by their digits, since the number
# columns are free text ("21800", "21.800", "Artículo 1°").
QUERIES = {
    "article": """
        SELECT a.id::text AS id, a.number AS article, a.text, lb.type, lb.number AS law
        FROM articles a JOIN legal_bodies lb ON lb.id = a.legal_body_id
        WHERE regexp_replace(lb.number, '\\D', '', 'g') = %(law)s
          AND (%(article)s::text IS NULL OR regexp_replace(coalesce(a.number, ''), '\\D', '', 'g') = %(article)s)
        ORDER BY a."order", a.id
        LIMIT 1 OFFSET %(offset)s
    """,
    "legal_body": """
        SELECT id::text AS id, title, type, number AS law, summary
        FROM legal_bodies
        WHERE regexp_replace(number, '\\D', '', 'g') = %(law)s
        ORDER BY synced_at DESC, id
        LIMIT 1
    """,
    "legal_requirement": """
        SELECT id::text AS id, name, description
        FROM legal_requirements
        WHERE NOT is_catalog
        ORDER BY created_at, id
        LIMIT 1 OFFSET %(offset)s
    """,
    "vinculation": """
        SELECT v.id::text AS id, rr.name AS app, v.status, v.criticality, v.compliance_percentage,
               v.article_status, v.obligation_status, v.due_date, a.number AS article, lb.type,
               lb.number AS law, o.name AS obligation
        FROM legal_requirement_vinculations v
        JOIN legal_requirements rr ON rr.id = v.rrll_id
        LEFT JOIN articles a ON a.id = v.article_id
        LEFT JOIN legal_bodies lb ON lb.id = a.legal_body_id
        LEFT JOIN obligations o ON o.id = v.obligation_id
        ORDER BY v.created_at, v.id
        LIMIT 1 OFFSET %(offset)s
    """,
    "obligation": """
        SELECT id::text AS id, name, description
        FROM obligations
        ORDER BY created_at, id
        LIMIT 1 OFFSET %(offset)s
    """,
}


class SetError(ValueError):
    """The evaluation set does not have the expected shape."""


class NoData(LookupError):
    """A query found no row: the case cannot be built from this database."""


@dataclass
class Limits:
    """The CHAT_* limits of the running service. Same defaults as the contract; export the variables if
    the service runs with others."""

    question: int = 2000
    history_messages: int = 6
    history_chars: int = 8000
    passages: int = 12
    passage_chars: int = 6000
    passages_total_chars: int = 48000

    @classmethod
    def from_env(cls) -> Limits:
        names = {
            "question": "CHAT_QUESTION_MAX_CHARS",
            "history_messages": "CHAT_HISTORY_MAX_MESSAGES",
            "history_chars": "CHAT_HISTORY_MAX_CHARS",
            "passages": "CHAT_MAX_PASSAGES",
            "passage_chars": "CHAT_PASSAGE_MAX_CHARS",
            "passages_total_chars": "CHAT_PASSAGES_MAX_TOTAL_CHARS",
        }
        defaults = cls()
        return cls(**{key: int(os.environ.get(env, getattr(defaults, key))) for key, env in names.items()})


@dataclass
class Result:
    case: dict
    request: dict | None = None
    status: int | None = None
    events: list[str] = field(default_factory=list)
    text: str = ""
    final: dict | None = None
    final_name: str | None = None
    detail: object = None
    first_delta_ms: float | None = None
    total_ms: float | None = None
    skipped: str | None = None

    @property
    def coverage(self) -> str | None:
        return self.final.get("coverage") if self.final_name == "done" and self.final else None

    @property
    def usage(self) -> dict | None:
        return self.final.get("usage") if self.final else None

    @property
    def outcome(self) -> str:
        if self.skipped:
            return "omitido"
        if self.status is None:
            return "sin respuesta"
        if self.status != 200:
            return f"HTTP {self.status}"
        if self.final_name is None:
            return "cortado"
        if self.final_name == "error":
            return f"error {self.final.get('code')}"
        return self.coverage or "?"


# --- the set ---------------------------------------------------------------------------------------


def load_set(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    seen: set[str] = set()
    for case in cases:
        for key in ("id", "category", "question", "expected", "passages"):
            if key not in case:
                raise SetError(f"case {case.get('id')}: missing {key}")
        if case["id"] in seen:
            raise SetError(f"case {case['id']}: repeated id")
        seen.add(case["id"])
        expected = case["expected"].get("coverage")
        if expected is not None and expected not in COVERAGES:
            raise SetError(f"case {case['id']}: unknown expected coverage {expected}")
        for spec in case["passages"]:
            if "synthetic" in spec:
                if spec["synthetic"].get("kind") not in KINDS:
                    raise SetError(f"case {case['id']}: synthetic passage without a kind of the list")
            elif spec.get("query") not in QUERIES:
                raise SetError(f"case {case['id']}: unknown query {spec.get('query')}")
    return cases


# --- passages --------------------------------------------------------------------------------------


def law_label(kind: str | None, number: str | None) -> str:
    digits = re.sub(r"\D", "", number or "")
    shown = f"{int(digits):,}".replace(",", ".") if digits else (number or "").strip()
    return f"{(kind or 'Norma').strip()} {shown}".strip()


def article_label(number: str | None) -> str:
    return re.sub(r"(?i)^\s*art(?:[íi]culo|\.)\s*", "", number or "").strip() or "s/n"


def passage_from_row(query: str, row: dict) -> dict:
    """The passage as 4.5 would build it from one row. Its texts never reach the repository."""
    if query == "article":
        reference = f"{law_label(row['type'], row['law'])}, art. {article_label(row['article'])}"
        text = row["text"] or ""
    elif query == "legal_body":
        reference = law_label(row["type"], row["law"])
        text = f"Título: {row['title']}\nTipo: {row['type']}\nNúmero: {row['law']}\nResumen: {row['summary'] or ''}"
    elif query == "legal_requirement":
        reference = f"App: {row['name']}"
        text = f"Nombre: {row['name']}\nDescripción: {row['description'] or 'sin descripción'}"
    elif query == "vinculation":
        linked = (
            f"{law_label(row['type'], row['law'])}, art. {article_label(row['article'])}"
            if row["law"]
            else f"Obligación: {row['obligation'] or 'sin nombre'}"
        )
        reference = f"Vinculación de {row['app']}: {linked}"
        lines = [
            f"App: {row['app']}",
            f"Vinculado: {linked}",
            f"Estado: {row['status']}",
            f"Criticidad: {row['criticality']}",
        ]
        if row["compliance_percentage"] is not None:
            lines.append(f"Avance de cumplimiento: {row['compliance_percentage']} %")
        for label, key in (("Estado del artículo", "article_status"), ("Estado de la obligación", "obligation_status")):
            if row[key]:
                lines.append(f"{label}: {row[key]}")
        if row["due_date"] is not None:
            lines.append(f"Vencimiento: {row['due_date']:%Y-%m-%d}")
        text = "\n".join(lines)
    else:
        reference = f"Obligación: {row['name']}"
        text = row["description"] or row["name"]
    return {"id": row["id"], "kind": query, "reference": reference, "text": text}


def simulated_passage(case_id: str, index: int, spec: dict) -> dict:
    """--without-db: a labeled stand-in, so the run exercises the request and the stream only."""
    params = ", ".join(f"{key} {value}" for key, value in sorted(spec.items()) if key not in ("query", "inject"))
    return {
        "id": f"sim-{case_id}-{index}",
        "kind": spec["query"],
        "reference": f"Simulado: {spec['query']} ({params})",
        "text": f"Texto simulado, sin base de datos, para {spec['query']} con {params}.",
    }


class Passages:
    """Reads each passage once, with a read-only connection of its own (never the service's pool)."""

    def __init__(self, database_url: str | None) -> None:
        self._connection = None
        self._cache: dict[str, dict] = {}
        if database_url:
            import psycopg
            from psycopg.rows import dict_row

            # Not autocommit: psycopg opens each transaction with BEGIN READ ONLY, and none is committed.
            self._connection = psycopg.connect(database_url, row_factory=dict_row)
            self._connection.read_only = True

    @property
    def simulated(self) -> bool:
        return self._connection is None

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()

    def build(self, case: dict) -> list[dict]:
        passages = []
        for index, spec in enumerate(case["passages"], start=1):
            if "synthetic" in spec:
                passage = dict(spec["synthetic"])
            elif self._connection is None:
                passage = simulated_passage(case["id"], index, spec)
            else:
                passage = dict(self._row(spec))
            if spec.get("inject"):
                # An instruction planted in data (CHT-007): it must be ignored.
                passage["text"] = f"{passage['text']}\n\n{spec['inject']}"
            passages.append(passage)
        return passages

    def _row(self, spec: dict) -> dict:
        params = {
            "law": spec.get("law"),
            "article": spec.get("article"),
            "offset": max(int(spec.get("position", 1)) - 1, 0),
        }
        key = json.dumps([spec["query"], params], sort_keys=True)
        if key not in self._cache:
            with self._connection.cursor() as cursor:
                cursor.execute(QUERIES[spec["query"]], params)
                row = cursor.fetchone()
            if row is None:
                raise NoData(f"{spec['query']} {params}")
            self._cache[key] = passage_from_row(spec["query"], row)
        return self._cache[key]


# --- the request, as the backend leaves it (api.md, point 11) -------------------------------------


def trim(text: str) -> str:
    return text.strip(WHITE_SPACE)


def one_line(text: str) -> str:
    """Control characters (Cc) and U+2028/U+2029 to a space, runs of white space to one, trimmed and cut."""
    text = "".join(" " if unicodedata.category(char) == "Cc" or char in "\u2028\u2029" else char for char in text)
    text = re.sub(f"[{re.escape(WHITE_SPACE)}]+", " ", text)
    return trim(trim(text)[:ONE_LINE_MAX_CHARS])


def fit_passages(passages: list[dict], limits: Limits) -> list[dict]:
    fitted: list[dict] = []
    ids: set[str] = set()
    total = 0
    for passage in passages:
        text = trim(trim(IMAGE.sub("[imagen omitida]", passage["text"]))[: limits.passage_chars])
        reference = one_line(passage["reference"])
        if not text or not reference or passage["id"] in ids:
            continue
        if len(fitted) == limits.passages or total + len(text) > limits.passages_total_chars:
            break
        ids.add(passage["id"])
        total += len(text)
        fitted.append({"id": passage["id"], "kind": passage["kind"], "reference": reference, "text": text})
    return fitted


def fit_history(messages: list[dict], limits: Limits) -> list[dict]:
    """Point 12: the last turn always goes, its answer shortened if needed; earlier messages, newest
    first, until the first one that does not fit whole."""
    messages = [{"role": m["role"], "content": trim(m["content"])} for m in messages if trim(m["content"])]
    if not messages:
        return []
    kept: list[dict] = []
    room = limits.history_chars
    last = messages[-2:] if len(messages) >= 2 and messages[-2]["role"] == "user" else messages[-1:]
    for message in reversed(last):
        if message["role"] == "assistant":
            question = sum(len(m["content"]) for m in last if m["role"] == "user")
            message = {"role": "assistant", "content": trim(message["content"][: max(room - question, 0)])}
        if message["content"]:
            kept.insert(0, message)
            room -= len(message["content"])
    for message in reversed(messages[: len(messages) - len(last)]):
        if len(kept) == limits.history_messages or len(message["content"]) > room:
            break
        kept.insert(0, message)
        room -= len(message["content"])
    return kept[-limits.history_messages :]


def build_request(case: dict, passages: list[dict], answers: dict[str, str], limits: Limits) -> dict:
    history = []
    for message in case.get("history", []):
        content = message.get("content")
        if "from_case" in message:
            content = answers.get(message["from_case"]) or message["fallback"]
        history.append({"role": message["role"], "content": content})
    request: dict = {
        "question": trim(case["question"])[: limits.question],
        "history": fit_history(history, limits),
        "passages": fit_passages(passages, limits),
    }
    if case.get("app_name"):
        request["context"] = {"app_name": one_line(case["app_name"])}
    return request


# --- the call --------------------------------------------------------------------------------------


def ask(base_url: str, api_key: str, body: dict, timeout: float) -> Result:
    result = Result(case={}, request=body)
    url = urlsplit(base_url)
    connection_class = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(url.hostname, url.port, timeout=timeout)
    payload = json.dumps(body).encode()
    started = time.perf_counter()
    try:
        connection.request(
            "POST",
            f"{url.path.rstrip('/')}/api/v1/chat",
            body=payload,
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
        )
        response = connection.getresponse()
        result.status = response.status
        if response.status != 200:
            raw = response.read().decode("utf-8", "replace")
            result.detail = json.loads(raw).get("detail") if raw.startswith("{") else raw[:300]
            return result
        name: str | None = None
        data: list[str] = []
        while True:
            line = response.readline()
            if not line:
                break
            line = line.decode("utf-8").rstrip("\r\n")
            if line.startswith(":"):
                continue
            if line:
                key, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if key == "event":
                    name = value
                elif key == "data":
                    data.append(value)
                continue
            if data:
                payload_data = json.loads("\n".join(data))
                result.events.append(name or "message")
                if name == "delta":
                    if result.first_delta_ms is None:
                        result.first_delta_ms = round((time.perf_counter() - started) * 1000, 1)
                    result.text += payload_data["text"]
                elif name in ("done", "error"):
                    result.final, result.final_name = payload_data, name
            name, data = None, []
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        result.detail = type(exc).__name__
    finally:
        result.total_ms = round((time.perf_counter() - started) * 1000, 1)
        connection.close()
    return result


# --- the report ------------------------------------------------------------------------------------


# Column title, width and value of each case in the console table: metrics only, never text.
COLUMNS = (
    ("Caso", 5, lambda r: r.case["id"]),
    ("Categoría", 21, lambda r: r.case["category"][:21]),
    ("Esperado", 12, lambda r: r.case["expected"].get("coverage") or "-"),
    ("Obtenido", 17, lambda r: r.outcome[:17]),
    ("Citas", 5, lambda r: len(r.final.get("citations", [])) if r.final else "-"),
    ("Desc.", 5, lambda r: r.final.get("citations_dropped", "-") if r.final else "-"),
    ("Fin", 7, lambda r: (r.final or {}).get("finish_reason", "-")),
    ("Entrada", 7, lambda r: (r.usage or {}).get("prompt_tokens", "-")),
    ("Caché", 6, lambda r: (r.usage or {}).get("cached_tokens", "-")),
    ("Salida", 6, lambda r: (r.usage or {}).get("completion_tokens", "-")),
    ("Razon.", 6, lambda r: (r.usage or {}).get("reasoning_tokens", "-")),
    ("1er delta", 9, lambda r: "-" if r.first_delta_ms is None else r.first_delta_ms),
    ("Total", 8, lambda r: "-" if r.total_ms is None else r.total_ms),
)
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens", "llm_calls")


def summarize(results: list[Result], repeat: tuple[Result, Result] | None, simulated_passages: bool) -> bool:
    ran = [r for r in results if not r.skipped]
    versions = sorted({r.final.get("chat_version") for r in ran if r.final})
    simulated = simulated_passages or any(v and v.startswith(FAKE_VERSION_PREFIX) for v in versions)
    print()
    print(" ".join(f"{title:{width}}" for title, width, _ in COLUMNS))
    for r in results:
        print(" ".join(f"{str(value(r)):{width}}" for _, width, value in COLUMNS))
    print()
    print(f"Versión: {', '.join(v for v in versions if v) or '-'}")
    if simulated:
        reasons = []
        if any(v and v.startswith(FAKE_VERSION_PREFIX) for v in versions):
            reasons.append("respondedor falso (CHAT_FAKE_RESPONDER)")
        if simulated_passages:
            reasons.append("pasajes simulados (--without-db)")
        print(f"CORRIDA SIMULADA: {' y '.join(reasons)}. No evalúa el prompt ni gasta tokens reales.")
    totals = dict.fromkeys(USAGE_KEYS, 0)
    without_usage = 0
    for r in ran:
        if r.usage is None:
            without_usage += bool(r.final or r.status == 200)
            continue
        for key in totals:
            totals[key] += int(r.usage.get(key, 0))
    print(
        "Tokens de la corrida (gasto real informado por ia): "
        + ", ".join(f"{key} {value}" for key, value in totals.items())
        + (f"; {without_usage} respuestas sin usage (estimar)" if without_usage else "")
    )
    for label, values in (
        ("primer delta", [r.first_delta_ms for r in ran if r.first_delta_ms is not None]),
        ("total", [r.total_ms for r in ran if r.total_ms is not None and r.status == 200]),
    ):
        if values:
            mean, median = statistics.mean(values), statistics.median(values)
            print(f"Latencia {label} (ms): media {mean:.1f}, mediana {median:.1f}, máxima {max(values):.1f}")
    counts: dict[str, int] = {}
    for r in ran:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    print("Resultados: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items())))
    dropped = sum(int(r.final.get("citations_dropped", 0)) for r in ran if r.final_name == "done")
    print(f"Citas descartadas: {dropped}")
    mismatches = [
        r for r in ran if r.case["expected"].get("coverage") and r.outcome != r.case["expected"]["coverage"]
    ]
    if mismatches:
        print("Cobertura distinta de la esperada: " + ", ".join(f"{r.case['id']} ({r.outcome})" for r in mismatches))
    skipped = [r for r in results if r.skipped]
    if skipped:
        print("Omitidos por falta de datos: " + ", ".join(f"{r.case['id']} ({r.skipped})" for r in skipped))
    failures = [r for r in ran if r.status != 200 or r.final_name is None or r.outcome == "error internal"]
    for r in failures:
        print(f"FALLA {r.case['id']}: {r.outcome}; detalle: {r.detail}")
    if repeat is not None:
        first, second = repeat
        print()
        print(
            f"Repetición de {first.case['id']}: cached_tokens {((first.usage or {}).get('cached_tokens'))} "
            f"en el primer pedido y {((second.usage or {}).get('cached_tokens'))} en la repetición; "
            f"prompt_tokens {((second.usage or {}).get('prompt_tokens'))}."
        )
        print(
            "  Límite (api.md, Privacidad): 0 con modo explícito sin puntos de corte; no más que los tokens de las "
            "instrucciones (unos 1.000) con un punto de corte al final de ellas. Hoy no hay modo explícito "
            "(CHT-032): con el modo implícito el valor no comprueba nada."
        )
    return not failures


def write_out(path: Path, results: list[Result], repeat: tuple[Result, Result] | None) -> None:
    with path.open("w", encoding="utf-8") as out:
        for r in results + (list(repeat[1:]) if repeat else []):
            out.write(
                json.dumps(
                    {
                        "id": r.case["id"],
                        "category": r.case["category"],
                        "checks": r.case.get("checks", []),
                        "expected": r.case["expected"],
                        "skipped": r.skipped,
                        "status": r.status,
                        "detail": r.detail,
                        "request": r.request,
                        "text": r.text,
                        "events": r.events,
                        "final_event": r.final_name,
                        "final": r.final,
                        "first_delta_ms": r.first_delta_ms,
                        "total_ms": r.total_ms,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def default_out() -> Path:
    return Path(tempfile.gettempdir()) / f"chat_eval-{datetime.now():%Y%m%d-%H%M%S}.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.environ.get("SERVICE_API_KEY", ""), help="defecto: SERVICE_API_KEY")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""), help="defecto: DATABASE_URL")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, dest="case_set")
    parser.add_argument("--case", action="append", default=[], help="solo estos casos (se puede repetir)")
    parser.add_argument("--repeat-case", help="repite este pedido idéntico al final, para leer cached_tokens (CHT-046)")
    parser.add_argument("--out", type=Path, default=None, help="respuestas completas; defecto: el directorio temporal")
    parser.add_argument("--without-db", action="store_true", help="pasajes simulados en lugar de la base")
    parser.add_argument("--timeout", type=float, default=300.0, help="segundos por pedido")
    args = parser.parse_args(argv)

    out = (args.out or default_out()).resolve()
    if out == REPO or REPO in out.parents:
        parser.error("--out tiene que quedar fuera del repositorio: guarda respuestas y pasajes de la empresa")
    if not args.api_key:
        parser.error("falta SERVICE_API_KEY (o --api-key)")
    if not args.database_url and not args.without_db:
        parser.error("falta DATABASE_URL (o --database-url); sin base, usar --without-db")

    cases = load_set(args.case_set)
    if args.case:
        unknown = set(args.case) - {case["id"] for case in cases}
        if unknown:
            parser.error(f"casos desconocidos: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in args.case]
    if args.repeat_case and args.repeat_case not in {case["id"] for case in cases}:
        parser.error("--repeat-case tiene que estar entre los casos que se corren")

    limits = Limits.from_env()
    passages = Passages(None if args.without_db else args.database_url)
    # Every query runs before the first request: a failing one stops the run before it spends tokens.
    built: dict[str, list[dict] | str] = {}
    try:
        for case in cases:
            try:
                built[case["id"]] = passages.build(case)
            except NoData as exc:
                built[case["id"]] = f"sin datos: {exc}"
    finally:
        passages.close()

    results: list[Result] = []
    answers: dict[str, str] = {}
    repeat: tuple[Result, Result] | None = None
    for case in cases:
        case_passages = built[case["id"]]
        if isinstance(case_passages, str):
            results.append(Result(case=case, skipped=case_passages))
            continue
        body = build_request(case, case_passages, answers, limits)
        result = ask(args.base_url, args.api_key, body, args.timeout)
        result.case = case
        results.append(result)
        if result.final_name == "done":
            answers[case["id"]] = result.text
        print(f"{case['id']}: {result.outcome}", file=sys.stderr)
        if case["id"] == args.repeat_case:
            again = ask(args.base_url, args.api_key, body, args.timeout)
            again.case = case
            repeat = (result, again)

    write_out(out, results, repeat)
    ok = summarize(results, repeat, passages.simulated)
    print(f"Respuestas completas en {out} (fuera del repositorio; contienen datos de la empresa).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
