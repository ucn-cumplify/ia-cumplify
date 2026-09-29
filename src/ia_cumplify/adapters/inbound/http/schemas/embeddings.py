from pydantic import BaseModel, Field

from ia_cumplify.domain.embedding import TextEmbeddings


class EmbedTextsRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, description="Texts to embed; vectors come back in the same order")
    model: str | None = Field(default=None, description="Embedding model; defaults to OPENAI_EMBEDDING_MODEL")
    dimensions: int | None = Field(
        default=None,
        ge=1,
        le=3072,
        description="Vector size; defaults to OPENAI_EMBEDDING_DIMENSIONS",
    )


class EmbeddingUsage(BaseModel):
    total_tokens: int


class EmbedTextsResponse(BaseModel):
    model: str
    dimensions: int
    vectors: list[list[float]]
    usage: EmbeddingUsage

    @classmethod
    def from_domain(cls, result: TextEmbeddings) -> "EmbedTextsResponse":
        return cls(
            model=result.model,
            dimensions=result.dimensions,
            vectors=[list(vector) for vector in result.vectors],
            usage=EmbeddingUsage(total_tokens=result.total_tokens),
        )
