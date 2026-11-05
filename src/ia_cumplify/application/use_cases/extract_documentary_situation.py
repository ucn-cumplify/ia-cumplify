from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.application.ports.documentary_situation_extractor import DocumentarySituationExtractorPort
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.documentary_situation import (
    DOCUMENTARY_SITUATION_PROMPT_VERSION,
    MAX_CONTENT_CHARS,
    DocumentarySituation,
    DocumentarySituationRequest,
    DocumentarySituationSubject,
    DocumentarySituationSubjectFile,
    ReviewedDocumentaryFile,
)
from ia_cumplify.domain.evidence_text import READ, ReadLimits


class ExtractDocumentarySituationUseCase:
    """Reads each file and asks the extractor for one company situation. It keeps no state and no database."""

    def __init__(
        self,
        extractor: DocumentarySituationExtractorPort,
        limits: ReadLimits | None = None,
    ) -> None:
        self._extractor = extractor
        self._limits = limits if limits is not None else ReadLimits()

    def execute(self, request: DocumentarySituationRequest) -> DocumentarySituation:
        extracted = tuple(
            extract_text(_extension(item.filename), item.content, self._limits) for item in request.files
        )
        files = tuple(
            ReviewedDocumentaryFile(filename=item.filename, status=reading.status)
            for item, reading in zip(request.files, extracted, strict=True)
        )
        if not any(reading.status == READ for reading in extracted):
            return DocumentarySituation(
                content="",
                prompt_version=DOCUMENTARY_SITUATION_PROMPT_VERSION,
                usage=TokenUsage(),
                model=None,
                files=files,
            )

        subject = DocumentarySituationSubject(
            files=tuple(
                DocumentarySituationSubjectFile(
                    id=item.id,
                    filename=item.filename,
                    status=reading.status,
                    fragments=tuple(fragment.text for fragment in reading.fragments),
                    truncated=reading.truncated,
                    detail=reading.detail,
                )
                for item, reading in zip(request.files, extracted, strict=True)
            )
        )
        judgment = self._extractor.extract(subject)
        content = judgment.content.strip()[:MAX_CONTENT_CHARS]
        return DocumentarySituation(
            content=content,
            prompt_version=judgment.prompt_version or self._extractor.version,
            usage=judgment.usage,
            model=judgment.model,
            files=files,
        )


def _extension(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""
