from pydantic import BaseModel, Field


class LlmCompanyProfileClassification(BaseModel):
    """Six regulatory dimensions for a company, as it describes itself."""

    # The descriptions travel in the JSON schema, next to each field the model fills. They speak of
    # the company, not of an article, so they are not shared with the article schema.
    scope: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Regulatory fields the company's operations fall under, at most 4 words per item "
            "(e.g. Medio Ambiente, Laboral)."
        ),
    )
    productive_sector: list[str] = Field(
        ...,
        min_length=1,
        description="The company's industries or lines of business, at most 3 words per item (e.g. Minería, Logística).",
    )
    territorial_coverage: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Places where the company operates: Nacional only if it operates throughout the country, "
            "otherwise the official name with its level (Región de ..., Provincia de ..., Comuna de ...)."
        ),
    )
    activity_action: list[str] = Field(
        ...,
        min_length=1,
        description="Activities the company performs, one short phrase per activity, at most 12 words, starting with a noun.",
    )
    facility_installation_equipment: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Facilities or equipment the company has or uses, at most 4 words per item. Infer only what "
            "the described activities clearly imply, never something the description denies."
        ),
    )
    others: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "The company's size and thresholds stated in the description (workers, fleet, capacities, "
            "volumes, shifts), with numbers and units as written. Do not infer. "
            "Use No especificado if there is nothing."
        ),
    )
