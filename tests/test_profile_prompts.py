"""Profile regression and the versioned normative catalogs (PRF-002).

The backend compares the labels of a company with those of the articles by exact text, so both prompts
keep the same six dimensions. Only normative articles now use closed catalogs; the profile keeps its
existing rules and candidates. Pin both versions to catch unintended prompt changes.
"""

import hashlib
import re

from ia_cumplify.adapters.outbound.openai.llm_profile_schema import (
    LlmCompanyProfileClassification,
)
from ia_cumplify.adapters.outbound.openai.llm_schema import LlmArticleClassification
from ia_cumplify.adapters.outbound.openai.profile_prompts import (
    PROFILE_PROMPT_VERSION,
    PROFILE_SYSTEM_PROMPT,
    render_profile_candidate_labels,
)
from ia_cumplify.adapters.outbound.openai.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    render_candidate_labels,
)
from ia_cumplify.domain.classification import CandidateLabels

SAMPLE = CandidateLabels(
    scope=("Laboral", "Medio Ambiente"),
    productive_sector=("Minería",),
    territorial_coverage=("Nacional",),
)
# SHA-256 of each system prompt plus its EXISTING LABELS block for SAMPLE, by version. The backend tells
# old answers from new ones by the version alone: a change to either text needs a new version (and its
# hash here).
PINNED_PROMPTS = {
    "classify-v3-closed-catalogs": "bd696f7b4015cf4ebd973f735248ff3ebc970613732e922f24da50e6418d363c",
    "classify-v2": "6208ec9d2108ac3e8ceca39039218f001c85cdeb5b80c1a3beb90112cb178842",
    "profile-v1": "d62a7db3f371fd1893c764e34666ee06189b3a80cbc303b2a7eeb5bc1b4862f0",
}


def label_rules(prompt: str) -> list[str]:
    rules = prompt.split("LABEL RULES", 1)[1].split("\n\n", 1)[0]
    return [line for line in rules.splitlines() if line.startswith("- ")]


def dimensions(prompt: str) -> list[str]:
    return re.findall(r"^\d\. (\w+) — ", prompt, re.MULTILINE)


def fingerprint(system_prompt: str, block: str) -> str:
    return hashlib.sha256(f"{system_prompt}\n\n{block}".encode()).hexdigest()


def test_both_prompts_and_schemas_have_the_same_dimensions_in_the_same_order() -> None:
    expected = list(LlmArticleClassification.model_fields)
    assert list(LlmCompanyProfileClassification.model_fields) == expected
    assert dimensions(SYSTEM_PROMPT) == dimensions(PROFILE_SYSTEM_PROMPT) == expected


def test_common_rules_stay_and_only_normative_dimensions_are_closed() -> None:
    article_rules = label_rules(SYSTEM_PROMPT)
    profile_rules = label_rules(PROFILE_SYSTEM_PROMPT)
    assert article_rules[:3] == profile_rules[:3]
    assert "Create a new label only when none fits." in profile_rules[3]
    assert "use ONLY the official catalog labels" in article_rules[3]
    assert "other three dimensions" in article_rules[4]
    start = "- For dimensions 1 to 4 and 6, if "
    end = ', return a single item: "No especificado". Do not invent those dimensions.'
    assert article_rules[-1] == f"{start}the value is not supported by the article plus the legal-body context{end}"
    assert profile_rules[-1] == f"{start}the description does not support a value{end}"


def test_profile_candidates_keep_their_original_format_and_values() -> None:
    profile_block = render_profile_candidate_labels(SAMPLE).splitlines()
    assert profile_block == [
        "--- EXISTING LABELS (reuse when they fit) ---",
        "Labels already used for the articles of legal bodies. If one fits, copy it exactly; create a new label only when none fits.",
        "scope: Laboral | Medio Ambiente",
        "productive_sector: Minería",
        "territorial_coverage: Nacional",
        "--- END EXISTING LABELS ---",
    ]
    article_block = render_candidate_labels(SAMPLE)
    assert "scope: General | Ambiental | Seguridad y salud en el trabajo | Laboral | Calidad" in article_block
    assert "Medio Ambiente" not in article_block


def test_only_normative_catalogs_are_complete_without_candidates() -> None:
    labels = CandidateLabels(territorial_coverage=("Nacional",))
    assert render_profile_candidate_labels(labels).splitlines()[2:] == [
        "territorial_coverage: Nacional",
        "--- END EXISTING LABELS ---",
    ]
    assert render_candidate_labels() == render_candidate_labels(labels)


def test_a_prompt_change_bumps_its_version() -> None:
    assert PINNED_PROMPTS.get(PROMPT_VERSION) == fingerprint(
        SYSTEM_PROMPT, render_candidate_labels(SAMPLE)
    ), (
        "SYSTEM_PROMPT or the EXISTING LABELS block changed: bump PROMPT_VERSION and pin the new hash"
    )
    assert PINNED_PROMPTS.get(PROFILE_PROMPT_VERSION) == fingerprint(
        PROFILE_SYSTEM_PROMPT, render_profile_candidate_labels(SAMPLE)
    ), (
        "PROFILE_SYSTEM_PROMPT or its EXISTING LABELS block changed: bump PROFILE_PROMPT_VERSION and pin the new hash"
    )
