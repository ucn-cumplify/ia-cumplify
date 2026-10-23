"""The use case behind POST /api/v1/action-plans: which findings reach the model and which tasks come back.

Every task needs a citation to a fragment of its own finding, quoted from its text. The data is synthetic: the
knowledge base of the client is not part of the tests.
"""

import pytest

from ia_cumplify.application.use_cases.propose_action_plan import ProposeActionPlanUseCase, quote_in_fragment
from ia_cumplify.domain.action_plan import (
    MAX_TASKS_PER_FINDING,
    ActionPlanDraft,
    ActionPlanFinding,
    ActionPlanRequest,
    FindingTasks,
    KnowledgeFragment,
    ProposedTask,
    TaskCitation,
)
from ia_cumplify.domain.classification import TokenUsage

FINDING_A = "3f1c6b0e-8a6d-4b8e-9d2a-0c7e5b1a2f01"
FINDING_B = "3f1c6b0e-8a6d-4b8e-9d2a-0c7e5b1a2f02"
MANUAL = "9b2d7c4a-1e3f-4a5b-8c6d-7e8f9a0b1c01"
GUIDE = "9b2d7c4a-1e3f-4a5b-8c6d-7e8f9a0b1c02"

PROCEDURE = (
    "Toda faena debe contar con un procedimiento de trabajo seguro escrito, firmado por el jefe de faena "
    "y difundido a cada trabajador antes de iniciar la tarea."
)
COMMITTEE = "El acta de constitución del Comité Paritario se archiva en la carpeta de prevención."
USAGE = TokenUsage(prompt_tokens=900, completion_tokens=150, total_tokens=1050, llm_calls=1, cached_tokens=640)


class ScriptedPlanner:
    """Returns a fixed draft and records what it was asked."""

    def __init__(self, *findings: FindingTasks, usage: TokenUsage = USAGE) -> None:
        self.draft = ActionPlanDraft(findings=findings, usage=usage)
        self.calls: list[tuple[ActionPlanFinding, ...]] = []

    @property
    def model(self) -> str:
        return "gpt-5.6-luna"

    @property
    def version(self) -> str:
        return "action-plan-v1@gpt-5.6-luna"

    def propose(self, findings):
        self.calls.append(findings)
        return self.draft


def task(
    name: str = "Elaborar el procedimiento de trabajo seguro",
    *,
    document_id: str = MANUAL,
    chunk_index: int = 0,
    quote: str = "debe contar con un procedimiento de trabajo seguro escrito",
    description: str | None = "Redactarlo, firmarlo y difundirlo a cada trabajador.",
    priority: str | None = "alta",
) -> ProposedTask:
    return ProposedTask(
        name=name,
        description=description,
        priority=priority,  # type: ignore[arg-type]
        citation=TaskCitation(document_id=document_id, chunk_index=chunk_index, quote=quote),
    )


def finding(finding_id: str = FINDING_A, *fragments: KnowledgeFragment) -> ActionPlanFinding:
    return ActionPlanFinding(
        id=finding_id,
        description="No hay procedimiento de trabajo seguro.",
        requirement="Contar con un procedimiento de trabajo seguro.",
        missing="Falta el procedimiento firmado.",
        fragments=fragments,
    )


def manual(chunk_index: int = 0, text: str = PROCEDURE) -> KnowledgeFragment:
    return KnowledgeFragment(document_id=MANUAL, chunk_index=chunk_index, text=text, locator="Manual (parte 1 de 2)")


def run(planner: ScriptedPlanner, *findings: ActionPlanFinding):
    return ProposeActionPlanUseCase(planner).execute(ActionPlanRequest(findings=findings))


def test_a_task_that_quotes_its_fragment_is_kept():
    planner = ScriptedPlanner(FindingTasks(FINDING_A, (task(),)))

    result = run(planner, finding(FINDING_A, manual()))

    [item] = result.findings
    assert item.id == FINDING_A
    assert item.tasks == (task(),)
    assert result.usage == USAGE
    assert result.model == "gpt-5.6-luna"
    assert result.proposal_version == "action-plan-v1@gpt-5.6-luna"


def test_case_and_whitespace_do_not_count_in_the_quote():
    quoted = task(quote="  DEBE contar con un\nprocedimiento  de trabajo SEGURO escrito ")
    result = run(ScriptedPlanner(FindingTasks(FINDING_A, (quoted,))), finding(FINDING_A, manual()))

    [kept] = result.findings[0].tasks
    assert kept.citation.quote == "DEBE contar con un\nprocedimiento  de trabajo SEGURO escrito"


