from __future__ import annotations

import html
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Дозволяє запускати і `streamlit run src/ui/app.py`, і `python -m streamlit run ...` з кореня проєкту
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.errors import (  # noqa: E402
    AccessDenied, ConflictError, NoteError, NotFoundError, TechnicalFailure, ValidationError,
)
from src.ops import observed, record, utc_now, utc_now_iso  # noqa: E402

# Шлях до БД можна перевизначити змінною середовища (використовують тести та навчальний інцидент)
DB_PATH = Path(os.getenv("PEACETALK_DB", str(Path(__file__).resolve().parents[2] / "data" / "peacetalk.db")))
WAR_START = date(2022, 2, 24)

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

STATUS_BADGE = {
    "PUBLISHED": ":green-badge[Опубліковано]",
    "ARCHIVED": ":gray-badge[:material/archive: В архіві]",
}
IMPORTANCE_BADGE = {
    "OFFICIALSTATEMENT": ":violet-badge[:material/campaign: Офіційна заява]",
    "COMMENT": ":blue-badge[:material/chat: Коментар]",
}


# DB
@st.cache_resource
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data(ttl=30, show_spinner=False)
def load_assessments(start_date: str, end_date: str, categories: tuple,
                     sources: tuple, min_confidence: float) -> pd.DataFrame:
    query = """
        SELECT a.id AS article_id, a.source, a.title, a.date, a.url,
               ar.metric_category, ar.score, ar.justification, ar.confidence,
               ar.overall_sentiment, ar.is_compromise_oriented
        FROM assessment_records ar
        JOIN news_articles a ON ar.article_id = a.id
        WHERE (ar.confidence IS NULL OR ar.confidence >= ?)
          AND a.date >= ? AND a.date <= ?
    """
    params: list = [min_confidence, start_date, end_date]
    if categories:
        query += f" AND ar.metric_category IN ({','.join('?' * len(categories))})"
        params += list(categories)
    if sources:
        query += f" AND a.source IN ({','.join('?' * len(sources))})"
        params += list(sources)
    df = pd.read_sql_query(query + " ORDER BY a.date", get_connection(), params=params)
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    df["is_compromise_oriented"] = df["is_compromise_oriented"].astype(bool)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def load_expert_notes(start_date: str | None = None, end_date: str | None = None,
                      categories: tuple = (), statuses: tuple = ()) -> pd.DataFrame:
    query = """
        SELECT id, event_date, metric_category, content, author_id, importance_type, status,
               created_at, updated_at
        FROM expert_notes WHERE 1=1
    """
    params: list = []
    if start_date:
        query += " AND event_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND event_date <= ?"
        params.append(end_date)
    if categories:
        query += f" AND metric_category IN ({','.join('?' * len(categories))})"
        params += list(categories)
    if statuses:
        query += f" AND status IN ({','.join('?' * len(statuses))})"
        params += list(statuses)
    return pd.read_sql_query(query + " ORDER BY event_date DESC, id DESC", get_connection(), params=params)


