import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime
from typing import Optional

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
    return sqlite3.connect(DB_PATH, check_same_thread=False)

@st.cache_data(ttl=60)
def load_assessments() -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT 
            a.id as article_id,
            a.source,
            a.title,
            a.date,
            ar.metric_category,
            ar.score,
            ar.justification,
            ar.confidence,
            ar.overall_sentiment,
            ar.is_compromise_oriented
        FROM assessment_records ar
        JOIN news_articles a ON ar.article_id = a.id
        WHERE ar.confidence >= 0.7
        ORDER BY a.date
    """
    return pd.read_sql_query(query, conn)

@st.cache_data(ttl=60)
def load_expert_notes() -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT id, event_date, metric_category, content, author_id, 
               importance_type, status, created_at
        FROM expert_notes
        WHERE status IN ('PUBLISHED', 'APPROVED')
        ORDER BY event_date DESC
    """
    return pd.read_sql_query(query, conn)

@st.cache_data(ttl=60)
def load_monthly_aggregates() -> pd.DataFrame:
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

# --- Auth / Role Switcher (Demo) ---
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
                content: str, importance_type: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO expert_notes (event_date, metric_category, content, author_id, importance_type, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (event_date, metric_category, content, user_id, importance_type, 
              "PENDING_APPROVAL" if importance_type == "OFFICIALSTATEMENT" else "PUBLISHED"))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

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

def delete_note(note_id: int, user_id: str, user_role: str) -> bool:
    conn = get_connection()
    if user_role == "Admin":
        conn.execute("DELETE FROM expert_notes WHERE id = ?", (note_id,))
    else:
        conn.execute("DELETE FROM expert_notes WHERE id = ? AND author_id = ?", (note_id, user_id))
    conn.commit()
    return conn.total_changes > 0

def moderate_note(note_id: int, admin_id: str, action: str, comment: str = "") -> bool:
    """Admin only: APPROVE or REJECT"""
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
def render_timeline(df: pd.DataFrame, notes_df: pd.DataFrame):
    st.subheader("📈 Динаміка готовності до миру (2022–2026)")
    
    if df.empty:
        st.warning("Немає даних для відображення. Запустіть Agent 1 для аналізу статей.")
        return
    
    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        years = sorted(df['date'].str[:4].unique())
        selected_years = st.multiselect("Роки", years, default=years)
    with col2:
        categories = sorted(df['metric_category'].unique())
        selected_cats = st.multiselect("Категорії", categories, default=categories)
    with col3:
        min_conf = st.slider("Мін. confidence", 0.0, 1.0, 0.7, 0.05)
    
    filtered = df[
        (df['date'].str[:4].isin(selected_years)) &
        (df['metric_category'].isin(selected_cats)) &
        (df['confidence'] >= min_conf)
    ]
    
    if filtered.empty:
        st.info("Немає даних за обраними фільтрами")
        return
    
    # Monthly aggregation for plotting
    monthly = filtered.copy()
    monthly['month'] = monthly['date'].str[:7]
    agg = monthly.groupby(['month', 'metric_category']).agg(
        avg_score=('score', 'mean'),
        avg_sentiment=('overall_sentiment', 'mean'),
        count=('score', 'count')
    ).reset_index()
    
    # Plotly line chart
    fig = px.line(
        agg, x='month', y='avg_score', color='metric_category',
        title="Середній бал риторики за місяцями",
        labels={'avg_score': 'Скор (-1..+1)', 'month': 'Місяць', 'metric_category': 'Категорія'},
        markers=True
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig.update_layout(height=500, hovermode='x unified')
    
    # Add expert notes as annotations
    if not notes_df.empty:
        for _, note in notes_df.iterrows():
            if note['event_date'][:7] in agg['month'].values:
                fig.add_vline(
                    x=note['event_date'][:7], 
                    line_dash="dot", line_color="red", opacity=0.3,
                    annotation_text=f"📝 {note['author_id']}", 
                    annotation_position="top"
                )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Detail table
    with st.expander("📋 Детальні дані"):
        st.dataframe(
            filtered[['date', 'source', 'title', 'metric_category', 'score', 'overall_sentiment', 'confidence']],
            use_container_width=True,
            hide_index=True
        )

def render_expert_notes(user):
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
            if create_note(user["id"], str(event_date), metric_cat, content, importance):
                st.success("Замітку збережено!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("Помилка: у вас вже є замітка на цю дату/категорію")
    
    # Display notes
    if notes_df.empty:
        st.info("Заміток поки немає")
        return
    
    for _, note in notes_df.iterrows():
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                status_badge = {
                    "PUBLISHED": "🟢 PUBLISHED",
                    "APPROVED": "✅ APPROVED",
                    "PENDING_APPROVAL": "🟡 PENDING",
                    "REJECTED": "🔴 REJECTED",
                    "DRAFT": "⚪ DRAFT",
                    "ARCHIVED": "⚫ ARCHIVED"
                }.get(note['status'], note['status'])
                
                imp_badge = "📢 OFFICIAL" if note['importance_type'] == "OFFICIALSTATEMENT" else "💬 COMMENT"
                
                st.markdown(f"""
                **{note['event_date']}** | {note['metric_category']} | {status_badge} | {imp_badge}  
                *Автор: {note['author_id']}*  
                {note['content']}
                """)
            
            with col2:
                if note['author_id'] == user["id"] or user["role"] == "Admin":
                    if st.button("✏️ Редагувати", key=f"edit_{note['id']}"):
                        st.session_state[f"editing_{note['id']}"] = True
                        st.rerun()
            
            with col3:
                can_delete = note['author_id'] == user["id"] or user["role"] == "Admin"
                if can_delete:
                    if st.button("🗑️ Видалити", key=f"del_{note['id']}"):
                        if delete_note(note['id'], user["id"], user["role"]):
                            st.success("Видалено")
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            st.error("Немає прав")
            
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

def render_summary():
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
    
    tabs = st.tabs(["📈 Timeline", "📝 Expert Notes", "📊 Summaries"])
    
    with tabs[0]:
        df = load_assessments()
        notes_df = load_expert_notes()
        render_timeline(df, notes_df)
    
    with tabs[1]:
        render_expert_notes(user)
    
    with tabs[2]:
        render_summary()
    
    # Footer
    st.sidebar.markdown("---")
    st.sidebar.caption("Peacetalk Tracker v1.0")
    st.sidebar.caption("Agent 1: Analyst | Agent 2: Synthesizer (TODO)")

if __name__ == "__main__":
    main()