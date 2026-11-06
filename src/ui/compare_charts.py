"""Розрахунки й графіки сторінки «Порівняння» (BBC проти Радіо Свобода).

Усе тут — чисті функції без Streamlit: вони приймають DataFrame зі схемою `load_assessments`
(та `article_level`) плюс колонку `period` (її додає `add_period`) і повертають числа або Figure.

Кольори джерел збігаються з рештою UI (BBC — синій, Радіо Свобода — помаранчевий), а шкала
«різниця» теплової карти використовує ті самі два кольори: колір клітинки одразу каже,
у якого джерела тон вищий.
"""
from __future__ import annotations

import html
import math
import textwrap
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.ui.charts import CAT_LABEL, CATEGORIES, SOURCE_COLORS, SOURCE_LABELS, _tidy

BBC, RADIO = "bbc", "radio_svoboda"
SOURCES = (BBC, RADIO)
SHORT_LABEL = {BBC: "BBC", RADIO: "Свобода"}

# Межі «ескалація / компроміс» збігаються зі score_color в app.py
STANCE_THRESHOLD = 0.3
STANCES = (
    ("escalation", "Ескалація", "#EF4444"),
    ("neutral", "Нейтрально", "#CBD5E1"),
    ("compromise", "Компроміс", "#22C55E"),
)

GRANULARITY = {"Півріччя": "half", "Квартал": "quarter", "Місяць": "month"}
_STEP_MONTHS = {"half": 6, "quarter": 3, "month": 1}
_FREQ = {"half": "6MS", "quarter": "QS", "month": "MS"}

MIN_ARTICLES_FOR_GAP = 3   # у півріччі менше статей — розбіжність не називаємо «найбільшою»
MIN_PERIODS_FOR_CORR = 3
NEUTRAL_MID = "#E2E8F0"
DIFF_SCALE = [[0.0, SOURCE_COLORS[BBC]], [0.5, NEUTRAL_MID], [1.0, SOURCE_COLORS[RADIO]]]

HEAT_DIFF, HEAT_BBC, HEAT_RADIO = "Різниця", "BBC", "Радіо Свобода"
HEAT_MODES = (HEAT_DIFF, HEAT_BBC, HEAT_RADIO)
SCOPE_OVERALL, SCOPE_CATEGORIES = "Загальний тон", "За категоріями"

CRITERIA = {
    "direction": dict(
        label="Напрям", fmt="+.2f", rng=(-1.0, 1.0), zero_line=True,
        values=lambda d: d["score"],
        about="Середня оцінка категорії: −1 — ескалація, +1 — компроміс.",
    ),
    "intensity": dict(
        label="Інтенсивність", fmt=".2f", rng=(0.0, 1.0), zero_line=False,
        values=lambda d: d["score"].abs(),
        about="Середній модуль оцінки: наскільки різко (в будь-який бік) джерело оцінює категорію.",
    ),
    "compromise": dict(
        label="Частка компромісних оцінок", fmt=".0%", rng=(0.0, 1.0), zero_line=False,
        values=lambda d: (d["score"] >= STANCE_THRESHOLD).astype(float),
        about=f"Яка частка оцінок категорії не нижча за +{STANCE_THRESHOLD}.",
    ),
    "confidence": dict(
        label="Впевненість моделі", fmt=".2f", rng=(0.0, 1.0), zero_line=False,
        values=lambda d: d["confidence"],
        about="Середня впевненість моделі в оцінках: показує, де порівняння найменш надійне.",
    ),
}
CATEGORY_COLUMNS = ["bbc", "radio", "bbc_ci", "radio_ci", "gap", "gap_ci", "n_bbc", "n_radio"]
PERIOD_COLUMNS = ["end", "mid", "label", "bbc", "n_bbc", "radio", "n_radio", "gap"]


# ---------------------------------------------------------------- підготовка й статистика

def add_period(df: pd.DataFrame) -> pd.DataFrame:
    """Додає `period` у форматі «2023-H1» — такий самий, як у period_summaries."""
    out = df.copy()
    half = np.where(out["date"].dt.month <= 6, "H1", "H2")
    out["period"] = out["date"].dt.year.astype(str) + "-" + half
    return out


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _ci(x: pd.Series) -> float:
    """Півширина 95% довірчого інтервалу середнього (нормальне наближення)."""
    x = x.dropna()
    if len(x) < 2:
        return float("nan")
    return 1.96 * float(x.std(ddof=1)) / math.sqrt(len(x))


