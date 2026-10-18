import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Granite Document Intelligence", layout="wide")

with st.sidebar:
    st.subheader("Output", anchor=False)
    st.toggle("Chart extraction", True)
    st.toggle("Chunks for RAG", True)
    st.toggle("Save page images", False)
    st.selectbox("Text source", ["VLM (granite-docling)", "PDF text layer"])
    st.number_input("Chunk tokens", 64, 8192, 512, step=64)
    st.caption("granite-docling-258M · granite-vision-4.1-4b (8-bit) · MLX")

st.title("Granite Document Intelligence", anchor=False)
with st.container(horizontal=True, vertical_alignment="center"):
    st.button("Convert", icon=":material/play_arrow:", type="primary")
    st.caption("report.pdf · 24 pages · charts, tables, chunks")

m = st.columns(4)
for col, (k, v) in zip(
    m, [("Pages", 24), ("Tables", 6), ("Charts", 4), ("Chunks", 112)]
):
    col.metric(k, v)

st.success(
    "Converted report.pdf in 3 min 12 s · peak 7.9 GB", icon=":material/check_circle:"
)
st.warning(
    "Page 17 hit max_tokens — likely a repetition loop.", icon=":material/warning:"
)
st.error("scan-2019.pdf: 612 pages exceeds --max-pages 500.", icon=":material/error:")
st.info("KVP ran on page 1 only; pass --pages all to widen.", icon=":material/info:")

st.tabs(
    [
        ":material/description: Document",
        ":material/table: Tables",
        ":material/bar_chart: Charts",
        ":material/key: Key-values",
        ":material/segment: Chunks",
        ":material/monitoring: Run",
    ]
)

rng = np.random.default_rng(0)
left, right = st.columns(2)
with left:
    st.subheader("Chart 1 — re-plotted from chart2csv", anchor=False)
    years = [str(y) for y in range(2019, 2025)]
    series = ["Revenue", "Costs", "Margin", "R&D", "Capex", "Opex", "Tax", "Other"]
    df = pd.DataFrame(
        [(y, s, float(rng.integers(20, 100))) for y in years for s in series],
        columns=["year", "series", "value"],
    )
    st.altair_chart(
        alt.Chart(df, mark="bar").encode(
            x="year:N", xOffset="series:N", y="value:Q", color="series:N"
        ),
        width="stretch",
    )
with right:
    st.subheader("Layout confidence by page (sequential)", anchor=False)
    hm = pd.DataFrame(
        [
            (p, r, float(rng.random()))
            for p in range(1, 13)
            for r in ["text", "table", "picture", "formula"]
        ],
        columns=["page", "region", "score"],
    )
    st.altair_chart(
        alt.Chart(hm, mark="rect").encode(
            x="page:O", y="region:N", color=alt.Color("score:Q")
        ),
        width="stretch",
    )

left, right = st.columns(2)
with left:
    st.subheader("Change vs previous run (diverging)", anchor=False)
    dv = pd.DataFrame(
        {"metric": [f"m{i}" for i in range(10)], "delta": np.linspace(-1, 1, 10)}
    )
    st.altair_chart(
        alt.Chart(dv, mark="bar").encode(
            x="metric:N",
            y="delta:Q",
            color=alt.Color("delta:Q", scale=alt.Scale(range="diverging", domainMid=0)),
        ),
        width="stretch",
    )
with right:
    st.subheader("Table 2 — Quarterly results", anchor=False)
    st.dataframe(
        pd.DataFrame(
            {
                "Quarter": ["Q1", "Q2", "Q3", "Q4"],
                "Revenue": [12.4, 13.1, 14.8, 15.2],
                "Margin %": [31.2, 32.0, 29.5, 33.1],
            }
        ),
        hide_index=True,
    )
    st.markdown(
        "Invoice total: **$1,240.00** :green-badge[valid] &nbsp; Due date: :red-badge[missing] &nbsp; :violet-badge[page 1]"
    )
    st.code(
        "import matplotlib.pyplot as plt\nplt.bar(['2019', '2020'], [41, 57])  # generated — untrusted",
        language="python",
    )
