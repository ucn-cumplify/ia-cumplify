"""Manual retrieval evaluation of the article embeddings: case AI-044 of backend-cumplify.

It compares the article embedding recipes that backend-cumplify stores in ai_embeddings (by default
art-v1, one vector per article with its text cut at 12,000 characters, and art-v2, chunks of up to
4,000) with a fixed set of questions whose expected articles are known. It spends real tokens, but
only the questions': each one is embedded once through POST /api/v1/embeddings of an ia-cumplify
that is already running, and that service calls OpenAI; the article vectors are already stored. It
is not part of pytest, which collects only tests/.

    uv run python scripts/retrieval_eval.py [--base-url URL] [--recipes ID ...] [--k K ...]
                                            [--question ID ...] [--out FILE] [--self-test]

SERVICE_API_KEY and DATABASE_URL come from the environment or the arguments, never from .env. The
database is read with this script's own read-only connection, and its checks run before the first
request: a recipe without vectors or a missing expected article shows up before any token is spent.

The search is exact, without an index: the question is compared with every chunk of the recipe with
pgvector's cosine distance (<=>), each article keeps the distance of its closest chunk, and all the
articles of the database are ranked by it; exact ties share a position. An expected article is
identified as in any environment, by its norm's bcn_id and its "order" (its position, stable since
the BCN hydration), checked against its readable number, and only among public BCN norms. A question
with an expected article missing here is reported and left out of the metrics; an expected article
without vectors of a recipe counts as not retrieved by that recipe.

Metrics, per recipe and per question type: recall@k, the share of a question's expected articles
among the first k, and MRR, the mean of 1/position of the first expected article in the whole
ranking (0 if the recipe has vectors of none). The console shows metrics only; the detail per
question goes to --out, outside the repository. --self-test checks the metrics with invented data,
without the service or the database.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_SET = HERE / "retrieval_eval_set.json"
SET_VERSION = 1
TYPES = ("referencia", "parafrasis", "largo")
ALL_TYPES = "todas"
DEFAULT_MODEL = "text-embedding-3-large"
DEFAULT_DIMENSIONS = 1024
DEFAULT_RECIPES = ("text-embedding-3-large@1024#art-v1", "text-embedding-3-large@1024#art-v2")
DEFAULT_K = (1, 5, 10)
# The endpoint's cap of texts per request (EMBEDDINGS_MAX_TEXTS, docs/Embeddings/api.md). Export the
# variable if the service runs with another value.
DEFAULT_BATCH = 256
# What backend-cumplify stores in ai_embeddings.embedding_model: model, dimensions and recipe.
RECIPE_ID = re.compile(r"^(?P<model>[^@#\s]+)@(?P<dimensions>[1-9][0-9]*)#(?P<recipe>\S+)$")

# A public BCN norm, as lb. The test norms of a local database ([DEV], [PRUEBA E2E]) never count.
PUBLIC_NORM = (
    "lb.bcn_id IS NOT NULL"
    " AND NOT starts_with(coalesce(lb.title, ''), '[DEV]')"
    " AND NOT starts_with(coalesce(lb.title, ''), '[PRUEBA E2E]')"
)

# --- read-only queries -----------------------------------------------------------------------------

# An expected article: one row per public norm with that bcn_id, with its article at that position, if any.
RESOLVE_QUERY = f"""
    SELECT a.id AS article_id, a.number, lb.type, lb.number AS law
    FROM legal_bodies lb
    LEFT JOIN articles a ON a.legal_body_id = lb.id AND a."order" = %(order)s
    WHERE lb.bcn_id = %(bcn_id)s AND {PUBLIC_NORM}
"""

# The corpus of each recipe: articles that still exist, their chunks, those of norms that are not public
# BCN norms, and those whose text changed after they were embedded (content_hash is the SHA-256 of the
# text the vectors came from).
CORPUS_QUERY = f"""
    SELECT e.embedding_model AS recipe,
           count(DISTINCT e.entity_id) AS articles,
           count(*) AS chunks,
           count(DISTINCT e.entity_id) FILTER (WHERE NOT ({PUBLIC_NORM})) AS not_public,
           count(DISTINCT e.entity_id) FILTER (
               WHERE e.content_hash <> encode(sha256(convert_to(a.text, 'UTF8')), 'hex')) AS changed
    FROM ai_embeddings e
    JOIN articles a ON a.id = e.entity_id
    JOIN legal_bodies lb ON lb.id = a.legal_body_id
    WHERE e.entity_type = 'article' AND e.embedding_model = ANY(%(recipes)s::text[])
    GROUP BY e.embedding_model