def _mean_gap(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    """(b − a, півширина 95% інтервалу різниці середніх за Велчем)."""
    gap = float(b.mean() - a.mean())
    return gap, math.sqrt(_ci(a) ** 2 + _ci(b) ** 2)


def fmt_val(value: float, fmt: str) -> str:
    return "—" if value is None or pd.isna(value) else format(value, fmt)


def fmt_gap(gap: float, fmt: str) -> str:
    """Різниця зі знаком; для часток — у відсоткових пунктах."""
    if gap is None or pd.isna(gap):
        return "—"
    if fmt.endswith("%"):
        return f"{gap * 100:+.0f} п.п."
    return format(gap, fmt if fmt.startswith("+") else "+" + fmt)


@dataclass(frozen=True)
class Metric:
    label: str
    a: float
    b: float
    gap: float
    half: float | None
    fmt: str
    help: str = ""

    @property
    def significant(self) -> bool:
        return self.half is not None and math.isfinite(self.half) and abs(self.gap) > self.half


def _by_source(df: pd.DataFrame, column: str) -> tuple[pd.Series, pd.Series]:
    return (df.loc[df["source"] == BBC, column].dropna(), df.loc[df["source"] == RADIO, column].dropna())


def head_to_head(df: pd.DataFrame, arts: pd.DataFrame) -> list[Metric]:
    """Чотири показники «віч-на-віч»; різниця завжди рахується як Радіо Свобода − BBC."""
    tone_a, tone_b = _by_source(arts, "overall_sentiment")
    comp_a, comp_b = (s.astype(float) for s in _by_source(arts, "is_compromise_oriented"))
    conf_a, conf_b = _by_source(df, "confidence")
    out = []
    for label, a, b, fmt, help_text, ci in (
        ("Середній тон", tone_a, tone_b, "+.2f",
         "Середній загальний тон статей: −1 ескалація, +1 компроміс. Різниця: Свобода − BBC.", True),
        ("Орієнтовані на компроміс", comp_a, comp_b, ".0%",
         "Частка статей, які модель вважає орієнтованими на компроміс. Різниця в п.п.", True),
        ("Розкид тону", tone_a.std(ddof=1), tone_b.std(ddof=1), ".2f",
         "Стандартне відхилення тону статей: що більше, то різноманітніше (або різкіше) подання.", False),
        ("Впевненість моделі", conf_a, conf_b, ".2f",
         "Середня впевненість моделі в оцінках. Показує, наскільки надійне саме це порівняння.", True),
    ):
        if ci:
            gap, half = _mean_gap(a, b)
            out.append(Metric(label, float(a.mean()), float(b.mean()), gap, half, fmt, help_text))
        else:
            out.append(Metric(label, float(a), float(b), float(b - a), None, fmt, help_text))
    return out


# ---------------------------------------------------------------- часові ряди

def _bucket_start(dates: pd.Series, granularity: str) -> pd.Series:
    if granularity == "month":
        return dates.dt.to_period("M").dt.start_time
    if granularity == "quarter":
        return dates.dt.to_period("Q").dt.start_time
    month = np.where(dates.dt.month <= 6, 1, 7)
    return pd.to_datetime(pd.DataFrame({"year": dates.dt.year, "month": month, "day": 1}))


def _bucket_label(ts: pd.Timestamp, granularity: str) -> str:
    if granularity == "month":
        return f"{ts:%Y-%m}"
    if granularity == "quarter":
        return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"
    return f"{ts.year}-H{1 if ts.month <= 6 else 2}"


def period_table(arts: pd.DataFrame, granularity: str = "half") -> pd.DataFrame:
    """Середній тон кожного джерела за період: повна шкала часу, прогалини лишаються NaN."""
    if arts.empty:
        return pd.DataFrame(columns=PERIOD_COLUMNS)
    starts = _bucket_start(arts["date"], granularity)
    grouped = (arts.assign(start=starts)
               .groupby(["start", "source"])["overall_sentiment"].agg(["mean", "count"]).unstack("source"))
    full = pd.date_range(starts.min(), starts.max(), freq=_FREQ[granularity])
    grouped = grouped.reindex(full)
    step = pd.DateOffset(months=_STEP_MONTHS[granularity])
    table = pd.DataFrame(index=full)
    table["end"] = full + step
    table["mid"] = full + (table["end"] - full) / 2
    table["label"] = [_bucket_label(t, granularity) for t in full]
    for key, src in (("bbc", BBC), ("radio", RADIO)):
        table[key] = grouped["mean"][src] if src in grouped["mean"] else np.nan
        table[f"n_{key}"] = (grouped["count"][src] if src in grouped["count"] else 0)
    table[["n_bbc", "n_radio"]] = table[["n_bbc", "n_radio"]].fillna(0).astype(int)
    table["gap"] = table["radio"] - table["bbc"]
    return table


def dynamics_correlation(arts: pd.DataFrame) -> tuple[float, int] | None:
    """Кореляція Пірсона між піврічними середніми двох джерел; None, якщо точок замало."""
    table = period_table(arts, "half").dropna(subset=["bbc", "radio"])
    if len(table) < MIN_PERIODS_FOR_CORR or table["bbc"].std() == 0 or table["radio"].std() == 0:
        return None  # замало точок або один із рядів сталий: кореляція не визначена
    r = table["bbc"].corr(table["radio"])
    return None if pd.isna(r) else (float(r), len(table))


def correlation_word(r: float) -> str:
    return "висока" if r >= 0.7 else "помірна" if r >= 0.4 else "слабка" if r >= 0 else "обернена"


def _densify(x_ns: np.ndarray, a: np.ndarray, b: np.ndarray, steps: int = 24):
    """Лінійно згущує ряди, щоб заливка між лініями точно змінювала колір у місці перетину.

    Суцільні відрізки (обидва ряди мають значення в обох сусідніх точках) зшиваються в один ряд;
    розрив (NaN) вставляється лише там, де справді є прогалина.
    """
    xs: list[float] = []
    ya: list[float] = []
    yb: list[float] = []
    t = np.linspace(0.0, 1.0, steps)
    prev_ok = False
    for i in range(len(x_ns) - 1):
        ok = bool(np.isfinite([a[i], a[i + 1], b[i], b[i + 1]]).all())
        if ok:
            part = slice(1, None) if prev_ok else slice(None)  # спільну точку не дублюємо
            xs.extend((x_ns[i] + t * (x_ns[i + 1] - x_ns[i]))[part])
            ya.extend((a[i] + t * (a[i + 1] - a[i]))[part])
            yb.extend((b[i] + t * (b[i + 1] - b[i]))[part])
        elif prev_ok:
            xs.append(x_ns[i])
            ya.append(np.nan)
            yb.append(np.nan)
        prev_ok = ok
    return pd.to_datetime(np.asarray(xs, dtype="int64"), unit="ns"), np.asarray(ya), np.asarray(yb)


def _wrap(text: str, width: int = 58) -> str:
    return textwrap.fill(html.escape(text), width).replace("\n", "<br>")


def _polygons(x, upper: np.ndarray, lower: np.ndarray, mask: np.ndarray):
    """Замкнені багатокутники (верхня межа вперед, нижня назад) для кожної суцільної ділянки маски."""
    xs: list = []
    ys: list = []
    i, n = 0, len(x)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and mask[j + 1]:
            j += 1
        xs += list(x[i:j + 1]) + list(x[i:j + 1][::-1]) + [None]
        ys += list(upper[i:j + 1]) + list(lower[i:j + 1][::-1]) + [None]
        i = j + 1
    return xs, ys


def _gap_fills(fig: go.Figure, table: pd.DataFrame) -> None:
    """Заливка між лініями: колір показує, у якого джерела тон вищий.

    Це окремі замкнені багатокутники (fill="toself"), а не «tonexty»: так заливка не з'єднує
    ділянки через прогалини в даних і змінює колір рівно в точці перетину ліній.
    """
    # явно наносекунди: у pandas 3 одиниця часу за замовчуванням уже не завжди ns
    mid_ns = table["mid"].to_numpy(dtype="datetime64[ns]").astype("int64")
    x, a, b = _densify(mid_ns, table["bbc"].to_numpy(float), table["radio"].to_numpy(float))
    finite = np.isfinite(a) & np.isfinite(b)
    for name, src, upper, lower, mask in (
        ("Тон вищий у Радіо Свобода", RADIO, b, a, finite & (b >= a)),
        ("Тон вищий у BBC", BBC, a, b, finite & (a > b)),
    ):
        xs, ys = _polygons(x, upper, lower, mask)
        if xs:
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(width=0), fill="toself",
                                     fillcolor=_rgba(SOURCE_COLORS[src], 0.30), name=name, hoverinfo="skip"),
                          row=1, col=1)


