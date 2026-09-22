from pydantic import BaseModel, Field


class LlmArticleClassification(BaseModel):
    """Structured output shape expected from the OpenAI API."""

    scope: list[str] = Field(..., min_length=1)
    productive_sector: list[str] = Field(..., min_length=1)
    territorial_coverage: list[str] = Field(..., min_length=1)
    activity_action: list[str] = Field(..., min_length=1)
    facility_installation_equipment: list[str] = Field(..., min_length=1)
