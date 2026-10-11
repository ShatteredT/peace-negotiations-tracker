"""Побудова графіків (Plotly): чисті функції без Streamlit, щоб їх можна було перевіряти й рендерити окремо."""
import html

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

CATEGORIES = ("diplomacy", "security", "territorial", "humanitarian")
CAT_LABEL = {
    "diplomacy": "Дипломатія",
    "security": "Безпека",
    "territorial": "Території",
    "humanitarian": "Гуманітарне",
}
CAT_COLOR = {
    "diplomacy": "#3B82F6",
    "security": "#F59E0B",
    "territorial": "#8B5CF6",
    "humanitarian": "#14B8A6",
}
OVERALL_COLOR = "#94A3B8"
VIEW_OVERALL = "Загальний тон"
VIEW_CATEGORIES = "За категоріями"


def _tidy(fig: go.Figure, height: int, top: int = 8) -> go.Figure:
    """Спільні поля: підписи й назви осей розміщуються автоматично й не накладаються."""
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=top, b=8))
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    return fig


def _smoother(window: int):
    return lambda s: s.rolling(window, min_periods=1).mean()


def _add_bands(fig: go.Figure) -> None:
    fig.add_hrect(y0=0.5, y1=1, fillcolor="#22C55E", opacity=0.07, line_width=0)
    fig.add_hrect(y0=-1, y1=-0.5, fillcolor="#EF4444", opacity=0.07, line_width=0)
    fig.add_hline(y=0, line_dash="dash", opacity=0.35)


def _add_overall(fig, months, overall_raw, base, window) -> None:
    fig.add_trace(go.Scatter(
        x=months, y=overall_raw, mode="markers", name="Середнє за місяць",
        marker=dict(size=6, opacity=0.35, color=OVERALL_COLOR),
        hovertemplate="%{y:+.2f}<extra>за місяць</extra>"))
    fig.add_trace(go.Scatter(
        x=months, y=base, mode="lines", name=f"Тренд ({window} міс.)", connectgaps=True,
        line=dict(width=3.5, color=OVERALL_COLOR, shape="spline"),
        hovertemplate="%{y:+.2f}<extra>тренд</extra>"))


def _add_categories(fig, months, smoothed) -> None:
    for cat in smoothed.columns:
        label = CAT_LABEL.get(cat, cat)
        fig.add_trace(go.Scatter(
            x=months, y=smoothed[cat], mode="lines+markers", name=label, connectgaps=True,
            line=dict(width=2.5, color=CAT_COLOR.get(cat), shape="spline"), marker=dict(size=4),
            hovertemplate="%{y:+.2f}<extra>" + label + "</extra>"))


def _add_notes(fig, notes, months, lookup) -> None:
    n = notes.copy()
    n["event_date"] = pd.to_datetime(n["event_date"])
    n = n[(n["event_date"] >= months.min()) & (n["event_date"] < months.max() + pd.offsets.MonthBegin(1))]
    if n.empty:
        return
    n["y"] = [
        (v if (v := lookup(d.to_period("M").to_timestamp(), c)) is not None and pd.notna(v) else 0.0)
        for d, c in zip(n["event_date"], n["metric_category"])
    ]
    fig.add_trace(go.Scatter(
        x=n["event_date"], y=n["y"], mode="markers", name="Експертні замітки",
        marker=dict(symbol="diamond", size=13, color="#EC4899", line=dict(width=1.5, color="white")),
        customdata=[[html.escape(a), CAT_LABEL.get(c, c), html.escape(t[:180])]
                    for a, c, t in zip(n["author_id"], n["metric_category"], n["content"])],
        hovertemplate="<b>%{customdata[0]}</b> · %{customdata[1]}<br>%{customdata[2]}"
                      "<extra>%{x|%d.%m.%Y}</extra>"))


ANNOTATION_STYLE = {
    "peak": dict(name="Анотація: пік", symbol="triangle-up", color="#22C55E"),
    "drop": dict(name="Анотація: спад", symbol="triangle-down", color="#EF4444"),
}


def annotation_traces(ann: pd.DataFrame) -> list:
    """Два елементи легенди (пік і спад), щоб колір і форма маркера були пояснені."""
    traces = []
    for kind, style in ANNOTATION_STYLE.items():
        part = ann[ann["kind"] == kind]
        if part.empty:
            continue
        traces.append(go.Scatter(
            x=part["date"], y=part["overall_sentiment"], mode="markers", name=style["name"],
            marker=dict(symbol=style["symbol"], size=10, opacity=0.9, color=style["color"],
                        line=dict(width=1, color="white")),
            customdata=[[html.escape(t), html.escape(title[:90])] for t, title in zip(part["text"], part["title"])],
            hovertemplate="<b>%{customdata[1]}</b><br>%{customdata[0]}<extra>%{x|%d.%m.%Y}</extra>"))
    return traces


