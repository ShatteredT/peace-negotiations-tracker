import pytest
from pydantic import ValidationError

from src import config
from src.agents.analyst_schemas import ArticleAnalysis, CategoryScore


# config: тести не залежать від наявності реального ключа
def test_validate_config_raises_without_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    with pytest.raises(ValueError):
        config.validate_config()


def test_validate_config_rejects_placeholder(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "ваш_справжній_ключ")
    with pytest.raises(ValueError):
        config.validate_config()


def test_validate_config_accepts_real_looking_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "AIza-test-key")
    config.validate_config()  # не має кидати виняток


# schemas
def test_category_score_boundary_valid():
    cat = CategoryScore(score=0.75, justification="Готовність до поступок")
    assert cat.score == 0.75


@pytest.mark.parametrize("bad_score", [1.5, -1.01])
def test_category_score_out_of_bounds(bad_score):
    with pytest.raises(ValidationError):
        CategoryScore(score=bad_score, justification="Невалідне значення")


def test_article_analysis_structure():
    raw_payload = {
        "article_id": "test_01",
        "overall_sentiment": 0.5,
        "is_compromise_oriented": True,
        "categories": {
            "diplomacy": {"score": 0.5, "justification": "test"},
            "security": {"score": 0.0, "justification": "test"},
            "territorial": {"score": -0.2, "justification": "test"},
            "humanitarian": {"score": 0.8, "justification": "test"},
        },
    }
    model = ArticleAnalysis.model_validate(raw_payload)
    assert model.article_id == "test_01"
    assert model.categories.humanitarian.score == 0.8


def test_article_analysis_requires_all_four_categories():
    raw_payload = {
        "article_id": "test_02",
        "overall_sentiment": 0.1,
        "is_compromise_oriented": False,
        "categories": {"diplomacy": {"score": 0.5, "justification": "test"}},
    }
    with pytest.raises(ValidationError):
        ArticleAnalysis.model_validate(raw_payload)


def test_confidence_is_optional_but_bounded():
    assert CategoryScore(score=0.1, justification="x").confidence is None
    assert CategoryScore(score=0.1, justification="x", confidence=0.8).confidence == 0.8
    with pytest.raises(ValidationError):
        CategoryScore(score=0.1, justification="x", confidence=1.2)
