import numpy as np
import pandas as pd
import pytest

from src.ui import compare_charts as cmp

CATS = cmp.CATEGORIES


def make_data(bbc_tone=0.0, radio_tone=0.2, years=(2022, 2023, 2024), per_half=4):
    """Синтетичні оцінки двох джерел: однакова кількість статей у кожному півріччі, фіксований зсув тону."""
    rows = []
    for year in years:
        for half, month in ((1, 3), (2, 9)):
            for src, tone in ((cmp.BBC, bbc_tone), (cmp.RADIO, radio_tone)):
                for i in range(per_half):
                    # невеликий детермінований розкид, щоб були ненульові довірчі інтервали
                    wiggle = (-0.1, 0.1)[i % 2]
                    for cat in CATS:
                        rows.append(dict(
                            article_id=f"{src}-{year}-{half}-{i}", source=src, title=f"{src} {year} H{half} #{i}",
                            date=pd.Timestamp(year, month, 10 + i), url=f"https://x/{src}/{year}/{half}/{i}",
                            metric_category=cat, score=tone + wiggle, justification="j", confidence=0.8,
                            overall_sentiment=tone + wiggle, is_compromise_oriented=(tone + wiggle) > 0.1))
    df = pd.DataFrame(rows)
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    arts = df.drop_duplicates("article_id").drop(columns=["metric_category", "score", "justification", "confidence"])
    return cmp.add_period(df), cmp.add_period(arts)


@pytest.fixture
def data():
    return make_data()


def trace_names(fig):
    return [t.name for t in fig.data if t.name]


def test_add_period_matches_summary_period_format():
    df = pd.DataFrame({"date": pd.to_datetime(["2023-01-01", "2023-06-30", "2023-07-01", "2024-12-31"])})
    assert list(cmp.add_period(df)["period"]) == ["2023-H1", "2023-H1", "2023-H2", "2024-H2"]


def test_head_to_head_gap_is_radio_minus_bbc(data):
    df, arts = data
    tone, comp, spread, conf = cmp.head_to_head(df, arts)
    assert tone.gap == pytest.approx(0.2) and tone.a == pytest.approx(0.0) and tone.b == pytest.approx(0.2)
    assert tone.significant, "зсув 0.2 при малому розкиді — помітна різниця"
    assert conf.gap == pytest.approx(0.0) and not conf.significant
    assert spread.half is None and not spread.significant, "розкид — описова характеристика без інтервалу"
    assert comp.fmt == ".0%"


def test_identical_sources_are_within_noise():
    df, arts = make_data(bbc_tone=0.1, radio_tone=0.1)
    tone = cmp.head_to_head(df, arts)[0]
    assert tone.gap == pytest.approx(0.0) and not tone.significant


def test_formatting_helpers():
    assert cmp.fmt_gap(0.034, "+.2f") == "+0.03" and cmp.fmt_gap(-0.034, ".2f") == "-0.03"
    assert cmp.fmt_gap(0.03, ".0%") == "+3 п.п."
    assert cmp.fmt_val(float("nan"), ".2f") == "—" and cmp.fmt_gap(float("nan"), ".2f") == "—"


@pytest.mark.parametrize("granularity, expected_first, expected_len", [
    ("half", "2022-H1", 6), ("quarter", "2022-Q1", 11), ("month", "2022-03", 31),
])
def test_period_table_covers_the_whole_range_including_gaps(data, granularity, expected_first, expected_len):
    _, arts = data
    table = cmp.period_table(arts, granularity)
    assert table["label"].iloc[0] == expected_first and len(table) == expected_len
    if granularity != "half":
        assert table["bbc"].isna().any(), "порожні місяці/квартали лишаються NaN, а не домальовуються"
    assert (table["mid"] > table.index).all() and (table["mid"] < table["end"]).all()


