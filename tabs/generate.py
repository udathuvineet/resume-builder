import re
from datetime import datetime

import streamlit as st

from database.db import get_db
from database.models import (AnalysisSession, GPT4Suggestion, Requirement,
                              Resume, SessionStatus, Suggestion, UserProfile)
from services import ai_service, resume_generator


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:30] or "company"


def _build_filename(company: str, ext: str) -> str:
    slug = _slugify(company) if company.strip() else "resume"
    ts = datetime.now().strftime("%H%M")
    return f"resume_{slug}_{ts}.{ext}"


def _render_prep_summary(sugg_data: list[dict], req_map: dict[str, str]):
    """Show a grouped list of accepted changes so the user knows what to prep."""
    if not sugg_data:
        return

    st.subheader("Prep Summary")
    st.caption(
        "These are the changes applied to your resume. "
        "Review additions especially — they highlight topics to brush up on."
    )

    # Group by section
    by_section: dict[str, list[dict]] = {}
    for s in sugg_data:
        sec = (s.get("section") or "General").strip()
        by_section.setdefault(sec, []).append(s)

    for sec, items in sorted(by_section.items()):
        mods = [i for i in items if i["type"] == "MODIFY"]
        adds = [i for i in items if i["type"] == "ADD"]

        with st.expander(f"**{sec}** — {len(items)} change(s)", expanded=False):
            if mods:
                st.markdown("**Modifications**")
                for item in mods:
                    orig = item.get("original_text") or ""
                    new  = item.get("edited_text") or item.get("suggested_text") or ""
                    if orig:
                        st.markdown(f"- ~~{orig}~~  →  {new}")
                    else:
                        st.markdown(f"- {new}")

            if adds:
                st.markdown("**Additions** _(topics to prepare for)_")
                for item in adds:
                    new = item.get("edited_text") or item.get("suggested_text") or ""
                    st.markdown(f"- {new}")


def render():
    st.header("Generate")
    st.caption(
        "Generates using all accepted suggestions from the Review tab. "
        "The Refine tab is optional — skip it if you don't need GPT-4o suggestions."
    )

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

    col_sel, col_co = st.columns([3, 2])
    with col_sel:
        selected_label = st.selectbox(
            "Analysis session", list(options.keys()), index=default_idx,
            key="session_select_generate",
        )
    with col_co:
        company = st.text_input(
            "Target company", placeholder="e.g. Google",
            key="target_company",
        )

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
        selected_gpt4 = (
            db.query(GPT4Suggestion)
            .filter_by(session_id=selected_id, is_selected=True)
            .all()
        )
        all_selected = list(selected_suggs) + list(selected_gpt4)
        req_ids = list({s.requirement_id for s in all_selected})
        req_rows = (
            db.query(Requirement)
            .filter(Requirement.id.in_(req_ids))
            .all()
            if req_ids else []
        )
        req_map = {r.id: r.text for r in req_rows}

        sugg_data = [{
            "type": s.type.value,
            "original_text": s.original_text,
            "suggested_text": s.suggested_text,
            "edited_text": s.edited_text,
            "section": s.section,
            "requirement_id": s.requirement_id,
        } for s in all_selected]

        if not resumes:
            st.warning("Upload a resume in the **Library** tab first.")
            return

        primary_resume   = resumes[0].content
        primary_bytes    = bytes(resumes[0].pdf_data) if resumes[0].pdf_data else None
        primary_filename = resumes[0].filename
        profile_row = db.query(UserProfile).filter_by(id="default").first()
        profile = {
            "name":     profile_row.name     or "" if profile_row else "",
            "email":    profile_row.email    or "" if profile_row else "",
            "phone":    profile_row.phone    or "" if profile_row else "",
            "linkedin": profile_row.linkedin or "" if profile_row else "",
            "location": profile_row.location or "" if profile_row else "",
        }
        session_row = db.query(AnalysisSession).filter_by(id=selected_id).first()
        jd_text = session_row.job_description if session_row else ""

    claude_count = len(selected_suggs)
    gpt4_count = len(selected_gpt4)
    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("Total accepted", len(sugg_data))
    col_m2.metric("From Claude", claude_count)
    col_m3.metric("From GPT-4o", gpt4_count)

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
            ai_service.stream_resume_generation(primary_resume, sugg_data, profile, jd_text)
        )
        st.session_state["generated_resume"] = generated
        st.session_state["generated_resume_source"] = (primary_bytes, primary_filename)
        st.session_state["generated_sugg_data"] = sugg_data
        st.session_state["generated_req_map"]   = req_map

        with get_db() as db:
            s = db.query(AnalysisSession).filter_by(id=selected_id).first()
            if s:
                s.status = SessionStatus.COMPLETE

        st.rerun()

    # ── Prep summary + Download ───────────────────────────────────────────────
    generated_text: str | None = st.session_state.get("generated_resume")
    if generated_text:
        saved_sugg = st.session_state.get("generated_sugg_data", sugg_data)
        saved_reqs = st.session_state.get("generated_req_map", req_map)

        _render_prep_summary(saved_sugg, saved_reqs)

        st.subheader("Download")
        st.text_area(
            "Resume text (editable)",
            value=generated_text,
            height=350,
            key="generated_resume_edit",
        )
        final_text = st.session_state.get("generated_resume_edit", generated_text)
        orig_bytes, orig_filename = st.session_state.get(
            "generated_resume_source", (primary_bytes, primary_filename)
        )

        co = st.session_state.get("target_company", company)
        docx_name = _build_filename(co, "docx")
        pdf_name  = _build_filename(co, "pdf")

        col1, col2 = st.columns(2)
        with col1:
            docx_bytes = resume_generator.generate_docx(
                final_text, orig_bytes, orig_filename
            )
            st.download_button(
                "⬇ Download DOCX",
                data=docx_bytes,
                file_name=docx_name,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
        with col2:
            pdf_bytes = resume_generator.generate_pdf(final_text)
            st.download_button(
                "⬇ Download PDF",
                data=pdf_bytes,
                file_name=pdf_name,
                mime="application/pdf",
                use_container_width=True,
            )
