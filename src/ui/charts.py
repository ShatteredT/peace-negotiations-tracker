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
SOURCE_COLORS = {"bbc": "#2563EB", "radio_svoboda": "#F97316"}
SOURCE_LABELS = {"bbc": "BBC", "radio_svoboda": "Радіо Свобода"}
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


def _add_overall(fig, months, overall_raw, base, window, source=None) -> None:
    label = SOURCE_LABELS.get(source, source) if source else None
    raw_name = f"{label} · середнє за місяць" if label else "Середнє за місяць"
    trend_name = f"{label} · тренд ({window} міс.)" if label else f"Тренд ({window} міс.)"
    color = SOURCE_COLORS.get(source, OVERALL_COLOR)
    fig.add_trace(go.Scatter(
        x=months, y=overall_raw, mode="markers", name=raw_name,
        marker=dict(size=6, opacity=0.35, color=color),
        hovertemplate="%{y:+.2f}<extra>за місяць</extra>"))
    fig.add_trace(go.Scatter(
        x=months, y=base, mode="lines", name=trend_name, connectgaps=source is None,
        line=dict(width=3.5, color=color, shape="spline"),
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
        (
            v if (v := lookup(
                d.to_period("M").to_timestamp(), c, n.iloc[i].get("source")
            )) is not None and pd.notna(v) else 0.0
        )
        for i, (d, c) in enumerate(zip(n["event_date"], n["metric_category"]))
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
        source_bases = {}
        if "source" in arts.columns and arts["source"].nunique() > 1:
            for source, source_arts in arts.groupby("source", sort=True):
                overall_raw = source_arts.groupby("month")["overall_sentiment"].mean().reindex(months)
                base = smooth(overall_raw)
                source_bases[source] = base
                _add_overall(fig, months, overall_raw, base, window, source)
            lookup = lambda month, cat, source=None: (  # noqa: E731
                source_bases[source].get(month) if source in source_bases
                else pd.concat(source_bases.values(), axis=1).mean(axis=1).get(month)
            )
        else:
            overall_raw = arts.groupby("month")["overall_sentiment"].mean().reindex(months)
            base = smooth(overall_raw)
            _add_overall(fig, months, overall_raw, base, window)
            lookup = lambda month, cat, source=None: base.get(month)  # noqa: E731
    else:
        cat_raw = df.groupby(["month", "metric_category"])["score"].mean().unstack().reindex(months)
        cat_raw = cat_raw[[c for c in CATEGORIES if c in cat_raw.columns]]
        smoothed = cat_raw.apply(smooth)
        _add_categories(fig, months, smoothed)
        lookup = lambda month, cat, source=None: (  # noqa: E731
            smoothed[cat].get(month) if cat in smoothed else None
        )
    if show_notes and not notes.empty:
        _add_notes(fig, notes, months, lookup)
    if show_annotations and view == VIEW_OVERALL and not annotations.empty:
        fig.add_traces(annotation_traces(annotations))
    _trend_layout(fig)
    return fig


def _month_start(value) -> pd.Timestamp | None:
    """Перший день місяця для Timestamp / datetime / рядка («2023-03-01», «2023-03-01 00:00:00»)."""
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.to_period("M").to_timestamp()


CELL_COLUMNS = ["article_id", "date", "source", "title", "url", "score", "confidence", "justification"]


def articles_for_cell(df: pd.DataFrame, category: str, month) -> pd.DataFrame:
    """Оцінки, з яких складено одну клітинку теплової карти (категорія × місяць).

    Це ті самі рядки df, які heatmap_figure усереднює в колір клітинки, тому середнє `score`
    результату дорівнює значенню клітинки. Не залежить від Streamlit. Порожня клітинка дає порожній
    DataFrame (із тими самими колонками).
    """
    cols = [c for c in CELL_COLUMNS if c in df.columns]
    start = _month_start(month)
    if start is None:
        return df.iloc[0:0][cols].reset_index(drop=True)
    sel = df[(df["metric_category"] == category) & (df["month"] == start)]
    order = [c for c in ("date", "article_id") if c in sel.columns]
    return sel.sort_values(order)[cols].reset_index(drop=True)


def cell_from_point(point: dict) -> tuple[str, pd.Timestamp] | None:
    """(категорія, місяць) клітинки за точкою події кліку; None, якщо клітинку визначити не вдалося.

    Основний шлях — `customdata` [категорія, місяць], який кладе heatmap_figure. Запасний — підписи
    осей клітинки (y — українська назва категорії, x — дата місяця).
    """
    cd = point.get("customdata")
    if isinstance(cd, (list, tuple)) and len(cd) >= 2 and cd[0] in CATEGORIES:
        month = _month_start(cd[1])
        if month is not None:
            return cd[0], month
    category = {label: key for key, label in CAT_LABEL.items()}.get(point.get("y"))
    month = _month_start(point.get("x")) if point.get("x") is not None else None
    return (category, month) if category and month is not None else None


def cell_from_event(event) -> tuple[str, pd.Timestamp] | None:
    """Клітинка з результату st.plotly_chart(..., on_select=...); None, якщо нічого не вибрано."""
    try:
        points = event["selection"]["points"]
    except (KeyError, TypeError):
        return None
    return cell_from_point(points[0]) if points else None


def _add_pressed_cell(fig: go.Figure, piv: pd.DataFrame, selected_cell) -> None:
    """Накладає втиснуту тінь на всю площу вибраної клітинки, а не на центральний маркер."""
    if not selected_cell or piv.empty or len(piv.columns) == 0:
        return
    category, month_value = selected_cell
    if category not in piv.index:
        return
    month = _month_start(month_value)
    if month is None or month not in piv.columns:
        return

    row = list(piv.index).index(category)
    col = list(piv.columns).index(month)
    centers = [pd.Timestamp(v) for v in piv.columns]
    if len(centers) == 1:
        left, right = centers[0] - pd.Timedelta(days=15), centers[0] + pd.Timedelta(days=15)
    else:
        left = centers[col] - (centers[1] - centers[0]) / 2 if col == 0 else centers[col - 1] + (centers[col] - centers[col - 1]) / 2
        right = centers[col] + (centers[-1] - centers[-2]) / 2 if col == len(centers) - 1 else centers[col] + (centers[col + 1] - centers[col]) / 2

    x0, x1 = pd.Timestamp(left).to_pydatetime(), pd.Timestamp(right).to_pydatetime()
    y0, y1 = row - 0.5, row + 0.5
    # Базове затемнення займає всю клітинку; тонкі краї створюють саме ефект втиснення.
    fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                  xref="x", yref="y", layer="above",
                  line=dict(width=0), fillcolor="rgba(15, 23, 42, 0.16)")
    dx = (right - left) * 0.025
    dy = 0.045
    # Верхній/лівий краї темніші, нижній/правий — світліші; це не рамка, а внутрішній рельєф.
    for xa, xb, ya, yb, color in (
        (x0, (left + dx).to_pydatetime(), y0, y1, "rgba(15, 23, 42, 0.28)"),
        ((right - dx).to_pydatetime(), x1, y0, y1, "rgba(255, 255, 255, 0.30)"),
        (x0, x1, y0, y0 + dy, "rgba(15, 23, 42, 0.24)"),
        (x0, x1, y1 - dy, y1, "rgba(255, 255, 255, 0.32)"),
    ):
        fig.add_shape(type="rect", x0=xa, x1=xb, y0=ya, y1=yb,
                      xref="x", yref="y", layer="above", line=dict(width=0), fillcolor=color)


def heatmap_figure(df: pd.DataFrame, selected_cell=None) -> go.Figure:
    piv = df.groupby(["metric_category", "month"])["score"].mean().unstack("month")
    piv = piv.reindex([c for c in CATEGORIES if c in piv.index])
    # customdata[i][j] = [категорія, місяць] клітинки: за ним клік повертає, яку клітинку вибрано
    customdata = [[[cat, month.strftime("%Y-%m-%d")] for month in piv.columns] for cat in piv.index]
    labels = [CAT_LABEL[c] for c in piv.index]
    fig = go.Figure(go.Heatmap(
        z=piv.values, x=piv.columns, y=labels, customdata=customdata,
        colorscale="RdYlGn", zmid=0, zmin=-1, zmax=1, xgap=1, ygap=1,
        colorbar=dict(title="Оцінка"), hoverinfo="skip"))
    # Прозорі центри приймають кліки, включно з порожніми клітинками. Вони НЕ малюють виділення.
    values = [v for row in piv.values for v in row]
    fig.add_trace(go.Scatter(
        x=[m for _ in labels for m in piv.columns], y=[lab for lab in labels for _ in piv.columns],
        customdata=[[*cell, "немає даних" if pd.isna(v) else f"{v:+.2f}"]
                    for cell, v in zip((c for row in customdata for c in row), values)],
        mode="markers", showlegend=False,
        hovertemplate="%{y}<br>%{x|%m.%Y}: %{customdata[2]}<extra></extra>",
        marker=dict(symbol="square", size=6, color="#111827", opacity=0),
        selected=dict(marker=dict(opacity=0, size=6, color="#111827")),
        unselected=dict(marker=dict(opacity=0, size=6, color="#111827")),
    ))
    xaxis = dict(tickformat="%m.%Y")
    if len(piv.columns) > 1:
        first, last = piv.columns[0], piv.columns[-1]
        lo = first - (piv.columns[1] - first) / 2
        hi = last + (last - piv.columns[-2]) / 2
        xaxis["range"] = [lo.strftime("%Y-%m-%d %H:%M:%S"), hi.strftime("%Y-%m-%d %H:%M:%S")]
    fig.update_layout(yaxis=dict(range=[len(labels) - 0.5, -0.5]), xaxis=xaxis,
                      hoverdistance=-1, clickmode="event+select")
    _add_pressed_cell(fig, piv, selected_cell)
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
