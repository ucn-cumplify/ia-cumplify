from pydantic import BaseModel, Field


class LlmDocumentarySituation(BaseModel):
    content: str = Field(
        ...,
        description=(
            "One company documentary situation in Spanish, taken only from the files: dimensions, "
            "capacities and authorized conditions. At most 4000 characters. Empty if nothing was read."
        ),
    )
