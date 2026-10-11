"""Макет графіків: захист від накладань підписів, легенди й кнопок періоду."""
import pandas as pd
import pytest

from src.ui import charts

CATS = charts.CATEGORIES


@pytest.fixture
def data():
    months = pd.date_range("2023-01-01", periods=8, freq="MS")
    rows = [(m, c, 0.1 * i - 0.3) for i, m in enumerate(months) for c in CATS]
    df = pd.DataFrame(rows, columns=["month", "metric_category", "score"])
    arts = pd.DataFrame({
        "article_id": [f"a{i}" for i in range(8)], "month": months, "date": months + pd.Timedelta(days=9),
        "overall_sentiment": [0.1 * i - 0.3 for i in range(8)], "is_compromise_oriented": [i % 2 == 0 for i in range(8)],
    })
    notes = pd.DataFrame({"event_date": ["2023-03-10"], "metric_category": ["diplomacy"],
                          "author_id": ["analyst_1"], "content": ["Замітка"]})
    ann = pd.DataFrame({
        "kind": ["peak", "drop"], "date": pd.to_datetime(["2023-02-10", "2023-05-10"]),
        "overall_sentiment": [0.5, -0.5], "text": ["Пік", "Спад"], "title": ["T1", "T2"],
    })
    return df, arts, notes, ann


def names(fig):
    return [t.name for t in fig.data]


def test_trend_legend_is_below_plot_and_period_buttons_above_it(data):
    fig = charts.trend_figure(*data)
    assert fig.layout.legend.y < 0, "легенда під графіком"
    assert fig.layout.xaxis.rangeselector.y >= 1 and fig.layout.xaxis.rangeselector.yanchor == "bottom"
    assert not fig.layout.xaxis.rangeslider.visible, "мінікарта дублювала б усі маркери"


@pytest.mark.parametrize("builder_name", ["trend", "heatmap", "year_share", "year_violin", "period_tone"])
def test_every_chart_lets_plotly_fit_axis_titles_and_labels(data, builder_name):
    df, arts, notes, ann = data
    table = pd.DataFrame({"Період": ["2023-H1", "2023-H2"], "Середній тон": [0.1, -0.2]})
    fig = {
        "trend": lambda: charts.trend_figure(df, arts, notes, ann),
        "heatmap": lambda: charts.heatmap_figure(df),
        "year_share": lambda: charts.year_share_figure(arts),
        "year_violin": lambda: charts.year_violin_figure(arts),
        "period_tone": lambda: charts.period_tone_figure(table),
    }[builder_name]()
    assert fig.layout.xaxis.automargin and fig.layout.yaxis.automargin
    assert fig.layout.margin.l <= 8 and fig.layout.margin.b <= 8, "поля задає automargin, а не фіксовані числа"


def test_overall_view_lists_every_marker_kind_in_the_legend(data):
    fig = charts.trend_figure(*data)
    assert names(fig) == ["Середнє за місяць", "Тренд (3 міс.)", "Експертні замітки", "Анотація: пік", "Анотація: спад"]


def test_categories_view_has_one_line_per_category_and_no_agent_annotations(data):
    fig = charts.trend_figure(*data, view=charts.VIEW_CATEGORIES)
    assert names(fig)[:4] == [charts.CAT_LABEL[c] for c in CATS]
    assert "Анотація: пік" not in names(fig)


def test_toggles_remove_notes_and_annotations(data):
    fig = charts.trend_figure(*data, show_notes=False, show_annotations=False)
    assert names(fig) == ["Середнє за місяць", "Тренд (3 міс.)"]


def test_trend_works_without_notes_and_annotations(data):
    df, arts, notes, ann = data
    fig = charts.trend_figure(df, arts, notes.iloc[0:0], ann.iloc[0:0])
    assert names(fig) == ["Середнє за місяць", "Тренд (3 міс.)"]


def test_heatmap_uses_ukrainian_labels(data):
    fig = charts.heatmap_figure(data[0])
    assert list(fig.data[0].y) == [charts.CAT_LABEL[c] for c in CATS]
    assert fig.data[0].colorbar.title.text == "Оцінка"