def _symmetric_limit(values, floor: float = 0.4, cap: float = 1.0) -> float:
    finite = [abs(v) for v in values if v is not None and np.isfinite(v)]
    return min(cap, max(floor, math.ceil(max(finite, default=0) * 10) / 10 + 0.1))


def trend_gap_figure(arts: pd.DataFrame, granularity: str = "half", show_articles: bool = False) -> go.Figure:
    """Два ряди тону із заливкою розриву між ними й смугою різниці (Свобода − BBC) під ними."""
    table = period_table(arts, granularity)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.74, 0.26], vertical_spacing=0.05)
    if show_articles:
        for src in SOURCES:
            part = arts[arts["source"] == src]
            fig.add_trace(go.Scatter(
                x=part["date"], y=part["overall_sentiment"], mode="markers", showlegend=False,
                marker=dict(size=5, opacity=0.28, color=SOURCE_COLORS[src]),
                customdata=[_wrap(t) for t in part["title"]],
                hovertemplate="<b>%{customdata}</b><br>%{x|%d.%m.%Y}: %{y:+.2f}"
                              f"<extra>{SHORT_LABEL[src]}</extra>"), row=1, col=1)
    _gap_fills(fig, table)
    for key, src in (("bbc", BBC), ("radio", RADIO)):
        n = table[f"n_{key}"]
        fig.add_trace(go.Scatter(
            x=table["mid"], y=table[key], mode="lines+markers", name=SOURCE_LABELS[src],
            line=dict(width=3, color=SOURCE_COLORS[src]),
            marker=dict(size=(6 + 2 * np.sqrt(n)).clip(upper=16), color=SOURCE_COLORS[src],
                        line=dict(width=1.5, color="white")),
            customdata=n,
            hovertemplate=f"<b>{SHORT_LABEL[src]}</b> %{{y:+.2f}} · статей: %{{customdata}}<extra></extra>"),
            row=1, col=1)

    gaps = table["gap"]
    widths = (table["end"] - table.index) / pd.Timedelta(milliseconds=1) * 0.62
    biggest = gaps.abs().idxmax() if gaps.notna().any() else None
    fig.add_trace(go.Bar(
        x=table["mid"], y=gaps, width=widths, showlegend=False,
        marker_color=[SOURCE_COLORS[RADIO] if (pd.notna(g) and g >= 0) else SOURCE_COLORS[BBC] for g in gaps],
        text=[f"{g:+.2f}" if idx == biggest else "" for idx, g in gaps.items()],
        textposition="outside", cliponaxis=False,
        customdata=table["label"],
        hovertemplate="Різниця (Свобода − BBC) <b>%{y:+.2f}</b> · %{customdata}<extra></extra>"), row=2, col=1)

    top = _symmetric_limit(list(table["bbc"]) + list(table["radio"]), floor=0.5)
    low = _symmetric_limit(list(gaps), floor=0.2)
    every = max(1, math.ceil(len(table) / 14))
    fig.update_yaxes(range=[-top, top], title_text="Тон", zeroline=False, row=1, col=1)
    fig.update_yaxes(range=[-low, low], title_text="Δ", zeroline=False, row=2, col=1)
    fig.add_hline(y=0, line_dash="dash", opacity=0.35, row=1, col=1)
    fig.add_hline(y=0, opacity=0.35, row=2, col=1)
    fig.update_xaxes(tickmode="array", tickvals=table["mid"][::every], ticktext=table["label"][::every],
                     hoverformat=" ", showspikes=True, spikemode="across", spikethickness=1)
    fig.update_layout(
        hovermode="closest" if show_articles else "x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, traceorder="normal"), bargap=0.1)
    return _tidy(fig, height=560, top=56)


# ---------------------------------------------------------------- категорії

def category_table(df: pd.DataFrame, criterion: str = "direction") -> pd.DataFrame:
    """Критерій × категорія для обох джерел: середні, 95% інтервали, різниця, кількість оцінок."""
    values = CRITERIA[criterion]["values"]
    rows = {}
    for cat in CATEGORIES:
        part = df[df["metric_category"] == cat]
        a = values(part[part["source"] == BBC]).dropna()
        b = values(part[part["source"] == RADIO]).dropna()
        if a.empty or b.empty:
            continue
        gap, half = _mean_gap(a, b)
        rows[cat] = dict(bbc=float(a.mean()), radio=float(b.mean()), bbc_ci=_ci(a), radio_ci=_ci(b),
                         gap=gap, gap_ci=half, n_bbc=len(a), n_radio=len(b))
    return pd.DataFrame.from_dict(rows, orient="index", columns=CATEGORY_COLUMNS)


def _is_significant(gap: float, half: float) -> bool:
    return bool(pd.notna(half) and abs(gap) > half)


def _nice_ticks(lo: float, hi: float) -> list[float]:
    step = 0.1 if hi - lo <= 0.4 else 0.2
    first = math.ceil(round(lo / step, 6))
    return [round(k * step, 2) for k in range(first, math.floor(round(hi / step, 6)) + 1)]


def radar_figure(table: pd.DataFrame, criterion: str = "direction") -> go.Figure:
    spec = CRITERIA[criterion]
    labels = [CAT_LABEL[c] for c in table.index]
    values = list(table["bbc"]) + list(table["radio"])
    lo = math.floor((min(values) - 0.05) * 10) / 10 if min(values) < 0 else 0.0
    hi = min(spec["rng"][1], math.ceil((max(values) + 0.05) * 10) / 10)
    ticks = _nice_ticks(lo, hi)
    fig = go.Figure()
    for key, src in (("bbc", BBC), ("radio", RADIO)):
        r = list(table[key])
        fig.add_trace(go.Scatterpolar(
            r=r + r[:1], theta=labels + labels[:1], name=SOURCE_LABELS[src], fill="toself",
            fillcolor=_rgba(SOURCE_COLORS[src], 0.22), line=dict(width=3, color=SOURCE_COLORS[src]),
            marker=dict(size=8, color=SOURCE_COLORS[src]),
            hovertemplate=f"<b>%{{theta}}</b><br>{SHORT_LABEL[src]}: %{{r:{spec['fmt']}}}<extra></extra>"))
    fig.update_layout(
        polar=dict(bgcolor="rgba(0,0,0,0)", domain=dict(x=[0.1, 0.9], y=[0.1, 0.88]),
                   radialaxis=dict(range=[lo, hi], angle=45, tickangle=0, tickfont=dict(size=10), tickmode="array",
                                   tickvals=ticks, ticktext=[format(t, spec["fmt"]) for t in ticks]),
                   angularaxis=dict(direction="clockwise", rotation=90)),
        legend=dict(orientation="h", yanchor="top", y=-0.02, x=0.5, xanchor="center"))
    _tidy(fig, height=440, top=24)
    fig.update_layout(margin=dict(l=16, r=16, t=32, b=16))
    return fig


def dumbbell_figure(table: pd.DataFrame, criterion: str = "direction") -> go.Figure:
    """Дві точки на категорію (трохи розведені по вертикалі), відрізок між ними — розрив; вуса — 95% інтервал."""
    spec = CRITERIA[criterion]
    labels = [CAT_LABEL[c] for c in table.index]
    rows = list(range(len(labels)))
    dodge = 0.13
    fig = go.Figure()
    line_x: list = []
    line_y: list = []
    for i, (_, row) in zip(rows, table.iterrows()):
        line_x += [row["bbc"], row["radio"], None]
        line_y += [i - dodge, i + dodge, None]
    fig.add_trace(go.Scatter(x=line_x, y=line_y, mode="lines", hoverinfo="skip", showlegend=False,
                             line=dict(width=5, color="rgba(148,163,184,0.5)")))
    for key, src, shift in (("bbc", BBC, -dodge), ("radio", RADIO, dodge)):
        fig.add_trace(go.Scatter(
            x=table[key], y=[i + shift for i in rows], mode="markers", name=SOURCE_LABELS[src],
            marker=dict(size=15, color=SOURCE_COLORS[src], line=dict(width=2, color="white")),
            error_x=dict(type="data", array=table[f"{key}_ci"].fillna(0), thickness=1.4, width=4,
                         color=_rgba(SOURCE_COLORS[src], 0.6)),
            customdata=np.column_stack([labels, table[f"{key}_ci"], table[f"n_{key}"]]),
            hovertemplate=f"<b>%{{customdata[0]}}</b><br>{SHORT_LABEL[src]}: %{{x:{spec['fmt']}}} "
                          f"(±%{{customdata[1]:.2f}}, n=%{{customdata[2]}})<extra></extra>"))
    ticktext = [
        f"{label}<br><b>Δ {fmt_gap(row['gap'], spec['fmt'])}{'*' if _is_significant(row['gap'], row['gap_ci']) else ''}</b>"
        for label, (_, row) in zip(labels, table.iterrows())]
    lo = min((table["bbc"] - table["bbc_ci"].fillna(0)).min(), (table["radio"] - table["radio_ci"].fillna(0)).min())
    hi = max((table["bbc"] + table["bbc_ci"].fillna(0)).max(), (table["radio"] + table["radio_ci"].fillna(0)).max())
    pad = max(0.04, (hi - lo) * 0.1)
    if spec["zero_line"]:
        fig.add_vline(x=0, line_dash="dash", opacity=0.35)
    fig.update_xaxes(range=[lo - pad, hi + pad], tickformat=spec["fmt"].lstrip("+"), zeroline=False)
    fig.update_yaxes(range=[len(labels) - 0.5, -0.5], tickmode="array", tickvals=rows, ticktext=ticktext)
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return _tidy(fig, height=420, top=56)


# ---------------------------------------------------------------- теплова карта різниці

def _half_labels(lo: pd.Timestamp, hi: pd.Timestamp) -> list[str]:
    start = pd.Timestamp(lo.year, 1 if lo.month <= 6 else 7, 1)
    end = pd.Timestamp(hi.year, 1 if hi.month <= 6 else 7, 1)
    return [_bucket_label(t, "half") for t in pd.date_range(start, end, freq="6MS")]


def heatmap_grid(df: pd.DataFrame) -> dict:
    """Середні оцінки й кількість статей: категорія × піврічний період, окремо для кожного джерела."""
    periods = _half_labels(df["date"].min(), df["date"].max())
    cats = [c for c in CATEGORIES if c in set(df["metric_category"])]
    grid = {"periods": periods, "cats": cats}
    for key, src in (("bbc", BBC), ("radio", RADIO)):
        part = df[df["source"] == src]
        for name, agg in (("mean", "mean"), ("n", "count")):
            piv = part.pivot_table(index="metric_category", columns="period", values="score", aggfunc=agg)
            grid[f"{name}_{key}"] = piv.reindex(index=cats, columns=periods)
    grid["n_bbc"] = grid["n_bbc"].fillna(0).astype(int)
    grid["n_radio"] = grid["n_radio"].fillna(0).astype(int)
    grid["diff"] = grid["mean_radio"] - grid["mean_bbc"]
    return grid


def cell_articles(df: pd.DataFrame, category: str, period: str) -> pd.DataFrame:
    """Оцінки, з яких складено одну клітинку (категорія × піврічний період), по обох джерелах."""
    cols = [c for c in ("source", "article_id", "date", "title", "url", "score", "confidence", "justification")
            if c in df.columns]
    sel = df[(df["metric_category"] == category) & (df["period"] == period)].copy()
    sel["_order"] = sel["source"].map({s: i for i, s in enumerate(SOURCES)}).fillna(len(SOURCES))
    return sel.sort_values(["_order", "date", "article_id"])[cols].reset_index(drop=True)


def cell_from_point(point: dict) -> tuple[str, str] | None:
    cd = point.get("customdata")
    if isinstance(cd, (list, tuple)) and len(cd) >= 2 and cd[0] in CATEGORIES and isinstance(cd[1], str):
        return cd[0], cd[1]
    category = {label: key for key, label in CAT_LABEL.items()}.get(point.get("y"))
    period = point.get("x")
    return (category, period) if category and isinstance(period, str) else None


def cell_from_event(event) -> tuple[str, str] | None:
    try:
        points = event["selection"]["points"]
    except (KeyError, TypeError):
        return None
    return cell_from_point(points[0]) if points else None


def _cell_outline(fig: go.Figure, grid: dict, selected_cell) -> None:
    if not selected_cell:
        return
    category, period = selected_cell
    if category not in grid["cats"] or period not in grid["periods"]:
        return
    row, col = grid["cats"].index(category), grid["periods"].index(period)
    for width, color in ((6, "white"), (2.5, "#111827")):
        fig.add_shape(type="rect", xref="x", yref="y", x0=col - 0.5, x1=col + 0.5, y0=row - 0.5, y1=row + 0.5,
                      layer="above", fillcolor="rgba(0,0,0,0)", line=dict(width=width, color=color))


def heatmap_figure(df: pd.DataFrame, mode: str = HEAT_DIFF, selected_cell=None) -> go.Figure:
    grid = heatmap_grid(df)
    cats, periods = grid["cats"], grid["periods"]
    labels = [CAT_LABEL[c] for c in cats]
    z = {HEAT_DIFF: grid["diff"], HEAT_BBC: grid["mean_bbc"], HEAT_RADIO: grid["mean_radio"]}[mode]
    if mode == HEAT_DIFF:
        m = _symmetric_limit(list(z.to_numpy().ravel()), floor=0.3)
        scale = dict(colorscale=DIFF_SCALE, zmin=-m, zmax=m, zmid=0)
        colorbar = dict(tickvals=[-m, 0, m], ticktext=[f"BBC вище −{m:.1f}", "0", f"Свобода вище +{m:.1f}"])
    else:
        scale = dict(colorscale="RdYlGn", zmin=-1, zmax=1, zmid=0)
        colorbar = dict(title="Оцінка")
    text = [["" if pd.isna(v) else f"{v:+.2f}" for v in row] for row in z.to_numpy()]
    fig = go.Figure(go.Heatmap(
        z=z.to_numpy(), x=periods, y=labels, text=text, texttemplate="%{text}", xgap=2, ygap=2,
        colorbar=colorbar, hoverinfo="skip", **scale))
    # Прозорі маркери в центрах клітинок приймають кліки (також по порожніх клітинках).
    xs, ys, custom = [], [], []
    for cat, label in zip(cats, labels):
        for period in periods:
            a, b = grid["mean_bbc"].at[cat, period], grid["mean_radio"].at[cat, period]
            xs.append(period)
            ys.append(label)
            custom.append([cat, period, fmt_val(a, "+.2f"), fmt_val(b, "+.2f"),
                           "немає даних" if pd.isna(a) or pd.isna(b) else f"{b - a:+.2f}",
                           int(grid["n_bbc"].at[cat, period]), int(grid["n_radio"].at[cat, period])])
    fig.add_trace(go.Scatter(
        x=xs, y=ys, customdata=custom, mode="markers", showlegend=False,
        marker=dict(symbol="square", size=6, color="#111827", opacity=0),
        selected=dict(marker=dict(opacity=0, size=6, color="#111827")),
        unselected=dict(marker=dict(opacity=0, size=6, color="#111827")),
        hovertemplate="<b>%{y} · %{x}</b><br>BBC: %{customdata[2]} (статей: %{customdata[5]})"
                      "<br>Свобода: %{customdata[3]} (статей: %{customdata[6]})"
                      "<br>Різниця: <b>%{customdata[4]}</b><extra></extra>"))
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=periods)
    fig.update_yaxes(type="category", autorange="reversed")
    fig.update_layout(hoverdistance=-1, clickmode="event+select")
    _cell_outline(fig, grid, selected_cell)
    return _tidy(fig, height=320)


