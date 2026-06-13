import streamlit as st

from database.db import get_db
from database.models import AnalysisSession, Resume, SessionStatus, Suggestion
from services import ai_service, resume_generator


def render():
    st.header("Generate")

    # ── Session selector ──────────────────────────────────────────────────────
    with get_db() as db:
        sessions = (
            db.query(AnalysisSession)
            .order_by(AnalysisSession.created_at.desc())
            .all()
        )
        options = {f"{s.job_description[:70]}...": s.id for s in sessions}

    if not options:
        st.info("No analyses yet. Complete one in the **Analyze** tab.")
        return

    current_id = st.session_state.get("current_session_id")
    ids = list(options.values())
    default_idx = ids.index(current_id) if current_id in ids else 0

    selected_label = st.selectbox("Analysis session", list(options.keys()), index=default_idx)
    selected_id = options[selected_label]
    st.session_state["current_session_id"] = selected_id

    # ── Load data ─────────────────────────────────────────────────────────────
    with get_db() as db:
        resumes = db.query(Resume).order_by(Resume.order).all()
        selected_suggs = (
            db.query(Suggestion)
            .filter_by(session_id=selected_id, is_selected=True)
            .all()
        )
        sugg_data = [{
            "type": s.type.value,
            "original_text": s.original_text,
            "suggested_text": s.suggested_text,
            "edited_text": s.edited_text,
            "section": s.section,
        } for s in selected_suggs]
        primary_resume = resumes[0].content if resumes else ""

    if not resumes:
        st.warning("Upload a resume in the **Library** tab first.")
        return

    st.metric("Accepted improvements", len(sugg_data))

    if not sugg_data:
        st.info(
            "No improvements selected. Go to the **Review** tab to accept suggestions, "
            "or generate the resume as-is below."
        )

    # ── Generate ──────────────────────────────────────────────────────────────
    if st.button("Generate Updated Resume", type="primary"):
        with get_db() as db:
            s = db.query(AnalysisSession).filter_by(id=selected_id).first()
            if s:
                s.status = SessionStatus.GENERATING

        st.subheader("Generated Resume")
        generated = st.write_stream(
            ai_service.stream_resume_generation(primary_resume, sugg_data)
        )
        st.session_state["generated_resume"] = generated

        with get_db() as db:
            s = db.query(AnalysisSession).filter_by(id=selected_id).first()
            if s:
                s.status = SessionStatus.COMPLETE

        st.rerun()

    # ── Download (shown after generation) ────────────────────────────────────
    generated_text: str | None = st.session_state.get("generated_resume")
    if generated_text:
        st.subheader("Download")
        st.text_area(
            "Resume text (editable)",
            value=generated_text,
            height=350,
            key="generated_resume_edit",
        )
        # Use whatever the user may have edited in the text area
        final_text = st.session_state.get("generated_resume_edit", generated_text)

        col1, col2 = st.columns(2)
        with col1:
            docx_bytes = resume_generator.generate_docx(final_text)
            st.download_button(
                "⬇ Download DOCX",
                data=docx_bytes,
                file_name="resume_updated.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with col2:
            pdf_bytes = resume_generator.generate_pdf(final_text)
            st.download_button(
                "⬇ Download PDF",
                data=pdf_bytes,
                file_name="resume_updated.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