"""

COMMON_QUERY = """
    SELECT (SELECT count(*) FROM articles) AS articles,
           (SELECT count(*) FROM (
                SELECT e.entity_id
                FROM ai_embeddings e
                JOIN articles a ON a.id = e.entity_id
                WHERE e.entity_type = 'article' AND e.embedding_model = ANY(%(recipes)s::text[])
                GROUP BY e.entity_id
                HAVING count(DISTINCT e.embedding_model) = %(count)s
           ) common) AS in_all
"""

# Exact search: the distance of every chunk of the recipe, then the closest chunk of each article. No
# vector index (HNSW, IVFFlat) can serve this shape, so nothing is approximate; ai_embeddings has none
# today either. The joins drop the vectors of articles that no longer exist. The id only fixes the order
# of the rows: positions are assigned in Python, where exact ties share one.
SEARCH_QUERY = f"""
    WITH best AS (
        SELECT DISTINCT ON (e.entity_id)
               e.entity_id, e.chunk_index, e.locator, e.embedding <=> %(vector)s::vector AS distance
        FROM ai_embeddings e
        WHERE e.entity_type = 'article' AND e.embedding_model = %(recipe)s
        ORDER BY e.entity_id, distance, e.chunk_index
    )
    SELECT b.entity_id AS article_id, b.distance, b.chunk_index, b.locator, lb.bcn_id, a."order",
           a.number, lb.type, lb.number AS law, lb.title, ({PUBLIC_NORM}) AS public
    FROM best b
    JOIN articles a ON a.id = b.entity_id
    JOIN legal_bodies lb ON lb.id = a.legal_body_id
    ORDER BY b.distance, b.entity_id
"""


class SetError(ValueError):
    """The question set does not have the expected shape."""


class ServiceError(RuntimeError):
    """The embeddings endpoint failed or answered something this script cannot use."""


@dataclass(frozen=True)
class Recipe:
    identifier: str
    label: str


@dataclass
class Expected:
    bcn_id: str
    order: int
    number: str
    # Local id: only to find the article in each ranking. It is never written out.
    article_id: object = None
    found_number: str | None = None
    norm: str | None = None
    problem: str | None = None

    @property
    def label(self) -> str:
        return f"bcn_id {self.bcn_id}, order {self.order} ({self.number})"


@dataclass
class Hit:
    position: int
    distance: float
    chunk_index: int
    locator: str | None
    bcn_id: str | None
    order: int
    number: str | None
    norm: str
    title: str | None
    public: bool
    # Local id: never written out.
    article_id: object = None


@dataclass
class Question:
    id: str
    type: str
    text: str
    note: str
    expected: list[Expected]
    excluded: str | None = None
    # Per recipe identifier: positions, metrics and the first results.
    results: dict[str, dict] = field(default_factory=dict)


# --- the set ---------------------------------------------------------------------------------------


def validate_set(data: object) -> list[dict]:
    if not isinstance(data, dict) or data.get("version") != SET_VERSION:
        raise SetError(f"version must be {SET_VERSION}")
    if not isinstance(data.get("description"), str):
        raise SetError("description must be a string")
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise SetError("questions must be a non-empty list")
    seen: set[str] = set()
    for question in questions:
        question_id = question.get("id") if isinstance(question, dict) else None
        if not isinstance(question_id, str) or not question_id.strip():
            raise SetError("a question without id")
        if question_id in seen:
            raise SetError(f"question {question_id}: repeated id")
        seen.add(question_id)
        if question.get("type") not in TYPES:
            raise SetError(f"question {question_id}: type must be one of {', '.join(TYPES)}")
        # The endpoint answers 422 to a blank text.
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            raise SetError(f"question {question_id}: blank question")
        if not isinstance(question.get("note", ""), str):
            raise SetError(f"question {question_id}: note must be a string")
        expected = question.get("expected")
        if not isinstance(expected, list) or not expected:
            raise SetError(f"question {question_id}: expected must be a non-empty list")
        keys: set[tuple[str, int]] = set()
        for item in expected:
            if not isinstance(item, dict) or not isinstance(item.get("bcn_id"), str) or not item["bcn_id"].strip():
                raise SetError(f"question {question_id}: an expected article without bcn_id")
            if not isinstance(item.get("order"), int) or isinstance(item["order"], bool):
                raise SetError(f"question {question_id}: an expected article without an integer order")
            if not isinstance(item.get("number"), str) or not item["number"].strip():
                raise SetError(f"question {question_id}: an expected article without number")
            key = (item["bcn_id"].strip(), item["order"])
            if key in keys:
                raise SetError(f"question {question_id}: repeated expected article {key}")
            keys.add(key)
    return questions


def load_set(path: Path) -> tuple[dict, list[Question]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = [
        Question(
            id=question["id"],
            type=question["type"],
            text=question["question"].strip(),
            note=question.get("note", ""),
            expected=[Expected(item["bcn_id"].strip(), item["order"], item["number"]) for item in question["expected"]],
        )
        for question in validate_set(data)
    ]
    return data, questions


def parse_recipe(identifier: str, model: str, dimensions: int) -> str | None:
    """None if the recipe is usable with the questions' model and dimensions; otherwise, why not."""
    match = RECIPE_ID.match(identifier)
    if match is None:
        return f"{identifier} no tiene la forma modelo@dimensiones#receta"
    if match["model"] != model or int(match["dimensions"]) != dimensions:
        return (
            f"{identifier} no es de {model}@{dimensions}: las preguntas se embeben con --model y --dimensions, "
            "y solo se comparan con vectores del mismo modelo y tamaño"
        )
    return None