# ---------------------------------------------------------------- розподіли

SCORE_LEVELS = np.round(np.arange(-1.0, 1.0001, 0.1), 1)


def _level_counts(values: pd.Series) -> pd.Series:
    """Скільки оцінок припадає на кожен рівень від −1 до +1 з кроком 0,1 (оцінки дискретні)."""
    return values.round(1).value_counts().reindex(SCORE_LEVELS, fill_value=0)


def distribution_figure(df: pd.DataFrame, arts: pd.DataFrame, scope: str = SCOPE_CATEGORIES) -> go.Figure:
    """Дзеркальні гістограми: ліворуч BBC, праворуч Радіо Свобода; пунктир — середня оцінка.

    Оцінки дискретні (кратні 0,1) і з піками на нулі, тому гістограма чесніша за згладжені «скрипки».
    """
    if scope == SCOPE_OVERALL:
        groups = [(SCOPE_OVERALL, arts, "overall_sentiment")]
    else:
        groups = [(CAT_LABEL[c], df[df["metric_category"] == c], "score")
                  for c in CATEGORIES if c in set(df["metric_category"])]
    data = []
    for title, part, column in groups:
        per_source = {}
        for src in SOURCES:
            values = part.loc[part["source"] == src, column].dropna()
            counts = _level_counts(values)
            per_source[src] = (counts, counts / max(len(values), 1) * 100, float(values.mean()))
        data.append((title, per_source))

    fig = make_subplots(rows=1, cols=len(groups), shared_yaxes=True, horizontal_spacing=0.03,
                        subplot_titles=[g[0] for g in groups])
    for col, (title, per_source) in enumerate(data, start=1):
        # власна шкала кожної панелі: інакше пік на нулі в одній категорії «стискає» решту
        top = max(float(sh.max()) for _, sh, _ in per_source.values())
        limit = max(5, math.ceil(top / 5) * 5)
        fig.update_xaxes(range=[-limit, limit], tickmode="array", tickvals=[-limit, 0, limit],
                         ticktext=[f"{limit}%", "0", f"{limit}%"], zeroline=True, row=1, col=col)
        for src, sign in ((BBC, -1), (RADIO, 1)):
            counts, shares, mean = per_source[src]
            color = SOURCE_COLORS[src]
            fig.add_trace(go.Bar(
                orientation="h", y=SCORE_LEVELS, x=sign * shares, width=0.085, marker_color=_rgba(color, 0.85),
                name=SOURCE_LABELS[src], legendgroup=src, showlegend=col == 1,
                customdata=np.column_stack([counts, shares]),
                hovertemplate=f"<b>{title}</b> · оцінка %{{y:+.1f}}<br>{SHORT_LABEL[src]}: "
                              f"%{{customdata[1]:.0f}}% (%{{customdata[0]}} оцінок)<extra></extra>"), row=1, col=col)
            if np.isfinite(mean):
                fig.add_trace(go.Scatter(
                    x=[0, sign * limit / 2, sign * limit * 0.97], y=[mean] * 3, mode="lines", showlegend=False,
                    line=dict(color=color, width=2.5, dash="dot"), legendgroup=src,
                    hovertemplate=f"{SHORT_LABEL[src]}: середнє <b>%{{y:+.2f}}</b><extra></extra>"), row=1, col=col)
    fig.add_hline(y=0, opacity=0.3, line_width=1)
    fig.update_yaxes(range=[-1.05, 1.05], zeroline=False)
    fig.update_yaxes(title_text="Оцінка", row=1, col=1)
    fig.update_layout(barmode="relative", bargap=0, legend=dict(orientation="h", yanchor="bottom", y=1.1, x=0))
    return _tidy(fig, height=430, top=84)