def test_dynamics_correlation_needs_enough_periods_and_handles_flat_series(data):
    _, arts = data
    assert cmp.dynamics_correlation(arts) is None, "обидва ряди сталі — кореляція не визначена"
    arts = arts.copy()
    arts.loc[(arts["source"] == cmp.BBC) & (arts["period"] == "2022-H1"), "overall_sentiment"] = -0.6
    arts.loc[(arts["source"] == cmp.RADIO) & (arts["period"] == "2022-H1"), "overall_sentiment"] = -0.4
    r, n = cmp.dynamics_correlation(arts)
    assert n == 6 and r == pytest.approx(1.0)
    assert cmp.dynamics_correlation(arts[arts["date"].dt.year == 2022]) is None


def test_trend_has_both_fills_when_lines_cross_and_one_when_they_do_not(data):
    _, arts = data
    assert [n for n in trace_names(cmp.trend_gap_figure(arts)) if n.startswith("Тон вищий")] == \
        ["Тон вищий у Радіо Свобода"], "Свобода завжди вище — заливка лише помаранчева"
    arts = arts.copy()
    mask = (arts["source"] == cmp.RADIO) & (arts["date"].dt.year == 2023)
    arts.loc[mask, "overall_sentiment"] = -0.5
    names = trace_names(cmp.trend_gap_figure(arts))
    assert "Тон вищий у BBC" in names and "Тон вищий у Радіо Свобода" in names


def test_gap_fill_polygons_are_closed_and_positioned_on_the_real_timeline(data):
    """Регресія: у pandas 3 одиниця часу не завжди ns, і заливка «з'їжджала» в 1970 рік."""
    _, arts = data
    fig = cmp.trend_gap_figure(arts)
    fill = next(t for t in fig.data if t.fill == "toself")
    xs = [pd.Timestamp(x) for x in fill.x if x is not None]
    assert min(xs) >= pd.Timestamp("2022-01-01") and max(xs) <= pd.Timestamp("2025-01-01")
    assert fill.x[-1] is None and fill.y[-1] is None, "багатокутники розділені розривом"


def test_period_table_of_nothing_is_empty_not_an_error(data):
    _, arts = data
    assert cmp.period_table(arts.iloc[0:0]).empty and cmp.dynamics_correlation(arts.iloc[0:0]) is None


def test_polygons_split_on_mask_runs():
    x = np.arange(7)
    upper, lower = np.full(7, 1.0), np.zeros(7)
    xs, ys = cmp._polygons(x, upper, lower, np.array([1, 1, 0, 0, 1, 0, 1], dtype=bool))
    assert xs.count(None) == 3 and len(xs) == len(ys)
    assert xs[:5] == [0, 1, 1, 0, None]


def test_densify_breaks_only_at_real_gaps():
    x = np.arange(4, dtype="int64") * 10**9
    a = np.array([0.0, 1.0, np.nan, 2.0])
    b = np.array([1.0, 0.0, 0.0, 1.0])
    xs, ya, yb = cmp._densify(x, a, b, steps=5)
    assert np.isnan(ya).sum() == 1 and np.isnan(yb).sum() == 1, "один розрив — на прогалині, а не між кожною парою"
    assert len(xs) == 5 + 1


def test_diff_bars_follow_radio_minus_bbc_and_label_the_biggest_gap(data):
    _, arts = data
    arts = arts.copy()
    arts.loc[(arts["source"] == cmp.RADIO) & (arts["period"] == "2023-H2"), "overall_sentiment"] = 0.9
    fig = cmp.trend_gap_figure(arts)
    bar = next(t for t in fig.data if t.type == "bar")
    assert [t for t in bar.text if t] == [f"{bar.y.max():+.2f}"]
    assert all(g >= 0 for g in bar.y)
    assert cmp.trend_gap_figure(arts, show_articles=True).layout.hovermode == "closest"
    assert cmp.trend_gap_figure(arts).layout.hovermode == "x unified"


