"""The profile prompt speaks the vocabulary of the article prompt (PRF-002).

The backend compares the labels of a company with those of the articles by exact text, so both prompts
must give the model the same dimensions, the same label rules and the same EXISTING LABELS block. They
live in two copies: these tests catch a change to one that does not reach the other, and a prompt change
that does not bump its version.
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


def test_the_label_rules_are_the_same_in_both_prompts() -> None:
    article_rules = label_rules(SYSTEM_PROMPT)
    profile_rules = label_rules(PROFILE_SYSTEM_PROMPT)
    assert len(article_rules) == len(profile_rules) == 5
    assert profile_rules[:4] == article_rules[:4], (
        "Change both prompts and bump both versions"
    )
    # The last rule only names what supports a value: the article and its legal body, or the description.
    start = "- For dimensions 1 to 4 and 6, if "
    end = ', return a single item: "No especificado". Do not invent those dimensions.'
    for rule in (article_rules[4], profile_rules[4]):
        assert rule.startswith(start) and rule.endswith(end)
    assert (
        article_rules[4]
        == f"{start}the value is not supported by the article plus the legal-body context{end}"
    )
    assert profile_rules[4] == f"{start}the description does not support a value{end}"


def test_the_existing_labels_block_has_the_same_format() -> None:
    article_block = render_candidate_labels(SAMPLE).splitlines()
    profile_block = render_profile_candidate_labels(SAMPLE).splitlines()
    assert (
        article_block[0]
        == profile_block[0]
        == "--- EXISTING LABELS (reuse when they fit) ---"
    )
    assert (
        article_block[2:]
        == profile_block[2:]
        == [
            "scope: Laboral | Medio Ambiente",
            "productive_sector: Minería",
            "territorial_coverage: Nacional",
            "--- END EXISTING LABELS ---",
        ]
    )
    # The explanation only names what the labels were used for.
    instruction = (
        "If one fits, copy it exactly; create a new label only when none fits."
    )
    assert (
        article_block[1] == f"Labels already used for other legal bodies. {instruction}"
    )
    assert (
        profile_block[1]
        == f"Labels already used for the articles of legal bodies. {instruction}"
    )


def test_a_dimension_without_labels_has_no_line_in_either_block() -> None:
    labels = CandidateLabels(territorial_coverage=("Nacional",))
    for render in (render_candidate_labels, render_profile_candidate_labels):
        assert render(labels).splitlines()[2:] == [
            "territorial_coverage: Nacional",
            "--- END EXISTING LABELS ---",
        ]


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