@st.cache_data(ttl=30, show_spinner=False)
def get_filter_options() -> dict:
    conn = get_connection()
    return {
        "sources": [r[0] for r in conn.execute("SELECT DISTINCT source FROM news_articles ORDER BY source")],
        "categories": [r[0] for r in conn.execute("SELECT DISTINCT metric_category FROM assessment_records")],
        # Період фільтра: від початку війни до сьогодні (UTC). Не за датами статей: інакше
        # замітки, створені після останньої статті, випадали б зі списку заміток.
        "date_range": (WAR_START, utc_now().date()),
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_article_text(article_id: str) -> str:
    row = get_connection().execute("SELECT text FROM news_articles WHERE id = ?", (article_id,)).fetchone()
    return (row[0] if row and row[0] else "")


ACTIVE = "PUBLISHED"
ARCHIVED = "ARCHIVED"
IMPORTANCE_TYPES = ("COMMENT", "OFFICIALSTATEMENT")
MAX_CONTENT_LEN = 2000


@contextmanager
def _write_conn():
    """Окреме з'єднання на кожну операцію: коректно при паралельних запитах.
    mode=rw не створює порожню БД, якщо файл зник """
    conn = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=rw", uri=True, timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fetch_note(conn: sqlite3.Connection, note_id: int) -> dict:
    row = conn.execute("SELECT id, author_id, status FROM expert_notes WHERE id = ?", (int(note_id),)).fetchone()
    if row is None:
        raise NotFoundError("Замітку не знайдено")
    return {"id": row[0], "author_id": row[1], "status": row[2]}


def _require_owner_or_admin(note: dict, user_id: str, user_role: str) -> None:
    if user_role != "Admin" and note["author_id"] != user_id:
        raise AccessDenied("Змінювати чи архівувати можна лише власні замітки")


def _clean_content(content: str) -> str:
    content = (content or "").strip()
    if not content:
        raise ValidationError("Зміст замітки не може бути порожнім")
    if len(content) > MAX_CONTENT_LEN:
        raise ValidationError(f"Зміст не може перевищувати {MAX_CONTENT_LEN} символів")
    return content


def _validate_new_note(event_date: str, metric_category: str, importance_type: str) -> None:
    if metric_category not in CATEGORIES:
        raise ValidationError("Невідома категорія")
    if importance_type not in IMPORTANCE_TYPES:
        raise ValidationError("Невідомий тип замітки")
    try:
        parsed = date.fromisoformat(event_date)
    except (TypeError, ValueError):
        raise ValidationError("Дата має бути у форматі РРРР-ММ-ДД") from None
    if not (WAR_START <= parsed <= utc_now().date()):
        raise ValidationError(f"Дата події має бути між {WAR_START:%d.%m.%Y} і сьогодні (UTC)")


@observed("create_note")
def create_note(user_id: str, event_date: str, metric_category: str,
                content: str, importance_type: str = "COMMENT") -> int:
    """Створює активну замітку. Повертає її id. Дубль активної замітки -> ConflictError."""
    content = _clean_content(content)
    _validate_new_note(event_date, metric_category, importance_type)
    now = utc_now_iso()
    try:
        with _write_conn() as conn:
            cur = conn.execute(
                """INSERT INTO expert_notes
                   (event_date, metric_category, content, author_id, importance_type, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_date, metric_category, content, user_id, importance_type, ACTIVE, now, now),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ConflictError("У вас вже є активна замітка на цю дату та категорію") from None


@observed("update_note")
def update_note(note_id: int, user_id: str, user_role: str, content: str) -> None:
    """Змінює текст активної замітки (автор або Admin)."""
    content = _clean_content(content)
    with _write_conn() as conn:
        note = _fetch_note(conn, note_id)
        _require_owner_or_admin(note, user_id, user_role)
        if note["status"] != ACTIVE:
            raise ConflictError("Архівну замітку не можна редагувати")
        conn.execute("UPDATE expert_notes SET content = ?, updated_at = ? WHERE id = ?",
                     (content, utc_now_iso(), int(note_id)))


@observed("archive_note")
def archive_note(note_id: int, user_id: str, user_role: str) -> None:
    """Знімає замітку з графіка (status = ARCHIVED) і звільняє слот. Автор або Admin."""
    with _write_conn() as conn:
        note = _fetch_note(conn, note_id)
        _require_owner_or_admin(note, user_id, user_role)
        cur = conn.execute("UPDATE expert_notes SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                           (ARCHIVED, utc_now_iso(), int(note_id), ACTIVE))
        if cur.rowcount == 0:
            raise ConflictError("Замітка вже в архіві")


#  Helpers
def flash(msg: str, icon: str = "✅") -> None:
    """Повідомлення, що переживає st.rerun() (toast одразу перед rerun зникає)."""
    st.session_state["_flash"] = (msg, icon)


def show_flash() -> None:
    if "_flash" in st.session_state:
        msg, icon = st.session_state.pop("_flash")
        st.toast(msg, icon=icon)


def score_color(score: float) -> str:
    if score >= 0.3:
        return "green"
    if score <= -0.3:
        return "red"
    return "gray"


def article_level(df: pd.DataFrame) -> pd.DataFrame:
    """Одна стаття = один рядок (overall_sentiment однаковий для всіх її категорій)."""
    return df.drop_duplicates("article_id")[
        ["article_id", "date", "month", "source", "title", "url", "overall_sentiment", "is_compromise_oriented"]
    ]


def ctx() -> dict:
    return st.session_state["_ctx"]


def current_filtered_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    f = ctx()["filters"]
    df = load_assessments(f["start"], f["end"], f["categories"], f["sources"], f["min_confidence"])
    return df, (article_level(df) if not df.empty else df)


def empty_state() -> None:
    st.info("За обраними фільтрами даних немає. Змініть період, категорії чи джерела в бічній панелі.",
            icon=":material/filter_alt_off:")


#  Sidebar: користувач + фільтри
ACCOUNTS = {
    "analyst_1": {"id": "analyst_1", "name": "Дослідник А", "role": "Analyst"},
    "analyst_2": {"id": "analyst_2", "name": "Дослідник Б", "role": "Analyst"},
    "admin_1": {"id": "admin_1", "name": "Адміністратор", "role": "Admin"},
}

def sidebar_user() -> dict:
    with st.sidebar:
        st.markdown("##### :material/person: Обліковий запис")
        key = st.selectbox(
            "Обліковий запис", list(ACCOUNTS), key="account_select", label_visibility="collapsed",
            format_func=lambda k: f"{ACCOUNTS[k]['name']} · {ACCOUNTS[k]['role']}",
        )
        user = ACCOUNTS[key]
        st.caption(f"id: `{user['id']}`")
    st.session_state.user = user
    return user


def sidebar_filters(options: dict) -> dict:
    dmin, dmax = options["date_range"]
    with st.sidebar:
        st.divider()
        st.markdown("##### :material/tune: Фільтри")

        years = list(range(dmin.year, dmax.year + 1))
        preset = st.segmented_control(
            "Період", ["Усе", *map(str, years), "Власний"], default="Усе", key="f_preset"
        ) or "Усе"
        if preset == "Усе":
            start, end = dmin, dmax
        elif preset == "Власний":
            picked = st.date_input("Діапазон дат", value=(dmin, dmax), min_value=dmin, max_value=dmax,
                                   format="DD.MM.YYYY", key="f_range")
            start, end = (picked if isinstance(picked, tuple) and len(picked) == 2 else (dmin, dmax))
        else:
            y = int(preset)
            start, end = date(y, 1, 1), date(y, 12, 31)

        cats_all = [c for c in CATEGORIES if c in options["categories"]] or list(CATEGORIES)
        cats = st.pills("Категорії", cats_all, selection_mode="multi", default=cats_all,
                        format_func=lambda c: CAT_LABEL.get(c, c), key="f_cats") or cats_all

        srcs_all = options["sources"]
        srcs = st.pills("Джерела", srcs_all, selection_mode="multi", default=srcs_all,
                        format_func=str.upper, key="f_srcs") or srcs_all

        min_conf = st.slider(
            "Мін. confidence", 0.0, 1.0, 0.0, 0.05, key="f_conf",
            help="Оцінки без confidence (старі дані, до появи цього поля) показуються завжди.",
        )
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "categories": tuple(cats), "sources": tuple(srcs), "min_confidence": float(min_conf),
    }


#  Сторінка 1: Огляд
def _delta_recent(series: pd.Series) -> float | None:
    """Різниця між останніми k місяцями та k попередніми."""
    s = series.dropna()
    if len(s) < 4:
        return None
    k = min(3, len(s) // 2)
    return float(s.iloc[-k:].mean() - s.iloc[-2 * k:-k].mean())


@st.fragment
def trend_section(df: pd.DataFrame, arts: pd.DataFrame, notes: pd.DataFrame) -> None:
    """Фрагмент: зміна згладжування/режиму перемальовує лише цей блок."""
    st.subheader("Динаміка готовності до компромісу")
    c1, c2, c3 = st.columns([3, 2, 2], vertical_alignment="bottom")
    with c1:
        view = st.segmented_control("Показати", ["Загальний тон", "За категоріями"],
                                    default="Загальний тон", key="trend_view") or "Загальний тон"
    with c2:
        window = st.select_slider("Згладжування, міс.", [1, 2, 3, 6], value=3, key="trend_smooth")
    with c3:
        show_notes = st.toggle("Експертні замітки", value=True, key="trend_notes")

    months = pd.date_range(arts["month"].min(), arts["month"].max(), freq="MS")
    overall_raw = arts.groupby("month")["overall_sentiment"].mean().reindex(months)
    cat_raw = df.groupby(["month", "metric_category"])["score"].mean().unstack().reindex(months)

    def smooth(s: pd.Series) -> pd.Series:
        return s.rolling(window, min_periods=1).mean()

    fig = go.Figure()
    fig.add_hrect(y0=0.5, y1=1, fillcolor="#22C55E", opacity=0.07, line_width=0)
    fig.add_hrect(y0=-1, y1=-0.5, fillcolor="#EF4444", opacity=0.07, line_width=0)
    fig.add_hline(y=0, line_dash="dash", opacity=0.35)

    if view == "Загальний тон":
        base = smooth(overall_raw)
        fig.add_trace(go.Scatter(
            x=months, y=overall_raw, mode="markers", name="Середнє за місяць",
            marker=dict(size=6, opacity=0.35, color=OVERALL_COLOR),
            hovertemplate="%{y:+.2f}<extra>за місяць</extra>"))
        fig.add_trace(go.Scatter(
            x=months, y=base, mode="lines", name=f"Тренд ({window} міс.)", connectgaps=True,
            line=dict(width=3.5, color=OVERALL_COLOR, shape="spline"),
            hovertemplate="%{y:+.2f}<extra>тренд</extra>"))
        lookup = lambda month, cat: base.get(month)  # noqa: E731
    else:
        sm_cat = cat_raw.apply(smooth)
        for cat in cat_raw.columns:
            fig.add_trace(go.Scatter(
                x=months, y=sm_cat[cat], mode="lines+markers", name=CAT_LABEL.get(cat, cat),
                connectgaps=True, line=dict(width=2.5, color=CAT_COLOR.get(cat), shape="spline"),
                marker=dict(size=4), hovertemplate="%{y:+.2f}<extra>" + CAT_LABEL.get(cat, cat) + "</extra>"))
        lookup = lambda month, cat: (sm_cat[cat].get(month) if cat in sm_cat else None)  # noqa: E731

    if show_notes and not notes.empty:
        n = notes.copy()
        n["event_date"] = pd.to_datetime(n["event_date"])
        n = n[(n["event_date"] >= months.min()) & (n["event_date"] < months.max() + pd.offsets.MonthBegin(1))]
        if not n.empty:
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

    fig.update_layout(
        height=520, hovermode="x unified", margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        yaxis=dict(range=[-1.05, 1.05], title="−1 ескалація → +1 компроміс", zeroline=False),
        xaxis=dict(
            rangeslider=dict(visible=True, thickness=0.06),
            rangeselector=dict(buttons=[
                dict(count=6, label="6 міс.", step="month", stepmode="backward"),
                dict(count=1, label="1 рік", step="year", stepmode="backward"),
                dict(step="all", label="Усе"),
            ]),
        ),
    )
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def dashboard_page() -> None:
    f = ctx()["filters"]
    df, arts = current_filtered_data()
    notes = load_expert_notes(f["start"], f["end"], f["categories"], (ACTIVE,))

    st.title("🕊️ Peacetalk Tracker")
    st.caption("Діахронічний аналіз динаміки мирних переговорів Україна–Росія · 2022–2026")
    if df.empty:
        empty_state()
        return

    m_overall = arts.groupby("month")["overall_sentiment"].mean().sort_index()
    m_count = arts.groupby("month").size().sort_index()
    m_comp = (arts.groupby("month")["is_compromise_oriented"].mean() * 100).sort_index()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Проаналізовано статей", f"{len(arts):,}".replace(",", " "),
              chart_data=m_count.tolist(), chart_type="bar", border=True)
    d = _delta_recent(m_overall)
    k2.metric("Середній тон", f"{arts['overall_sentiment'].mean():+.2f}",
              delta=None if d is None else f"{d:+.2f}", chart_data=m_overall.tolist(), chart_type="line",
              border=True, help="Дельта: останні 3 місяці проти попередніх 3 (в межах фільтра).")
    dc = _delta_recent(m_comp)
    k3.metric("Орієнтовані на компроміс", f"{arts['is_compromise_oriented'].mean() * 100:.0f}%",
              delta=None if dc is None else f"{dc:+.0f} п.п.", chart_data=m_comp.tolist(), chart_type="area",
              border=True)
    k4.metric("Експертних заміток", len(notes), border=True,
              help="Активні (опубліковані) замітки в обраному періоді.")

    trend_section(df, arts, notes)

    t_heat, t_year, t_extreme = st.tabs([":material/grid_on: Теплова карта", ":material/bar_chart: По роках",
                                         ":material/swap_vert: Екстремуми"])
    with t_heat:
        piv = df.groupby(["metric_category", "month"])["score"].mean().unstack("month")
        piv = piv.reindex([c for c in CATEGORIES if c in piv.index])
        fig = go.Figure(go.Heatmap(
            z=piv.values, x=piv.columns, y=[CAT_LABEL[c] for c in piv.index],
            colorscale="RdYlGn", zmid=0, zmin=-1, zmax=1, xgap=1, ygap=1,
            colorbar=dict(title="Скор"), hovertemplate="%{y}<br>%{x|%m.%Y}: %{z:+.2f}<extra></extra>"))
        fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
        st.caption("Червоний — ескалація, зелений — компроміс. Порожні клітинки — місяці без оцінених статей.")

    with t_year:
        y = arts.assign(year=arts["date"].dt.year.astype(str))
        ca, cb = st.columns(2)
        share = y.groupby("year")["is_compromise_oriented"].mean().mul(100).reset_index()
        figb = px.bar(share, x="year", y="is_compromise_oriented", text_auto=".0f",
                      labels={"year": "Рік", "is_compromise_oriented": "% компромісних"},
                      title="Частка статей, орієнтованих на компроміс")
        figb.update_traces(marker_color="#3B82F6")
        figb.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10))
        ca.plotly_chart(figb, width="stretch", config={"displaylogo": False})
        figv = px.violin(y, x="year", y="overall_sentiment", box=True, points="all",
                         labels={"year": "Рік", "overall_sentiment": "Загальний тон"},
                         title="Розподіл загального тону")
        figv.update_traces(marker_color="#8B5CF6", marker_size=4)
        figv.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10), yaxis_range=[-1.05, 1.05])
        cb.plotly_chart(figv, width="stretch", config={"displaylogo": False})

    with t_extreme:
        neg = arts.nsmallest(5, "overall_sentiment")
        pos = arts.nlargest(5, "overall_sentiment")
        cn, cp = st.columns(2)
        for col, title, data in ((cn, ":red[:material/trending_down: Найбільша ескалація]", neg),
                                 (cp, ":green[:material/trending_up: Найбільший компроміс]", pos)):
            with col:
                st.markdown(f"**{title}**")
                for _, r in data.iterrows():
                    with st.container(border=True):
                        st.markdown(f"[{r['title']}]({r['url']})")
                        st.markdown(f":gray-badge[{r['date']:%d.%m.%Y}] :gray-badge[{r['source'].upper()}] "
                                    f":{score_color(r['overall_sentiment'])}-badge[{r['overall_sentiment']:+.2f}]")


#  Сторінка 2: Статті
def articles_page() -> None:
    st.title(":material/article: Статті та оцінки")
    df, arts = current_filtered_data()
    if df.empty:
        empty_state()
        return

    wide = df.pivot_table(index=["article_id", "date", "source", "title", "url"],
                          columns="metric_category", values="score").reset_index()
    wide.columns.name = None
    wide = wide.merge(arts[["article_id", "overall_sentiment", "is_compromise_oriented"]], on="article_id")
    present = [c for c in CATEGORIES if c in wide.columns]

    c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
    q = c1.text_input("Пошук за заголовком", placeholder="напр. Стамбул, обмін полоненими…",
                      icon=":material/search:")
    lo, hi = c2.slider("Загальний тон", -1.0, 1.0, (-1.0, 1.0), 0.05)
    only_comp = c3.toggle("Лише компромісні")

    view = wide[wide["overall_sentiment"].between(lo, hi)]
    if q:
        view = view[view["title"].str.contains(q, case=False, regex=False)]
    if only_comp:
        view = view[view["is_compromise_oriented"]]
    view = view.sort_values("date", ascending=False).reset_index(drop=True)

    st.caption(f"Знайдено **{len(view)}** із {len(wide)} · клікніть рядок, щоб побачити обґрунтування оцінок")
    prog = lambda label: st.column_config.ProgressColumn(  # noqa: E731
        label, min_value=-1.0, max_value=1.0, format="%.2f")
    config = {
        "date": st.column_config.DateColumn("Дата", format="DD.MM.YYYY", width="small"),
        "source": st.column_config.TextColumn("Джерело", width="small"),
        "title": st.column_config.TextColumn("Заголовок", width="large"),
        "overall_sentiment": prog("Загальний"),
        "is_compromise_oriented": st.column_config.CheckboxColumn("Компроміс", width="small"),
        "url": st.column_config.LinkColumn("Оригінал", display_text="відкрити", width="small"),
        **{c: prog(CAT_LABEL[c]) for c in present},
    }
    cols = ["date", "source", "title", "overall_sentiment", *present, "is_compromise_oriented", "url"]
    event = st.dataframe(view[cols], hide_index=True, width="stretch", height=430, column_config=config,
                         on_select="rerun", selection_mode="single-row", key="art_table")

    st.download_button("Завантажити CSV", view[cols].to_csv(index=False).encode("utf-8-sig"),
                       "peacetalk_articles.csv", "text/csv", icon=":material/download:")

    if event.selection.rows:
        row = view.iloc[event.selection.rows[0]]
        parts = df[df["article_id"] == row["article_id"]].set_index("metric_category")
        with st.container(border=True):
            st.subheader(row["title"])
            st.markdown(f":gray-badge[{row['date']:%d.%m.%Y}] :gray-badge[{row['source'].upper()}] "
                        f":{score_color(row['overall_sentiment'])}-badge[Загальний {row['overall_sentiment']:+.2f}] "
                        + (":green-badge[компромісна]" if row["is_compromise_oriented"] else ":gray-badge[не компромісна]"))
            cols_ui = st.columns(max(len(present), 1))
            for col, cat in zip(cols_ui, present):
                if cat not in parts.index:
                    continue
                r = parts.loc[cat]
                with col, st.container(border=True):
                    st.markdown(f"**{CAT_LABEL[cat]}** :{score_color(r['score'])}-badge[{r['score']:+.2f}]")
                    st.caption(r["justification"] or "—")
                    if pd.notna(r["confidence"]):
                        st.progress(float(r["confidence"]), text=f"confidence {r['confidence']:.2f}")
            with st.expander("Текст статті"):
                st.write(get_article_text(row["article_id"]) or "_Текст відсутній у БД_")
            st.link_button("Відкрити оригінал", row["url"], icon=":material/open_in_new:")


#  Сторінка 3: Експертні замітки
def fmt_utc(ts) -> str:
    """2026-10-10T12:30:00Z -> '2026-10-10 12:30 UTC' (час зберігається й показується в UTC)."""
    text = str(ts or "")
    return f"{text[:10]} {text[11:16]} UTC" if len(text) >= 16 else "—"


def _run_action(action, *args, ok_msg: str, icon: str = "✅") -> None:
    """Виконує операцію й показує результат.
    Очікувана відмова (NoteError) -> попередження; технічний збій -> помилка з кодом для журналу."""
    try:
        action(*args)
    except NoteError as exc:
        st.warning(str(exc), icon=":material/block:")
        return
    except TechnicalFailure as exc:
        st.error(str(exc), icon=":material/error:")
        return
    st.cache_data.clear()
    flash(ok_msg, icon)
    st.rerun()


def new_note_form(user: dict) -> None:
    today = utc_now().date()
    with st.expander("Нова замітка", icon=":material/add:"):
        with st.form("new_note_form", border=False, clear_on_submit=True):
            c1, c2 = st.columns(2)
            event_date = c1.date_input("Дата події (UTC)", value=today, min_value=WAR_START,
                                       max_value=today, format="DD.MM.YYYY", key="nn_date")
            cat = c2.selectbox("Категорія", CATEGORIES, format_func=CAT_LABEL.get, key="nn_cat")
            imp = st.segmented_control(
                "Тип", list(IMPORTANCE_TYPES), default="COMMENT", key="nn_imp",
                format_func=lambda x: "Коментар" if x == "COMMENT" else "Офіційна заява",
            ) or "COMMENT"
            content = st.text_area("Зміст", height=120, max_chars=MAX_CONTENT_LEN, key="nn_content",
                                   placeholder="Що сталося і чому це важливо для динаміки переговорів…")
            submitted = st.form_submit_button("Додати замітку", type="primary", icon=":material/save:")
        if submitted:
            _run_action(create_note, user["id"], event_date.isoformat(), cat, content, imp,
                        ok_msg="Замітку збережено")


def _note_actions(note: pd.Series, user: dict) -> None:
    """Редагування й архівація (лише для активних заміток автора або Admin)."""
    if note["status"] != ACTIVE:
        return
    nid = int(note["id"])
    a1, a2, _ = st.columns([1, 1, 4])
    with a1.popover("Редагувати", icon=":material/edit:"):
        new_text = st.text_area("Текст", value=note["content"], key=f"edit_text_{nid}", max_chars=MAX_CONTENT_LEN)
        if st.button("Зберегти зміни", key=f"edit_save_{nid}", type="primary"):
            _run_action(update_note, nid, user["id"], user["role"], new_text, ok_msg="Замітку оновлено")
    with a2.popover("Архівувати", icon=":material/archive:"):
        st.write("Зняти замітку з графіка? Слот буде звільнено.")
        if st.button("Так, архівувати", key=f"archive_{nid}", type="primary"):
            _run_action(archive_note, nid, user["id"], user["role"], ok_msg="Замітку архівовано", icon="📦")


def _note_card(note: pd.Series, user: dict) -> None:
    with st.container(border=True):
        st.markdown(
            f"**{pd.to_datetime(note['event_date']):%d.%m.%Y}** &nbsp; "
            f":gray-badge[{CAT_LABEL.get(note['metric_category'], note['metric_category'])}] "
            f"{IMPORTANCE_BADGE.get(note['importance_type'], '')} {STATUS_BADGE.get(note['status'], note['status'])}"
        )
        st.write(note["content"])
        st.caption(f":material/person: {note['author_id']} · оновлено {fmt_utc(note['updated_at'])}")
        if note["author_id"] == user["id"] or user["role"] == "Admin":
            _note_actions(note, user)


def notes_page() -> None:
    user = ctx()["user"]
    f = ctx()["filters"]
    st.title(":material/edit_note: Експертні замітки")
    new_note_form(user)

    notes = load_expert_notes(f["start"], f["end"], f["categories"])
    is_admin = user["role"] == "Admin"
    # Активні бачать усі; архівні — лише автор (та Admin)
    visible = notes[(notes["status"] == ACTIVE) | (notes["author_id"] == user["id"]) | is_admin]
    scopes = {
        "Усі": visible[visible["status"] == ACTIVE],
        "Мої": visible[visible["author_id"] == user["id"]],
        "В архіві": visible[visible["status"] == ARCHIVED],
    }
    scope = st.segmented_control("Показати", list(scopes), default="Усі", key="notes_scope",
                                 format_func=lambda s: f"{s} · {len(scopes[s])}") or "Усі"
    shown = scopes[scope]

    st.caption("Застосовано фільтри періоду й категорій із бічної панелі. Час змін — UTC.")
    if shown.empty:
        st.info("Заміток немає. Відкрийте «Нова замітка», щоб додати першу.", icon=":material/inbox:")
        return
    for _, note in shown.iterrows():
        _note_card(note, user)


#  Сторінка 4: Підсумки
def summary_page() -> None:
    st.title(":material/summarize: Аналітичні підсумки")
    st.info("Agent 2 (Synthesizer) ще не реалізований, тому нижче — статистичне зведення за піврічними "
            "періодами без LLM-інтерпретації.", icon=":material/info:")
    df, arts = current_filtered_data()
    if df.empty:
        empty_state()
        return

    arts = arts.assign(period=arts["date"].dt.year.astype(str) + "-"
                       + arts["date"].dt.month.le(6).map({True: "H1", False: "H2"}))
    base = arts.groupby("period").agg(
        articles=("article_id", "count"),
        avg_tone=("overall_sentiment", "mean"),
        compromise_pct=("is_compromise_oriented", lambda s: s.mean() * 100),
    )
    d2 = df.merge(arts[["article_id", "period"]], on="article_id")
    per_cat = d2.pivot_table(index="period", columns="metric_category", values="score").rename(columns=CAT_LABEL)
    hi = arts.loc[arts.groupby("period")["overall_sentiment"].idxmax()].set_index("period")["title"].rename("Найпозитивніша")
    lo = arts.loc[arts.groupby("period")["overall_sentiment"].idxmin()].set_index("period")["title"].rename("Найнегативніша")
    table = base.join(per_cat).join(hi).join(lo).reset_index()
    table = table.rename(columns={"period": "Період", "articles": "Статей", "avg_tone": "Середній тон",
                                  "compromise_pct": "% компромісних"})

    period_order = sorted(table["Період"].unique())
    figb = px.bar(table, x="Період", y="Середній тон", color=table["Середній тон"] >= 0,
        color_discrete_map={True: "#22C55E", False: "#EF4444"},
        text_auto=".2f",
        category_orders={"Період": period_order})
    figb.update_layout(height=320, showlegend=False, margin=dict(l=10, r=10, t=10, b=10), yaxis_range=[-1, 1],)
    st.plotly_chart(figb, width="stretch", config={"displaylogo": False})

    cfg = {
        "Середній тон": st.column_config.ProgressColumn(min_value=-1.0, max_value=1.0, format="%.2f"),
        "% компромісних": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
        **{CAT_LABEL[c]: st.column_config.NumberColumn(format="%.2f") for c in CATEGORIES},
        "Найпозитивніша": st.column_config.TextColumn(width="large"),
        "Найнегативніша": st.column_config.TextColumn(width="large"),
    }
    st.dataframe(table, hide_index=True, width="stretch", column_config=cfg)
    st.download_button("Завантажити CSV", table.to_csv(index=False).encode("utf-8-sig"),
                       "peacetalk_summary.csv", "text/csv", icon=":material/download:")


#  Entry point
def _healthcheck() -> None:
    """Перевіряє доступність БД, пише подію healthcheck у журнал; при збої зупиняє сторінку з кодом."""
    started = time.perf_counter()
    try:
        if not DB_PATH.exists():
            raise FileNotFoundError("файл БД не знайдено")
        get_connection().execute("SELECT 1 FROM expert_notes LIMIT 1").fetchone()
    except (OSError, sqlite3.Error) as exc:
        rid = record("healthcheck", "error", (time.perf_counter() - started) * 1000, type(exc).__name__)
        st.error(
            f"База даних недоступна ({type(exc).__name__}). Код для пошуку в журналі: `{rid}`.\n\n"
            f"Файл: `{DB_PATH}`. Створіть БД і завантажте дані (з кореня проєкту): "
            "`python -m scripts.load_to_db`",
            icon=":material/error:",
        )
        st.stop()
    record("healthcheck", "ok", (time.perf_counter() - started) * 1000)


def bootstrap() -> None:
    """Перевірка БД, сайдбар (обліковий запис + фільтри), контекст для сторінок."""
    _healthcheck()
    user = sidebar_user()
    filters = sidebar_filters(get_filter_options())
    st.session_state["_ctx"] = {"user": user, "filters": filters}
    with st.sidebar:
        st.divider()
        st.caption(f"Peacetalk Tracker v1.0 · БД: `{DB_PATH.name}`")
        st.caption("Agent 1: Analyst ✅ · Agent 2: Synthesizer ⏳")
    show_flash()


def main() -> None:
    st.set_page_config(page_title="Peacetalk Tracker", page_icon="🕊️", layout="wide")
    bootstrap()
    pages = [
        st.Page(dashboard_page, title="Огляд", icon=":material/monitoring:", url_path="overview", default=True),
        st.Page(articles_page, title="Статті", icon=":material/article:", url_path="articles"),
        st.Page(notes_page, title="Замітки", icon=":material/edit_note:", url_path="notes"),
        st.Page(summary_page, title="Підсумки", icon=":material/summarize:", url_path="summary"),
    ]
    st.navigation(pages, position="top").run()


if __name__ == "__main__":
    main()
