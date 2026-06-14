import uuid

import streamlit as st

from database.db import get_db
from database.models import (AnalysisSession, AuditVerdict, ContentAuditItem,
                              ProjectsDocument, Requirement, Resume,
                              SessionStatus, Suggestion, SuggestionType)
from services import ai_service

_SUGGESTION_THRESHOLD = 0.8


def render():
    st.header("Analyze")

    with get_db() as db:
        resume_count = db.query(Resume).count()
        resumes = db.query(Resume).order_by(Resume.order).all()
        resume_texts = [r.content for r in resumes]
        projects_texts = [p.content for p in db.query(ProjectsDocument).all()]
        sessions = (
            db.query(AnalysisSession)
            .order_by(AnalysisSession.created_at.desc())
            .all()
        )
        past_sessions = [
            (s.id,
             f"{s.created_at.strftime('%m/%d %H:%M') if s.created_at else '?'} — {s.job_description[:55]}...",
             s.status.value, s.overall_score)
            for s in sessions
        ]

    if resume_count == 0:
        st.warning("Upload at least one resume in the **Library** tab first.")
        return

    st.info(f"{resume_count} resume(s) loaded. The first (starred) will be used as the primary.")

    jd = st.text_area(
        "Paste the full job description",
        height=280,
        placeholder="Copy and paste the job posting here...",
    )

    if st.button("Analyze", type="primary", disabled=not jd.strip()):
        _run_analysis(jd.strip(), resume_texts, projects_texts)

    # ── Past sessions ─────────────────────────────────────────────────────────
    if past_sessions:
        st.divider()
        st.subheader("Past Analyses")
        for sid, preview, status, score in past_sessions:
            c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
            c1.caption(preview)
            c2.caption(status)
            if score is not None:
                c3.caption(f"{score:.0%}")
            if c4.button("Load", key=f"load_{sid}"):
                st.session_state["current_session_id"] = sid
                st.success("Session loaded — switch to the Review tab.")


def _run_analysis(jd: str, resume_texts: list[str], projects_texts: list[str]):
    session_id = str(uuid.uuid4())

    with get_db() as db:
        db.add(AnalysisSession(
            id=session_id,
            job_description=jd,
            status=SessionStatus.ANALYZING,
        ))

    st.session_state["current_session_id"] = session_id
    # Clear selectbox widget state in other tabs so they pick up the new session
    for _k in ["session_select_review", "session_select_refine", "session_select_generate"]:
        st.session_state.pop(_k, None)

    with st.status("Running analysis...", expanded=True) as status_widget:
        try:
            # ── Step 1: extract requirements & scores ─────────────────────────
            st.write("Extracting and scoring requirements...")
            result = ai_service.analyze_resume(resume_texts, jd, projects_texts)

            requirements_raw = result.get("requirements", [])
            overall_score = float(result.get("overall_score", 0.0))

            st.write(f"Found **{len(requirements_raw)}** requirements — overall match **{overall_score:.0%}**")

            # Save requirements
            saved_reqs: list[dict] = []
            with get_db() as db:
                for i, req in enumerate(requirements_raw):
                    rid = str(uuid.uuid4())
                    db.add(Requirement(
                        id=rid,
                        session_id=session_id,
                        text=req.get("text", ""),
                        category=req.get("category", "general"),
                        match_score=float(req.get("match_score", 0.0)),
                        match_detail=req.get("match_detail", ""),
                        order=i,
                    ))
                    saved_reqs.append({
                        "id": rid,
                        "text": req.get("text", ""),
                        "match_score": float(req.get("match_score", 0.0)),
                        "match_detail": req.get("match_detail", ""),
                    })

                session = db.query(AnalysisSession).filter_by(id=session_id).first()
                session.status = SessionStatus.READY
                session.overall_score = overall_score

            # ── Step 2: detailed review (modifications + additions + removals) ──
            st.write("Running detailed review — modifications, additions, and removals...")
            review_result = ai_service.review_resume_detailed(resume_texts, jd, saved_reqs)

            modifications = review_result.get("modifications", [])
            additions     = review_result.get("additions", [])
            removals      = review_result.get("removals", [])

            # Sentinel requirement anchors all detailed-review suggestions (FK is NOT NULL)
            sentinel_id = str(uuid.uuid4())
            with get_db() as db:
                db.add(Requirement(
                    id=sentinel_id,
                    session_id=session_id,
                    text="[Detailed Review]",
                    category="sentinel",
                    match_score=0.0,
                    match_detail="",
                    order=9999,
                ))

                for m in modifications:
                    text = m.get("suggested_revision", "")
                    if not text:
                        continue
                    db.add(Suggestion(
                        id=str(uuid.uuid4()),
                        session_id=session_id,
                        requirement_id=sentinel_id,
                        original_text=m.get("current_text"),
                        suggested_text=text,
                        type=SuggestionType.MODIFY,
                        section=m.get("section"),
                        gap_addressed=m.get("gap_addressed"),
                        evidence_type=m.get("evidence_type"),
                        evidence_explanation=m.get("evidence_explanation"),
                        reasoning=m.get("reasoning"),
                        impact=m.get("impact"),
                    ))

                for a in additions:
                    bullet = a.get("suggested_bullet")
                    if not bullet:
                        # No evidence — save as placeholder so user sees the gap
                        bullet = f"[No evidence — {a.get('jd_requirement', 'gap')}]"
                    db.add(Suggestion(
                        id=str(uuid.uuid4()),
                        session_id=session_id,
                        requirement_id=sentinel_id,
                        original_text=None,
                        suggested_text=bullet,
                        type=SuggestionType.ADD,
                        section=a.get("section"),
                        gap_addressed=a.get("jd_requirement"),
                        evidence_type=a.get("evidence_type"),
                        evidence_explanation=a.get("evidence_explanation"),
                        reasoning=a.get("reasoning"),
                        impact=a.get("relevance"),
                    ))

                for r in removals:
                    action = r.get("suggested_action", "remove").lower()
                    if action not in ("remove", "shorten", "merge"):
                        continue
                    db.add(ContentAuditItem(
                        id=str(uuid.uuid4()),
                        session_id=session_id,
                        section=r.get("section"),
                        text=r.get("resume_point", ""),
                        verdict=AuditVerdict.REMOVE,
                        reason=r.get("reasoning", ""),
                        relevance=r.get("relevance"),
                        evidence_type=r.get("evidence_type"),
                        evidence_explanation=r.get("evidence_explanation"),
                        suggested_action=action,
                    ))

                s = db.query(AnalysisSession).filter_by(id=session_id).first()
                s.status = SessionStatus.COMPLETE

            total_sugg = len(modifications) + len(additions)
            total_rm   = len([r for r in removals if r.get("suggested_action") in ("remove", "shorten", "merge")])
            st.write(f"Generated **{total_sugg}** suggestions and **{total_rm}** removal recommendations.")

            # Keep only the 10 most recent sessions
            with get_db() as db:
                all_sessions = (
                    db.query(AnalysisSession)
                    .order_by(AnalysisSession.created_at.desc())
                    .all()
                )
                for old in all_sessions[10:]:
                    db.delete(old)

            status_widget.update(label="Analysis complete!", state="complete")
            st.success("Done — switch to the **Review** tab to see results.")

        except Exception as exc:
            with get_db() as db:
                s = db.query(AnalysisSession).filter_by(id=session_id).first()
                if s:
                    s.status = SessionStatus.PENDING
            status_widget.update(label="Analysis failed", state="error")
            st.error(f"Error: {exc}")
