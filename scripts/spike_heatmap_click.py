import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ui.charts import CAT_LABEL, CATEGORIES, articles_for_cell, cell_from_event, heatmap_figure  # noqa: E402

months = pd.date_range("2023-01-01", periods=6, freq="MS")
rows = [
    (f"a{i}-{c}", m + pd.Timedelta(days=i), m, c, 0.3 * ((i + k) % 5) - 0.5)
    for i, m in enumerate(months) for k, c in enumerate(CATEGORIES)
]
df = pd.DataFrame(rows, columns=["article_id", "date", "month", "metric_category", "score"])

st.title("Spike PT-78: клік по теплокарті")
variant = st.radio("Варіант графіка", ["Лише go.Heatmap", "Heatmap + прозорий шар для кліку (як у продукті)"])
if variant.startswith("Лише"):
    piv = df.groupby(["metric_category", "month"])["score"].mean().unstack("month").reindex(list(CATEGORIES))
    fig = go.Figure(go.Heatmap(z=piv.values, x=piv.columns, y=[CAT_LABEL[c] for c in piv.index],
                               colorscale="RdYlGn", zmid=0, zmin=-1, zmax=1))
    fig.update_layout(yaxis=dict(autorange="reversed"), height=300)
else:
    fig = heatmap_figure(df)

event = st.plotly_chart(fig, width="stretch", key=f"spike_{variant[:5]}", on_select="rerun", selection_mode="points")
st.subheader("Сирий результат події")
st.write(event)
cell = cell_from_event(event)
st.subheader("Визначена клітинка")
st.write(f"{cell[0]} · {cell[1]:%m.%Y}" if cell else "Клітинку не визначено (кліку ще не було або вибір не підтримується)")
if cell:
    st.dataframe(articles_for_cell(df, *cell))