def stance_shares(arts: pd.DataFrame) -> pd.DataFrame:
    """Частка статей за класом тону для кожної пари (джерело, рік); сума по парі — 100."""
    tone = arts["overall_sentiment"]
    cls = np.select([tone <= -STANCE_THRESHOLD, tone >= STANCE_THRESHOLD], ["escalation", "compromise"], "neutral")
    counts = (arts.assign(year=arts["date"].dt.year.astype(str), stance=cls)
              .groupby(["source", "year", "stance"]).size().unstack("stance")
              .reindex(columns=[k for k, _, _ in STANCES]).fillna(0))
    return counts.div(counts.sum(axis=1), axis=0) * 100


def stance_figure(arts: pd.DataFrame) -> go.Figure:
    shares = stance_shares(arts)
    totals = arts.assign(year=arts["date"].dt.year.astype(str)).groupby(["source", "year"]).size()
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.04,
                        subplot_titles=[SOURCE_LABELS[s] for s in SOURCES])
    for col, src in enumerate(SOURCES, start=1):
        part = shares.loc[src] if src in shares.index.get_level_values(0) else shares.iloc[0:0]
        for key, name, color in STANCES:
            vals = part[key]
            fig.add_trace(go.Bar(
                x=list(part.index), y=vals, name=name, marker_color=color, legendgroup=key, showlegend=col == 1,
                text=[f"{v:.0f}%" if v >= 9 else "" for v in vals], textposition="inside",
                customdata=[int(totals[(src, y)]) for y in part.index],
                hovertemplate=f"<b>%{{x}}</b> · {name}: %{{y:.0f}}% (з %{{customdata}} статей)<extra></extra>"),
                row=1, col=col)
    fig.update_layout(barmode="stack", legend=dict(orientation="h", yanchor="bottom", y=1.1, x=0), bargap=0.15)
    fig.update_yaxes(range=[0, 100], ticksuffix="%")
    fig.update_xaxes(type="category")
    return _tidy(fig, height=380, top=84)


