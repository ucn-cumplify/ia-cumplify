from typing import Protocol

from ia_cumplify.domain.action_plan import ActionPlanDraft, ActionPlanFinding


class ActionPlannerPort(Protocol):
    @property
    def model(self) -> str:
        """The model the proposal is asked of."""
        ...

    @property
    def version(self) -> str:
        """Prompt version and model, as ``"<prompt>@<model>"``."""
        ...

    def propose(self, findings: tuple[ActionPlanFinding, ...]) -> ActionPlanDraft:
        """Propose tasks for each finding from its fragments. Raises ActionPlanError when the provider fails."""
        ...
