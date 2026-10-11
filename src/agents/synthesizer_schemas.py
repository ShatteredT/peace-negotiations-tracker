from typing import List, Literal

from pydantic import BaseModel, Field


class AnnotationText(BaseModel):
    article_id: str = Field(..., description="id статті зі списку для анотацій")
    text: str = Field(..., description="1-2 речення: що сталося і чому це змінило тон")


class PeriodSynthesis(BaseModel):
    trend: Literal["escalation", "compromise", "stable", "mixed"] = Field(
        ..., description="Загальна тенденція періоду"
    )
    summary: str = Field(..., description="3-5 речень українською: тенденція, ключові події, суперечності")
    annotations: List[AnnotationText] = Field(..., description="По одній анотації на кожен запитаний id")