def compromise_year_figure(arts: pd.DataFrame) -> go.Figure:
    y = arts.assign(year=arts["date"].dt.year.astype(str))
    fig = go.Figure()
    for src in SOURCES:
        part = y[y["source"] == src].groupby("year")["is_compromise_oriented"].agg(["mean", "count"])
        color = SOURCE_COLORS[src]
        fig.add_trace(go.Bar(
            x=part.index, y=part["mean"] * 100, name=SOURCE_LABELS[src], marker_color=color,
            text=[f"{v:.0f}%" for v in part["mean"] * 100], textposition="outside", cliponaxis=False,
            customdata=part["count"],
            hovertemplate=f"<b>%{{x}}</b> · {SHORT_LABEL[src]}: %{{y:.0f}}% (з %{{customdata}} статей)<extra></extra>"))
    fig.update_layout(barmode="group", bargap=0.2, legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      yaxis=dict(range=[0, 112], ticksuffix="%", title="Орієнтовані на компроміс"),
                      xaxis=dict(type="category", title=None))
    return _tidy(fig, height=380, top=56)


# ---------------------------------------------------------------- підсумки агента й висновки

def summary_timeline_figure(summaries: pd.DataFrame, selected: str) -> go.Figure:
    """Середній тон із піврічних підсумків агента; вибране півріччя підсвічене."""
    periods = sorted(summaries["period"].unique())
    fig = go.Figure()
    for src in SOURCES:
        part = summaries[summaries["source"] == src].set_index("period").reindex(periods)
        fig.add_trace(go.Scatter(
            x=periods, y=part["avg_sentiment"], mode="lines+markers", name=SOURCE_LABELS[src],
            line=dict(width=3, color=SOURCE_COLORS[src]),
            marker=dict(size=[13 if p == selected else 7 for p in periods], color=SOURCE_COLORS[src],
                        line=dict(width=1.5, color="white")),
            hovertemplate=f"<b>{SHORT_LABEL[src]}</b> · %{{x}}: %{{y:+.2f}}<extra></extra>"))
    if selected in periods:
        i = periods.index(selected)
        fig.add_shape(type="rect", xref="x", yref="paper", x0=i - 0.5, x1=i + 0.5, y0=0, y1=1,
                      layer="below", line=dict(width=0), fillcolor="rgba(148,163,184,0.22)")
    limit = _symmetric_limit(list(summaries["avg_sentiment"]), floor=0.5)
    fig.add_hline(y=0, line_dash="dash", opacity=0.35)
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=periods)
    fig.update_yaxes(range=[-limit, limit], title="Тон", zeroline=False)
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return _tidy(fig, height=260, top=40)


