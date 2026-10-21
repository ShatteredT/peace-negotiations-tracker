from __future__ import annotations

import hmac
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.runtime_db import prepare_demo_database
from src.errors import (
    AccessDenied, ConflictError, NoteError, NotFoundError, TechnicalFailure, ValidationError,
)
from src.ops import observed, record, utc_now, utc_now_iso
from src.ui.charts import (
    CAT_LABEL, CATEGORIES, VIEW_CATEGORIES, VIEW_OVERALL, heatmap_figure, period_tone_figure, trend_figure,
    year_share_figure, year_violin_figure,
)

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value
    try:
        return str(st.secrets[name])
    except (KeyError, FileNotFoundError):
        return default


DEMO_MODE = _setting("PEACETALK_DEMO_MODE").strip().lower() in {"1", "true", "yes"}

@st.cache_resource
def _get_demo_db_path() -> Path:
    """Створює тимчасову демобазу один раз на процес."""
    # Після кожної дії НЕ створюється нова тимчасова БД
    return prepare_demo_database(BASE_DIR / "data" / "demo" / "peacetalk_demo.db")

DB_PATH = Path(os.getenv("PEACETALK_DB", str(BASE_DIR / "data" / "peacetalk.db")))

if DEMO_MODE:
    DB_PATH = _get_demo_db_path()

WAR_START = date(2022, 2, 24)


STATUS_BADGE = {
    "PUBLISHED": ":green-badge[Опубліковано]",
    "ARCHIVED": ":gray-badge[:material/archive: В архіві]",
}
IMPORTANCE_BADGE = {
    "OFFICIALSTATEMENT": ":violet-badge[:material/campaign: Офіційна заява]",
    "COMMENT": ":blue-badge[:material/chat: Коментар]",
}
TREND_BADGE = {
    "escalation": ":red-badge[Ескалація]",
    "compromise": ":green-badge[Компроміс]",
    "stable": ":gray-badge[Стабільно]",
    "mixed": ":orange-badge[Змішано]",
}
REVIEW_BADGE = {
    "APPROVED": ":green-badge[Схвалено]",
    "PENDING_APPROVAL": ":orange-badge[На розгляді]",
    "REJECTED": ":red-badge[Відхилено]",
}
TREND_BADGE = {
    "escalation": ":red-badge[Ескалація]",
    "compromise": ":green-badge[Компроміс]",
    "stable": ":gray-badge[Стабільно]",
    "mixed": ":orange-badge[Змішано]",
}


# DB
@st.cache_resource
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _assessment_scope(start_date: str, end_date: str, categories: tuple, sources: tuple) -> tuple[str, list]:
    """WHERE-умова за датою, категоріями й джерелами (без порогу впевненості) + її параметри."""
    where = "a.date >= ? AND a.date <= ?"
    params: list = [start_date, end_date]
    if categories:
        where += f" AND ar.metric_category IN ({','.join('?' * len(categories))})"
        params += list(categories)
    if sources:
        where += f" AND a.source IN ({','.join('?' * len(sources))})"
        params += list(sources)
    return where, params


# Усі аргументи (зокрема min_confidence) входять у ключ кешу st.cache_data, тому інший поріг
# дає окремий запис кешу, а не застарілий результат (PT-74).
@st.cache_data(ttl=30, show_spinner=False)
def load_assessments(start_date: str, end_date: str, categories: tuple,
                     sources: tuple, min_confidence: float) -> pd.DataFrame:
    where, params = _assessment_scope(start_date, end_date, categories, sources)
    query = f"""
        SELECT a.id AS article_id, a.source, a.title, a.date, a.url,
               ar.metric_category, ar.score, ar.justification, ar.confidence,
               ar.overall_sentiment, ar.is_compromise_oriented
        FROM assessment_records ar
        JOIN news_articles a ON ar.article_id = a.id
        WHERE (ar.confidence IS NULL OR ar.confidence >= ?)
          AND {where}
    """
    df = pd.read_sql_query(query + " ORDER BY a.date", get_connection(), params=[min_confidence, *params])
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M").dt.to_timestamp()
    df["is_compromise_oriented"] = df["is_compromise_oriented"].astype(bool)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def count_hidden_assessments(start_date: str, end_date: str, categories: tuple,
                             sources: tuple, min_confidence: float) -> tuple[int, int]:
    """(приховано, усього) оцінок за поточними фільтрами. Приховані — ті, що нижчі за поріг."""
    where, params = _assessment_scope(start_date, end_date, categories, sources)
    row = get_connection().execute(
        f"""
        SELECT COUNT(*),
               COALESCE(SUM(ar.confidence IS NOT NULL AND ar.confidence < ?), 0)
        FROM assessment_records ar
        JOIN news_articles a ON ar.article_id = a.id
        WHERE {where}
        """,
        [min_confidence, *params],
    ).fetchone()
    return int(row[1]), int(row[0])


