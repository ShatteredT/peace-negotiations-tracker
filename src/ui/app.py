"""
Peacetalk Tracker - Streamlit MVP
PT-9: Перегляд діахронічних графіків у UI
PT-10: Ініціалізація Streamlit
PT-11: Зчитування даних з SQLite
PT-12: Побудова графіків (Plotly) з фільтрами
"""
import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

# --- Config ---
DB_PATH = Path("data/peacetalk.db")
st.set_page_config(
    page_title="Peacetalk Tracker",
    page_icon="🕊️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- DB Helpers ---
@st.cache_resource
def get_connection():
    """Get SQLite connection."""
    return sqlite3.connect(DB_PATH, check_same_thread=False)

@st.cache_data(ttl=30)
def load_assessments(
    start_date: str = None,
    end_date: str = None,
    categories: List[str] = None,
    sources: List[str] = None,
    min_confidence: float = 0.0
) -> pd.DataFrame:
    """Load assessment records with filters."""
    conn = get_connection()
    
    query = """
        SELECT 
            a.id as article_id,
            a.source,
            a.title,
            a.date,
            a.url,
            ar.metric_category,
            ar.score,
            ar.justification,
            ar.confidence,
            ar.overall_sentiment,
            ar.is_compromise_oriented,
            ar.agent_version,
            ar.created_at as assessed_at
        FROM assessment_records ar
        JOIN news_articles a ON ar.article_id = a.id
        WHERE ar.confidence >= ?
    """
    params = [min_confidence]
    
    if start_date:
        query += " AND a.date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND a.date <= ?"
        params.append(end_date)
    if categories:
        placeholders = ",".join(["?"] * len(categories))
        query += f" AND ar.metric_category IN ({placeholders})"
        params.extend(categories)
    if sources:
        placeholders = ",".join(["?"] * len(sources))
        query += f" AND a.source IN ({placeholders})"
        params.extend(sources)
    
    query += " ORDER BY a.date"
    
    return pd.read_sql_query(query, conn, params=params)

@st.cache_data(ttl=30)
def load_expert_notes(
    start_date: str = None,
    end_date: str = None,
    categories: List[str] = None,
    statuses: List[str] = None,
    author_id: str = None
) -> pd.DataFrame:
    """Load expert notes with filters."""
    conn = get_connection()
    
    query = """
        SELECT id, event_date, metric_category, content, author_id,
               importance_type, status, created_at, updated_at
        FROM expert_notes
        WHERE 1=1
    """
    params = []
    
    if start_date:
        query += " AND event_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND event_date <= ?"
        params.append(end_date)
    if categories:
        placeholders = ",".join(["?"] * len(categories))
        query += f" AND metric_category IN ({placeholders})"
        params.extend(categories)
    if statuses:
        placeholders = ",".join(["?"] * len(statuses))
        query += f" AND status IN ({placeholders})"
        params.extend(statuses)
    if author_id:
        query += " AND author_id = ?"
        params.append(author_id)
    
    query += " ORDER BY event_date DESC"
    
    return pd.read_sql_query(query, conn, params=params)

@st.cache_data(ttl=30)
def load_monthly_aggregates() -> pd.DataFrame:
    """Load monthly aggregated scores for overview chart."""
    conn = get_connection()
    query = """
        SELECT 
            strftime('%Y-%m', a.date) as month,
            ar.metric_category,
            AVG(ar.score) as avg_score,
            AVG(ar.confidence) as avg_confidence,
            COUNT(*) as count
        FROM assessment_records ar
        JOIN news_articles a ON ar.article_id = a.id
        WHERE ar.confidence >= 0.7
        GROUP BY month, ar.metric_category
        ORDER BY month
    """
    return pd.read_sql_query(query, conn)

@st.cache_data(ttl=30)
def get_filter_options() -> dict:
    """Get unique values for filter dropdowns."""
    conn = get_connection()
    return {
        "sources": [r[0] for r in conn.execute("SELECT DISTINCT source FROM news_articles ORDER BY source").fetchall()],
        "categories": [r[0] for r in conn.execute("SELECT DISTINCT metric_category FROM assessment_records ORDER BY metric_category").fetchall()],
        "statuses": [r[0] for r in conn.execute("SELECT DISTINCT status FROM expert_notes ORDER BY status").fetchall()],
        "importance_types": [r[0] for r in conn.execute("SELECT DISTINCT importance_type FROM expert_notes ORDER BY importance_type").fetchall()],
        "date_range": conn.execute("SELECT MIN(date), MAX(date) FROM news_articles WHERE date >= '2022-02-24'").fetchone()
    }

# --- Auth / Role Switcher ---
def get_current_user():
    if "user" not in st.session_state:
        st.session_state.user = {"id": "analyst_1", "role": "Analyst", "name": "Дослідник 1"}
    return st.session_state.user

def sidebar_auth():
    st.sidebar.header("👤 Користувач")
    user = get_current_user()
    
    role = st.sidebar.selectbox(
        "Роль",
        ["Analyst", "Admin"],
        index=0 if user["role"] == "Analyst" else 1,
        key="role_select"
    )
    
    if role != user["role"]:
        user["role"] = role
        user["id"] = "admin_1" if role == "Admin" else "analyst_1"
        user["name"] = "Головний редактор" if role == "Admin" else "Дослідник 1"
        st.rerun()
    
    st.sidebar.caption(f"ID: {user['id']}")
    return user

# --- Expert Note CRUD ---
def create_note(user_id: str, event_date: str, metric_category: str, 
                content: str, importance_type: str) -> Tuple[bool, str]:
    conn = get_connection()
    try:
        status = "PENDING_APPROVAL" if importance_type == "OFFICIALSTATEMENT" else "PUBLISHED"
        conn.execute("""
            INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (event_date, metric_category, content, user_id, importance_type, status))
        conn.commit()
        return True, "Замітку збережено!"
    except sqlite3.IntegrityError:
        return False, "Помилка: у вас вже є замітка на цю дату/категорію"

def update_note(note_id: int, content: str, status: str = None) -> bool:
    conn = get_connection()
    if status:
        conn.execute("""
            UPDATE expert_notes SET content = ?, status = ?, updated_at = ?
            WHERE id = ?
        """, (content, status, datetime.now().isoformat(), note_id))
    else:
        conn.execute("""
            UPDATE expert_notes SET content = ?, updated_at = ?
            WHERE id = ?
        """, (content, datetime.now().isoformat(), note_id))
    conn.commit()
    return True

def delete_note(note_id: int, user_id: str, user_role: str) -> Tuple[bool, str]:
    conn = get_connection()
    if user_role == "Admin":
        conn.execute("DELETE FROM expert_notes WHERE id = ?", (note_id,))
    else:
        conn.execute("DELETE FROM expert_notes WHERE id = ? AND author_id = ?", (note_id, user_id))
    conn.commit()
    if conn.total_changes > 0:
        return True, "Видалено"
    return False, "Немає прав або замітка не знайдена"

def moderate_note(note_id: int, admin_id: str, action: str, comment: str = "") -> bool:
    conn = get_connection()
    new_status = "APPROVED" if action == "approve" else "REJECTED"
    conn.execute("""
        UPDATE expert_notes 
        SET status = ?, moderated_by = ?, moderated_at = ?, moderator_comment = ?, updated_at = ?
        WHERE id = ?
    """, (new_status, admin_id, datetime.now().isoformat(), comment, datetime.now().isoformat(), note_id))
    conn.commit()
    return conn.total_changes > 0

# --- UI Components ---
def render_filters_sidebar(options: dict) -> dict:
    """Render filter controls in sidebar."""
    st.sidebar.header("🔍 Фільтри")
    
    date_range = options.get("date_range", ("2022-02-24", "2026-10-06"))
    default_start = date_range[0] if date_range[0] else "2022-02-24"
    default_end = date_range[1] if date_range[1] else "2026-10-06"
    
    col1, col2 = st.sidebar.columns(2)
    with col1:
        start_date = st.date_input("Від", value=pd.to_datetime(default_start))
    with col2:
        end_date = st.date_input("До", value=pd.to_datetime(default_end))
    
    categories = st.sidebar.multiselect(
        "Категорії", 
        options.get("categories", []),
        default=options.get("categories", [])
    )
    
    sources = st.sidebar.multiselect(
        "Джерела",
        options.get("sources", []),
        default=options.get("sources", [])
    )
    
    min_confidence = st.sidebar.slider(
        "Мін. confidence", 0.0, 1.0, 0.7, 0.05
    )
    
    return {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "categories": categories,
        "sources": sources,
        "min_confidence": min_confidence
    }

def render_timeline_chart(df: pd.DataFrame, notes_df: pd.DataFrame):
    """Render interactive Plotly timeline."""
    st.subheader("📈 Динаміка готовності до миру (2022–2026)")
    
    if df.empty:
        st.warning("Немає даних для відображення. Запустіть Agent 1 для аналізу статей.")
        return
    
    # Monthly aggregation
    monthly = df.copy()
    monthly['month'] = monthly['date'].str[:7]
    agg = monthly.groupby(['month', 'metric_category']).agg(
        avg_score=('score', 'mean'),
        avg_sentiment=('overall_sentiment', 'mean'),
        avg_confidence=('confidence', 'mean'),
        count=('score', 'count')
    ).reset_index()
    
    # Plotly line chart
    fig = px.line(
        agg, x='month', y='avg_score', color='metric_category',
        title="Середній бал риторики за місяцями (-1.0 ескалація → +1.0 компроміс)",
        labels={'avg_score': 'Скор (-1.0 … +1.0)', 'month': 'Місяць', 'metric_category': 'Категорія'},
        markers=True
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5, annotation_text="Нейтрально")
    fig.add_hline(y=0.5, line_dash="dot", line_color="green", opacity=0.3, annotation_text="Компроміс")
    fig.add_hline(y=-0.5, line_dash="dot", line_color="red", opacity=0.3, annotation_text="Ескалація")
    fig.update_layout(
        height=500, 
        hovermode='x unified',
        xaxis_title="Місяць",
        yaxis_title="Середній бал",
        legend_title="Категорія"
    )
    
    # Add expert notes as vertical annotations
    if not notes_df.empty:
        for _, note in notes_df.iterrows():
            if note['event_date'][:7] in agg['month'].values:
                fig.add_vline(
                    x=note['event_date'][:7], 
                    line_dash="dot", line_color="red", opacity=0.3,
                    annotation_text=f"📝 {note['author_id'][:10]}", 
                    annotation_position="top"
                )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Category breakdown as subplots
    st.subheader("📊 По категоріях")
    cat_fig = px.line(
        agg, x='month', y='avg_score', color='metric_category',
        facet_col='metric_category', facet_col_wrap=2,
        labels={'avg_score': 'Скор', 'month': 'Місяць'},
        height=500
    )
    cat_fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.3)
    cat_fig.update_layout(showlegend=False)
    st.plotly_chart(cat_fig, use_container_width=True)

def render_expert_notes(user: dict):
    """Render expert notes with CRUD."""
    st.subheader("📝 Експертні замітки")
    
    notes_df = load_expert_notes()
    
    # Create new note
    with st.expander("➕ Створити замітку", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            event_date = st.date_input("Дата події", key="new_note_date")
            metric_cat = st.selectbox("Категорія", ["diplomacy", "security", "territorial", "humanitarian"], key="new_note_cat")
        with col2:
            importance = st.selectbox("Тип", ["COMMENT", "OFFICIALSTATEMENT"], key="new_note_imp")
        
        content = st.text_area("Зміст замітки", key="new_note_content", height=100)
        
        if st.button("Зберегти", key="save_note"):
            success, msg = create_note(user["id"], str(event_date), metric_cat, content, importance)
            if success:
                st.success(msg)
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(msg)
    
    # Display notes
    if notes_df.empty:
        st.info("Заміток поки немає")
        return
    
    # Filter notes for display
    display_notes = notes_df[
        (notes_df['status'].isin(['PUBLISHED', 'APPROVED'])) | 
        (notes_df['author_id'] == user["id"]) |
        (user["role"] == "Admin")
    ].copy()
    
    for _, note in display_notes.iterrows():
        with st.container():
            status_badge = {
                "PUBLISHED": "🟢 PUBLISHED",
                "APPROVED": "✅ APPROVED",
                "PENDING_APPROVAL": "🟡 PENDING",
                "REJECTED": "🔴 REJECTED",
                "DRAFT": "⚪ DRAFT",
                "ARCHIVED": "⚫ ARCHIVED"
            }.get(note['status'], note['status'])
            
            imp_badge = "📢 OFFICIAL" if note['importance_type'] == "OFFICIALSTATEMENT" else "💬 COMMENT"
            
            # Note card
            col1, col2, col3 = st.columns([4, 1, 1])
            with col1:
                st.markdown(f"""
                **{note['event_date']}** | {note['metric_category']} | {status_badge} | {imp_badge}  
                *Автор: {note['author_id']}*  
                {note['content']}
                """)
            
            with col2:
                if note['author_id'] == user["id"] or user["role"] == "Admin":
                    if st.button("✏️", key=f"edit_{note['id']}", help="Редагувати"):
                        st.session_state[f"editing_{note['id']}"] = True
                        st.rerun()
            
            with col3:
                can_delete = note['author_id'] == user["id"] or user["role"] == "Admin"
                if can_delete:
                    if st.button("🗑️", key=f"del_{note['id']}", help="Видалити"):
                        success, msg = delete_note(note['id'], user["id"], user["role"])
                        if success:
                            st.success(msg)
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error(msg)
            
            # Edit form
            if st.session_state.get(f"editing_{note['id']}", False):
                new_content = st.text_area("Редагувати", value=note['content'], key=f"edit_content_{note['id']}")
                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("💾 Зберегти", key=f"save_edit_{note['id']}"):
                        update_note(note['id'], new_content)
                        st.session_state[f"editing_{note['id']}"] = False
                        st.success("Оновлено")
                        st.cache_data.clear()
                        st.rerun()
                with col_b:
                    if st.button("❌ Скасувати", key=f"cancel_edit_{note['id']}"):
                        st.session_state[f"editing_{note['id']}"] = False
                        st.rerun()
            
            # Admin moderation
            if user["role"] == "Admin" and note['status'] == "PENDING_APPROVAL":
                st.markdown("---")
                col_m1, col_m2, col_m3 = st.columns(3)
                with col_m1:
                    if st.button("✅ Схвалити", key=f"approve_{note['id']}"):
                        moderate_note(note['id'], user["id"], "approve")
                        st.success("Схвалено")
                        st.cache_data.clear()
                        st.rerun()
                with col_m2:
                    if st.button("❌ Відхилити", key=f"reject_{note['id']}"):
                        moderate_note(note['id'], user["id"], "reject")
                        st.success("Відхилено")
                        st.cache_data.clear()
                        st.rerun()
                with col_m3:
                    mod_comment = st.text_input("Коментар модератора", key=f"mod_comment_{note['id']}")
            
            st.markdown("---")

def render_summary_tab():
    """Render analytical summaries tab."""
    st.subheader("📊 Аналітичні підсумки (Agent 2 - Synthesizer)")
    st.info("Agent 2 (Synthesizer) ще не реалізований. Поки що показуємо агреговані дані Agent 1.")
    
    monthly = load_monthly_aggregates()
    if not monthly.empty:
        pivot = monthly.pivot(index='month', columns='metric_category', values='avg_score')
        st.line_chart(pivot)
    else:
        st.warning("Немає агрегованих даних")

# --- Main ---
def main():
    st.title("🕊️ Peacetalk Tracker")
    st.caption("Діахронічний аналіз динаміки мирних переговорів 2022–2026")
    
    user = sidebar_auth()
    
    # Load filter options
    options = get_filter_options()
    filters = render_filters_sidebar(options)
    
    # Load data with filters
    df = load_assessments(
        start_date=filters["start_date"],
        end_date=filters["end_date"],
        categories=filters["categories"],
        sources=filters["sources"],
        min_confidence=filters["min_confidence"]
    )
    notes_df = load_expert_notes(
        start_date=filters["start_date"],
        end_date=filters["end_date"],
        categories=filters["categories"]
    )
    
    tabs = st.tabs(["📈 Timeline", "📝 Expert Notes", "📊 Summaries"])
    
    with tabs[0]:
        render_timeline_chart(df, notes_df)
        
        # Detail table
        with st.expander("📋 Детальні дані", expanded=False):
            if not df.empty:
                display_cols = ['date', 'source', 'title', 'metric_category', 'score', 'overall_sentiment', 'confidence']
                st.dataframe(
                    df[display_cols].sort_values('date', ascending=False),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "date": "Дата",
                        "source": "Джерело",
                        "title": "Заголовок",
                        "metric_category": "Категорія",
                        "score": st.column_config.NumberColumn("Скор", format="%.2f"),
                        "overall_sentiment": st.column_config.NumberColumn("Загальний", format="%.2f"),
                        "confidence": st.column_config.NumberColumn("Confidence", format="%.2f"),
                    }
                )
            else:
                st.info("Немає даних за обраними фільтрами")
    
    with tabs[1]:
        render_expert_notes(user)
    
    with tabs[2]:
        render_summary_tab()
    
    # Footer
    st.sidebar.markdown("---")
    st.sidebar.caption("Peacetalk Tracker v1.0")
    st.sidebar.caption("Agent 1: Analyst | Agent 2: Synthesizer (TODO)")
    st.sidebar.caption(f"DB: {DB_PATH}")

if __name__ == "__main__":
    main()