import streamlit as st

from database.db import init_db
from tabs import analyze, generate, library, review

st.set_page_config(
    page_title="Resume Builder",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)

init_db()

st.title("Resume Builder")
st.caption("AI-powered resume optimization — upload, analyze, review, and download.")

tab1, tab2, tab3, tab4 = st.tabs(["📚 Library", "🔍 Analyze", "📝 Review", "⬇ Generate"])

with tab1:
    library.render()

with tab2:
    analyze.render()

with tab3:
    review.render()

with tab4:
    generate.render()
