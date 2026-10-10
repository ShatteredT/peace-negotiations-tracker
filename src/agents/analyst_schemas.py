from typing import List, Optional

from pydantic import BaseModel, Field


class CategoryScore(BaseModel):
    score: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Оцінка риторики: від -1.0 (ескалація) до +1.0 (мир/компроміс)",
    )
    justification: str = Field(..., description="Цитата чи аргументація з тексту")
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Впевненість моделі в оцінці: 0.0 (здогад) … 1.0 (однозначно). "
        "Для старих записів може бути відсутня.",
    )


class Categories(BaseModel):
    diplomacy: CategoryScore = Field(..., description="Дипломатичні контакти та переговори")
    security: CategoryScore = Field(..., description="Безпекові гарантії та припинення вогню")
    territorial: CategoryScore = Field(..., description="Позиції щодо кордонів та територій")
    humanitarian: CategoryScore = Field(..., description="Гуманітарні аспекти, полонені, цивільні")


class ArticleAnalysis(BaseModel):
    article_id: str
    overall_sentiment: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Загальний підсумковий бал готовності до компромісу",
    )
    categories: Categories = Field(..., description="Оцінки за 4 обов'язковими категоріями")
    is_compromise_oriented: bool


class BatchAnalysis(BaseModel):
    """Результат аналізу пачки статей"""

    analyses: List[ArticleAnalysis] = Field(..., description="Список аналізів кожної статті")


if __name__ == "__main__":
    print("Pydantic-схему ArticleAnalysis завантажено успішно.")
