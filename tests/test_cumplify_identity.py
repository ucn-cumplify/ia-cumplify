"""Every language-model system prompt tells the model it works for Cumplify.

The chat keeps its own first sentence (chat-v4). The other calls share CUMPLIFY_ROLE.
"""

from ia_cumplify.adapters.outbound.openai.action_plan_prompts import ACTION_PLAN_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.applicability_prompts import APPLICABILITY_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.chat_prompts import CHAT_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.documentary_situation_prompts import (
    DOCUMENTARY_SITUATION_SYSTEM_PROMPT,
)
from ia_cumplify.adapters.outbound.openai.evidence_review_prompts import EVIDENCE_REVIEW_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.profile_prompts import PROFILE_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.prompt_text import CUMPLIFY_ROLE
from ia_cumplify.adapters.outbound.openai.prompts import SYSTEM_PROMPT


def test_the_chat_names_cumplify_in_its_own_first_sentence() -> None:
    assert CHAT_SYSTEM_PROMPT.startswith(
        "You are the regulatory assistant of Cumplify, a Chilean compliance platform."
    )


def test_the_other_language_model_prompts_open_with_the_cumplify_role() -> None:
    for prompt in (
        SYSTEM_PROMPT,
        PROFILE_SYSTEM_PROMPT,
        APPLICABILITY_SYSTEM_PROMPT,
        EVIDENCE_REVIEW_SYSTEM_PROMPT,
        ACTION_PLAN_SYSTEM_PROMPT,
        DOCUMENTARY_SITUATION_SYSTEM_PROMPT,
    ):
        assert prompt.startswith(CUMPLIFY_ROLE + "\n")
        assert "Cumplify" in prompt.split("\n", 1)[0]