def test_category_table_criteria(data):
    df, _ = data
    direction = cmp.category_table(df, "direction")
    assert list(direction.index) == list(CATS)
    assert direction["gap"].tolist() == pytest.approx([0.2] * 4)
    assert direction[["n_bbc", "n_radio"]].eq(24).all().all()
    intensity = cmp.category_table(df, "intensity")
    assert (intensity["bbc"] > 0).all(), "інтенсивність — середній модуль, навіть коли напрям ≈ 0"
    assert cmp.category_table(df, "confidence")["gap"].abs().max() == pytest.approx(0.0)
    share = cmp.category_table(df, "compromise")
    assert (share["radio"] >= share["bbc"]).all() and share["radio"].between(0, 1).all()


def test_category_table_skips_categories_missing_in_one_source(data):
    df, _ = data
    df = df[~((df["source"] == cmp.BBC) & (df["metric_category"] == "security"))]
    assert "security" not in cmp.category_table(df).index
    assert cmp.category_table(df.iloc[0:0]).empty


def test_radar_and_dumbbell_for_every_criterion(data):
    df, _ = data
    for key in cmp.CRITERIA:
        table = cmp.category_table(df, key)
        radar = cmp.radar_figure(table, key)
        assert [t.name for t in radar.data] == ["BBC", "Радіо Свобода"]
        assert len(radar.data[0].r) == len(CATS) + 1, "полігон замкнений"
        dumb = cmp.dumbbell_figure(table, key)
        assert [t.name for t in dumb.data if t.name] == ["BBC", "Радіо Свобода"]
    labels = list(cmp.dumbbell_figure(cmp.category_table(df, "direction")).layout.yaxis.ticktext)
    assert all("Δ +0.20" in t for t in labels)


def test_dumbbell_marks_only_significant_gaps(data):
    df, _ = data
    ticks = cmp.dumbbell_figure(cmp.category_table(df, "direction")).layout.yaxis.ticktext
    assert all(t.endswith("*</b>") for t in ticks)
    same, _ = make_data(bbc_tone=0.1, radio_tone=0.1)
    ticks = cmp.dumbbell_figure(cmp.category_table(same, "direction")).layout.yaxis.ticktext
    assert not any("*" in t for t in ticks)


def test_heatmap_grid_and_diff_values(data):
    df, _ = data
    grid = cmp.heatmap_grid(df)
    assert grid["periods"] == ["2022-H1", "2022-H2", "2023-H1", "2023-H2", "2024-H1", "2024-H2"]
    assert np.allclose(grid["diff"].to_numpy(), 0.2)
    assert (grid["n_bbc"] == 4).all().all()


@pytest.mark.parametrize("mode", cmp.HEAT_MODES)
def test_heatmap_figure_modes_and_click_targets(data, mode):
    df, _ = data
    fig = cmp.heatmap_figure(df, mode)
    heat, overlay = fig.data
    assert heat.type == "heatmap" and overlay.type == "scatter"
    assert len(overlay.x) == len(CATS) * 6, "прозорий маркер у кожній клітинці, навіть порожній"
    assert fig.layout.clickmode == "event+select"
    assert (heat.zmin, heat.zmax) == pytest.approx((-0.3, 0.3) if mode == cmp.HEAT_DIFF else (-1, 1))


def test_heatmap_click_round_trips_to_a_cell(data):
    df, _ = data
    overlay = cmp.heatmap_figure(df).data[1]
    i = 7
    point = {"customdata": list(overlay.customdata[i]), "x": overlay.x[i], "y": overlay.y[i]}
    cell = cmp.cell_from_event({"selection": {"points": [point]}})
    assert cell == (overlay.customdata[i][0], overlay.customdata[i][1])
    assert cmp.cell_from_point({"x": "2023-H1", "y": "Безпека"}) == ("security", "2023-H1"), "запасний шлях"
    assert cmp.cell_from_event({"selection": {"points": []}}) is None and cmp.cell_from_event(None) is None


def test_selected_cell_is_outlined_only_when_it_exists(data):
    df, _ = data
    assert len(cmp.heatmap_figure(df, selected_cell=("security", "2023-H1")).layout.shapes) == 2
    assert len(cmp.heatmap_figure(df, selected_cell=("security", "1999-H1")).layout.shapes) == 0
    assert len(cmp.heatmap_figure(df).layout.shapes) == 0


