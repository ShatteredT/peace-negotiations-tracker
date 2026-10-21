import pytest
from pydantic import ValidationError

from src import config
from src.agents.analyst_schemas import ArticleAnalysis, BatchAnalysis, CategoryScore, LegacyArticleAnalysis


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
    cat = CategoryScore(score=0.75, justification="Готовність до поступок", confidence=0.9)
    assert cat.score == 0.75


@pytest.mark.parametrize("bad_score", [1.5, -1.01])
def test_category_score_out_of_bounds(bad_score):
    with pytest.raises(ValidationError):
        CategoryScore(score=bad_score, justification="Невалідне значення", confidence=0.5)


def test_article_analysis_structure():
    raw_payload = {
        "article_id": "test_01",
        "overall_sentiment": 0.5,
        "is_compromise_oriented": True,
        "categories": {
            "diplomacy": {"score": 0.5, "justification": "test", "confidence": 0.9},
            "security": {"score": 0.0, "justification": "test", "confidence": 0.1},
            "territorial": {"score": -0.2, "justification": "test", "confidence": 0.5},
            "humanitarian": {"score": 0.8, "justification": "test", "confidence": 1.0},
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
        "categories": {"diplomacy": {"score": 0.5, "justification": "test", "confidence": 0.5}},
    }
    with pytest.raises(ValidationError):
        ArticleAnalysis.model_validate(raw_payload)


def test_confidence_is_required_and_bounded():
    assert CategoryScore(score=0.1, justification="x", confidence=0.8).confidence == 0.8
    with pytest.raises(ValidationError):
        CategoryScore(score=0.1, justification="x")  # без confidence
    with pytest.raises(ValidationError):
        CategoryScore(score=0.1, justification="x", confidence=None)
    for bad in (1.2, -0.1):
        with pytest.raises(ValidationError):
            CategoryScore(score=0.1, justification="x", confidence=bad)


def _payload(with_confidence):
    cat = {"score": 0.1, "justification": "x"}
    if with_confidence:
        cat["confidence"] = 0.7
    return {
        "article_id": "t",
        "overall_sentiment": 0.0,
        "is_compromise_oriented": False,
        "categories": {k: dict(cat) for k in ("diplomacy", "security", "territorial", "humanitarian")},
    }


def test_llm_response_without_confidence_is_rejected():
    with pytest.raises(ValidationError):
        BatchAnalysis.model_validate({"analyses": [_payload(with_confidence=False)]})
    assert BatchAnalysis.model_validate({"analyses": [_payload(with_confidence=True)]})


def test_legacy_model_still_reads_old_data_without_confidence():
    old = LegacyArticleAnalysis.model_validate(_payload(with_confidence=False))
    assert old.categories.diplomacy.confidence is None
