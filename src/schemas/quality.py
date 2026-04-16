from pydantic import BaseModel, Field


class DimensionScore(BaseModel):
    score: float = Field(ge=1, le=5)
    reasoning: str
    gaps: list[str]