@pytest.mark.parametrize(
    "wrong",
    [
        task(quote="debe contar con un plan de emergencia"),
        task(quote="procedimiento de trabajo seguro escrito y aprobado por la autoridad"),
        task(quote="   "),
        task(document_id=GUIDE),
        task(chunk_index=1),
        # The fragment is real, but it belongs to the other finding.
        task(document_id=GUIDE, chunk_index=3, quote="se archiva en la carpeta de prevención"),
    ],
    ids=["paraphrase", "longer-than-the-fragment", "blank", "unknown-document", "unknown-chunk", "other-finding"],
)
def test_a_task_without_a_valid_citation_is_dropped(wrong):
    planner = ScriptedPlanner(FindingTasks(FINDING_A, (wrong,)))
    committee = KnowledgeFragment(document_id=GUIDE, chunk_index=3, text=COMMITTEE, locator=None)

    result = run(planner, finding(FINDING_A, manual()), finding(FINDING_B, committee))

    assert result.findings[0].tasks == ()


def test_the_quote_is_matched_the_way_the_backend_matches_it():
    # casefold() would turn ß into ss; ToLowerInvariant in the backend does not, and would drop the task.
    assert quote_in_fragment("strasse", "Straße") is False
    assert quote_in_fragment("STRAßE", "straße") is True
    assert quote_in_fragment("a b", "A B") is True


def test_a_finding_without_fragments_gets_no_tasks_and_does_not_reach_the_model():
    planner = ScriptedPlanner(
        FindingTasks(FINDING_A, (task(),)),
        # The model never saw FINDING_B; whatever it says about it is ignored.
        FindingTasks(FINDING_B, (task(),)),
    )

    result = run(planner, finding(FINDING_A, manual()), finding(FINDING_B))

    [sent] = planner.calls
    assert [item.id for item in sent] == [FINDING_A]
    assert [(item.id, len(item.tasks)) for item in result.findings] == [(FINDING_A, 1), (FINDING_B, 0)]


def test_without_any_fragment_the_model_is_not_called_and_the_response_still_names_the_model():
    planner = ScriptedPlanner()

    result = run(planner, finding(FINDING_A), finding(FINDING_B))

    assert planner.calls == []
    assert [item.tasks for item in result.findings] == [(), ()]
    assert result.usage == TokenUsage()
    # The backend stores nothing without a model and a proposal_version (ActionPlanResponseReader.CanStore).
    assert result.model == "gpt-5.6-luna"
    assert result.proposal_version == "action-plan-v1@gpt-5.6-luna"


def test_a_finding_the_model_leaves_out_or_answers_twice():
    planner = ScriptedPlanner(
        FindingTasks(FINDING_B, (task(document_id=GUIDE, chunk_index=3, quote="se archiva"),)),
        FindingTasks(FINDING_B, (task(name="Otra"),)),
        FindingTasks("00000000-0000-4000-8000-000000000000", (task(),)),
    )
    committee = KnowledgeFragment(document_id=GUIDE, chunk_index=3, text=COMMITTEE)

    result = run(planner, finding(FINDING_A, manual()), finding(FINDING_B, committee))

    # The order of the request, the first answer for a finding, and nothing for one it did not send.
    assert [item.id for item in result.findings] == [FINDING_A, FINDING_B]
    assert result.findings[0].tasks == ()
    assert [kept.citation.quote for kept in result.findings[1].tasks] == ["se archiva"]


def test_names_descriptions_and_priorities_are_fitted():
    tasks = (
        task(name="  Elaborar\nel procedimiento  ", description="   ", priority="ALTA"),
        task(name="x" * 256),
        task(name="   "),
        task(name="Difundir el procedimiento", description="d" * 2001, priority="urgente"),
        task(name="Firmar el procedimiento", description=None, priority=None),
        # 128 emoji are 256 UTF-16 units: past the 255 the backend counts.
        task(name="\U0001f4cb" * 128),
    )
    result = run(ScriptedPlanner(FindingTasks(FINDING_A, tasks)), finding(FINDING_A, manual()))

    assert [(kept.name, kept.description, kept.priority) for kept in result.findings[0].tasks] == [
        ("Elaborar el procedimiento", None, "alta"),
        ("Difundir el procedimiento", None, None),
        ("Firmar el procedimiento", None, None),
    ]


def test_only_the_first_tasks_of_a_finding_are_kept():
    tasks = tuple(task(name=f"Tarea {number}") for number in range(MAX_TASKS_PER_FINDING + 2))
    result = run(ScriptedPlanner(FindingTasks(FINDING_A, tasks)), finding(FINDING_A, manual()))

    assert [kept.name for kept in result.findings[0].tasks] == [f"Tarea {n}" for n in range(MAX_TASKS_PER_FINDING)]