def _trend_layout(fig: go.Figure) -> None:
    # кнопки періоду зверху зліва, легенда під графіком: так вони не перекриваються за будь-якої ширини
    _tidy(fig, height=480, top=44)
    fig.update_layout(
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.08, x=0),
        yaxis=dict(range=[-1.05, 1.05], title="Тон: −1 ескалація, +1 компроміс", zeroline=False),
        xaxis=dict(rangeselector=dict(x=0, y=1.0, yanchor="bottom", buttons=[
            dict(count=6, label="6 міс.", step="month", stepmode="backward"),
            dict(count=1, label="1 рік", step="year", stepmode="backward"),
            dict(step="all", label="Усе"),
        ])),
    )


def trend_figure(df: pd.DataFrame, arts: pd.DataFrame, notes: pd.DataFrame, annotations: pd.DataFrame,
                 view: str = VIEW_OVERALL, window: int = 3, show_notes: bool = True,
                 show_annotations: bool = True) -> go.Figure:
    months = pd.date_range(arts["month"].min(), arts["month"].max(), freq="MS")
    smooth = _smoother(window)
    fig = go.Figure()
    _add_bands(fig)
    if view == VIEW_OVERALL:
        overall_raw = arts.groupby("month")["overall_sentiment"].mean().reindex(months)
        base = smooth(overall_raw)
        _add_overall(fig, months, overall_raw, base, window)
        lookup = lambda month, cat: base.get(month)  # noqa: E731
    else:
        cat_raw = df.groupby(["month", "metric_category"])["score"].mean().unstack().reindex(months)
        cat_raw = cat_raw[[c for c in CATEGORIES if c in cat_raw.columns]]
        smoothed = cat_raw.apply(smooth)
        _add_categories(fig, months, smoothed)
        lookup = lambda month, cat: (smoothed[cat].get(month) if cat in smoothed else None)  # noqa: E731
    if show_notes and not notes.empty:
        _add_notes(fig, notes, months, lookup)
    if show_annotations and view == VIEW_OVERALL and not annotations.empty:
        fig.add_traces(annotation_traces(annotations))
    _trend_layout(fig)
    return fig


def heatmap_figure(df: pd.DataFrame) -> go.Figure:
    piv = df.groupby(["metric_category", "month"])["score"].mean().unstack("month")
    piv = piv.reindex([c for c in CATEGORIES if c in piv.index])
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=piv.columns, y=[CAT_LABEL[c] for c in piv.index],
        colorscale="RdYlGn", zmid=0, zmin=-1, zmax=1, xgap=1, ygap=1,
        colorbar=dict(title="Оцінка"), hovertemplate="%{y}<br>%{x|%m.%Y}: %{z:+.2f}<extra></extra>"))
    fig.update_layout(yaxis=dict(autorange="reversed"), xaxis=dict(tickformat="%m.%Y"))
    return _tidy(fig, height=300)


def year_share_figure(arts: pd.DataFrame) -> go.Figure:
    y = arts.assign(year=arts["date"].dt.year.astype(str))
    share = y.groupby("year")["is_compromise_oriented"].mean().mul(100).reset_index()
    fig = px.bar(share, x="year", y="is_compromise_oriented", text_auto=".0f",
                 labels={"year": "Рік", "is_compromise_oriented": "% компромісних"},
                 title="Частка статей, орієнтованих на компроміс")
    fig.update_traces(marker_color="#3B82F6")
    fig.update_layout(title_x=0, xaxis_title=None)
    return _tidy(fig, height=360, top=48)


def year_violin_figure(arts: pd.DataFrame) -> go.Figure:
    y = arts.assign(year=arts["date"].dt.year.astype(str))
    fig = px.violin(y, x="year", y="overall_sentiment", box=True, points="all",
                    labels={"year": "Рік", "overall_sentiment": "Загальний тон"},
                    title="Розподіл загального тону")
    fig.update_traces(marker_color="#8B5CF6", marker_size=4)
    fig.update_layout(title_x=0, xaxis_title=None, yaxis_range=[-1.05, 1.05])
    return _tidy(fig, height=360, top=48)


def period_tone_figure(table: pd.DataFrame) -> go.Figure:
    fig = px.bar(table, x="Період", y="Середній тон", color=table["Середній тон"] >= 0,
                 color_discrete_map={True: "#22C55E", False: "#EF4444"}, text_auto=".2f",
                 category_orders={"Період": sorted(table["Період"].unique())})
    fig.update_layout(showlegend=False, yaxis_range=[-1, 1], xaxis_title=None)
    return _tidy(fig, height=320)
