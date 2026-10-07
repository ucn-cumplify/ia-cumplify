"""Evaluación manual del perfil embebido: fase 0 de la tarea 2.4 de backend-cumplify (EMB-008).

Arma, para cada app elegible de las empresas de demostración (Norte, Altiplano y Litoral), el texto de su
perfil con la receta prof-v1, la que embeberá la tarea 2.3; lo embebe con POST /api/v1/embeddings de un
ia-cumplify ya en marcha, y mide, sin reimplementar el puntaje del cruce, cuánto se parece ese vector a los
trozos art-v2 vigentes que backend-cumplify guarda en ai_embeddings:

  (a) la distribución del coseno entre el vector y el mejor trozo de cada artículo, en tres grupos: las
      normas que la app sigue, las candidatas de la taxonomía y las normas públicas sin relación, con una
      muestra por tramo de 0,05 para la revisión humana;
  (b) la evaluación que deja afuera cada norma seguida: rearma el derived sin sus artículos, vuelve a
      embeber el texto y registra el puesto de la norma apartada, por similitud sola, entre las normas
      públicas que la app no sigue;
  (c) variantes de la receta (sin actividades, sin etiquetas, por empresa, el centroide de los vectores
      tax-v1 ya guardados y topes distintos), más un caso sintético con más valores que los topes;
  (d) el control entre rubros: las normas cuyo título nombra el rubro de otra empresa de demostración, y
      la norma de control de AI-036 («emblemas»).

    uv run python scripts/profile_retrieval_eval.py [--apps APP ...] [--variants V ...] [--skip-holdout]
        [--unrelated-sample N] [--out FILE] [--vectors-out FILE] [--base-url URL] [--api-key KEY]
        [--database-url URL] [--timeout S] [--dry-run] [--self-test]

Gasta tokens reales: un texto por app y variante, más uno por norma seguida y variante en (b). Cada texto
distinto se embebe una sola vez, en su propio pedido, para informar sus tokens. --dry-run arma los textos,
muestra los de prof-v1 con su SHA-256 y estima el costo, sin llamar al endpoint. No es parte de pytest.

SERVICE_API_KEY y DATABASE_URL vienen del entorno o de los argumentos, nunca de .env. La base se lee con una
conexión propia de solo lectura (Database de retrieval_eval.py, que nunca repite DATABASE_URL en un error), y
todas sus consultas, incluida una prueba de la búsqueda por similitud, se ejecutan antes del primer pedido.

prof-v1 es una reimplementación declarada, solo del texto: la PR del backend que la implemente tiene que dar el
mismo texto y el mismo SHA-256 para las mismas apps y para los casos de profile_text_parity.json. El recorte por
el total es provisional: su detalle lo fija esa PR, y los casos que dependen de él están marcados. Las reglas
están en "Evaluación del perfil embebido" de docs/Embeddings/requirements.md. Las normas cuentan con la regla
de visibilidad del cruce (sin empresa, y globales o de la BCN), no con la de retrieval_eval.py. La consola
muestra solo métricas; los textos de perfil, que son datos de empresas, van a --out, fuera del repositorio.
--self-test comprueba la receta, los casos de paridad y las métricas con datos inventados, sin servicio ni
base.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from retrieval_eval import Database, DatabaseError, ServiceError, norm_label, post_embeddings, vector_literal

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PARITY_FILE = HERE / "profile_text_parity.json"

MODEL = "text-embedding-3-large"
DIMENSIONS = 1024
# Identificadores de ai_embeddings.embedding_model y ai_taxonomy_values.embedding_model: modelo, dimensiones y
# receta. El perfil tiene que estar en el mismo espacio que los trozos con que se compara.
ARTICLE_MODEL = f"{MODEL}@{DIMENSIONS}#art-v2"
TAXONOMY_MODEL = f"{MODEL}@{DIMENSIONS}#tax-v1"
PROFILE_MODEL = f"{MODEL}@{DIMENSIONS}#prof-v1"
# Caracteres por token del texto en español (medición de las tareas 2.1 y 2.2): solo para estimar el costo.
CHARS_PER_TOKEN = 3.8
# Semilla de la muestra de normas sin relación y de la muestra por tramo: con la misma base, la misma muestra.
SEED = "prof-eval-v1"

# --- la receta prof-v1 -------------------------------------------------------------------------------

SCOPE = "scope"
SECTOR = "productive_sector"
ACTIVITY = "activity_action"
FACILITY = "facility_installation_equipment"
TERRITORY = "territorial_coverage"
# Las cuatro dimensiones de tax-v1, en su orden (TaxonomyEmbeddingText.Dimensions). Nunca el territorio, que
# sigue como filtro, ni others, que no puntúa y en declared trae datos de la empresa.
RECIPE_DIMENSIONS = (SCOPE, SECTOR, ACTIVITY, FACILITY)
DIMENSION_INDEX = {dimension: index for index, dimension in enumerate(RECIPE_DIMENSIONS)}
# Las etiquetas de tax-v1 (TaxonomyEmbeddingText.Labels), una vez por línea.
LABELS = {
    SCOPE: "Ámbito regulatorio",
    SECTOR: "Sector productivo",
    ACTIVITY: "Actividad",
    FACILITY: "Obra, instalación o equipo",
}
PROF_V1_CAPS = {SCOPE: 20, SECTOR: 10, ACTIVITY: 30, FACILITY: 20}
PROF_V1_MAX_LENGTH = 8000

DECLARED = "declared"
DERIVED = "derived"
STRUCTURED = "structured"
EXCLUDED = "excluded"
# Desempate de procedencias con el mismo peso (SuggestionScoring.SourcePriority).
SOURCE_PRIORITY = {DECLARED: 0, DERIVED: 1, STRUCTURED: 2}
# Señal mínima: alguna raíz con peso de las dimensiones de la receta tiene una entrada de una de estas
# procedencias, gane la que gane.
SIGNAL_SOURCES = (DECLARED, DERIVED)


@dataclass(frozen=True)
class Settings:
    """Pesos del perfil y por dimensión: los valores por defecto de AiOptions (AI_SCORE_*)."""

    declared: float = 1.0
    derived_min: float = 0.5
    derived_saturation: float = 5.0
    structured: float = 0.4
    dimension_weights: tuple[tuple[str, float], ...] = (
        (SECTOR, 1.5),
        (SCOPE, 1.0),
        (TERRITORY, 0.5),
        (ACTIVITY, 0.0),
        (FACILITY, 0.0),
    )

    def dimension_weight(self, dimension: str) -> float:
        return dict(self.dimension_weights).get(dimension, 0.0)


SETTINGS = Settings()


@dataclass(frozen=True)
class Entry:
    """Una entrada del perfil con su valor resuelto a la raíz de su familia (ProfileValue del backend)."""

    root: str
    dimension: str
    source: str


@dataclass(frozen=True)
class Weight:
    weight: float
    source: str


@dataclass(frozen=True)
class RootInfo:
    """Una raíz de la taxonomía: su value (nunca display_value) y los artículos de su familia."""

    value: str
    dimension: str
    family_count: int
    stored_family_count: int | None = None
    has_tax_vector: bool = False


@dataclass(frozen=True)
class Candidate:
    """Un valor del perfil que puede entrar en el texto."""

    root: str
    dimension: str
    value: str
    weight: float
    source: str
    family_count: int


@dataclass(frozen=True)
class Recipe:
    """Cómo se arma el texto. prof-v1 es la receta; las demás son sus variantes de la fase 0."""

    name: str
    dimensions: tuple[str, ...] = RECIPE_DIMENSIONS
    caps: tuple[tuple[str, int], ...] = tuple(PROF_V1_CAPS.items())
    # Con un total, los topes por dimensión se reparten en proporción a los valores disponibles.
    proportional_total: int | None = None
    max_length: int = PROF_V1_MAX_LENGTH
    labels: bool = True
    min_signal: bool = True
    # "app": la empresa más el derived de la app; "empresa": solo declared y structured.
    unit: str = "app"

    def caps_for(self, available: dict[str, int]) -> dict[str, int]:
        if self.proportional_total is not None:
            return proportional_caps(available, self.dimensions, self.proportional_total)
        caps = dict(self.caps)
        return {dimension: caps[dimension] for dimension in self.dimensions}


@dataclass
class ProfileText:
    """El texto de un perfil y cómo se eligió. text es None si la receta no da texto (reason dice por qué)."""

    text: str | None
    reason: str | None
    selected: dict[str, list[Candidate]]
    available: dict[str, int]
    cut_by_cap: dict[str, int]
    cut_by_length: dict[str, int]
    signal: bool

    @property
    def sha256(self) -> str | None:
        return sha256_hex(self.text) if self.text is not None else None

    @property
    def length(self) -> int:
        return utf16_len(self.text) if self.text is not None else 0

    def selection(self) -> list[Candidate]:
        return [candidate for values in self.selected.values() for candidate in values]


def derived_weight(occurrences: int, settings: Settings = SETTINGS) -> float:
    """ScoringSettings.DerivedWeight: crece con los artículos vinculados que traen el valor y se satura."""
    return settings.derived_min + (1 - settings.derived_min) * min(
        1.0, math.log(1 + occurrences) / math.log(1 + settings.derived_saturation)
    )


def profile_source_weights(
    entries: list[Entry], occurrences: dict[str, int], settings: Settings = SETTINGS
) -> dict[str, Weight]:
    """SuggestionScoring.ProfileSourceWeights: el máximo entre procedencias, nunca la suma. Una exclusión quita a
    toda la familia, y en un empate de peso gana declared, después derived y al final structured."""
    excluded = {entry.root for entry in entries if entry.source == EXCLUDED}
    weights: dict[str, Weight] = {}
    for entry in entries:
        if entry.root in excluded:
            continue
        if entry.source == DECLARED:
            weight = settings.declared
        elif entry.source == DERIVED:
            weight = derived_weight(occurrences.get(entry.root, 0), settings)
        elif entry.source == STRUCTURED:
            weight = settings.structured
        else:
            weight = 0.0
        if weight <= 0:
            continue
        current = weights.get(entry.root)
        if (
            current is None
            or weight > current.weight
            or (
                weight == current.weight
                and SOURCE_PRIORITY.get(entry.source, 3) < SOURCE_PRIORITY.get(current.source, 3)
            )
        ):
            weights[entry.root] = Weight(weight, entry.source)
    return weights


def utf16_len(text: str) -> int:
    """El largo en unidades UTF-16, el string.Length de .NET: un carácter fuera del plano básico cuenta 2."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def utf16_key(text: str) -> bytes:
    """Clave del orden ordinal por unidades UTF-16, el de string.CompareOrdinal de .NET. No es el orden de los
    puntos de código de Python: difieren desde U+E000 cuando el otro carácter está fuera del plano básico."""
    return text.encode("utf-16-be", "surrogatepass")


