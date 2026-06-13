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
            (s.id, s.job_description[:70] + "...", s.status.value, s.overall_score)
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

            # ── Step 2: generate suggestions for gaps ─────────────────────────
            needs_suggestions = [r for r in saved_reqs if r["match_score"] < _SUGGESTION_THRESHOLD]

            if needs_suggestions:
                st.write(f"Generating suggestions for **{len(needs_suggestions)}** gaps...")
                sugg_result = ai_service.generate_suggestions(needs_suggestions, resume_texts)
                by_req = sugg_result.get("suggestions_by_requirement", {})

                valid_ids = {r["id"] for r in needs_suggestions}
                with get_db() as db:
                    for req_id, suggs in by_req.items():
                        if req_id not in valid_ids:
                            continue
                        for s in suggs:
                            db.add(Suggestion(
                                id=str(uuid.uuid4()),
                                session_id=session_id,
                                requirement_id=req_id,
                                original_text=s.get("original_text"),
                                suggested_text=s.get("suggested_text", ""),
                                type=SuggestionType(s.get("type", "MODIFY")),
                                section=s.get("section"),
                            ))

                    s = db.query(AnalysisSession).filter_by(id=session_id).first()
                    s.status = SessionStatus.COMPLETE

            else:
                with get_db() as db:
                    s = db.query(AnalysisSession).filter_by(id=session_id).first()
                    s.status = SessionStatus.COMPLETE

            # ── Step 3: content audit ─────────────────────────────────────────
            st.write("Auditing existing resume content for relevance...")
            try:
                audit_result = ai_service.audit_resume_content(
                    resume_texts[0], jd
                )
                audit_items = audit_result.get("audit", [])
                valid_verdicts = {v.value for v in AuditVerdict}
                with get_db() as db:
                    for item in audit_items:
                        v = item.get("verdict", "").lower()
                        if v not in valid_verdicts:
                            continue
                        db.add(ContentAuditItem(
                            id=str(uuid.uuid4()),
                            session_id=session_id,
                            section=item.get("section"),
                            text=item.get("text", ""),
                            verdict=AuditVerdict(v),
                            reason=item.get("reason", ""),
                        ))
                flagged = len([i for i in audit_items
                               if i.get("verdict", "").lower() in valid_verdicts])
                st.write(f"Flagged **{flagged}** existing bullet(s) for review.")
            except Exception as audit_exc:
                st.warning(f"Content audit skipped: {audit_exc}")

            status_widget.update(label="Analysis complete!", state="complete")
            st.success("Done — switch to the **Review** tab to see results.")

        except Exception as exc:
            with get_db() as db:
                s = db.query(AnalysisSession).filter_by(id=session_id).first()
                if s:
                    s.status = SessionStatus.PENDING
            status_widget.update(label="Analysis failed", state="error")
            st.error(f"Error: {exc}")