def recipe_labels(identifiers: list[str]) -> list[Recipe]:
    """The recipe name (art-v1) as the label, or the whole identifier if two recipes share a name."""
    names = [identifier.partition("#")[2] or identifier for identifier in identifiers]
    unique = len(set(names)) == len(names)
    return [Recipe(identifier, name if unique else identifier) for identifier, name in zip(identifiers, names)]


# --- the database ----------------------------------------------------------------------------------


class Database:
    """Reads with a read-only connection of its own (never the service's pool)."""

    def __init__(self, database_url: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        # Not autocommit: psycopg opens each transaction with BEGIN READ ONLY, and none is committed.
        self._connection = psycopg.connect(database_url, row_factory=dict_row)
        self._connection.read_only = True

    def rows(self, query: str, params: dict) -> list[dict]:
        with self._connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def end_transaction(self) -> None:
        """Ends the read-only transaction, so that it does not stay open while the endpoint answers."""
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def number_key(number: str) -> str:
    """An article number without "Artículo", accents, ordinal signs, case, spaces or punctuation:
    "Artículo 1°", "Art. 1º" and "artículo 1" give "1"; "Artículo 1° bis" gives "1bis"."""
    text = unicodedata.normalize("NFKD", re.sub("[°º]", "", number))
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    text = re.sub(r"^\s*art(?:iculo\b|\.)?\s*", "", text)
    return re.sub(r"[^0-9a-z]", "", text)


def norm_label(kind: str | None, number: str | None) -> str:
    """"Ley 21.813" from BCN's type ("Ley N 21813") and number, as chat_eval.py labels a law."""
    digits = re.sub(r"\D", "", number or "")
    shown = f"{int(digits):,}".replace(",", ".") if digits else (number or "").strip()
    base = re.sub(r"(?i)\s*(?:n[°º.]?|núm\.?|número)?\s*[\d.]+\s*$", "", kind or "").strip() or "Norma"
    return f"{base} {shown}".strip()


def resolve(db: Database, item: Expected) -> None:
    """Finds the expected article among the public BCN norms, or leaves in item.problem why it cannot."""
    rows = db.rows(RESOLVE_QUERY, {"bcn_id": item.bcn_id, "order": item.order})
    if not rows:
        item.problem = f"no hay una norma pública de la BCN con bcn_id {item.bcn_id}"
        return
    item.norm = norm_label(rows[0]["type"], rows[0]["law"])
    if len(rows) > 1:
        item.problem = f"bcn_id {item.bcn_id} y order {item.order} no identifican un solo artículo ({len(rows)} filas)"
    elif rows[0]["article_id"] is None:
        item.problem = f"la norma {item.norm} (bcn_id {item.bcn_id}) no tiene un artículo con order {item.order}"
    elif number_key(rows[0]["number"] or "") != number_key(item.number):
        item.found_number = rows[0]["number"]
        item.problem = f"el artículo con order {item.order} de {item.norm} es «{rows[0]['number']}», no «{item.number}»"
    else:
        item.article_id, item.found_number = rows[0]["article_id"], rows[0]["number"]


def corpus_stats(db: Database, recipes: list[Recipe]) -> tuple[dict[str, dict], dict]:
    identifiers = [recipe.identifier for recipe in recipes]
    found = {row["recipe"]: row for row in db.rows(CORPUS_QUERY, {"recipes": identifiers})}
    empty = {"articles": 0, "chunks": 0, "not_public": 0, "changed": 0}
    stats = {
        identifier: {key: int(found.get(identifier, empty)[key]) for key in empty} for identifier in identifiers
    }
    common = db.rows(COMMON_QUERY, {"recipes": identifiers, "count": len(identifiers)})[0]
    return stats, {"articles": int(common["articles"]), "in_all": int(common["in_all"])}


def vector_literal(vector: list[float]) -> str:
    """pgvector's text form, sent as a parameter: the script does not need the pgvector package."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def rank(db: Database, recipe: str, vector: list[float]) -> list[Hit]:
    """Every article with vectors of the recipe, closest first."""
    rows = db.rows(SEARCH_QUERY, {"vector": vector_literal(vector), "recipe": recipe})
    positions = assign_positions([float(row["distance"]) for row in rows])
    return [
        Hit(
            position=position,
            distance=float(row["distance"]),
            chunk_index=row["chunk_index"],
            locator=row["locator"],
            bcn_id=row["bcn_id"],
            order=row["order"],
            number=row["number"],
            norm=norm_label(row["type"], row["law"]),
            title=row["title"],
            public=bool(row["public"]),
            article_id=row["article_id"],
        )
        for position, row in zip(positions, rows)
    ]


# --- the endpoint ----------------------------------------------------------------------------------


def batches(items: list, size: int) -> list[list]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def post_embeddings(base_url: str, api_key: str, body: dict, timeout: float) -> dict:
    url = urlsplit(base_url)
    connection_class = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(url.hostname, url.port, timeout=timeout)
    try:
        connection.request(
            "POST",
            f"{url.path.rstrip('/')}/api/v1/embeddings",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
        )
        response = connection.getresponse()
        status, raw = response.status, response.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as exc:
        raise ServiceError(f"sin respuesta del servicio ({type(exc).__name__})") from exc
    finally:
        connection.close()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if status != 200:
        detail = payload.get("detail") if isinstance(payload, dict) else raw[:300]
        raise ServiceError(f"HTTP {status}: {detail}")
    if not isinstance(payload, dict):
        raise ServiceError("la respuesta no es un objeto JSON")
    return payload


def embed_texts(texts: list[str], args: argparse.Namespace, usage: dict) -> list[list[float]]:
    """One vector per text, in order, in requests of at most --batch texts. usage adds up the tokens the
    endpoint reports, including those of the requests before one that fails."""
    vectors: list[list[float]] = []
    for chunk in batches(texts, args.batch):
        body = {"texts": chunk, "model": args.model, "dimensions": args.dimensions}
        payload = post_embeddings(args.base_url, args.api_key, body, args.timeout)
        usage["requests"] += 1
        usage["total_tokens"] += int((payload.get("usage") or {}).get("total_tokens") or 0)
        usage["model"] = payload.get("model")
        got = payload.get("vectors")
        if not isinstance(got, list) or len(got) != len(chunk):
            count = len(got) if isinstance(got, list) else 0
            raise ServiceError(f"{count} vectores para {len(chunk)} preguntas")
        # Another size would fail in pgvector against the recipes' vector(1024) halfway through the run.
        if any(not isinstance(vector, list) or len(vector) != args.dimensions for vector in got):
            raise ServiceError(f"vectores de un tamaño distinto de {args.dimensions}")
        vectors.extend(got)
    return vectors


# --- the metrics (pure functions) ------------------------------------------------------------------


def assign_positions(distances: list[float]) -> list[int]:
    """Positions of distances sorted ascending, from 1. Exact ties share the best position (1, 2, 2, 4)."""
    positions: list[int] = []
    for index, distance in enumerate(distances):
        positions.append(positions[-1] if index and distance == distances[index - 1] else index + 1)
    return positions


def expected_positions(hits: list[Hit], article_ids: list) -> list[int | None]:
    """The position of each expected article in a ranking; None if the recipe has no vector of it."""
    positions = {hit.article_id: hit.position for hit in hits}
    return [positions.get(article_id) for article_id in article_ids]


def question_metrics(positions: list[int | None], ks: list[int]) -> dict:
    """recall@k (share of the expected articles among the first k) and reciprocal rank of one question.
    An expected article without a position counts as not retrieved."""
    found = [position for position in positions if position is not None]
    first = min(found) if found else None
    return {
        "recall": {k: sum(1 for position in found if position <= k) / len(positions) for k in ks},
        "reciprocal_rank": 1 / first if first else 0.0,
        "first_position": first,
    }


def summarize(rows: list[tuple[str, dict]], ks: list[int]) -> dict[str, dict]:
    """Means per question type and over all of them ("todas"), from (type, question_metrics) pairs."""
    groups: dict[str, list[dict]] = {}
    for question_type, metrics in rows:
        groups.setdefault(question_type, []).append(metrics)
        groups.setdefault(ALL_TYPES, []).append(metrics)
    return {
        name: {
            "questions": len(items),
            "recall": {k: sum(item["recall"][k] for item in items) / len(items) for k in ks},
            "mrr": sum(item["reciprocal_rank"] for item in items) / len(items),
        }
        for name, items in groups.items()
    }


# --- the report ------------------------------------------------------------------------------------


def hit_detail(hit: Hit, expected: bool) -> dict:
    return {
        "position": hit.position,
        "distance": round(hit.distance, 6),
        "norm": hit.norm,
        "bcn_id": hit.bcn_id,
        "public_bcn": hit.public,
        "title": hit.title,
        "order": hit.order,
        "number": hit.number,
        "chunk_index": hit.chunk_index,
        "locator": hit.locator,
        "expected": expected,
    }


def expected_detail(item: Expected, hit: Hit | None) -> dict:
    """Where the recipe ranked an expected article; nulls if it has no vector of it."""
    return {
        "bcn_id": item.bcn_id,
        "order": item.order,
        "number": item.number,
        "position": hit.position if hit else None,
        "distance": round(hit.distance, 6) if hit else None,
        "chunk_index": hit.chunk_index if hit else None,
        "locator": hit.locator if hit else None,
    }


def evaluate(question: Question, recipe: Recipe, hits: list[Hit], ks: list[int], top: int) -> list[str]:
    """Stores the question's result with the recipe and returns its warnings."""
    article_ids = [item.article_id for item in question.expected]
    positions = expected_positions(hits, article_ids)
    by_id = {hit.article_id: hit for hit in hits}
    question.results[recipe.identifier] = {
        **question_metrics(positions, ks),
        "expected": [expected_detail(item, by_id.get(item.article_id)) for item in question.expected],
        "top": [hit_detail(hit, hit.article_id in article_ids) for hit in hits if hit.position <= top],
    }
    return [
        f"AVISO {question.id}: {recipe.label} no tiene vectores de {item.label}; cuenta como no recuperado."
        for item, position in zip(question.expected, positions)
        if position is None
    ]


def print_report(
    set_path: Path,
    questions: list[Question],
    recipes: list[Recipe],
    corpus: dict[str, dict],
    common: dict,
    usage: dict,
    warnings: list[str],
    summary: dict[str, dict[str, dict]],
    ks: list[int],
) -> None:
    evaluated = [question for question in questions if not question.excluded]
    print()
    print(f"Conjunto: {set_path} ({len(questions)} preguntas, {len(evaluated)} evaluadas).")
    for recipe in recipes:
        stats = corpus[recipe.identifier]
        print(
            f"Corpus de {recipe.label} ({recipe.identifier}): {stats['articles']} artículos en {stats['chunks']} "
            f"trozos; {stats['not_public']} de normas que no son públicas de la BCN y {stats['changed']} con el "
            "texto cambiado desde que se embebieron."
        )
    print(f"Artículos de la base: {common['articles']}; con vectores de todas las recetas: {common['in_all']}.")
    print(
        f"Tokens del endpoint de embeddings (usage.total_tokens): {usage['total_tokens']}; "
        f"pedidos: {usage['requests']}; modelo: {usage['model'] or '-'}."
    )
    for warning in warnings:
        print(warning)
    if not evaluated:
        print("Ninguna pregunta evaluable: no hay métricas.")
        return

    width = max(10, *(len(recipe.label) for recipe in recipes))
    print()
    print(f"{'Pregunta':10} {'Tipo':12}" + "".join(f" {recipe.label:{width}}" for recipe in recipes))
    for question in questions:
        if question.excluded:
            cells = ["excluida"] + [""] * (len(recipes) - 1)
        else:
            # The position of each expected article with the recipe; "-" if it has no vector of it.
            cells = [
                ", ".join(str(item["position"] or "-") for item in question.results[recipe.identifier]["expected"])
                for recipe in recipes
            ]
        print(f"{question.id:10} {question.type:12}" + "".join(f" {cell:{width}}" for cell in cells))

    print()
    header = f"{'Tipo':12} {'Preg.':>5}  {'Receta':{width}}"
    print(header + "".join(f" {'R@' + str(k):>6}" for k in ks) + f" {'MRR':>6}")
    for question_type in (*TYPES, ALL_TYPES):
        for index, recipe in enumerate(recipes):
            metrics = summary[recipe.identifier].get(question_type)
            if metrics is None:
                break
            prefix = f"{question_type:12} {metrics['questions']:>5}" if index == 0 else " " * 18
            print(
                f"{prefix}  {recipe.label:{width}}"
                + "".join(f" {metrics['recall'][k]:6.3f}" for k in ks)
                + f" {metrics['mrr']:6.3f}"
            )


def write_out(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_out() -> Path:
    return Path(tempfile.gettempdir()) / f"retrieval_eval-{datetime.now():%Y%m%d-%H%M%S}.json"


# --- self-test -------------------------------------------------------------------------------------


def self_test() -> int:
    """--self-test: the metrics, with invented rankings, without the service or the database."""
    failures: list[str] = []
    total = 0

    def check(label: str, got: object, want: object) -> None:
        nonlocal total
        total += 1
        if got != want:
            failures.append(f"{label}: {got!r} en lugar de {want!r}")

    def rounded(recall: dict[int, float], ks: list[int]) -> tuple[float, ...]:
        return tuple(round(recall[k], 4) for k in ks)

    ks = [1, 5, 10]
    check("posiciones con un empate", assign_positions([0.10, 0.20, 0.20, 0.35]), [1, 2, 2, 4])
    check("posiciones sin artículos", assign_positions([]), [])

    hits = [
        Hit(position, distance, 0, None, "1", order, None, "Ley 1", None, True, article_id)
        for position, distance, order, article_id in (
            (1, 0.10, 1, "a"),
            (2, 0.20, 2, "b"),
            (2, 0.20, 3, "c"),
            (4, 0.35, 4, "d"),
        )
    ]
    check("posiciones de los esperados", expected_positions(hits, ["c", "a", "z"]), [2, 1, None])

    # Positions of the expected articles -> recall@1, @5, @10, reciprocal rank and first position.
    metrics: dict[str, dict] = {}
    for label, positions, want in (
        ("esperado primero", [1], ((1.0, 1.0, 1.0), 1.0, 1)),
        ("esperado tercero", [3], ((0.0, 1.0, 1.0), 0.3333, 3)),
        ("dos esperados, 2 y 12", [2, 12], ((0.0, 0.5, 0.5), 0.5, 2)),
        ("esperado sin vectores", [None], ((0.0, 0.0, 0.0), 0.0, None)),
        ("uno sin vectores y otro 7", [None, 7], ((0.0, 0.0, 0.5), 0.1429, 7)),
    ):
        metrics[label] = question_metrics(positions, ks)
        got = metrics[label]
        check(label, (rounded(got["recall"], ks), round(got["reciprocal_rank"], 4), got["first_position"]), want)

    # Means per type and over all of them: (questions, recall@1, @5, @10, MRR).
    summary = summarize(
        [
            ("referencia", metrics["esperado primero"]),
            ("referencia", metrics["esperado tercero"]),
            ("largo", metrics["esperado sin vectores"]),
            ("parafrasis", metrics["dos esperados, 2 y 12"]),
        ],
        ks,
    )
    for name, want in (
        ("referencia", (2, (0.5, 1.0, 1.0), 0.6667)),
        ("largo", (1, (0.0, 0.0, 0.0), 0.0)),
        ("parafrasis", (1, (0.0, 0.5, 0.5), 0.5)),
        (ALL_TYPES, (4, (0.25, 0.625, 0.625), 0.4583)),
    ):
        got = summary[name]
        check(f"medias de {name}", (got["questions"], rounded(got["recall"], ks), round(got["mrr"], 4)), want)

    check("número con °", number_key("Artículo 1°"), "1")
    check("número con º y abreviado", number_key("Art. 1º"), "1")
    check("número bis", number_key("Artículo 1° bis"), "1bis")
    check("número en palabras", number_key("Artículo único"), "unico")
    check("números distintos", number_key("Artículo 1") == number_key("Artículo 11"), False)
    check("vector para pgvector", vector_literal([0.5, -0.25, 1e-05]), "[0.5,-0.25,1e-05]")
    check("lotes", batches([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
    check("receta válida", parse_recipe(DEFAULT_RECIPES[1], DEFAULT_MODEL, DEFAULT_DIMENSIONS), None)
    check("receta de otro tamaño", parse_recipe(f"{DEFAULT_MODEL}@512#art-v2", DEFAULT_MODEL, 1024) is None, False)
    check("receta sin forma", parse_recipe("art-v2", DEFAULT_MODEL, DEFAULT_DIMENSIONS) is None, False)
    check("etiquetas", [recipe.label for recipe in recipe_labels(list(DEFAULT_RECIPES))], ["art-v1", "art-v2"])

    article = {"bcn_id": "1", "order": 2, "number": "Artículo 1"}
    valid = {
        "version": 1,
        "description": "",
        "questions": [{"id": "Q1", "type": "largo", "question": "¿?", "expected": [article]}],
    }
    check("conjunto válido", len(validate_set(valid)), 1)
    for label, change in (
        ("versión", {"version": 2}),
        ("tipo", {"type": "otro"}),
        ("pregunta en blanco", {"question": "  "}),
        ("sin esperados", {"expected": []}),
        ("order como texto", {"expected": [{"bcn_id": "1", "order": "2", "number": "Artículo 1"}]}),
    ):
        broken = json.loads(json.dumps(valid))
        if "version" in change:
            broken.update(change)
        else:
            broken["questions"][0].update(change)
        try:
            validate_set(broken)
            check(f"conjunto con {label} rechazado", "aceptado", "SetError")
        except SetError:
            check(f"conjunto con {label} rechazado", "SetError", "SetError")
    try:
        check("conjunto del repositorio", len(load_set(DEFAULT_SET)[1]) > 0, True)
    except (OSError, ValueError) as exc:
        check("conjunto del repositorio", f"{type(exc).__name__}: {exc}", "válido")

    for failure in failures:
        print(f"FALLA {failure}")
    print(f"Autoprueba: {total - len(failures)} de {total} comprobaciones correctas.")
    return 1 if failures else 0


# --- main ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.environ.get("SERVICE_API_KEY", ""), help="defecto: SERVICE_API_KEY")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""), help="defecto: DATABASE_URL")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, dest="question_set")
    parser.add_argument(
        "--recipes",
        nargs="+",
        default=list(DEFAULT_RECIPES),
        metavar="ID",
        help="identificadores de ai_embeddings.embedding_model que se comparan; defecto: art-v1 y art-v2",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="modelo de las preguntas: el de las recetas")
    parser.add_argument(
        "--dimensions", type=int, default=DEFAULT_DIMENSIONS, help="dimensiones de las preguntas: las de las recetas"
    )
    parser.add_argument("--k", nargs="+", type=int, default=list(DEFAULT_K), help="cortes de recall@k; defecto: 1 5 10")
    parser.add_argument(
        "--top", type=int, help="primeros resultados que guarda --out por pregunta y receta; defecto: el mayor k"
    )
    parser.add_argument("--question", action="append", default=[], help="solo estas preguntas (se puede repetir)")
    parser.add_argument(
        "--batch",
        type=int,
        default=int(os.environ.get("EMBEDDINGS_MAX_TEXTS", DEFAULT_BATCH)),
        help="preguntas por pedido; defecto: EMBEDDINGS_MAX_TEXTS o 256, el tope del endpoint",
    )
    parser.add_argument("--out", type=Path, default=None, help="detalle por pregunta; defecto: el directorio temporal")
    parser.add_argument("--timeout", type=float, default=300.0, help="segundos por pedido")
    parser.add_argument(
        "--self-test", action="store_true", help="comprueba las métricas con datos inventados, sin servicio ni base"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    out = (args.out or default_out()).resolve()
    if out == REPO or REPO in out.parents:
        parser.error("--out tiene que quedar fuera del repositorio: guarda resultados de la base")
    if not args.api_key:
        parser.error("falta SERVICE_API_KEY (o --api-key)")
    if not args.database_url:
        parser.error("falta DATABASE_URL (o --database-url)")
    if len(set(args.recipes)) != len(args.recipes):
        parser.error("--recipes repite un identificador")
    for identifier in args.recipes:
        problem = parse_recipe(identifier, args.model, args.dimensions)
        if problem:
            parser.error(problem)
    if any(k < 1 for k in args.k) or (args.top is not None and args.top < 1) or args.batch < 1:
        parser.error("--k, --top y --batch tienen que ser mayores que 0")
    ks = sorted(set(args.k))
    top = args.top or max(ks)
    recipes = recipe_labels(args.recipes)

    try:
        data, questions = load_set(args.question_set)
    except (OSError, ValueError) as exc:
        parser.error(f"no se pudo leer el conjunto {args.question_set}: {exc}")
    if args.question:
        unknown = set(args.question) - {question.id for question in questions}
        if unknown:
            parser.error(f"preguntas desconocidas: {', '.join(sorted(unknown))}")
        questions = [question for question in questions if question.id in args.question]

    usage = {"total_tokens": 0, "requests": 0, "model": None}
    warnings: list[str] = []
    db = Database(args.database_url)
    try:
        # Every check runs before the first request: a recipe without vectors stops the run before it spends tokens.
        corpus, common = corpus_stats(db, recipes)
        empty = [recipe.identifier for recipe in recipes if corpus[recipe.identifier]["articles"] == 0]
        if empty:
            parser.error(f"sin vectores de artículos en esta base: {', '.join(empty)}")
        for question in questions:
            for item in question.expected:
                resolve(db, item)
            problems = [item.problem for item in question.expected if item.problem]
            if problems:
                question.excluded = "; ".join(problems)
                warnings.append(f"AVISO {question.id}: fuera de las métricas: {question.excluded}.")
        db.end_transaction()

        evaluated = [question for question in questions if not question.excluded]
        texts = list(dict.fromkeys(question.text for question in evaluated))
        try:
            vectors = dict(zip(texts, embed_texts(texts, args, usage))) if texts else {}
        except ServiceError as exc:
            print(f"FALLA del endpoint de embeddings: {exc}", file=sys.stderr)
            print(f"Tokens gastados antes de la falla (usage.total_tokens): {usage['total_tokens']}.", file=sys.stderr)
            return 1
        for question in evaluated:
            for recipe in recipes:
                hits = rank(db, recipe.identifier, vectors[question.text])
                warnings.extend(evaluate(question, recipe, hits, ks, top))
            print(f"{question.id}: evaluada", file=sys.stderr)
    finally:
        db.close()

    summary = {
        recipe.identifier: summarize(
            [(question.type, question.results[recipe.identifier]) for question in evaluated], ks
        )
        for recipe in recipes
    }
    write_out(
        out,
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "set": {"path": str(args.question_set), "version": data["version"], "description": data["description"]},
            "service": {"base_url": args.base_url, "model": args.model, "dimensions": args.dimensions},
            "usage": usage,
            "recipes": [recipe.identifier for recipe in recipes],
            "k": ks,
            "top": top,
            "corpus": corpus,
            "articles": common,
            "metrics": summary,
            "warnings": warnings,
            "questions": [
                {
                    "id": question.id,
                    "type": question.type,
                    "question": question.text,
                    "note": question.note,
                    "expected": [
                        {
                            "bcn_id": item.bcn_id,
                            "order": item.order,
                            "number": item.number,
                            "norm": item.norm,
                            "found_number": item.found_number,
                            "problem": item.problem,
                        }
                        for item in question.expected
                    ],
                    "excluded": question.excluded,
                    "results": question.results,
                }
                for question in questions
            ],
        },
    )
    print_report(args.question_set, questions, recipes, corpus, common, usage, warnings, summary, ks)
    print(f"\nDetalle por pregunta en {out} (fuera del repositorio).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