def sha256_hex(text: str) -> str:
    """SHA-256 del texto en UTF-8, en hexadecimal en minúsculas: el content_hash de ai_embeddings."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proportional_caps(available: dict[str, int], dimensions: tuple[str, ...], total: int) -> dict[str, int]:
    """Reparte total entre las dimensiones en proporción a sus valores disponibles, por el mayor resto (empates
    en el orden de las dimensiones). Con aritmética entera, sin redondeo. Si caben todos, cada una toma los
    suyos."""
    count = sum(available.get(dimension, 0) for dimension in dimensions)
    if count <= total:
        return {dimension: available.get(dimension, 0) for dimension in dimensions}
    caps = {dimension: total * available.get(dimension, 0) // count for dimension in dimensions}
    remainders = {dimension: total * available.get(dimension, 0) % count for dimension in dimensions}
    left = total - sum(caps.values())
    for dimension in sorted(dimensions, key=lambda item: (-remainders[item], dimensions.index(item)))[:left]:
        caps[dimension] += 1
    return caps


def dimension_rank(candidate: Candidate) -> tuple:
    """Orden dentro de una dimensión: peso de mayor a menor; después la familia más específica (menos artículos,
    que es mayor IDF: se compara el entero para no depender del redondeo); al final el value, ordinal UTF-16."""
    return (-candidate.weight, candidate.family_count, utf16_key(candidate.value))


def global_rank(candidate: Candidate) -> tuple:
    """Orden entre dimensiones para el tope de largo: el mismo, con el orden fijo de las dimensiones antes del
    value. El tope quita siempre el valor de este orden que queda último. Es provisional: el detalle del recorte
    por el total lo fija la PR del backend que implemente prof-v1 (regla 8 de requirements.md)."""
    return (
        -candidate.weight,
        candidate.family_count,
        DIMENSION_INDEX.get(candidate.dimension, len(DIMENSION_INDEX)),
        utf16_key(candidate.value),
    )


def render(selected: dict[str, list[Candidate]], recipe: Recipe) -> str:
    """Una línea por dimensión con valores, en el orden de la receta: «etiqueta: v1; v2», con los valores en orden
    ordinal UTF-16. Una dimensión sin valores no deja línea. Las líneas se unen con \\n, sin salto final."""
    lines = []
    for dimension in recipe.dimensions:
        values = sorted((candidate.value for candidate in selected.get(dimension, [])), key=utf16_key)
        if not values:
            continue
        body = "; ".join(values)
        lines.append(f"{LABELS[dimension]}: {body}" if recipe.labels else body)
    return "\n".join(lines)


def build_profile_text(
    entries: list[Entry],
    occurrences: dict[str, int],
    roots: dict[str, RootInfo],
    recipe: Recipe,
    settings: Settings = SETTINGS,
) -> ProfileText:
    """El texto de un perfil con la receta (prof-v1 o una variante). Función pura.

    1. Pesos por raíz con profile_source_weights (las familias excluidas ya no están).
    2. Candidatos: las raíces con peso de las dimensiones de la receta, con el value de la raíz.
    3. Por dimensión, los primeros según dimension_rank, hasta su tope.
    4. Mientras el texto pase de max_length unidades UTF-16, se quita el último según global_rank.
    5. Señal mínima (si la receta la exige), sobre los candidatos, antes de los topes: alguno tiene una entrada
       declared o derived, gane la procedencia que gane. Con los pesos por defecto da lo mismo que mirar la
       procedencia ganadora de los valores del texto final.
    """
    weights = profile_source_weights(entries, occurrences, settings)
    dimension_of: dict[str, str] = {}
    for entry in entries:
        if entry.root in weights:
            dimension_of.setdefault(entry.root, entry.dimension)

    candidates: dict[str, list[Candidate]] = {dimension: [] for dimension in recipe.dimensions}
    for root, weight in weights.items():
        dimension = dimension_of[root]
        if dimension not in candidates:
            continue
        info = roots[root]
        candidates[dimension].append(
            Candidate(root, dimension, info.value, weight.weight, weight.source, info.family_count)
        )
    for values in candidates.values():
        values.sort(key=dimension_rank)
    signal_roots = {entry.root for entry in entries if entry.source in SIGNAL_SOURCES}
    signal = any(candidate.root in signal_roots for values in candidates.values() for candidate in values)

    available = {dimension: len(values) for dimension, values in candidates.items()}
    caps = recipe.caps_for(available)
    selected = {dimension: candidates[dimension][: caps[dimension]] for dimension in recipe.dimensions}
    cut_by_cap = {dimension: available[dimension] - len(selected[dimension]) for dimension in recipe.dimensions}
    cut_by_length = {dimension: 0 for dimension in recipe.dimensions}

    text = render(selected, recipe)
    while text and utf16_len(text) > recipe.max_length:
        last = max((candidate for values in selected.values() for candidate in values), key=global_rank)
        selected[last.dimension].remove(last)
        cut_by_length[last.dimension] += 1
        text = render(selected, recipe)

    result = ProfileText(text, None, selected, available, cut_by_cap, cut_by_length, signal)
    if not text:
        result.text, result.reason = None, "sin valores en las dimensiones de la receta"
    elif recipe.min_signal and not signal:
        result.text, result.reason = None, "sin señal mínima: ningún valor declared ni derived"
    return result


PROF_V1 = Recipe("prof-v1")
CENTROID = "centroide-tax"
RECIPES: dict[str, Recipe] = {
    "prof-v1": PROF_V1,
    "sin-actividades": replace(
        PROF_V1, name="sin-actividades", dimensions=tuple(d for d in RECIPE_DIMENSIONS if d != ACTIVITY)
    ),
    "sin-etiquetas": replace(PROF_V1, name="sin-etiquetas", labels=False),
    # Solo lo de la empresa (declared y structured), sin la regla de señal mínima: sin textos declarados, mide el
    # supuesto de que un texto de structured se comporta como tax-v1. El informe dice si prof-v1 lo aceptaría.
    "por-empresa": replace(PROF_V1, name="por-empresa", min_signal=False, unit="empresa"),
    "tope-alto": replace(
        PROF_V1,
        name="tope-alto",
        caps=tuple((dimension, 2 * cap) for dimension, cap in PROF_V1_CAPS.items()),
        max_length=2 * PROF_V1_MAX_LENGTH,
    ),
    "tope-proporcional": replace(PROF_V1, name="tope-proporcional", proportional_total=sum(PROF_V1_CAPS.values())),
}
VARIANTS = ("prof-v1", "sin-actividades", "sin-etiquetas", "por-empresa", CENTROID, "tope-alto", "tope-proporcional")
# El caso sintético solo mide los topes.
SYNTHETIC_VARIANTS = ("prof-v1", "tope-alto", "tope-proporcional")

# --- el catálogo de demostración ---------------------------------------------------------------------


@dataclass(frozen=True)
class DemoCompany:
    """Una empresa de AiSuggestionsDemoCatalog del backend: la busca por RUT, como el seeder."""

    key: str
    rut: str
    name: str
    keywords: tuple[str, ...]
    app_id: str

    @property
    def rut_key(self) -> str:
        return self.rut.replace(".", "").replace("-", "").strip().lower()


# Copia de Cumplify.Api/Seeders/AiSuggestionsDemoCatalog.cs: las palabras van sin tildes y como raíz, y se buscan
# en el título sin distinguir mayúsculas ni tildes.
DEMO_COMPANIES = (
    DemoCompany(
        "norte",
        "11.111.111-1",
        "Empresa Norte SpA",
        ("miner", "cobre", "litio", "relave", "yacimiento", "arido", "desaliniz"),
        "d3000000-0000-4000-8000-000000000014",
    ),
    DemoCompany(
        "altiplano",
        "66.666.666-6",
        "Empresa Altiplano SpA",
        ("energ", "electric"),
        "d3000000-0000-4000-8000-000000000024",
    ),
    DemoCompany(
        "litoral",
        "10.000.000-0",
        "Empresa Litoral Ltda",
        ("transport", "de transito", "vehicul"),
        "d3000000-0000-4000-8000-000000000034",
    ),
)
DEMO_BY_KEY = {demo.key: demo for demo in DEMO_COMPANIES}
# La norma de control de AI-036 se creó a mano, así que su id cambia con la base: se busca por el título.
CONTROL_KEYWORD = "emblema"
TEST_PREFIXES = ("[DEV]", "[PRUEBA E2E]")

# Constantes del backend: LegalRequirementAppTypeCatalog, LegalRequirementSourceTypeCatalog, CompanyStatusCatalog
# y TemplateCompanyCatalog.
APP_TYPE = "legal_requirements"
SOURCE_TYPE = "legal_body"
ACTIVE = "Activa"
TEMPLATE_COMPANY = "11111111-0000-0000-0000-000000000001"

# --- el perfil de una app (funciones puras) ------------------------------------------------------------


@dataclass
class AppData:
    """Lo que el script lee de una app elegible. Los ids son locales y nunca salen del informe."""

    id: str
    company_id: str
    company: str
    name: str
    demo: str | None
    demo_app: bool
    # declared, structured y excluded: los de la empresa, que valen para todas sus apps.
    company_entries: list[Entry]
    # Artículos vinculados (LinkedArticles.Of) y su norma.
    linked: dict[str, str | None]
    # Clasificaciones de esos artículos: (valor crudo, raíz, dimensión).
    article_values: dict[str, set[tuple[str, str, str]]]
    # derived guardado: valor crudo -> occurrences.
    stored_derived: dict[str, int | None]
    followed: set[str]
    notified: set[str]
    discarded: set[str]
    # Sugerencias guardadas de la app, por norma: solo para marcar las candidatas, sin recalcular su score.
    suggestions: dict[str, dict]
    candidates: set[str] = field(default_factory=set)
    synthetic: bool = False


def derived_profile(
    article_values: dict[str, set[tuple[str, str, str]]],
    linked: dict[str, str | None],
    held_out: str | None = None,
) -> tuple[list[Entry], dict[str, int], dict[str, int]]:
    """El derived de una app desde sus vinculaciones, como el regenerador (ReadDerivedAsync) y el cruce
    (LoadDerivedOccurrencesAsync), sin los artículos de held_out si se indica. Devuelve las entradas, las
    ocurrencias por raíz (artículos distintos de la familia) y las ocurrencias por valor crudo."""
    raw: dict[str, tuple[str, str]] = {}
    by_root: dict[str, set[str]] = defaultdict(set)
    by_raw: dict[str, set[str]] = defaultdict(set)
    for article, values in article_values.items():
        if held_out is not None and linked.get(article) == held_out:
            continue
        for raw_id, root, dimension in values:
            raw[raw_id] = (root, dimension)
            by_root[root].add(article)
            by_raw[raw_id].add(article)
    entries = [Entry(root, dimension, DERIVED) for _, (root, dimension) in sorted(raw.items())]
    return (
        entries,
        {root: len(articles) for root, articles in by_root.items()},
        {raw_id: len(articles) for raw_id, articles in by_raw.items()},
    )


def app_profile(app: AppData, recipe: Recipe, held_out: str | None = None) -> tuple[list[Entry], dict[str, int]]:
    """Las entradas y ocurrencias con que se arma el texto: por app, lo de la empresa más el derived de la app; por
    empresa, solo lo de la empresa."""
    if recipe.unit == "empresa":
        return list(app.company_entries), {}
    entries, occurrences, _ = derived_profile(app.article_values, app.linked, held_out)
    return list(app.company_entries) + entries, occurrences


def match_roots(
    entries: list[Entry], occurrences: dict[str, int], country_roots: set[str], settings: Settings = SETTINGS
) -> set[str]:
    """Las raíces que generan candidatas en CompanySuggestionMatcher: con peso en el perfil, de una dimensión con
    peso y que no son el país."""
    weights = profile_source_weights(entries, occurrences, settings)
    return {
        entry.root
        for entry in entries
        if entry.root in weights and settings.dimension_weight(entry.dimension) > 0 and entry.root not in country_roots
    }


def synthetic_app(apps: list[AppData]) -> AppData:
    """Una app inventada con el perfil unido de las apps elegidas, para que los topes corten."""
    entries = sorted(
        {entry for app in apps for entry in app.company_entries}, key=lambda e: (e.root, e.dimension, e.source)
    )
    article_values: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    linked: dict[str, str | None] = {}
    for app in apps:
        linked.update(app.linked)
        for article, values in app.article_values.items():
            article_values[article] |= values
    return AppData(
        id="sintetica",
        company_id="sintetica",
        company="(caso sintético)",
        name="Unión de los perfiles de las apps elegidas",
        demo=None,
        demo_app=False,
        company_entries=entries,
        linked=linked,
        article_values=dict(article_values),
        stored_derived={},
        followed=set().union(*(app.followed for app in apps)),
        notified=set().union(*(app.notified for app in apps)),
        discarded=set().union(*(app.discarded for app in apps)),
        suggestions={},
        candidates=set().union(*(app.candidates for app in apps)),
        synthetic=True,
    )


# --- vectores y métricas (funciones puras) -----------------------------------------------------------


def parse_vector(text: str) -> list[float]:
    """La forma de texto de pgvector ("[0.1,0.2]") como lista."""
    return [float(value) for value in text.strip().strip("[]").split(",") if value.strip()]


def centroid(selection: list[Candidate], vectors: dict[str, list[float]]) -> tuple[list[float] | None, int, int]:
    """El centroide de los vectores tax-v1 de los valores elegidos, ponderado por su peso en el perfil y llevado a
    norma 1. Devuelve el vector (None si ningún valor tiene vector), los usados y los que no tienen vector."""
    total: list[float] | None = None
    used = missing = 0
    for candidate in selection:
        vector = vectors.get(candidate.root)
        if vector is None:
            missing += 1
            continue
        if total is None:
            total = [0.0] * len(vector)
        for index, value in enumerate(vector):
            total[index] += candidate.weight * value
        used += 1
    if total is None:
        return None, used, missing
    norm = math.sqrt(sum(value * value for value in total))
    if norm == 0:
        return None, used, missing
    return [value / norm for value in total], used, missing


def percentile(values: list[float], q: float) -> float | None:
    """Percentil q (0 a 1) con interpolación lineal entre los valores ordenados, como el de numpy por defecto."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


QUANTILES = (("p05", 0.05), ("p10", 0.10), ("p25", 0.25), ("p50", 0.50), ("p75", 0.75), ("p90", 0.90), ("p95", 0.95))


def describe(values: list[float]) -> dict:
    """Cantidad, media, extremos y percentiles de una distribución, redondeados a 4 decimales."""
    if not values:
        return {"n": 0}
    stats = {"n": len(values), "media": round(sum(values) / len(values), 4), "min": round(min(values), 4)}
    for name, q in QUANTILES:
        stats[name] = round(percentile(values, q), 4)
    stats["max"] = round(max(values), 4)
    return stats


def auc(positives: list[float], negatives: list[float]) -> float | None:
    """Área bajo la curva ROC (Mann-Whitney): la probabilidad de que un positivo al azar tenga más similitud que un
    negativo al azar; los empates valen la mitad. 0,5 es no separar."""
    if not positives or not negatives:
        return None
    combined = sorted([(value, 1) for value in positives] + [(value, 0) for value in negatives])
    rank_sum = 0.0
    index = 0
    while index < len(combined):
        end = index
        while end + 1 < len(combined) and combined[end + 1][0] == combined[index][0]:
            end += 1
        average = (index + end) / 2 + 1
        rank_sum += average * sum(label for _, label in combined[index : end + 1])
        index = end + 1
    count = len(positives)
    return (rank_sum - count * (count + 1) / 2) / (count * len(negatives))


def positions_desc(scores: dict[str, float]) -> dict[str, int]:
    """Puestos desde 1 por similitud de mayor a menor. Los empates exactos comparten el mejor puesto (1, 2, 2, 4)."""
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    positions: dict[str, int] = {}
    previous: float | None = None
    previous_position = 0
    for index, (key, score) in enumerate(ordered):
        position = previous_position if index and score == previous else index + 1
        positions[key] = position
        previous, previous_position = score, position
    return positions


