"""POST /api/v1/action-plans.

The backend sends the open findings of an evidence review with the knowledge-base fragments it found for each
one (step 2B.10 of its sprint 3 plan). This endpoint reads no database, the same way the evidence review judges
only the files of the request.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ia_cumplify.adapters.inbound.http.dependencies import get_action_planner
from ia_cumplify.adapters.inbound.http.schemas.action_plan import ActionPlanBody, ActionPlanResponse
from ia_cumplify.application.ports.action_planner import ActionPlannerPort
from ia_cumplify.application.use_cases.propose_action_plan import ProposeActionPlanUseCase
from ia_cumplify.config.settings import get_settings
from ia_cumplify.domain.exceptions import ActionPlanError

router = APIRouter(prefix="/action-plans", tags=["action-plans"])
logger = logging.getLogger(__name__)


def get_propose_action_plan_use_case(
    planner: ActionPlannerPort = Depends(get_action_planner),
) -> ProposeActionPlanUseCase:
    return ProposeActionPlanUseCase(planner)


# A plain def: FastAPI runs it in the thread pool, so the model call does not hold the event loop, /health
# included.
@router.post("", response_model=ActionPlanResponse)
def create_action_plan(
    body: ActionPlanBody,
    use_case: ProposeActionPlanUseCase = Depends(get_propose_action_plan_use_case),
) -> ActionPlanResponse:
    """Return tasks for each finding, each one citing a fragment of that finding.

    A finding the fragments do not support comes back with no tasks. Nothing is read from the database, and
    the text of the findings and the fragments is not logged.
    """
    request = body.to_request(get_settings().action_plan_max_findings)
    try:
        result = use_case.execute(request)
    except ActionPlanError as exc:
        logger.error("Action plan failed for %d findings: %s", len(request.findings), type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info(
        "Action plan response: %d findings, %d fragments, %d with tasks, %d tasks, proposal_version %s.",
        len(result.findings),
        sum(len(finding.fragments) for finding in request.findings),
        sum(1 for finding in result.findings if finding.tasks),
        sum(len(finding.tasks) for finding in result.findings),
        result.proposal_version,
    )
    return ActionPlanResponse.from_domain(result)
