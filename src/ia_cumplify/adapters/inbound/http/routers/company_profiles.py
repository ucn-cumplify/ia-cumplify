import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException

from ia_cumplify.adapters.inbound.http.dependencies import get_profile_classifier
from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload
from ia_cumplify.adapters.inbound.http.schemas.company_profile import (
    ClassifyCompanyProfileRequest,
    ClassifyCompanyProfileResponse,
)
from ia_cumplify.adapters.outbound.openai.profile_classifier import OpenAICompanyProfileClassifierAdapter
from ia_cumplify.application.use_cases.classify_company_profile import ClassifyCompanyProfileUseCase
from ia_cumplify.config.settings import get_settings
from ia_cumplify.domain.exceptions import ClassificationError, InvalidProfileTextError

router = APIRouter(prefix="/company-profiles", tags=["company-profiles"])
logger = logging.getLogger(__name__)


def get_classify_profile_use_case(
    classifier: OpenAICompanyProfileClassifierAdapter = Depends(get_profile_classifier),
) -> ClassifyCompanyProfileUseCase:
    return ClassifyCompanyProfileUseCase(
        classifier=classifier,
        max_chars=get_settings().profile_text_max_chars,
    )


@router.post("/classify", response_model=ClassifyCompanyProfileResponse)
def classify_company_profile(
    body: ClassifyCompanyProfileRequest,
    use_case: ClassifyCompanyProfileUseCase = Depends(get_classify_profile_use_case),
) -> ClassifyCompanyProfileResponse:
    candidates = body.candidate_values.to_domain() if body.candidate_values else None
    started_at = perf_counter()
    # The text never reaches the log or an error body: only the kind of failure and its length. 422 is
    # only for a text the use case rejects; any other failure, a ValueError too, is a 502 the backend
    # retries.
    try:
        result = use_case.execute(body.text, candidates)
    except InvalidProfileTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ClassificationError as exc:
        logger.error("Company profile classification failed (%d characters): %s", len(body.text), exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Unexpected company profile failure (%d characters): %s", len(body.text), type(exc).__name__)
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc

    response = ClassifyCompanyProfileResponse.from_domain(result)
    if get_settings().include_dev_metrics:  # DEV-ONLY
        response.dev_metrics = DevMetricsPayload.from_usage(
            result.usage, (perf_counter() - started_at) * 1000
        )
    return response