def percentile_rank(value: float, population: list[float]) -> float | None:
    """En qué percentil de population cae value: la parte menor, más la mitad de los empates, de 0 a 100."""
    if not population:
        return None
    below = sum(1 for item in population if item < value)
    equal = sum(1 for item in population if item == value)
    return round(100 * (below + equal / 2) / len(population), 1)


def top_mean(values: list[float], count: int = 3) -> float:
    """El promedio de las min(count, n) mayores similitudes: un agregado de similitud, no el score del cruce."""
    best = sorted(values, reverse=True)[:count]
    return sum(best) / len(best)


BIN_WIDTH = 0.05


def bin_index(cosine: float) -> int:
    """El tramo de 0,05 del coseno: 7 es [0,35, 0,40). El 1 va al último tramo y un negativo al -1."""
    if cosine < 0:
        return -1
    return min(int(math.floor(cosine / BIN_WIDTH + 1e-9)), int(round(1 / BIN_WIDTH)) - 1)


def bin_label(index: int) -> str:
    if index < 0:
        return "< 0"
    return f"{index * BIN_WIDTH:.2f}-{(index + 1) * BIN_WIDTH:.2f}".replace(".", ",")


def sample_key(key: str) -> str:
    """Orden fijo para elegir muestras: el SHA-256 de la semilla con el id. Con la misma base, la misma muestra."""
    return hashlib.sha256(f"{SEED}:{key}".encode()).hexdigest()


def fold(text: str | None) -> str:
    """Minúsculas y sin tildes, como unaccent con ILIKE en PostgreSQL para títulos en español."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def title_keywords(title: str | None, keywords: tuple[str, ...]) -> list[str]:
    """Las palabras clave que aparecen en el título, con el criterio del seeder de demostración."""
    folded = fold(title)
    return [keyword for keyword in keywords if fold(keyword) in folded]


def holdout_summary(results: list[dict], ks: tuple[int, ...] = (1, 5, 10, 25)) -> dict:
    """Mediana del puesto, MRR y recall@k de las normas apartadas que se evaluaron, por el máximo del mejor trozo
    (la métrica principal) y por el promedio de los tres mejores artículos."""
    evaluated = [item for item in results if item.get("position") is not None]
    skipped: dict[str, int] = defaultdict(int)
    for item in results:
        if item.get("position") is None:
            skipped[item.get("skipped") or "sin puesto"] += 1
    summary: dict = {"evaluated": len(evaluated), "skipped": dict(skipped)}
    if not evaluated:
        return summary
    positions = [item["position"] for item in evaluated]
    summary["median_position"] = percentile([float(p) for p in positions], 0.5)
    summary["mrr"] = round(sum(1 / p for p in positions) / len(positions), 4)
    summary["recall"] = {str(k): round(sum(1 for p in positions if p <= k) / len(positions), 4) for k in ks}
    top3 = [item["position_top3"] for item in evaluated]
    summary["median_position_top3"] = percentile([float(p) for p in top3], 0.5)
    summary["mrr_top3"] = round(sum(1 / p for p in top3) / len(top3), 4)
    summary["median_cosine"] = round(percentile([item["cosine"] for item in evaluated], 0.5), 4)
    return summary


# --- la base -------------------------------------------------------------------------------------------

# La regla de visibilidad del cruce, sobre el alias lb: sin empresa, y global o de la BCN
# (CompanySuggestionMatcher.CandidateNormsAsync; la constante LegalBodyPublic que propone la tarea 2.4). No deja
# fuera las normas de prueba ([DEV], [PRUEBA E2E]): el cruce tampoco, y la norma de control de AI-036 es una.
PUBLIC_NORM = "lb.company_id IS NULL AND (lb.is_global OR lb.bcn_id IS NOT NULL)"
# Sobre el alias a: sin las filas «Encabezado» y «Promulgación» (AiSql.ArticleIsNotHeaderOrPromulgation).
NOT_HEADER = "coalesce(a.number, '') NOT IN ('Encabezado', 'Promulgación')"
# Sobre los alias e y a: el trozo es del texto actual del artículo (la misma expresión de AiSql).
CURRENT_CHUNK = "coalesce(e.content_hash = encode(sha256(convert_to(a.text, 'UTF8')), 'hex'), false)"
RUT_KEY = "lower(btrim(replace(replace(c.rut, '.', ''), '-', '')))"

COMPANIES_QUERY = f"""
    SELECT c.id::text AS id, c.name, c.status, {RUT_KEY} AS rut_key
    FROM companies c
    WHERE {RUT_KEY} = ANY(%(ruts)s::text[])
"""

APPS_QUERY = """
    SELECT lr.id::text AS id, lr.name, lr.app_type, lr.is_catalog,
           lr.company_id::text AS company_id, c.name AS company, c.status
    FROM legal_requirements lr
    JOIN companies c ON c.id = lr.company_id
    WHERE lr.company_id = ANY(%(companies)s::uuid[]) OR lr.id = ANY(%(apps)s::uuid[])
    ORDER BY c.name, lr.name, lr.id
"""

# CompanyProfileQueries.LoadEntriesAsync: las entradas con su valor resuelto a la raíz. Trae también el derived
# guardado, que solo se compara con el que se rearma desde las vinculaciones.
ENTRIES_QUERY = """
    SELECT e.company_id::text AS company_id, e.legal_requirement_id::text AS app_id, e.source,
           e.taxonomy_value_id::text AS value_id, COALESCE(v.canonical_id, v.id)::text AS root_id,
           v.dimension, e.occurrences
    FROM company_profile_entries e
    JOIN ai_taxonomy_values v ON v.id = e.taxonomy_value_id
    WHERE e.company_id = ANY(%(companies)s::uuid[])
"""

# LinkedArticles.Of: los artículos de cuerpos legales que la app sigue, de su propia empresa.
LINKED_QUERY = f"""
    SELECT DISTINCT v.rrll_id::text AS app_id, v.article_id::text AS article_id,
           a.legal_body_id::text AS legal_body_id
    FROM legal_requirement_vinculations v
    JOIN legal_requirements lr ON lr.id = v.rrll_id AND lr.company_id = v.company_id
    LEFT JOIN articles a ON a.id = v.article_id
    WHERE v.rrll_id = ANY(%(apps)s::uuid[]) AND v.source_type = '{SOURCE_TYPE}' AND v.article_id IS NOT NULL
"""

# Las normas seguidas, como las excluye CandidateNormsAsync: toda vinculación de la app a un cuerpo legal.
FOLLOWED_QUERY = f"""
    SELECT DISTINCT v.rrll_id::text AS app_id, v.source_id::text AS legal_body_id
    FROM legal_requirement_vinculations v
    JOIN legal_requirements lr ON lr.id = v.rrll_id AND lr.company_id = v.company_id
    WHERE v.rrll_id = ANY(%(apps)s::uuid[]) AND v.source_type = '{SOURCE_TYPE}'
"""

CLASSIFICATIONS_QUERY = """
    SELECT c.article_id::text AS article_id, c.taxonomy_value_id::text AS value_id,
           COALESCE(t.canonical_id, t.id)::text AS root_id, t.dimension
    FROM ai_article_classifications c
    JOIN ai_taxonomy_values t ON t.id = c.taxonomy_value_id
    WHERE c.article_id = ANY(%(articles)s::uuid[])
"""

# Las raíces con su value y los artículos de su familia contados en vivo, con el SQL de
# AiSql.RecalculateFamilyArticleCountsAsync: es lo que la próxima ejecución usaría para el IDF.
ROOTS_QUERY = """
    WITH families AS (
        SELECT COALESCE(t.canonical_id, t.id) AS root_id, count(DISTINCT c.article_id) AS articles
        FROM ai_article_classifications c
        JOIN ai_taxonomy_values t ON t.id = c.taxonomy_value_id
        GROUP BY 1
    )
    SELECT r.id::text AS id, r.dimension, r.value, r.canonical_id IS NOT NULL AS is_member,
           r.family_article_count AS stored_family_count, COALESCE(f.articles, 0) AS family_count,
           (r.embedding IS NOT NULL AND r.embedding_model = %(tax_model)s) AS has_tax_vector
    FROM ai_taxonomy_values r
    LEFT JOIN families f ON f.root_id = r.id
    WHERE r.id = ANY(%(roots)s::uuid[])
"""

TAX_VECTORS_QUERY = """
    SELECT id::text AS id, embedding::text AS embedding
    FROM ai_taxonomy_values
    WHERE id = ANY(%(roots)s::uuid[]) AND embedding IS NOT NULL AND embedding_model = %(tax_model)s
"""

COUNTRY_QUERY = "SELECT taxonomy_value_id::text AS id FROM cl_territories WHERE kind = 'country'"

# Las sugerencias guardadas de la app: las notificadas dejan de ser candidatas (CandidateNormsAsync).
SUGGESTIONS_QUERY = """
    SELECT s.legal_requirement_id::text AS app_id, s.legal_body_id::text AS legal_body_id, s.score, s.status,
           s.notified_at IS NOT NULL AS notified
    FROM legal_body_company_suggestions s
    WHERE s.legal_requirement_id = ANY(%(apps)s::uuid[])
"""

# RegulatoryAlertGateway.DiscardedLegalBodyIdsAsync: las normas con un artículo descartado en una alerta de la app.
DISCARDED_QUERY = """
    SELECT DISTINCT ra.rrll_id::text AS app_id, ra.legal_body_id::text AS legal_body_id
    FROM regulatory_alert_suggestion_discards d
    JOIN regulatory_alert_suggestions s ON s.id = d.regulatory_alert_suggestion_id
    JOIN regulatory_alerts ra ON ra.id = s.regulatory_alert_id
    JOIN legal_requirements lr ON lr.id = ra.rrll_id AND lr.company_id = ra.company_id
    WHERE ra.rrll_id = ANY(%(apps)s::uuid[])
"""

# Las normas públicas con un artículo clasificado con una raíz dada: la base de las candidatas.
CANDIDATE_ROOTS_QUERY = f"""
    SELECT DISTINCT c.legal_body_id::text AS legal_body_id, COALESCE(t.canonical_id, t.id)::text AS root_id
    FROM ai_article_classifications c
    JOIN ai_taxonomy_values t ON t.id = c.taxonomy_value_id
    JOIN legal_bodies lb ON lb.id = c.legal_body_id
    WHERE COALESCE(t.canonical_id, t.id) = ANY(%(roots)s::uuid[]) AND {PUBLIC_NORM}
"""

NORMS_QUERY = f"""
    SELECT lb.id::text AS id, lb.title, lb.type, lb.number, lb.bcn_id, lb.is_global
    FROM legal_bodies lb
    WHERE {PUBLIC_NORM}
"""

# Los artículos de normas públicas, sin encabezado ni promulgación, con sus trozos art-v2: los vigentes son el
# corpus; los demás quedan fuera y se cuentan.
CORPUS_QUERY = f"""
    SELECT a.id::text AS article_id, a.legal_body_id::text AS legal_body_id, a.number, a."order",
           count(*) AS chunks, count(*) FILTER (WHERE {CURRENT_CHUNK}) AS current_chunks
    FROM ai_embeddings e
    JOIN articles a ON a.id = e.entity_id
    JOIN legal_bodies lb ON lb.id = a.legal_body_id
    WHERE e.entity_type = 'article' AND e.embedding_model = %(model)s AND {PUBLIC_NORM} AND {NOT_HEADER}
    GROUP BY a.id, a.legal_body_id, a.number, a."order"
"""

PUBLIC_ARTICLES_QUERY = f"""
    SELECT count(*) AS articles
    FROM articles a
    JOIN legal_bodies lb ON lb.id = a.legal_body_id
    WHERE {PUBLIC_NORM} AND {NOT_HEADER}
"""

# Búsqueda exacta, sin índice: el coseno de cada trozo vigente de los artículos del corpus y, por artículo, el
# trozo más cercano. Vuelve a comprobar que el trozo es del texto actual.
SIMILARITY_QUERY = f"""
    SELECT DISTINCT ON (e.entity_id)
           e.entity_id::text AS article_id, e.chunk_index, e.locator,
           e.embedding <=> %(vector)s::vector AS distance
    FROM ai_embeddings e
    JOIN articles a ON a.id = e.entity_id
    WHERE e.entity_type = 'article' AND e.embedding_model = %(model)s
      AND e.entity_id = ANY(%(articles)s::uuid[]) AND {CURRENT_CHUNK}
    ORDER BY e.entity_id, distance, e.chunk_index
"""

PROBE_VECTOR_QUERY = f"""
    SELECT e.embedding::text AS embedding
    FROM ai_embeddings e
    JOIN articles a ON a.id = e.entity_id
    WHERE e.entity_type = 'article' AND e.embedding_model = %(model)s AND {CURRENT_CHUNK}
    ORDER BY e.entity_id, e.chunk_index
    LIMIT 1
"""

CHUNK_TEXT_QUERY = """
    SELECT e.entity_id::text AS article_id, e.chunk_index, e.chunk_text
    FROM ai_embeddings e
    JOIN unnest(%(articles)s::uuid[], %(chunks)s::int[]) AS s(article_id, chunk_index)
      ON s.article_id = e.entity_id AND s.chunk_index = e.chunk_index
    WHERE e.entity_type = 'article' AND e.embedding_model = %(model)s
