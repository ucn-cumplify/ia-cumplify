import logging
from collections.abc import Sequence
from time import perf_counter

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ia_cumplify.adapters.inbound.http.dependencies import (
    get_documentary_situation_extractor,
    get_profile_classifier,
)
from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload
from ia_cumplify.adapters.inbound.http.schemas.company_profile import (
    ClassifyCompanyProfileRequest,
    ClassifyCompanyProfileResponse,
)
from ia_cumplify.adapters.inbound.http.schemas.documentary_situation import (
    DocumentarySituationResponse,
    files_too_large,
    parse_documentary_situation,
    too_many_files,
)
from ia_cumplify.adapters.outbound.openai.profile_classifier import OpenAICompanyProfileClassifierAdapter
from ia_cumplify.application.ports.documentary_situation_extractor import DocumentarySituationExtractorPort
from ia_cumplify.application.use_cases.classify_company_profile import ClassifyCompanyProfileUseCase
from ia_cumplify.application.use_cases.extract_documentary_situation import ExtractDocumentarySituationUseCase
from ia_cumplify.config.settings import get_settings
from ia_cumplify.domain.documentary_situation import DOCUMENTARY_SITUATION_MAX_FILES, DOCUMENTARY_SITUATION_MAX_TOTAL_BYTES
from ia_cumplify.domain.exceptions import ClassificationError, DocumentarySituationError, InvalidProfileTextError

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


_READ_CHUNK = 64 * 1024


def get_extract_documentary_situation_use_case(
    extractor: DocumentarySituationExtractorPort = Depends(get_documentary_situation_extractor),
) -> ExtractDocumentarySituationUseCase:
    return ExtractDocumentarySituationUseCase(extractor)


@router.post("/documentary-situation", response_model=DocumentarySituationResponse)
async def extract_documentary_situation(
    situation: str = Form(..., description="JSON: the files of the pack"),
    files: list[UploadFile] | None = File(default=None),
    use_case: ExtractDocumentarySituationUseCase = Depends(get_extract_documentary_situation_use_case),
) -> DocumentarySituationResponse:
    """Return one company documentary situation from the uploaded files.

    ``situation`` is one form field. Each file is a part named ``files``, and its filename
    matches one ``files[].filename`` in the JSON. Nothing is read from the database.
    """
    try:
        uploads = await _read_uploads(files or [])
        accepted = parse_documentary_situation(situation, uploads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        result = await run_in_threadpool(use_case.execute, accepted)
    except DocumentarySituationError as exc:
        logger.error("Documentary situation failed for %d files: %s", len(accepted.files), type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Unexpected documentary situation failure for %d files: %s",
            len(accepted.files),
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
    logger.info(
        "Documentary situation response: %d files (%s), prompt_version %s.",
        len(result.files),
        ", ".join(item.status for item in result.files) or "none",
        result.prompt_version,
    )
    return DocumentarySituationResponse.from_domain(result)


async def _read_uploads(files: Sequence[UploadFile]) -> list[tuple[str | None, bytes]]:
    if len(files) > DOCUMENTARY_SITUATION_MAX_FILES:
        raise ValueError(too_many_files(len(files)))
    uploads: list[tuple[str | None, bytes]] = []
    total = 0
    for upload in files:
        chunks: list[bytes] = []
        while True:
            chunk = await upload.read(_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > DOCUMENTARY_SITUATION_MAX_TOTAL_BYTES:
                raise ValueError(files_too_large(DOCUMENTARY_SITUATION_MAX_TOTAL_BYTES))
            chunks.append(chunk)
        uploads.append((upload.filename, b"".join(chunks)))
    return uploads
