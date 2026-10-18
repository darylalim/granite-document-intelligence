"""Responsive layout prototype for the v1.x UI (docs/plans/ui-layout/).

Streamlit tells the script nothing about the viewport (st.context has no width), so
nothing here branches on screen size. Every adaptation is the browser's: horizontal
containers that wrap, and integer widths that act as a *maximum* and shrink to fit.
"""

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Granite Document Intelligence", layout="wide")
rng = np.random.default_rng(0)

VIEWS = [
    ":material/description: Document",
    ":material/table: Tables",
    ":material/bar_chart: Charts",
    ":material/key: Key-values",
    ":material/segment: Chunks",
    ":material/monitoring: Run",
]
COUNT_WIDTH = 130  # 4 in a row from 574 px of content; 2x2 on a phone
CARD_WIDTH = 344  # 1/row on phones and at 1024 with the sidebar; 2 at 768 and 1280; 3 at 1920; 5 at 2560
PROSE_WIDTH = 760  # a readable line on wide displays; shrinks to fit on narrow ones


def _keep_view() -> None:
    # A single-select pill can be clicked off, which would leave no view selected;
    # put the last one back instead of silently falling back to the first.
    if st.session_state.view is None:
        st.session_state.view = st.session_state.get("_last_view", VIEWS[0])
    st.session_state._last_view = st.session_state.view


# Sidebar: settings only — it collapses at <= 768 px, so nothing essential lives here.
with st.sidebar:
    st.subheader("Output", anchor=False)
    st.toggle("Chart extraction", True)
    st.toggle("Chunks for RAG", True)
    st.selectbox("Text source", ["VLM (granite-docling)", "PDF text layer"])
    st.number_input("Chunk tokens", 64, 8192, 512, step=64)

st.title("Granite Document Intelligence", anchor=False)

# Run row: wraps the caption under the button on a phone, one line everywhere else.
with st.container(horizontal=True, vertical_alignment="center", key="run_row"):
    st.button("Convert", icon=":material/play_arrow:", type="primary")
    st.caption("report.pdf · 24 pages · charts, tables, chunks")

with st.container(horizontal=True, gap="small", key="counts"):
    for label, value in [("Pages", 24), ("Tables", 6), ("Charts", 5), ("Chunks", 112)]:
        st.metric(label, value, border=True, width=COUNT_WIDTH)

notices = st.empty()
notices.success(
    "Converted report.pdf in 3 min 12 s · peak 7.9 GB", icon=":material/check_circle:"
)

# View switcher: pills wrap onto a second row on a phone, where st.tabs hid two of six
# labels behind a scroll chevron. Only the selected view runs (lazy by construction).
view = st.pills(
    "View",
    VIEWS,
    default=VIEWS[2],
    key="view",
    on_change=_keep_view,
    label_visibility="collapsed",
    bind="query-params",
)

if view == VIEWS[0]:
    with st.container(width=PROSE_WIDTH, key="prose"):
        st.subheader("1 Introduction", anchor=False)
        st.text("Granite Document Intelligence converts PDFs on a Mac. " * 12)
elif view == VIEWS[2]:
    # Card grid: a wrapping row of fixed-width cards — the number per row follows the
    # display without a breakpoint in sight.
    with st.container(horizontal=True, gap="medium", key="chart_grid"):
        for i in range(5):
            with st.container(border=True, width=CARD_WIDTH, key=f"card{i}"):
                st.markdown(f"**Chart {i + 1}** · page {i * 3 + 2}")
                df = pd.DataFrame(
                    {
                        "year": [str(y) for y in range(2019, 2025)],
                        "value": rng.integers(20, 100, 6),
                    }
                )
                st.altair_chart(
                    alt.Chart(df, mark="bar").encode(x="year:N", y="value:Q"),
                    width="stretch",
                    height=200,
                )
                with st.expander("CSV and summary"):
                    st.dataframe(df, hide_index=True, width="content")
else:
    # Tables keep their natural width (no 2000 px stretched columns on a wide display)
    # and scroll inside themselves on a phone.
    st.dataframe(
        pd.DataFrame(
            rng.integers(0, 100, (5, 8)), columns=[f"Column {c}" for c in "ABCDEFGH"]
        ),
        width="content",
    )
