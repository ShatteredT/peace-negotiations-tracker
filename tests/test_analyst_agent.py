import pytest
from pydantic import ValidationError
from src.agents.analyst_schemas import ArticleAnalysis, CategoryScore


def test_category_score_boundary_valid():
    cat = CategoryScore(score=0.75, justification="Готовність до поступок")
    assert cat.score == 0.75


def test_category_score_out_of_bounds():
    with pytest.raises(ValidationError):
        CategoryScore(score=1.5, justification="Невалідне значення")


def test_article_analysis_structure():
    raw_payload = {
        "article_id": "test_01",
        "overall_sentiment": 0.5,
        "is_compromise_oriented": True,
        "categories": {
            "diplomacy": {"score": 0.5, "justification": "Зустріч делегацій"},
            "security": {"score": 0.0, "justification": "Без змін"},
            "territorial": {"score": -0.2, "justification": "Жорстка позиція"},
            "humanitarian": {"score": 0.8, "justification": "Обмін полоненими"},
        },
    }
    model = ArticleAnalysis.model_validate(raw_payload)
    assert model.article_id == "test_01"
    assert model.categories.humanitarian.score == 0.8