"""


class AppSelectionError(ValueError):
    """Una app pedida por id no existe o no es elegible."""


@dataclass(frozen=True)
class NormInfo:
    id: str
    title: str | None
    kind: str | None
    number: str | None
    bcn_id: str | None
    is_global: bool

    @property
    def label(self) -> str:
        return norm_label(self.kind, self.number)

    @property
    def is_test(self) -> bool:
        return (self.title or "").startswith(TEST_PREFIXES)


@dataclass(frozen=True)
class ArticleInfo:
    id: str
    norm: str
    number: str | None
    order: int | None


@dataclass(frozen=True)
class Similarity:
    cosine: float
    chunk_index: int
    locator: str | None


@dataclass
class Dataset:
    apps: list[AppData]
    roots: dict[str, RootInfo]
    norms: dict[str, NormInfo]
    # Solo el corpus: artículos de normas públicas con algún trozo art-v2 vigente.
    articles: dict[str, ArticleInfo]
    corpus: dict
    country_roots: set[str]
    tax_vectors: dict[str, list[float]]
    warnings: list[str]

    def norm_articles(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        for article in self.articles.values():
            result[article.norm].append(article.id)
        return result


def resolve_apps(rows: list[dict], requested: list[str], companies: dict[str, str]) -> tuple[list[dict], list[str]]:
    """Las apps elegibles pedidas y los avisos. Una app elegible es de Requisitos Legales, no es de catálogo y su
    empresa está activa y no es la plantilla (SuggestionApps.Of y AiSuggestionWorker). companies: id de empresa ->
    clave de demostración. Una app pedida por id que no existe o no es elegible es un error (AppSelectionError)."""
    warnings: list[str] = []
    explicit = {item for item in requested if item not in DEMO_BY_KEY}
    keys = {item for item in requested if item in DEMO_BY_KEY}
    chosen: list[dict] = []
    for row in rows:
        reasons = []
        if row["app_type"] != APP_TYPE:
            reasons.append(f"es de tipo {row['app_type']}")
        if row["is_catalog"]:
            reasons.append("es de catálogo")
        if row["status"] != ACTIVE:
            reasons.append(f"su empresa está {row['status']}")
        if row["company_id"] == TEMPLATE_COMPANY:
            reasons.append("es de la empresa plantilla")
        wanted = row["id"] in explicit or companies.get(row["company_id"]) in keys
        if row["id"] in explicit and reasons:
            raise AppSelectionError(f"la app {row['id']} no es elegible: {', '.join(reasons)}")
        if wanted and not reasons:
            chosen.append(row)
    missing = explicit - {row["id"] for row in rows}
    if missing:
        raise AppSelectionError(f"no existen las apps {', '.join(sorted(missing))}")
    for key in sorted(keys):
        demo = DEMO_BY_KEY[key]
        if key not in companies.values():
            warnings.append(f"AVISO: no existe una empresa con el RUT de {demo.name} ({demo.rut}); se omite.")
        elif demo.app_id not in {row["id"] for row in chosen}:
            warnings.append(f"AVISO: la app de demostración {demo.app_id} de {demo.name} no existe o no es elegible.")
    return chosen, warnings


def load_dataset(db: Database, requested: list[str], unrelated_sample: int, need_tax_vectors: bool) -> Dataset:
    """Lee todo lo que la evaluación necesita, en una transacción de solo lectura."""
    warnings: list[str] = []
    # Las tres empresas de demostración, aunque se pidan apps por id: así una app suya pedida por id también
    # tiene su control entre rubros.
    companies: dict[str, str] = {}
    for row in db.rows(COMPANIES_QUERY, {"ruts": [demo.rut_key for demo in DEMO_COMPANIES]}):
        companies[row["id"]] = next(demo.key for demo in DEMO_COMPANIES if demo.rut_key == row["rut_key"])
    keys = {item for item in requested if item in DEMO_BY_KEY}
    explicit = [item for item in requested if item not in DEMO_BY_KEY]
    wanted_companies = [company for company, key in companies.items() if key in keys]
    app_rows = db.rows(APPS_QUERY, {"companies": wanted_companies, "apps": explicit})
    rows, app_warnings = resolve_apps(app_rows, requested, companies)
    warnings.extend(app_warnings)
    if not rows:
        return Dataset([], {}, {}, {}, {}, set(), {}, warnings)

    app_ids = [row["id"] for row in rows]
    company_ids = sorted({row["company_id"] for row in rows})
    entries_rows = db.rows(ENTRIES_QUERY, {"companies": company_ids})
    linked_rows = db.rows(LINKED_QUERY, {"apps": app_ids})
    followed_rows = db.rows(FOLLOWED_QUERY, {"apps": app_ids})
    suggestion_rows = db.rows(SUGGESTIONS_QUERY, {"apps": app_ids})
    discarded_rows = db.rows(DISCARDED_QUERY, {"apps": app_ids})
    linked_articles = sorted({row["article_id"] for row in linked_rows})
    classification_rows = db.rows(CLASSIFICATIONS_QUERY, {"articles": linked_articles})
    country_roots = {row["id"] for row in db.rows(COUNTRY_QUERY, {})}

    values_by_article: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in classification_rows:
        values_by_article[row["article_id"]].add((row["value_id"], row["root_id"], row["dimension"]))

    apps: list[AppData] = []
    demo_app_ids = {demo.app_id for demo in DEMO_COMPANIES}
    for row in rows:
        company_entries = sorted(
            {
                Entry(entry["root_id"], entry["dimension"], entry["source"])
                for entry in entries_rows
                if entry["company_id"] == row["company_id"] and entry["app_id"] is None
            },
            key=lambda e: (e.root, e.dimension, e.source),
        )
        linked = {item["article_id"]: item["legal_body_id"] for item in linked_rows if item["app_id"] == row["id"]}
        suggestions = {
            item["legal_body_id"]: {
                "score": float(item["score"]),
                "status": item["status"],
                "notified": bool(item["notified"]),
            }
            for item in suggestion_rows
            if item["app_id"] == row["id"]
        }
        apps.append(
            AppData(
                id=row["id"],
                company_id=row["company_id"],
                company=row["company"],
                name=row["name"],
                demo=companies.get(row["company_id"]),
                demo_app=row["id"] in demo_app_ids,
                company_entries=company_entries,
                linked=linked,
                article_values={article: set(values_by_article.get(article, set())) for article in linked},
                stored_derived={
                    entry["value_id"]: entry["occurrences"]
                    for entry in entries_rows
                    if entry["app_id"] == row["id"] and entry["source"] == DERIVED
                },
                followed={item["legal_body_id"] for item in followed_rows if item["app_id"] == row["id"]},
                notified={norm for norm, item in suggestions.items() if item["notified"]},
                discarded={item["legal_body_id"] for item in discarded_rows if item["app_id"] == row["id"]},
                suggestions=suggestions,
            )
        )

    for app in apps:
        _, _, by_raw = derived_profile(app.article_values, app.linked)
        if by_raw != app.stored_derived:
            warnings.append(
                f"AVISO {app.company} / {app.name}: el derived guardado no coincide con sus vinculaciones "
                f"({len(app.stored_derived)} valores guardados, {len(by_raw)} rearmados): el script usa el rearmado, "
                "que es el que dejaría la próxima ejecución."
            )

    # Las raíces de todos los perfiles, incluidas las de los derived con una norma apartada (son subconjuntos).
    root_ids = sorted(
        {entry.root for app in apps for entry in app.company_entries}
        | {root for values in values_by_article.values() for _, root, _ in values}
    )
    roots: dict[str, RootInfo] = {}
    stale_counts = 0
    for row in db.rows(ROOTS_QUERY, {"roots": root_ids, "tax_model": TAXONOMY_MODEL}):
        if row["is_member"]:
            warnings.append(f"AVISO: el valor {row['id']} es raíz de un perfil pero tiene canonical_id (cadena).")
        stored = row["stored_family_count"]
        if (stored or 0) != row["family_count"]:
            stale_counts += 1
        roots[row["id"]] = RootInfo(
            row["value"], row["dimension"], int(row["family_count"]), stored, bool(row["has_tax_vector"])
        )
    if stale_counts:
        warnings.append(
            f"AVISO: {stale_counts} raíces tienen family_article_count guardado distinto del conteo en vivo; el "
            "script usa el conteo en vivo, el que recalcula la próxima ejecución."
        )
    missing_roots = set(root_ids) - set(roots)
    if missing_roots:
        raise DatabaseError(f"{len(missing_roots)} raíces de los perfiles no están en ai_taxonomy_values")

    # Candidatas de la taxonomía: CandidateNormsAsync en un cruce completo, con el perfil que arma prof-v1.
    roots_by_app = {}
    for app in apps:
        entries, occurrences = app_profile(app, PROF_V1)
        roots_by_app[app.id] = match_roots(entries, occurrences, country_roots)
    all_match_roots = sorted(set().union(*roots_by_app.values())) if roots_by_app else []
    norms_by_root: dict[str, set[str]] = defaultdict(set)
    for row in db.rows(CANDIDATE_ROOTS_QUERY, {"roots": all_match_roots}):
        norms_by_root[row["root_id"]].add(row["legal_body_id"])
    for app in apps:
        reachable = set().union(*(norms_by_root.get(root, set()) for root in roots_by_app[app.id]))
        app.candidates = reachable - app.followed - app.notified - app.discarded

    norms = {
        row["id"]: NormInfo(row["id"], row["title"], row["type"], row["number"], row["bcn_id"], bool(row["is_global"]))
        for row in db.rows(NORMS_QUERY, {})
    }
    articles: dict[str, ArticleInfo] = {}
    stale = 0
    chunks = 0
    for row in db.rows(CORPUS_QUERY, {"model": ARTICLE_MODEL}):
        if row["current_chunks"]:
            articles[row["article_id"]] = ArticleInfo(
                row["article_id"], row["legal_body_id"], row["number"], row["order"]
            )
            chunks += int(row["current_chunks"])
        else:
            stale += 1
    public_articles = int(db.rows(PUBLIC_ARTICLES_QUERY, {})[0]["articles"])
    corpus = {
        "public_norms": len(norms),
        "public_norms_with_chunks": len({article.norm for article in articles.values()}),
        "public_articles": public_articles,
        "articles_with_current_chunks": len(articles),
        "current_chunks": chunks,
        "articles_with_stale_chunks": stale,
        "articles_without_chunks": public_articles - len(articles) - stale,
        "test_norms": sum(1 for norm in norms.values() if norm.is_test),
    }

    for app in apps:
        private = app.followed - set(norms)
        if private:
            warnings.append(
                f"AVISO {app.company} / {app.name}: {len(private)} normas seguidas no son públicas; el cruce nunca las "
                "sugiere y quedan fuera de los grupos y de la evaluación que deja una norma afuera."
            )

    tax_vectors: dict[str, list[float]] = {}
    if need_tax_vectors:
        for row in db.rows(TAX_VECTORS_QUERY, {"roots": root_ids, "tax_model": TAXONOMY_MODEL}):
            tax_vectors[row["id"]] = parse_vector(row["embedding"])
    if not any(norm for norm in norms.values() if CONTROL_KEYWORD in fold(norm.title)):
        warnings.append(
            f"AVISO: ninguna norma pública tiene «{CONTROL_KEYWORD}» en el título: no hay norma de control."
        )
    return Dataset(apps, roots, norms, articles, corpus, country_roots, tax_vectors, warnings)


def similarity_search(db: Database, vector: list[float], article_ids: list[str]) -> dict[str, Similarity]:
    """El coseno del trozo vigente más cercano de cada artículo del corpus."""
    rows = db.rows(
        SIMILARITY_QUERY, {"vector": vector_literal(vector), "model": ARTICLE_MODEL, "articles": article_ids}
    )
    return {
        row["article_id"]: Similarity(1 - float(row["distance"]), row["chunk_index"], row["locator"]) for row in rows
    }


def probe_similarity(db: Database, article_ids: list[str]) -> int:
    """Ejecuta la búsqueda con un vector ya guardado, antes de gastar tokens: comprueba la consulta y el tipo."""
    rows = db.rows(PROBE_VECTOR_QUERY, {"model": ARTICLE_MODEL})
    if not rows:
        return 0
    return len(similarity_search(db, parse_vector(rows[0]["embedding"]), article_ids))


def chunk_texts(db: Database, pairs: list[tuple[str, int]]) -> dict[tuple[str, int], str]:
    if not pairs:
        return {}
    rows = db.rows(
        CHUNK_TEXT_QUERY,
        {
            "articles": [article for article, _ in pairs],
            "chunks": [chunk for _, chunk in pairs],
            "model": ARTICLE_MODEL,
        },
    )
    return {(row["article_id"], row["chunk_index"]): row["chunk_text"] for row in rows}


# --- el endpoint -----------------------------------------------------------------------------------------


def embed_text(text: str, args: argparse.Namespace, usage: dict) -> list[float]:
    """Un texto por pedido, para conocer sus tokens. usage suma lo que informa el endpoint."""
    body = {"texts": [text], "model": MODEL, "dimensions": DIMENSIONS}
    payload = post_embeddings(args.base_url, args.api_key, body, args.timeout)
    usage["requests"] += 1
    tokens = int((payload.get("usage") or {}).get("total_tokens") or 0)
    usage["total_tokens"] += tokens
    usage["model"] = payload.get("model")
    vectors = payload.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != 1:
        raise ServiceError(f"{len(vectors) if isinstance(vectors, list) else 0} vectores para 1 texto")
    if not isinstance(vectors[0], list) or len(vectors[0]) != DIMENSIONS:
        raise ServiceError(f"un vector de un tamaño distinto de {DIMENSIONS}")
    usage["by_text"][sha256_hex(text)] = tokens
    return vectors[0]


# --- el plan: qué textos y vectores se miden ---------------------------------------------------------------


@dataclass
class Unit:
    """Un vector que se mide: una app con una variante y, en (b), una norma apartada."""

    app: AppData
    variant: str
    held_out: str | None = None
    profile: ProfileText | None = None
    text: str | None = None
    vector: list[float] | None = None
    reason: str | None = None
    centroid_used: int = 0
    centroid_missing: int = 0


def plan_units(dataset: Dataset, variants: list[str], holdout: bool) -> list[Unit]:
    """Arma todos los textos (y los centroides, que no cuestan tokens) antes del primer pedido."""
    units: list[Unit] = []
    norm_articles = dataset.norm_articles()
    apps = list(dataset.apps)
    if len(apps) > 1:
        apps.append(synthetic_app(dataset.apps))
    for app in apps:
        app_variants = [variant for variant in variants if not app.synthetic or variant in SYNTHETIC_VARIANTS]
        held_outs: list[str | None] = [None]
        if holdout and not app.synthetic:
            held_outs += sorted(
                app.followed,
                key=lambda norm: (dataset.norms[norm].label, norm) if norm in dataset.norms else ("~", norm),
            )
        for variant in app_variants:
            for held_out in held_outs:
                unit = Unit(app, variant, held_out)
                if held_out is not None and held_out not in dataset.norms:
                    unit.reason = "no es pública: el cruce nunca la sugiere"
                elif held_out is not None and not norm_articles.get(held_out):
                    unit.reason = "sin trozos art-v2 vigentes"
                else:
                    build_unit(unit, dataset)
                units.append(unit)
    return units


def build_unit(unit: Unit, dataset: Dataset) -> None:
    """El texto del vector, o su centroide de tax-v1."""
    recipe = PROF_V1 if unit.variant == CENTROID else RECIPES[unit.variant]
    entries, occurrences = app_profile(unit.app, recipe, unit.held_out)
    unit.profile = build_profile_text(entries, occurrences, dataset.roots, recipe)
    if unit.profile.text is None:
        unit.reason = unit.profile.reason
        return
    if unit.variant == CENTROID:
        vector, used, missing = centroid(unit.profile.selection(), dataset.tax_vectors)
        unit.vector, unit.centroid_used, unit.centroid_missing = vector, used, missing
        if vector is None:
            unit.reason = "ningún valor elegido tiene vector tax-v1"
        return
    unit.text = unit.profile.text


def texts_to_embed(units: list[Unit]) -> list[str]:
    """Los textos distintos, en el orden en que aparecen: un texto repetido se embebe una vez."""
    return list(dict.fromkeys(unit.text for unit in units if unit.text is not None))


# --- la evaluación -------------------------------------------------------------------------------------------


@dataclass
class Groups:
    """Las normas del corpus según su relación con la app."""

    followed: set[str]
    candidates: set[str]
    unrelated: set[str]
    # Notificadas o descartadas en la app: fuera de los grupos; en el orden por similitud aparecen con su rótulo.
    other: set[str]
    unrelated_total: int


def app_groups(app: AppData, norm_articles: dict[str, list[str]], unrelated_sample: int) -> Groups:
    """Los grupos son disjuntos. En una app real las candidatas ya excluyen las seguidas, las notificadas y las
    descartadas; en el caso sintético, que une varias apps, la resta lo asegura."""
    corpus = set(norm_articles)
    followed = app.followed & corpus
    other = (app.notified | app.discarded) & corpus - followed
    candidates = app.candidates & corpus - followed - other
    unrelated = corpus - followed - candidates - other
    total = len(unrelated)
    if unrelated_sample and len(unrelated) > unrelated_sample:
        unrelated = set(sorted(unrelated, key=sample_key)[:unrelated_sample])
    return Groups(followed, candidates, unrelated, other, total)


def norm_group(norm: str, app: AppData, groups: Groups) -> str:
    if norm in groups.followed:
        return "seguida"
    if norm in app.notified:
        return "notificada"
    if norm in app.discarded:
        return "descartada"
    if norm in groups.candidates:
        return "candidata"
    if norm in groups.unrelated:
        return "sin_relacion"
    return "sin_relacion_fuera_de_muestra"


def norm_similarities(sims: dict[str, Similarity], norm_articles: dict[str, list[str]]) -> tuple[dict, dict]:
    """Por norma: el coseno de su mejor artículo y el promedio de sus tres mejores artículos."""
    best: dict[str, float] = {}
    top3: dict[str, float] = {}
    for norm, articles in norm_articles.items():
        values = [sims[article].cosine for article in articles if article in sims]
        if values:
            best[norm] = max(values)
            top3[norm] = top_mean(values)
    return best, top3


def evaluate_main(
    unit: Unit,
    sims: dict[str, Similarity],
    dataset: Dataset,
    norm_articles: dict[str, list[str]],
    groups: Groups,
) -> dict:
    """(a), (c) y (d) para el vector de una app con una variante."""
    app = unit.app
    best, top3 = norm_similarities(sims, norm_articles)

    def cosines(norms: set[str], only: set[str] | None = None) -> list[float]:
        return [
            sims[article].cosine
            for norm in norms
            for article in norm_articles.get(norm, [])
            if article in sims and (only is None or article in only)
        ]

    followed = cosines(groups.followed)
    linked = cosines(groups.followed, set(app.linked))
    candidates = cosines(groups.candidates)
    suggested = cosines({norm for norm in groups.candidates if norm in app.suggestions})
    unrelated = cosines(groups.unrelated)
    unrelated_norms = [best[norm] for norm in groups.unrelated if norm in best]
    p90 = percentile(unrelated_norms, 0.90)
    p95 = percentile(unrelated_norms, 0.95)

    ranked = {norm: score for norm, score in best.items() if norm not in groups.followed}
    positions = positions_desc(ranked)
    result: dict = {
        "cosine": {
            "seguidas": describe(followed),
            "seguidas_vinculados": describe(linked),
            "candidatas": describe(candidates),
            "candidatas_con_sugerencia": describe(suggested),
            "sin_relacion": describe(unrelated),
        },
        "norm_cosine": {
            "seguidas": describe([best[norm] for norm in groups.followed if norm in best]),
            "candidatas": describe([best[norm] for norm in groups.candidates if norm in best]),
            "sin_relacion": describe(unrelated_norms),
        },
        "auc": {
            "seguidas_vs_sin_relacion": rounded(auc(followed, unrelated)),
            "candidatas_vs_sin_relacion": rounded(auc(candidates, unrelated)),
        },
        "ranking": {
            "norms": len(ranked),
            "top": [
                norm_detail(norm, positions[norm], best[norm], top3[norm], dataset, app, groups)
                for norm in sorted(ranked, key=lambda item: (positions[item], item))[:15]
            ],
        },
        "control": [
            {
                **norm_detail(norm, positions.get(norm), best[norm], top3[norm], dataset, app, groups),
                "percentile_vs_unrelated": percentile_rank(best[norm], unrelated_norms),
            }
            for norm in sorted(best)
            if CONTROL_KEYWORD in fold(dataset.norms[norm].title)
        ],
    }
    if app.demo and not app.synthetic:
        own = DEMO_BY_KEY[app.demo].keywords
        others = tuple(keyword for demo in DEMO_COMPANIES if demo.key != app.demo for keyword in demo.keywords)
        foreign = {}
        for norm in ranked:
            matched = title_keywords(dataset.norms[norm].title, others)
            if matched and not title_keywords(dataset.norms[norm].title, own):
                foreign[norm] = matched
        ordered = sorted(foreign, key=lambda norm: (positions[norm], norm))
        result["cross_rubro"] = {
            "own_keywords": list(own),
            "other_keywords": list(others),
            "norms": len(foreign),
            "in_top_10": sum(1 for norm in foreign if positions[norm] <= 10),
            "in_top_25": sum(1 for norm in foreign if positions[norm] <= 25),
            "over_p90_unrelated": sum(1 for norm in foreign if p90 is not None and best[norm] >= p90),
            "over_p95_unrelated": sum(1 for norm in foreign if p95 is not None and best[norm] >= p95),
            "unrelated_p90": rounded(p90),
            "unrelated_p95": rounded(p95),
            "top": [
                {
                    **norm_detail(norm, positions[norm], best[norm], top3[norm], dataset, app, groups),
                    "keywords": foreign[norm],
                }
                for norm in ordered[:5]
            ],
        }
    return result


def evaluate_holdout(
    unit: Unit, sims: dict[str, Similarity], norm_articles: dict[str, list[str]], groups: Groups
) -> dict:
    """(b): el puesto de la norma apartada entre las normas públicas que la app no sigue, por similitud sola."""
    best, top3 = norm_similarities(sims, norm_articles)
    held_out = unit.held_out
    if held_out not in best:
        return {"position": None, "skipped": "sin similitud: ningún artículo con trozos vigentes"}
    ranked = {norm: score for norm, score in best.items() if norm not in groups.followed or norm == held_out}
    ranked_top3 = {norm: top3[norm] for norm in ranked}
    unrelated_norms = [best[norm] for norm in groups.unrelated if norm in best]
    return {
        "position": positions_desc(ranked)[held_out],
        "position_top3": positions_desc(ranked_top3)[held_out],
        "ranked_norms": len(ranked),
        "cosine": round(best[held_out], 4),
        "top3": round(top3[held_out], 4),
        "percentile_vs_unrelated": percentile_rank(best[held_out], unrelated_norms),
    }


def rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def norm_detail(
    norm: str,
    position: int | None,
    cosine: float,
    top3: float,
    dataset: Dataset,
    app: AppData,
    groups: Groups,
) -> dict:
    info = dataset.norms[norm]
    return {
        "position": position,
        "norm": info.label,
        "title": info.title,
        "bcn_id": info.bcn_id,
        "test_norm": info.is_test,
        "group": norm_group(norm, app, groups),
        "cosine": round(cosine, 4),
        "top3": round(top3, 4),
        "suggestion_score": app.suggestions.get(norm, {}).get("score"),
    }


def review_bins(
    sims: dict[str, Similarity], norm_articles: dict[str, list[str]], groups: Groups, per_group: int = 2
) -> tuple[list[dict], list[tuple[str, int]]]:
    """Cuántos artículos de cada grupo caen en cada tramo de 0,05 y una muestra fija de cada uno para la revisión
    humana. Devuelve los tramos y los trozos cuyo texto hay que leer."""
    members: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for group, norms in (
        ("seguidas", groups.followed),
        ("candidatas", groups.candidates),
        ("sin_relacion", groups.unrelated),
    ):
        for norm in norms:
            for article in norm_articles.get(norm, []):
                if article in sims:
                    members[bin_index(sims[article].cosine)][group].append(article)
    bins = []
    pairs = []
    for index in sorted(members):
        sample = []
        for group in ("seguidas", "candidatas", "sin_relacion"):
            for article in sorted(members[index][group], key=sample_key)[:per_group]:
                sample.append({"group": group, "article_id": article})
                pairs.append((article, sims[article].chunk_index))
        bins.append(
            {
                "range": bin_label(index),
                "counts": {group: len(members[index][group]) for group in ("seguidas", "candidatas", "sin_relacion")},
                "sample": sample,
            }
        )
    return bins, pairs


def fill_samples(bins: list[dict], sims: dict[str, Similarity], dataset: Dataset, texts: dict) -> None:
    """Completa la muestra con la norma, el artículo y el comienzo del trozo. El id local no queda en el informe."""
    for item in bins:
        for entry in item["sample"]:
            article_id = entry.pop("article_id")
            article = dataset.articles[article_id]
            norm = dataset.norms[article.norm]
            similarity = sims[article_id]
            excerpt = texts.get((article_id, similarity.chunk_index)) or ""
            entry.update(
                {
                    "norm": norm.label,
                    "title": norm.title,
                    "bcn_id": norm.bcn_id,
                    "article": article.number,
                    "order": article.order,
                    "cosine": round(similarity.cosine, 4),
                    "chunk_index": similarity.chunk_index,
                    "locator": similarity.locator,
                    "excerpt": re.sub(r"\s+", " ", excerpt)[:300],
                }
            )


def text_detail(unit: Unit, usage_by_text: dict[str, int]) -> dict:
    profile = unit.profile
    detail: dict = {"reason": unit.reason}
    if profile is not None:
        detail.update(
            {
                "text": unit.text,
                "sha256": sha256_hex(unit.text) if unit.text else None,
                "length_utf16": utf16_len(unit.text) if unit.text else None,
                "tokens": usage_by_text.get(sha256_hex(unit.text)) if unit.text else 0,
                "signal": profile.signal,
                "available": profile.available,
                "selected": {dimension: len(values) for dimension, values in profile.selected.items()},
                "cut_by_cap": profile.cut_by_cap,
                "cut_by_length": profile.cut_by_length,
            }
        )
    if unit.variant == CENTROID:
        # Sin texto propio: promedia los vectores tax-v1 de los valores que elige prof-v1.
        detail.update(
            {
                "tokens": 0,
                "selection_sha256": profile.sha256 if profile is not None else None,
                "centroid_values": unit.centroid_used,
                "centroid_without_vector": unit.centroid_missing,
            }
        )
    return detail


def evaluate(
    dataset: Dataset,
    units: list[Unit],
    similarity: Callable[[list[float]], dict[str, Similarity]],
    read_chunks: Callable[[list[tuple[str, int]]], dict[tuple[str, int], str]],
    usage_by_text: dict[str, int],
    unrelated_sample: int,
    progress: Callable[[str], None] = lambda message: None,
) -> list[dict]:
    """Mide cada unidad con vector. similarity y read_chunks son la base (o sus dobles en la autoprueba)."""
    norm_articles = dataset.norm_articles()
    reports: dict[str, dict] = {}
    groups_by_app: dict[str, Groups] = {}
    review: list[tuple[list[dict], dict[str, Similarity]]] = []
    pairs: list[tuple[str, int]] = []
    for unit in units:
        app = unit.app
        if app.id not in reports:
            groups = app_groups(app, norm_articles, unrelated_sample)
            groups_by_app[app.id] = groups
            reports[app.id] = app_header(app, dataset, groups)
        groups = groups_by_app[app.id]
        variant = reports[app.id]["variants"].setdefault(unit.variant, {"holdout": {"norms": []}})
        if unit.held_out is None:
            variant.update(text_detail(unit, usage_by_text))
            if unit.vector is not None:
                sims = similarity(unit.vector)
                variant.update(evaluate_main(unit, sims, dataset, norm_articles, groups))
                if unit.variant == "prof-v1":
                    bins, new_pairs = review_bins(sims, norm_articles, groups)
                    variant["bins"] = bins
                    review.append((bins, sims))
                    pairs.extend(new_pairs)
            progress(f"{app.company} / {app.name}: {unit.variant} medida")
            continue
        info = dataset.norms.get(unit.held_out)
        item = {
            "norm": info.label if info else None,
            "title": info.title if info else None,
            "bcn_id": info.bcn_id if info else None,
            "sha256": sha256_hex(unit.text) if unit.text else None,
            "tokens": usage_by_text.get(sha256_hex(unit.text)) if unit.text else 0,
        }
        if unit.vector is None:
            item.update({"position": None, "skipped": unit.reason})
        else:
            item.update(evaluate_holdout(unit, similarity(unit.vector), norm_articles, groups))
        variant["holdout"]["norms"].append(item)
    texts = read_chunks(pairs)
    for bins, sims in review:
        fill_samples(bins, sims, dataset, texts)
    for report in reports.values():
        for variant in report["variants"].values():
            variant["holdout"]["summary"] = holdout_summary(variant["holdout"]["norms"])
    return list(reports.values())


def app_header(app: AppData, dataset: Dataset, groups: Groups) -> dict:
    return {
        "app_id": app.id,
        "app": app.name,
        "company": app.company,
        "demo": app.demo,
        "demo_app": app.demo_app,
        "synthetic": app.synthetic,
        "linked_articles": len(app.linked),
        "groups": {
            "seguidas": len(groups.followed),
            "candidatas": len(groups.candidates),
            "sin_relacion": len(groups.unrelated),
            "sin_relacion_total": groups.unrelated_total,
            "notificadas_o_descartadas": len(groups.other),
            "seguidas_no_publicas": len(app.followed - set(dataset.norms)),
        },
        "variants": {},
    }


def pooled_summary(apps: list[dict], variants: list[str]) -> dict:
    """Por variante, la evaluación que deja una norma afuera de todas las apps reales juntas y las AUC medias."""
    summary = {}
    for variant in variants:
        reports = [app["variants"][variant] for app in apps if not app["synthetic"] and variant in app["variants"]]
        norms = [item for report in reports for item in report["holdout"]["norms"]]
        aucs = {
            name: [report["auc"][name] for report in reports if report.get("auc", {}).get(name) is not None]
            for name in ("seguidas_vs_sin_relacion", "candidatas_vs_sin_relacion")
        }
        summary[variant] = {
            "apps_with_vector": sum(1 for report in reports if "auc" in report),
            "holdout": holdout_summary(norms),
            "auc_mean": {name: rounded(sum(values) / len(values)) if values else None for name, values in aucs.items()},
        }
    return summary


# --- el informe ----------------------------------------------------------------------------------------------


def fmt(value: object, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}".replace(".", ",")
    return str(value)


def table_row(name: str, cells: list[object], widths: list[int]) -> str:
    """Una fila de tabla: el nombre a la izquierda y cada celda alineada a la derecha en su ancho."""
    return f"  {name:18}" + "".join(f" {fmt(cell):>{width}}" for cell, width in zip(cells, widths, strict=True))


APP_COLUMNS = ["Largo", "Tokens", "AUC seg.", "AUC cand.", "p50 seg.", "p50 cand.", "p50 s/r", "p95 s/r"]
APP_COLUMNS += ["Apartadas", "Mediana", "MRR"]
SUMMARY_COLUMNS = ["Evaluadas", "Mediana", "MRR", "R@1", "R@5", "R@10", "R@25", "AUC seg."]


def print_report(report: dict) -> None:
    corpus = report["corpus"]
    usage = report["usage"]
    print()
    print(
        f"Corpus art-v2: {corpus['articles_with_current_chunks']} artículos de {corpus['public_norms_with_chunks']} "
        f"normas públicas, en {corpus['current_chunks']} trozos vigentes; fuera: "
        f"{corpus['articles_with_stale_chunks']} con el texto cambiado y {corpus['articles_without_chunks']} sin "
        f"trozos. Normas de prueba entre las públicas: {corpus['test_norms']}."
    )
    print(
        f"Tokens del endpoint de embeddings (usage.total_tokens): {usage['total_tokens']} en {usage['requests']} "
        f"pedidos; textos distintos: {usage['texts']}, repetidos sin volver a pagar: {usage['reused']}."
    )
    for warning in report["warnings"]:
        print(warning)
    app_widths = [max(6, len(column)) for column in APP_COLUMNS]
    for app in report["apps"]:
        groups = app["groups"]
        print()
        print(
            f"{app['company']} / {app['app']}: {app['linked_articles']} artículos vinculados; normas seguidas "
            f"{groups['seguidas']}, candidatas {groups['candidatas']}, sin relación {groups['sin_relacion']} "
            f"(de {groups['sin_relacion_total']}), notificadas o descartadas {groups['notificadas_o_descartadas']}."
        )
        print(
            f"  {'Variante':18}"
            + "".join(f" {column:>{width}}" for column, width in zip(APP_COLUMNS, app_widths, strict=True))
        )
        for name, variant in app["variants"].items():
            if variant.get("reason") and "auc" not in variant:
                print(f"  {name:18} sin vector: {variant['reason']}")
                continue
            cosine = variant["cosine"]
            holdout = variant["holdout"]["summary"]
            cells = [
                variant.get("length_utf16"),
                variant.get("tokens"),
                variant["auc"]["seguidas_vs_sin_relacion"],
                variant["auc"]["candidatas_vs_sin_relacion"],
                cosine["seguidas"].get("p50"),
                cosine["candidatas"].get("p50"),
                cosine["sin_relacion"].get("p50"),
                cosine["sin_relacion"].get("p95"),
                holdout.get("evaluated"),
                holdout.get("median_position"),
                holdout.get("mrr"),
            ]
            print(table_row(name, cells, app_widths))
        prof = app["variants"].get("prof-v1", {})
        if "cross_rubro" in prof:
            cross = prof["cross_rubro"]
            print(
                f"  Entre rubros (prof-v1): {cross['norms']} normas de otro rubro; en el top 10: "
                f"{cross['in_top_10']}, en el top 25: {cross['in_top_25']}, sobre el p95 de las sin relación: "
                f"{cross['over_p95_unrelated']}."
            )
        for control in prof.get("control", []):
            print(
                f"  Control «{CONTROL_KEYWORD}» (prof-v1): {control['norm']}, puesto {fmt(control['position'])} de "
                f"{prof['ranking']['norms']}, coseno {fmt(control['cosine'])}, percentil "
                f"{fmt(control['percentile_vs_unrelated'], 1)} de las sin relación."
            )
    print()
    print("Evaluación que deja una norma afuera, todas las apps juntas:")
    summary_widths = [max(6, len(column)) for column in SUMMARY_COLUMNS]
    print(
        f"  {'Variante':18}"
        + "".join(f" {column:>{width}}" for column, width in zip(SUMMARY_COLUMNS, summary_widths, strict=True))
    )
    for name, item in report["summary"].items():
        holdout = item["holdout"]
        recall = holdout.get("recall", {})
        cells = [holdout["evaluated"], holdout.get("median_position"), holdout.get("mrr")]
        cells += [recall.get(k) for k in ("1", "5", "10", "25")]
        cells.append(item["auc_mean"]["seguidas_vs_sin_relacion"])
        print(table_row(name, cells, summary_widths))


def print_dry_run(units: list[Unit], dataset: Dataset) -> None:
    """Los textos de prof-v1 con su SHA-256, y lo que costaría la medición."""
    for warning in dataset.warnings:
        print(warning)
    for unit in units:
        if unit.variant != "prof-v1" or unit.held_out is not None:
            continue
        profile = unit.profile
        print()
        print(f"{unit.app.company} / {unit.app.name} ({unit.app.id})")
        if profile is not None:
            print(
                "  Disponibles por dimensión: "
                + ", ".join(f"{dimension} {profile.available[dimension]}" for dimension in RECIPE_DIMENSIONS)
                + "; quitados por tope: "
                + ", ".join(f"{dimension} {profile.cut_by_cap[dimension]}" for dimension in RECIPE_DIMENSIONS)
                + "; por largo: "
                + ", ".join(f"{dimension} {profile.cut_by_length[dimension]}" for dimension in RECIPE_DIMENSIONS)
            )
        if unit.text is None:
            print(f"  Sin texto: {unit.reason}")
            continue
        print(f"  {PROFILE_MODEL}  SHA-256 {sha256_hex(unit.text)}  largo {utf16_len(unit.text)}")
        for line in unit.text.split("\n"):
            print(f"  | {line}")
    texts = texts_to_embed(units)
    with_text = [unit for unit in units if unit.text is not None]
    characters = sum(utf16_len(text) for text in texts)
    print()
    print("Lo que embebería la medición:")
    by_variant: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        by_variant[unit.variant].append(unit)
    for variant, items in by_variant.items():
        main = [unit for unit in items if unit.held_out is None]
        held = [unit for unit in items if unit.held_out is not None]
        print(
            f"  {variant:18} vectores de app: {sum(1 for unit in main if unit.text or unit.vector)} de {len(main)}; "
            f"apartadas: {sum(1 for unit in held if unit.text or unit.vector)} de {len(held)}"
        )
    print(
        f"  Textos: {len(with_text)}, distintos: {len(texts)} (un pedido por texto), {characters} caracteres: unos "
        f"{round(characters / CHARS_PER_TOKEN)} tokens."
    )


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_out() -> Path:
    return Path(tempfile.gettempdir()) / f"profile_retrieval_eval-{datetime.now():%Y%m%d-%H%M%S}.json"


def check_out(parser: argparse.ArgumentParser, path: Path, option: str) -> Path:
    path = path.resolve()
    if path == REPO or REPO in path.parents:
        parser.error(f"{option} tiene que quedar fuera del repositorio: guarda textos de perfil de empresas")
    if path.is_dir() or not path.parent.is_dir():
        parser.error(f"{option} tiene que ser un archivo en un directorio que exista: {path}")
    if not os.access(path.parent, os.W_OK) or (path.exists() and not os.access(path, os.W_OK)):
        parser.error(f"no se puede escribir {option}: {path}")
    return path


# --- autoprueba ----------------------------------------------------------------------------------------------


def load_parity(path: Path = PARITY_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parity_case_inputs(case: dict) -> tuple[list[Entry], dict[str, int], dict[str, RootInfo]]:
    entries = [Entry(item["root"], item["dimension"], item["source"]) for item in case["entries"]]
    roots = {
        root: RootInfo(item["value"], item["dimension"], int(item.get("family_article_count", 0)))
        for root, item in case["roots"].items()
    }
    return entries, {root: int(count) for root, count in case.get("occurrences", {}).items()}, roots


def parity_settings(case: dict, default: dict) -> Settings:
    """Los pesos de un caso: los suyos si trae settings; si no, los de la raíz del archivo."""
    data = case.get("settings", default)
    return Settings(
        declared=float(data["declared"]),
        derived_min=float(data["derived_min"]),
        derived_saturation=float(data["derived_saturation"]),
        structured=float(data["structured"]),
    )


def self_test() -> int:
    """--self-test: la receta, los casos de paridad y las métricas, con datos inventados, sin servicio ni base."""
    failures: list[str] = []
    total = 0

    def check(label: str, got: object, want: object) -> None:
        nonlocal total
        total += 1
        if got != want:
            failures.append(f"{label}: {got!r} en lugar de {want!r}")

    # Pesos del perfil.
    check("derived con 0 artículos", derived_weight(0), 0.5)
    check("derived con 1 artículo", round(derived_weight(1), 6), round(0.5 + 0.5 * math.log(2) / math.log(6), 6))
    check("derived saturado con 5", derived_weight(5), 1.0)
    check("derived saturado con 9", derived_weight(9), 1.0)
    weights = profile_source_weights(
        [
            Entry("a", SCOPE, STRUCTURED),
            Entry("a", SCOPE, DERIVED),
            Entry("b", SECTOR, DERIVED),
            Entry("b", SECTOR, DECLARED),
            Entry("c", SCOPE, DECLARED),
            Entry("c", SCOPE, EXCLUDED),
            Entry("d", TERRITORY, STRUCTURED),
        ],
        {"a": 1, "b": 5},
    )
    check("máximo entre procedencias", weights["a"], Weight(derived_weight(1), DERIVED))
    check("empate de peso: gana declared", weights["b"], Weight(1.0, DECLARED))
    check("la exclusión quita la familia", "c" in weights, False)
    check("structured", weights["d"], Weight(0.4, STRUCTURED))

    # Largo y orden en unidades UTF-16, como .NET.
    check("largo de un carácter del plano básico", utf16_len("á"), 1)
    check("largo de un carácter fuera del plano básico", utf16_len("\U0001d406"), 2)
    check("orden ordinal UTF-16", sorted(["Ｇa", "\U0001d406a"], key=utf16_key), ["\U0001d406a", "Ｇa"])
    check("orden de puntos de código, distinto", sorted(["Ｇa", "\U0001d406a"]), ["Ｇa", "\U0001d406a"])
    check("Á después de Z", sorted(["Área", "Zona"], key=utf16_key), ["Zona", "Área"])
    check("SHA-256", sha256_hex("Actividad: x"), hashlib.sha256(b"Actividad: x").hexdigest())

    # Topes proporcionales.
    check(
        "reparto exacto",
        proportional_caps({SCOPE: 50, SECTOR: 5, ACTIVITY: 100, FACILITY: 45}, RECIPE_DIMENSIONS, 80),
        {SCOPE: 20, SECTOR: 2, ACTIVITY: 40, FACILITY: 18},
    )
    caps = proportional_caps({SCOPE: 7, SECTOR: 3, ACTIVITY: 90, FACILITY: 1}, RECIPE_DIMENSIONS, 80)
    check("reparto por el mayor resto", caps, {SCOPE: 6, SECTOR: 2, ACTIVITY: 71, FACILITY: 1})
    check("reparto suma el total", sum(caps.values()), 80)
    check(
        "si caben todos",
        proportional_caps({SCOPE: 3, SECTOR: 1, ACTIVITY: 0, FACILITY: 2}, RECIPE_DIMENSIONS, 80),
        {SCOPE: 3, SECTOR: 1, ACTIVITY: 0, FACILITY: 2},
    )

    # Casos de paridad: el texto y el SHA-256 que la PR del backend tiene que reproducir.
    try:
        parity = load_parity()
        check("paridad: receta", (parity["recipe"], parity["embedding_model"]), ("prof-v1", PROFILE_MODEL))
        check("paridad: topes", (parity["caps"], parity["max_length_utf16"]), (PROF_V1_CAPS, PROF_V1_MAX_LENGTH))
        check(
            "paridad: pesos",
            parity["settings"],
            {"declared": 1.0, "derived_min": 0.5, "derived_saturation": 5.0, "structured": 0.4},
        )
        for case in parity["cases"]:
            entries, occurrences, roots = parity_case_inputs(case)
            built = build_profile_text(entries, occurrences, roots, PROF_V1, parity_settings(case, parity["settings"]))
            expected = case["expected"]
            check(f"paridad {case['id']}: texto", built.text, expected["text"])
            check(f"paridad {case['id']}: SHA-256", built.sha256, expected["sha256"])
            check(f"paridad {case['id']}: largo", built.length, expected.get("length_utf16", 0))
            check(f"paridad {case['id']}: motivo", built.reason, expected.get("reason"))
            if "cut_by_cap" in expected:
                check(f"paridad {case['id']}: quitados por tope", built.cut_by_cap, expected["cut_by_cap"])
                check(f"paridad {case['id']}: quitados por largo", built.cut_by_length, expected["cut_by_length"])
    except (OSError, ValueError, KeyError) as exc:
        check("casos de paridad", f"{type(exc).__name__}: {exc}", "legibles")

    # Reglas de la receta con un perfil chico.
    roots = {
        "s1": RootInfo("Seguridad vial", SCOPE, 10),
        "s2": RootInfo("Laboral", SCOPE, 90),
        "e1": RootInfo("Minería", SECTOR, 30),
        "a1": RootInfo("Transporte de carga", ACTIVITY, 4),
        "f1": RootInfo("Camiones", FACILITY, 4),
        "t1": RootInfo("Región de Antofagasta", TERRITORY, 50),
        "o1": RootInfo("Empresa Ejemplo", "others", 1),
    }
    entries = [
        Entry("s1", SCOPE, DECLARED),
        Entry("s2", SCOPE, DERIVED),
        Entry("e1", SECTOR, STRUCTURED),
        Entry("a1", ACTIVITY, DERIVED),
        Entry("f1", FACILITY, DERIVED),
        Entry("t1", TERRITORY, DECLARED),
        Entry("o1", "others", DECLARED),
    ]
    occurrences = {"s2": 2, "a1": 1, "f1": 1}
    text = build_profile_text(entries, occurrences, roots, PROF_V1).text
    check(
        "prof-v1",
        text,
        "Ámbito regulatorio: Laboral; Seguridad vial\nSector productivo: Minería\nActividad: Transporte de carga\n"
        "Obra, instalación o equipo: Camiones",
    )
    check(
        "sin etiquetas",
        build_profile_text(entries, occurrences, roots, RECIPES["sin-etiquetas"]).text,
        "Laboral; Seguridad vial\nMinería\nTransporte de carga\nCamiones",
    )
    check(
        "sin actividades",
        build_profile_text(entries, occurrences, roots, RECIPES["sin-actividades"]).text,
        "Ámbito regulatorio: Laboral; Seguridad vial\nSector productivo: Minería\nObra, instalación o equipo: Camiones",
    )
    short = replace(PROF_V1, max_length=60)
    trimmed = build_profile_text(entries, occurrences, roots, short)
    # Quita Minería (structured) y después las dos derived de un artículo, hasta que el texto entra. El orden entre
    # valores con el mismo peso y el mismo conteo lo fija el caso de paridad P6.
    check(
        "tope de largo: quita lo de menor peso hasta que entra",
        trimmed.text,
        "Ámbito regulatorio: Laboral; Seguridad vial",
    )
    check("tope de largo: cuenta lo quitado", trimmed.cut_by_length, {SCOPE: 0, SECTOR: 1, ACTIVITY: 1, FACILITY: 1})
    structured_only = build_profile_text([Entry("e1", SECTOR, STRUCTURED)], {}, roots, PROF_V1)
    check("sin señal mínima", (structured_only.text, structured_only.signal), (None, False))
    check(
        "por empresa no exige señal mínima",
        build_profile_text([Entry("e1", SECTOR, STRUCTURED)], {}, roots, RECIPES["por-empresa"]).text,
        "Sector productivo: Minería",
    )
    check(
        "sin valores",
        build_profile_text([Entry("t1", TERRITORY, DECLARED)], {}, roots, PROF_V1).reason,
        "sin valores en las dimensiones de la receta",
    )

    # El derived con una norma apartada.
    article_values = {
        "x1": {("v1", "r1", SCOPE), ("v2", "r2", ACTIVITY)},
        "x2": {("v1", "r1", SCOPE), ("v3", "r1", SCOPE)},
        "y1": {("v4", "r4", SECTOR)},
    }
    linked = {"x1": "n1", "x2": "n1", "y1": "n2"}
    derived, by_root, by_raw = derived_profile(article_values, linked)
    check("derived: ocurrencias por familia", by_root, {"r1": 2, "r2": 1, "r4": 1})
    check("derived: ocurrencias por valor crudo", by_raw, {"v1": 2, "v2": 1, "v3": 1, "v4": 1})
    check("derived: una entrada por valor crudo", len(derived), 4)
    _, held_root, _ = derived_profile(article_values, linked, "n1")
    check("derived sin la norma apartada", held_root, {"r4": 1})
    check(
        "raíces que generan candidatas",
        match_roots(
            [
                Entry("r1", SCOPE, DERIVED),
                Entry("r2", ACTIVITY, DERIVED),
                Entry("cl", TERRITORY, STRUCTURED),
                Entry("rg", TERRITORY, STRUCTURED),
                Entry("r4", SECTOR, EXCLUDED),
            ],
            {"r1": 1, "r2": 1},
            {"cl"},
        ),
        {"r1", "rg"},
    )

    # Métricas.
    check("percentil 50 par", percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
    check("percentil 0 y 100", (percentile([3.0, 1.0, 2.0], 0.0), percentile([3.0, 1.0, 2.0], 1.0)), (1.0, 3.0))
    check("percentil de uno", percentile([0.4], 0.95), 0.4)
    check("percentil vacío", percentile([], 0.5), None)
    check("describir vacío", describe([]), {"n": 0})
    check("AUC perfecta", auc([0.9, 0.8], [0.1, 0.2]), 1.0)
    check("AUC invertida", auc([0.1], [0.9, 0.8]), 0.0)
    check("AUC con empates", auc([0.5, 0.5], [0.5]), 0.5)
    positives, negatives = [0.3, 0.5, 0.5, 0.9, 0.2], [0.1, 0.5, 0.4, 0.3, 0.3, 0.8]
    brute = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positives for n in negatives) / 30
    check("AUC igual a contar pares", round(auc(positives, negatives), 9), round(brute, 9))
    check("AUC sin negativos", auc([0.5], []), None)
    check(
        "puestos con un empate",
        positions_desc({"a": 0.9, "b": 0.8, "c": 0.8, "d": 0.5}),
        {"a": 1, "b": 2, "c": 2, "d": 4},
    )
    check("percentil de un valor", percentile_rank(0.5, [0.1, 0.5, 0.9, 0.3]), 62.5)
    check("promedio de los tres mejores", round(top_mean([0.2, 0.9, 0.5, 0.7]), 6), 0.7)
    check("promedio con menos de tres", top_mean([0.4, 0.6]), 0.5)
    check(
        "tramos",
        [bin_index(0.35), bin_index(0.349), bin_index(1.0), bin_index(-0.01), bin_index(0.0)],
        [7, 6, 19, -1, 0],
    )
    check("rótulo del tramo", bin_label(7), "0,35-0,40")
    check("muestra fija", sorted(["a", "b", "c"], key=sample_key) == sorted(["c", "b", "a"], key=sample_key), True)
    check(
        "título sin tildes",
        title_keywords("Ley de Tránsito y VEHÍCULOS", ("de transito", "vehicul", "miner")),
        ["de transito", "vehicul"],
    )
    check("transitorias no es de tránsito", title_keywords("Disposiciones transitorias", ("de transito",)), [])
    check("minería", title_keywords("Reglamento de seguridad MINERA", ("miner",)), ["miner"])
    vector, used, missing = centroid(
        [
            Candidate("a", SCOPE, "A", 1.0, DECLARED, 1),
            Candidate("b", SCOPE, "B", 0.5, DERIVED, 1),
            Candidate("c", SCOPE, "C", 1.0, DERIVED, 1),
        ],
        {"a": [1.0, 0.0], "b": [0.0, 2.0]},
    )
    check(
        "centroide ponderado",
        ([round(value, 6) for value in vector], used, missing),
        ([round(1 / math.sqrt(2), 6)] * 2, 2, 1),
    )
    check("centroide sin vectores", centroid([Candidate("z", SCOPE, "Z", 1.0, DECLARED, 1)], {}), (None, 0, 1))
    check("vector de pgvector", parse_vector(vector_literal([0.5, -0.25, 1e-05])), [0.5, -0.25, 1e-05])
    summary = holdout_summary(
        [
            {"position": 1, "position_top3": 2, "cosine": 0.6},
            {"position": 4, "position_top3": 4, "cosine": 0.5},
            {"position": None, "skipped": "sin trozos art-v2 vigentes"},
        ]
    )
    check(
        "apartadas: evaluadas y omitidas",
        (summary["evaluated"], summary["skipped"]),
        (2, {"sin trozos art-v2 vigentes": 1}),
    )
    check("apartadas: mediana y MRR", (summary["median_position"], summary["mrr"]), (2.5, 0.625))
    check("apartadas: recall", summary["recall"], {"1": 0.5, "5": 1.0, "10": 1.0, "25": 1.0})

    # Apps elegibles.
    def app_row(app_id: str, company: str, **changes: object) -> dict:
        row = {"id": app_id, "company_id": company, "app_type": APP_TYPE, "is_catalog": False, "status": ACTIVE}
        row.update(changes)
        return row

    demo_app = DEMO_BY_KEY["norte"].app_id
    rows = [
        app_row(demo_app, "c-norte"),
        app_row("a-dev", "c-norte"),
        app_row("a-cat", "c-norte", is_catalog=True),
        app_row("a-cm", "c-norte", app_type="compliance_management"),
        app_row("a-sus", "c-otra", status="Suspendida"),
        app_row("a-tpl", TEMPLATE_COMPANY, status="Plantilla"),
    ]
    chosen, app_warnings = resolve_apps(rows, ["norte", "altiplano"], {"c-norte": "norte"})
    check("apps elegibles de una empresa", [row["id"] for row in chosen], [demo_app, "a-dev"])
    check("aviso de empresa ausente", len(app_warnings), 1)
    for label, requested in (
        ("de catálogo", "a-cat"),
        ("de otro tipo", "a-cm"),
        ("de empresa suspendida", "a-sus"),
        ("de la plantilla", "a-tpl"),
        ("inexistente", "a-nada"),
    ):
        try:
            resolve_apps(rows, [requested], {"c-norte": "norte"})
            check(f"app {label} pedida por id", "aceptada", "AppSelectionError")
        except AppSelectionError:
            check(f"app {label} pedida por id", "AppSelectionError", "AppSelectionError")

    # La evaluación completa con dobles de la base y del endpoint.
    for label, got, want in pipeline_checks():
        check(label, got, want)

    for failure in failures:
        print(f"FALLA {failure}")
    print(f"Autoprueba: {total - len(failures)} de {total} comprobaciones correctas.")
    return 1 if failures else 0


def pipeline_checks() -> list[tuple[str, object, object]]:
    """Planifica y evalúa dos apps inventadas con vectores de dos dimensiones: la norma apartada que más se parece
    al perfil tiene que quedar primera."""
    roots = {
        "r-trans": RootInfo("Transporte", SCOPE, 3),
        "r-carga": RootInfo("Transporte de carga", ACTIVITY, 2),
        "r-min": RootInfo("Minería", SECTOR, 5),
    }
    norms = {
        norm: NormInfo(norm, title, "Ley N 1", number, None, True)
        for norm, title, number in (
            ("n-trans", "Ley de transporte", "1"),
            ("n-carga", "Ley de vehículos de carga", "2"),
            ("n-min", "Código de minería", "3"),
            ("n-emb", "[PRUEBA E2E] Uso de emblemas", "4"),
        )
    }
    articles = {
        article: ArticleInfo(article, norm, f"Artículo {index}", index)
        for index, (article, norm) in enumerate(
            (("a1", "n-trans"), ("a2", "n-trans"), ("a3", "n-carga"), ("a4", "n-min"), ("a5", "n-emb")), start=1
        )
    }
    # Dirección de cada artículo en el plano: el transporte arriba, la minería a la derecha.
    directions = {"a1": (0.1, 1.0), "a2": (0.2, 1.0), "a3": (0.15, 1.0), "a4": (1.0, 0.1), "a5": (0.7, 0.7)}
    litoral = AppData(
        "app-lit",
        "c-lit",
        "Empresa Litoral Ltda",
        "Requisitos",
        "litoral",
        True,
        company_entries=[],
        linked={"a1": "n-trans", "a3": "n-carga"},
        article_values={
            "a1": {("v-trans", "r-trans", SCOPE)},
            "a3": {("v-carga", "r-carga", ACTIVITY), ("v-trans", "r-trans", SCOPE)},
        },
        stored_derived={},
        followed={"n-trans", "n-carga"},
        notified=set(),
        discarded=set(),
        suggestions={},
    )
    norte = AppData(
        "app-nor",
        "c-nor",
        "Empresa Norte SpA",
        "Requisitos",
        "norte",
        True,
        company_entries=[Entry("r-min", SECTOR, STRUCTURED)],
        linked={},
        article_values={},
        stored_derived={},
        followed=set(),
        notified=set(),
        discarded=set(),
        suggestions={},
    )
    dataset = Dataset(
        [litoral, norte], roots, norms, articles, {}, set(), {"r-trans": [0.0, 1.0], "r-carga": [0.1, 1.0]}, []
    )

    def fake_vector(text: str) -> list[float]:
        return [0.05, 1.0] if "Transporte" in text else [1.0, 0.0]

    def fake_similarity(vector: list[float]) -> dict[str, Similarity]:
        norm = math.sqrt(sum(value * value for value in vector))
        result = {}
        for article, (x, y) in directions.items():
            length = math.sqrt(x * x + y * y)
            result[article] = Similarity((vector[0] * x + vector[1] * y) / (norm * length), 0, None)
        return result

    units = plan_units(dataset, ["prof-v1", CENTROID, "por-empresa"], holdout=True)
    for unit in units:
        if unit.text is not None:
            unit.vector = fake_vector(unit.text)
    reports = evaluate(dataset, units, fake_similarity, lambda pairs: {}, {}, 0)
    by_app = {report["app_id"]: report for report in reports}
    lit = by_app["app-lit"]["variants"]
    holdout = {item["norm"]: item for item in lit["prof-v1"]["holdout"]["norms"]}
    synthetic = by_app.get("sintetica", {}).get("variants", {})
    return [
        ("plan: el caso sintético solo con las variantes de topes", sorted(synthetic), ["prof-v1"]),
        (
            "plan: Norte sin señal mínima",
            by_app["app-nor"]["variants"]["prof-v1"]["reason"],
            "sin señal mínima: ningún valor declared ni derived",
        ),
        ("plan: Norte por empresa sí tiene vector", "auc" in by_app["app-nor"]["variants"]["por-empresa"], True),
        ("apartada de Litoral evaluada", sorted(item["position"] for item in holdout.values()), [1, 1]),
        ("apartada: orden entre las no seguidas", holdout["Ley 1"]["ranked_norms"], 3),
        ("centroide sin tokens", lit[CENTROID]["tokens"], 0),
        ("grupos de Litoral", by_app["app-lit"]["groups"]["seguidas"], 2),
        ("control «emblema» rotulado", [item["test_norm"] for item in lit["prof-v1"]["control"]], [True]),
        ("entre rubros: Código de minería es de otro rubro", lit["prof-v1"]["cross_rubro"]["norms"], 1),
        ("tramos de revisión", sum(sum(item["counts"].values()) for item in lit["prof-v1"]["bins"]), 5),
    ]


# --- main ----------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apps",
        nargs="+",
        default=[demo.key for demo in DEMO_COMPANIES],
        metavar="APP",
        help="empresas de demostración (norte, altiplano, litoral: todas sus apps elegibles) o ids de apps; "
        "defecto: las tres empresas",
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        default=list(VARIANTS),
        metavar="V",
        help=f"variantes de la receta; prof-v1 va siempre. Defecto: todas ({', '.join(VARIANTS)})",
    )
    parser.add_argument("--skip-holdout", action="store_true", help="sin la evaluación que deja una norma afuera")
    parser.add_argument(
        "--unrelated-sample",
        type=int,
        default=0,
        metavar="N",
        help="normas sin relación por app, elegidas con una semilla fija; defecto: 0, todas",
    )
    parser.add_argument("--out", type=Path, default=None, help="informe completo; defecto: el directorio temporal")
    parser.add_argument(
        "--vectors-out", type=Path, default=None, help="guarda los vectores para la fase A (JSON Lines)"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=os.environ.get("SERVICE_API_KEY", ""), help="defecto: SERVICE_API_KEY")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""), help="defecto: DATABASE_URL")
    parser.add_argument("--timeout", type=float, default=120.0, help="segundos por pedido")
    parser.add_argument(
        "--dry-run", action="store_true", help="arma y muestra los textos de prof-v1 con su SHA-256, sin el endpoint"
    )
    parser.add_argument(
        "--self-test", action="store_true", help="comprueba la receta y las métricas sin servicio ni base"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    unknown = [variant for variant in args.variants if variant not in VARIANTS]
    if unknown:
        parser.error(f"variantes desconocidas: {', '.join(unknown)}; las conocidas son {', '.join(VARIANTS)}")
    variants = [variant for variant in VARIANTS if variant == "prof-v1" or variant in args.variants]
    uuid_form = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
    bad = [item for item in args.apps if item.lower() not in DEMO_BY_KEY and not uuid_form.match(item)]
    if bad:
        parser.error(f"--apps acepta norte, altiplano, litoral o ids de apps: {', '.join(bad)}")
    requested = [item.lower() for item in args.apps]
    if args.unrelated_sample < 0 or args.timeout <= 0:
        parser.error("--unrelated-sample no puede ser negativo y --timeout tiene que ser mayor que 0")
    out = check_out(parser, args.out, "--out") if args.out else None
    if not args.dry_run:
        out = out or check_out(parser, default_out(), "--out")
    vectors_out = check_out(parser, args.vectors_out, "--vectors-out") if args.vectors_out else None
    if not args.dry_run and not args.api_key:
        parser.error("falta SERVICE_API_KEY (o --api-key)")
    if not args.database_url:
        parser.error("falta DATABASE_URL (o --database-url)")

    try:
        db = Database(args.database_url)
    except DatabaseError as exc:
        parser.error(str(exc))
    usage = {"total_tokens": 0, "requests": 0, "model": None, "by_text": {}}
    try:
        # Todas las consultas, incluida la búsqueda por similitud, antes del primer pedido.
        try:
            dataset = load_dataset(db, requested, args.unrelated_sample, CENTROID in variants)
            if not dataset.apps:
                parser.error("ninguna app elegible entre las pedidas: " + " ".join(dataset.warnings))
            if not dataset.articles:
                parser.error(f"sin trozos {ARTICLE_MODEL} vigentes de normas públicas en esta base")
            article_ids = sorted(dataset.articles)
            if probe_similarity(db, article_ids) == 0:
                parser.error("la búsqueda por similitud de prueba no devolvió artículos")
            db.end_transaction()
        except AppSelectionError as exc:
            parser.error(str(exc))
        except DatabaseError as exc:
            parser.error(f"falló la base antes del primer pedido: {exc}")

        units = plan_units(dataset, variants, not args.skip_holdout)
        if args.dry_run:
            print_dry_run(units, dataset)
            if out:
                write_json(out, dry_run_report(units, dataset, variants))
                print(f"\nTextos en {out} (fuera del repositorio).")
            return 0

        texts = texts_to_embed(units)
        try:
            vectors: dict[str, list[float]] = {}
            for index, text in enumerate(texts, start=1):
                vectors[text] = embed_text(text, args, usage)
                print(f"{index} de {len(texts)} textos embebidos", file=sys.stderr)
            for unit in units:
                if unit.text is not None:
                    unit.vector = vectors[unit.text]
            reports = evaluate(
                dataset,
                units,
                lambda vector: similarity_search(db, vector, article_ids),
                lambda pairs: chunk_texts(db, pairs),
                usage["by_text"],
                args.unrelated_sample,
                progress=lambda message: print(message, file=sys.stderr),
            )
        except (ServiceError, DatabaseError) as exc:
            source = "del endpoint de embeddings" if isinstance(exc, ServiceError) else "de la base"
            print(f"FALLA {source}: {exc}", file=sys.stderr)
            print(f"Tokens gastados antes de la falla (usage.total_tokens): {usage['total_tokens']}.", file=sys.stderr)
            return 1
    finally:
        db.close()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recipe": recipe_description(),
        "article_recipe": ARTICLE_MODEL,
        "taxonomy_recipe": TAXONOMY_MODEL,
        "service": {"base_url": args.base_url, "model": MODEL, "dimensions": DIMENSIONS},
        "variants": variants,
        "holdout": not args.skip_holdout,
        "seed": SEED,
        "unrelated_sample": args.unrelated_sample or None,
        "usage": {
            "total_tokens": usage["total_tokens"],
            "requests": usage["requests"],
            "model": usage["model"],
            "texts": len(texts),
            "reused": sum(1 for unit in units if unit.text is not None) - len(texts),
        },
        "corpus": dataset.corpus,
        "warnings": dataset.warnings,
        "apps": reports,
        "summary": pooled_summary(reports, variants),
    }
    # La consola primero: si --out no se puede escribir, las métricas pagadas no se pierden.
    print_report(report)
    try:
        write_json(out, report)
        if vectors_out:
            write_vectors(vectors_out, units)
    except OSError as exc:
        print(f"\nFALLA al guardar el informe: {exc}", file=sys.stderr)
        return 1
    print(f"\nInforme completo en {out} (fuera del repositorio).")
    if vectors_out:
        print(f"Vectores en {vectors_out}.")
    return 0


def recipe_description() -> dict:
    return {
        "name": PROF_V1.name,
        "embedding_model": PROFILE_MODEL,
        "dimensions": list(RECIPE_DIMENSIONS),
        "labels": LABELS,
        "caps": PROF_V1_CAPS,
        "max_length_utf16": PROF_V1_MAX_LENGTH,
        "settings": {
            "declared": SETTINGS.declared,
            "derived_min": SETTINGS.derived_min,
            "derived_saturation": SETTINGS.derived_saturation,
            "structured": SETTINGS.structured,
        },
    }


def dry_run_report(units: list[Unit], dataset: Dataset, variants: list[str]) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recipe": recipe_description(),
        "variants": variants,
        "warnings": dataset.warnings,
        "corpus": dataset.corpus,
        "texts": [
            {
                "app_id": unit.app.id,
                "app": unit.app.name,
                "company": unit.app.company,
                "variant": unit.variant,
                "held_out": dataset.norms[unit.held_out].label if unit.held_out in dataset.norms else unit.held_out,
                "text": unit.text,
                "sha256": sha256_hex(unit.text) if unit.text else None,
                "length_utf16": utf16_len(unit.text) if unit.text else 0,
                "reason": unit.reason,
            }
            for unit in units
            if unit.variant != CENTROID
        ],
    }


def write_vectors(path: Path, units: list[Unit]) -> None:
    """Un vector por línea, con la app, la variante, la norma apartada (id local) y el SHA-256 del texto, para la
    simulación de la fase A, que los recibe en el cuerpo sin volver a llamar a la IA. El caso sintético no es una
    app y no se guarda."""
    with path.open("w", encoding="utf-8") as handle:
        for unit in units:
            if unit.vector is None or unit.app.synthetic:
                continue
            record = {
                "app_id": unit.app.id,
                "variant": unit.variant,
                "held_out_legal_body_id": unit.held_out,
                "sha256": sha256_hex(unit.text) if unit.text else None,
                "vector": unit.vector,
            }
            handle.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    sys.exit(main())