def test_cell_articles_lists_both_sources_bbc_first_and_matches_heatmap(data):
    df, _ = data
    rows = cmp.cell_articles(df, "security", "2023-H1")
    assert list(rows["source"]) == [cmp.BBC] * 4 + [cmp.RADIO] * 4
    grid = cmp.heatmap_grid(df)
    assert rows[rows["source"] == cmp.RADIO]["score"].mean() == pytest.approx(grid["mean_radio"].at["security", "2023-H1"])
    assert cmp.cell_articles(df, "security", "1999-H1").empty


def test_distribution_panels_and_shares(data):
    df, arts = data
    fig = cmp.distribution_figure(df, arts, cmp.SCOPE_CATEGORIES)
    bars = [t for t in fig.data if t.type == "bar"]
    assert len(bars) == 2 * len(CATS)
    assert all(abs(abs(np.asarray(b.x)).sum() - 100) < 1e-6 for b in bars), "кожна половина — 100% оцінок"
    assert all((np.asarray(b.x) <= 0).all() for b in bars if b.name == "BBC")
    assert all((np.asarray(b.x) >= 0).all() for b in bars if b.name == "Радіо Свобода")
    overall = cmp.distribution_figure(df, arts, cmp.SCOPE_OVERALL)
    assert len([t for t in overall.data if t.type == "bar"]) == 2


def test_stance_shares_sum_to_100_and_use_thresholds():
    arts = pd.DataFrame({
        "source": [cmp.BBC] * 4, "date": pd.to_datetime(["2023-02-01"] * 4),
        "overall_sentiment": [-0.3, 0.0, 0.29, 0.3], "is_compromise_oriented": [False, False, True, True]})
    shares = cmp.stance_shares(arts).loc[(cmp.BBC, "2023")]
    assert shares.to_dict() == {"escalation": 25.0, "neutral": 50.0, "compromise": 25.0}
    fig = cmp.stance_figure(make_data()[1])
    assert len([t for t in fig.data if t.showlegend]) == 3, "легенда один раз для двох панелей"


def test_compromise_year_figure_has_one_bar_series_per_source(data):
    _, arts = data
    fig = cmp.compromise_year_figure(arts)
    assert [t.name for t in fig.data] == ["BBC", "Радіо Свобода"]
    assert list(fig.data[0].x) == ["2022", "2023", "2024"]


def test_highlights_name_the_real_biggest_gaps(data):
    df, arts = data
    df = df.copy()
    arts = arts.copy()
    df.loc[(df["source"] == cmp.RADIO) & (df["metric_category"] == "territorial"), "score"] += 0.5
    arts.loc[(arts["source"] == cmp.RADIO) & (arts["period"] == "2023-H2"), "overall_sentiment"] += 0.6
    text = " ".join(cmp.highlights(df, arts))
    assert "**Території**" in text and "**2023-H2**" in text and "Радіо Свобода" in text


def test_highlights_do_not_crash_on_tiny_data(data):
    df, arts = data
    one = df[df["period"] == "2022-H1"]
    assert isinstance(cmp.highlights(one, one.drop_duplicates("article_id")), list)
    assert cmp.highlights(df.iloc[0:0], arts.iloc[0:0]) == []


def test_summary_timeline_highlights_selected_period():
    summaries = pd.DataFrame({
        "source": [cmp.BBC, cmp.BBC, cmp.RADIO, cmp.RADIO], "period": ["2023-H1", "2023-H2"] * 2,
        "avg_sentiment": [0.1, -0.2, 0.3, 0.0]})
    fig = cmp.summary_timeline_figure(summaries, "2023-H2")
    assert [t.name for t in fig.data] == ["BBC", "Радіо Свобода"]
    assert list(fig.data[0].marker.size) == [7, 13] and len(fig.layout.shapes) == 2