@st.cache_data(ttl=30, show_spinner=False)
def load_expert_notes(start_date: str | None = None, end_date: str | None = None,
                      categories: tuple = (), statuses: tuple = ()) -> pd.DataFrame:
    query = """
        SELECT id, event_date, metric_category, content, author_id, importance_type, status,
               created_at, updated_at, review_status, reviewed_by, reviewed_at
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
def load_pending_notes() -> pd.DataFrame:
    query = """
        SELECT id, event_date, metric_category, content, author_id, importance_type, status,
               created_at, updated_at, review_status
        FROM expert_notes 
        WHERE status = 'PUBLISHED' AND review_status = 'PENDING_APPROVAL'
        ORDER BY created_at DESC
    """
    return pd.read_sql_query(query, get_connection())


@st.cache_data(ttl=30, show_spinner=False)
def get_filter_options() -> dict:
    conn = get_connection()
    return {
        "sources": [r[0] for r in conn.execute("SELECT DISTINCT source FROM news_articles ORDER BY source")],
        "categories": [r[0] for r in conn.execute("SELECT DISTINCT metric_category FROM assessment_records")],
        "date_range": (WAR_START, utc_now().date()),
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_article_text(article_id: str) -> str:
    row = get_connection().execute("SELECT text FROM news_articles WHERE id = ?", (article_id,)).fetchone()
    return (row[0] if row and row[0] else "")


SUMMARY_COLUMNS = ["period", "trend", "summary", "articles_count", "avg_sentiment", "model", "created_at"]
ANNOTATION_COLUMNS = ["period", "article_id", "kind", "text", "date", "title", "url", "overall_sentiment"]


@st.cache_data(ttl=30, show_spinner=False)
def load_summaries() -> pd.DataFrame:
    try:
        return pd.read_sql_query(f"SELECT {', '.join(SUMMARY_COLUMNS)} FROM period_summaries ORDER BY period DESC",
                                 get_connection())
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=SUMMARY_COLUMNS)


@st.cache_data(ttl=30, show_spinner=False)
def load_annotations() -> pd.DataFrame:
    query = """
        SELECT s.period, s.article_id, s.kind, s.text, a.date, a.title, a.url,
               (SELECT MAX(overall_sentiment) FROM assessment_records WHERE article_id = a.id) AS overall_sentiment
        FROM summary_annotations s JOIN news_articles a ON a.id = s.article_id
        ORDER BY a.date
    """
    try:
        df = pd.read_sql_query(query, get_connection())
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        return pd.DataFrame(columns=ANNOTATION_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    return df


ACTIVE = "PUBLISHED"
ARCHIVED = "ARCHIVED"
IMPORTANCE_TYPES = ("COMMENT", "OFFICIALSTATEMENT")
MAX_CONTENT_LEN = 2000


@contextmanager
def _write_conn():
    """Окреме з'єднання на кожну операцію: коректно при паралельних запитах."""
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
    row = conn.execute(
        "SELECT id, author_id, status, review_status FROM expert_notes WHERE id = ?",
        (int(note_id),),
    ).fetchone()
    if row is None:
        raise NotFoundError("Замітку не знайдено")
    return {"id": row[0], "author_id": row[1], "status": row[2], "review_status": row[3]}


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
    """Створює замітку; офіційні заяви очікують схвалення адміністратора."""
    content = _clean_content(content)
    _validate_new_note(event_date, metric_category, importance_type)

    review_status = "PENDING_APPROVAL" if importance_type == "OFFICIALSTATEMENT" else "APPROVED"

    now = utc_now_iso()
    try:
        with _write_conn() as conn:
            cur = conn.execute(
                """INSERT INTO expert_notes
                   (event_date, metric_category, content, author_id, importance_type, status, created_at, updated_at, review_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_date, metric_category, content, user_id, importance_type, ACTIVE, now, now, review_status),
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


@observed("approve_note")
def approve_note(note_id: int, admin_id: str, admin_role: str) -> None:
    """Схвалює замітку (Admin only)."""
    if admin_role != "Admin":
        raise AccessDenied("Доступ лише для адміна")
    with _write_conn() as conn:
        note = _fetch_note(conn, note_id)
        if note["review_status"] != "PENDING_APPROVAL":
            raise ConflictError("На розгляд очікують лише замітки зі статусом «На розгляді»")
        cur = conn.execute(
            """UPDATE expert_notes
               SET review_status = 'APPROVED', reviewed_by = ?, reviewed_at = ?
               WHERE id = ? AND review_status = 'PENDING_APPROVAL'""",
            (admin_id, utc_now_iso(), int(note_id)),
        )
        if cur.rowcount == 0:
            raise ConflictError("Замітку вже розглянуто")


@observed("reject_note")
def reject_note(note_id: int, admin_id: str, admin_role: str) -> None:
    """Відхиляє замітку (Admin only)."""
    if admin_role != "Admin":
        raise AccessDenied("Доступ лише для адміна")
    with _write_conn() as conn:
        note = _fetch_note(conn, note_id)
        if note["review_status"] != "PENDING_APPROVAL":
            raise ConflictError("На розгляд очікують лише замітки зі статусом «На розгляді»")
        cur = conn.execute(
            """UPDATE expert_notes
               SET review_status = 'REJECTED', reviewed_by = ?, reviewed_at = ?
               WHERE id = ? AND review_status = 'PENDING_APPROVAL'""",
            (admin_id, utc_now_iso(), int(note_id)),
        )
        if cur.rowcount == 0:
            raise ConflictError("Замітку вже розглянуто")


#  Helpers
def flash(msg: str, icon: str = "✅") -> None:
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


def _admin_password() -> str:
    return _setting("PEACETALK_ADMIN_PASSWORD").strip()


def sidebar_user() -> dict:
    with st.sidebar:
        st.markdown("##### :material/person: Обліковий запис")
        key = st.selectbox(
            "Обліковий запис", ("analyst_1", "analyst_2", "admin_1"), key="account_select",
            label_visibility="collapsed",
            format_func=lambda k: f"{ACCOUNTS[k]['name']} · {ACCOUNTS[k]['role']}",)

        previous_key = st.session_state.get("_previous_account_key")
        if previous_key != key:
            st.session_state["_admin_authenticated"] = False
            st.session_state["_previous_account_key"] = key

        user = ACCOUNTS[key]

        if key == "admin_1":
            if st.session_state.get("_admin_authenticated", False):
                st.divider()
                st.success("Адміністратор")

                if st.button("Вийти з режиму адміністратора", key="admin_logout"):
                    st.session_state["_admin_authenticated"] = False
                    st.rerun()

                # доступ адміна надається лише після автентифікації
                user = ACCOUNTS["admin_1"]

            else:
                with st.form("admin_login_form"):
                    password = st.text_input(
                        "Пароль адміністратора",
                        type="password",
                        key="admin_password_input",
                    )
                    submitted = st.form_submit_button(
                        "Увійти як адміністратор",
                        disabled=not _admin_password(),
                    )

                expected_password = _admin_password()

                if not expected_password:
                    st.caption(
                        "Вхід адміністратора вимкнений: "
                        "налаштуйте секрет PEACETALK_ADMIN_PASSWORD."
                    )
                elif submitted:
                    if hmac.compare_digest(
                        password.encode("utf-8"),
                        expected_password.encode("utf-8"),
                    ):
                        st.session_state["_admin_authenticated"] = True
                        st.rerun()
                    else:
                        st.error("Неправильний пароль адміністратора.")

                # не повертати адміна як авторизованого користувача до входу
                user = ACCOUNTS["analyst_1"] # analyst_1 за замовчуванням

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
            "Мін. впевненість моделі", 0.0, 1.0, 0.0, 0.05, key="f_conf",
            help="Оцінки без значення впевненості (старі дані) показуються завжди.",
        )
        # round: слайдер із кроком 0.05 може дати 0.15000000000000002, і оцінка 0.15 «випала б»
        min_conf = round(float(min_conf), 2)
        hidden, total = count_hidden_assessments(
            start.isoformat(), end.isoformat(), tuple(cats), tuple(srcs), min_conf,
        )
        note = f"Приховано **{hidden}** із **{total}** оцінок"
        if total and hidden == total:
            note += " — усі приховано, зменште поріг"
        st.caption(note)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "categories": tuple(cats), "sources": tuple(srcs), "min_confidence": min_conf,
    }


#  Сторінка 1: Огляд
def _delta_recent(series: pd.Series) -> float | None:
    s = series.dropna()
    if len(s) < 4:
        return None
    k = min(3, len(s) // 2)
    return float(s.iloc[-k:].mean() - s.iloc[-2 * k:-k].mean())


def _visible_annotations(arts: pd.DataFrame) -> pd.DataFrame:
    ann = load_annotations()
    return ann[ann["article_id"].isin(arts["article_id"])]


@st.fragment
def trend_section(df: pd.DataFrame, arts: pd.DataFrame, notes: pd.DataFrame, annotations: pd.DataFrame) -> None:
    st.subheader("Динаміка готовності до компромісу")
    c1, c2, c3, c4 = st.columns([3, 2, 2, 2], vertical_alignment="bottom")
    with c1:
        view = st.segmented_control("Показати", [VIEW_OVERALL, VIEW_CATEGORIES],
                                    default=VIEW_OVERALL, key="trend_view") or VIEW_OVERALL
    with c2:
        window = st.select_slider("Згладжування, міс.", [1, 2, 3, 6], value=3, key="trend_smooth")
    with c3:
        show_notes = st.toggle("Експертні замітки", value=True, key="trend_notes")
    with c4:
        show_ann = st.toggle("Анотації агента", value=True, key="trend_ann")
    fig = trend_figure(df, arts, notes, annotations, view, window, show_notes, show_ann)
    st.plotly_chart(fig, width="stretch", config={"displaylogo": False})


def dashboard_page() -> None:
    f = ctx()["filters"]
    df, arts = current_filtered_data()
    # Показуємо тільки схвалені замітки
    notes = load_expert_notes(f["start"], f["end"], f["categories"], (ACTIVE,))
    # Додатковий фільтр по схваленню: review_status must be APPROVED
    notes = notes[notes['review_status'] == 'APPROVED']

    st.title(":material/monitoring: Огляд")
    st.caption("Peacetalk Tracker · діахронічний аналіз динаміки мирних переговорів Україна–Росія · 2022–2026")
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

    trend_section(df, arts, notes, _visible_annotations(arts))

    t_heat, t_year, t_extreme = st.tabs([":material/grid_on: Теплова карта", ":material/bar_chart: По роках",
                                         ":material/swap_vert: Екстремуми"])
    with t_heat:
        st.plotly_chart(heatmap_figure(df), width="stretch", config={"displaylogo": False})
    with t_year:
        ca, cb = st.columns(2)
        ca.plotly_chart(year_share_figure(arts), width="stretch", config={"displaylogo": False})
        cb.plotly_chart(year_violin_figure(arts), width="stretch", config={"displaylogo": False})
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

    st.caption(f"Знайдено **{len(view)}** із {len(wide)}")
    prog = lambda label: st.column_config.ProgressColumn(label, min_value=-1.0, max_value=1.0, format="%.2f")
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


def fmt_utc(ts) -> str:
    text = str(ts or "")
    if len(text) < 16:
        return "—"
    return f"{text[8:10]}.{text[5:7]}.{text[:4]} {text[11:16]} UTC"


def _run_action(action, *args, ok_msg: str, icon: str = "✅") -> None:
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
    if note["status"] != ACTIVE:
        return
    nid = int(note["id"])
    a1, a2, _ = st.columns([1, 1, 4])
    with a1.popover("Редагувати", icon=":material/edit:"):
        new_text = st.text_area("Текст", value=note["content"], key=f"edit_text_{nid}", max_chars=MAX_CONTENT_LEN)
        if st.button("Зберегти зміни", key=f"edit_save_{nid}", type="primary"):
            _run_action(update_note, nid, user["id"], user["role"], new_text, ok_msg="Замітку оновлено")
    with a2.popover("Архівувати", icon=":material/archive:"):
        if st.button("Так, архівувати", key=f"archive_{nid}", type="primary"):
            _run_action(archive_note, nid, user["id"], user["role"], ok_msg="Замітку архівовано", icon="📦")


def _note_card(note: pd.Series, user: dict) -> None:
    with st.container(border=True):
        st.markdown(
            f"**{pd.to_datetime(note['event_date']):%d.%m.%Y}** &nbsp; "
            f":gray-badge[{CAT_LABEL.get(note['metric_category'], note['metric_category'])}] "
            f"{IMPORTANCE_BADGE.get(note['importance_type'], '')} {STATUS_BADGE.get(note['status'], note['status'])} "
            f"{REVIEW_BADGE.get(note['review_status'], '')}"
        )
        st.write(note["content"])
        st.caption(f":material/person: {note['author_id']} · оновлено {fmt_utc(note['updated_at'])}")
        if note["author_id"] == user["id"] or user["role"] == "Admin":
            _note_actions(note, user)


def _moderation_page(user: dict) -> None:
    st.subheader(":material/fact_check: Модерація")
    pending = load_pending_notes()
    if pending.empty:
        st.info("Нових заміток на перевірку немає.")
        return

    for _, note in pending.iterrows():
        with st.container(border=True):
            st.markdown(f"**{note['author_id']}** · {note['metric_category']} · {note['event_date']}")
            st.write(note["content"])
            c1, c2 = st.columns(2)
            if c1.button("Схвалити", key=f"approve_{note['id']}", type="primary"):
                _run_action(approve_note, note['id'], user['id'], user['role'], ok_msg="Замітку схвалено")
            if c2.button("Відхилити", key=f"reject_{note['id']}", type="secondary"):
                _run_action(reject_note, note['id'], user['id'], user['role'], ok_msg="Замітку відхилено")


def notes_page() -> None:
    user = ctx()["user"]
    f = ctx()["filters"]
    st.title(":material/edit_note: Експертні замітки")
    
    tabs = [":material/list: Усі замітки"]
    if user["role"] == "Admin":
        tabs.append(":material/fact_check: Модерація")
    
    selected_tab = st.tabs(tabs)
    
    with selected_tab[0]:
        new_note_form(user)
        notes = load_expert_notes(f["start"], f["end"], f["categories"])
        is_admin = user["role"] == "Admin"
        # Активні бачать усі; архівні — лише автор (та Admin)
        visible = notes[(notes["status"] == ACTIVE) | (notes["author_id"] == user["id"]) | is_admin]
        published = visible[
            (visible["status"] == ACTIVE) & (visible["review_status"] == "APPROVED")
        ]
        scopes = {
            "Усі": published,
            "Мої": visible[visible["author_id"] == user["id"]],
            "В архіві": visible[visible["status"] == ARCHIVED],
        }
        scope = st.segmented_control("Показати", list(scopes), default="Усі", key="notes_scope",
                                     format_func=lambda s: f"{s} · {len(scopes[s])}") or "Усі"
        shown = scopes[scope]

        if shown.empty:
            st.info("Заміток немає.", icon=":material/inbox:")
        else:
            for _, note in shown.iterrows():
                _note_card(note, user)

    if user["role"] == "Admin" and len(selected_tab) > 1:
        with selected_tab[1]:
            _moderation_page(user)


def summary_page() -> None:
    st.title(":material/summarize: Аналітичні підсумки")
    tab_agent, tab_stats = st.tabs([":material/auto_awesome: Резюме агента", ":material/table_chart: Статистика"])
    with tab_agent:
        _agent_summaries()
    with tab_stats:
        _stats_summary()


def _agent_summaries() -> None:
    summaries = load_summaries()
    if summaries.empty:
        st.info("Резюме ще не згенеровано.", icon=":material/info:")
        return
    annotations = load_annotations()
    for _, row in summaries.iterrows():
        _summary_card(row, annotations[annotations["period"] == row["period"]])


def _summary_card(row: pd.Series, annotations: pd.DataFrame) -> None:
    with st.container(border=True):
        st.markdown(f"### {row['period']} &nbsp; {TREND_BADGE.get(row['trend'], row['trend'])}")
        st.write(row["summary"])
        for _, a in annotations.iterrows():
            mark = ":green[▲ пік]" if a["kind"] == "peak" else ":red[▼ спад]"
            st.markdown(f"{mark} **{a['date']:%d.%m.%Y}** · [{a['title']}]({a['url']}) "
                        f"({a['overall_sentiment']:+.2f})  \n{a['text']}")


def _stats_summary() -> None:
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
    table = base.reset_index().rename(columns={"period": "Період", "articles": "Статей", "avg_tone": "Середній тон",
                                  "compromise_pct": "% компромісних"})
    table[["Середній тон", "% компромісних"]] = (table[["Середній тон", "% компромісних"]].round(2))

    st.plotly_chart(period_tone_figure(table), width="stretch", config={"displaylogo": False})
    st.dataframe(table, hide_index=True, width="stretch")


def _healthcheck() -> None:
    try:
        if not DB_PATH.exists(): raise FileNotFoundError("файл БД не знайдено")
        get_connection().execute("SELECT 1 FROM expert_notes LIMIT 1").fetchone()
    except (OSError, sqlite3.Error) as exc:
        st.error(f"База даних недоступна: {type(exc).__name__}", icon=":material/error:")
        st.stop()


def bootstrap() -> None:
    _healthcheck()
    user = sidebar_user()
    filters = sidebar_filters(get_filter_options())
    st.session_state["_ctx"] = {"user": user, "filters": filters}
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