def highlights(df: pd.DataFrame, arts: pd.DataFrame) -> list[str]:
    """Три короткі спостереження простою мовою; завжди про тон добірок, а не про факти."""
    out = []
    cats = category_table(df, "direction")
    if not cats.empty:
        cat = cats["gap"].abs().idxmax()
        row = cats.loc[cat]
        lead = "Радіо Свобода" if row["gap"] > 0 else "BBC"
        note = "" if _is_significant(row["gap"], row["gap_ci"]) else ", але в межах похибки"
        out.append(f"Найбільше джерела розходяться в категорії **{CAT_LABEL[cat]}**: у {lead} тон вищий на "
                   f"{abs(row['gap']):.2f} (BBC {row['bbc']:+.2f}, Свобода {row['radio']:+.2f}){note}.")
    periods = period_table(arts, "half")
    periods = periods[(periods["n_bbc"] >= MIN_ARTICLES_FOR_GAP) & (periods["n_radio"] >= MIN_ARTICLES_FOR_GAP)]
    if not periods.empty:
        row = periods.loc[periods["gap"].abs().idxmax()]
        lead = "Радіо Свобода" if row["gap"] > 0 else "BBC"
        out.append(f"Найбільша розбіжність у часі — **{row['label']}**: BBC {row['bbc']:+.2f}, "
                   f"Свобода {row['radio']:+.2f}; вищий тон у {lead} (Δ {abs(row['gap']):.2f}).")
    corr = dynamics_correlation(arts)
    if corr:
        r, n = corr
        out.append(f"Узгодженість динаміки тону — **{correlation_word(r)}** (r = {r:+.2f} за {n} піврічними "
                   f"періодами): {'джерела здебільшого рухаються в один бік' if r >= 0.4 else 'спільного напрямку майже немає'}.")
    return out
