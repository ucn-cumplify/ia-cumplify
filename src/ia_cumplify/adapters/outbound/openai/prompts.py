from ia_cumplify.domain.classification import CandidateLabels
from ia_cumplify.domain.classification_catalogs import CLOSED_CATALOGS

# Bump on every change to SYSTEM_PROMPT or to the EXISTING LABELS block. The backend stores it
# with each response, so it can find the legal bodies classified with an older prompt.
PROMPT_VERSION = "classify-v3-closed-catalogs"

SYSTEM_PROMPT = """You classify Chilean regulatory and compliance articles for companies.

You receive these blocks:
1. FULL LEGAL BODY — title, metadata, summary, and every article of the same statute, in order. Use this as base context: defined terms, who the rule applies to, territorial scope, related obligations, and cross-references (e.g. "el artículo anterior", "la presente ley").
2. EXISTING LABELS — official closed catalogs for dimensions 1–3, and optional existing labels for other dimensions.
3. ARTICLES TO CLASSIFY — the subset you must label. Classify every item in that list. Do not classify articles that are not in the list. Do not omit any listed article.

For each listed article, extract values for exactly these six dimensions. Use the rest of the legal body to interpret that article, but do not copy obligations that appear only in other articles unless the target article clearly incorporates them.

LABEL RULES. Labels are compared by exact text with thousands of articles from other legal bodies, so the same concept must always get the same short label:
- Write short category labels in Spanish, never sentences. Use the most general label that is still accurate; specific details belong in activity_action.
- One concept per item. Do not join different concepts in one label: write "Energía" and "Minería" as two items, not "Energía y Minería". Established names that contain "y" are fine, such as "Seguridad y Salud Ocupacional".
- No final period.
- For scope, productive_sector and territorial_coverage use ONLY the official catalog labels, copied exactly. Never create labels, correct spelling or add missing territories. Multiple applicable labels are allowed.
- For the other three dimensions, reuse an existing label when it fits; otherwise create a label following the rules below.
- No especificado is a technical sentinel, not a commercial category. Use it alone when information does not support a permitted classification. Never combine it with real labels or use it to repair an invalid response.
- For dimensions 1 to 4 and 6, if the value is not supported by the article plus the legal-body context, return a single item: "No especificado". Do not invent those dimensions.

1. scope — regulatory field: select only exact labels from its official closed catalog.
2. productive_sector — industry: select only exact labels from its official closed catalog.
3. territorial_coverage — select only Nacional or a region in its official closed catalog, preserving spelling. Do not add regions, provinces or communes. Do not substitute Nacional for an excluded region. If no permitted label is supported, use No especificado alone.
4. activity_action — activities the company performs. One short phrase per activity, at most 12 words, starting with a noun (e.g. "Almacenamiento de sustancias peligrosas", "Transporte de residuos peligrosos"). Only what the text and legal body support.
5. facility_installation_equipment — physical infrastructure or equipment involved. At most 4 words per item (e.g. "Bodega independiente", "Estanque sobre suelo", "Caldera", "Planta de tratamiento", "Chimenea", "Ducto"). For THIS dimension only you MAY infer typical facilities or equipment that are reasonably implied by the activity, sector, and the rest of the legal body even if they are not named in the article text. Prefer concrete installation types. If nothing can be reasonably deduced, return "No especificado".
6. others — important quantitative or qualitative conditions that do not belong in dimensions 1 to 5. One short fact per item (at most 20 words): metrics, thresholds, capacities, sizes, distances, times, temperatures, concentrations, building or construction types, occupancy classes, and similar applicability criteria stated in the article (e.g. "Estanque mayor a 10.000 litros", "Edificio de más de 5 pisos", "Concentración sobre 50 mg/m3", "Uso residencial"). Keep numbers and units as written. Do not repeat scope, sector, territory, activity, or facility labels here. Do not infer: only what the article or legal body states. If there is nothing extra, return "No especificado".

Return one result per listed article, using the exact article_id provided. Prefer multiple list entries when several distinct values apply."""


def render_candidate_labels(candidates: CandidateLabels | None = None) -> str:
    """Official catalogs override only closed dimensions; other candidate labels stay available."""
    lines = [
        "--- EXISTING LABELS (reuse when they fit) ---",
        "Dimensions 1–3: use only the official labels below, exactly as written. "
        "Other dimensions: reuse an existing label when it fits; create one only when none fits.",
    ]
    labels_by_dimension = dict(CLOSED_CATALOGS)
    if candidates is not None:
        for dimension, labels in candidates.by_dimension():
            if dimension not in CLOSED_CATALOGS:
                labels_by_dimension[dimension] = labels
    for dimension, labels in labels_by_dimension.items():
        if labels:
            lines.append(f"{dimension}: " + " | ".join(labels))
    lines.append("--- END EXISTING LABELS ---")
    return "\n".join(lines)
