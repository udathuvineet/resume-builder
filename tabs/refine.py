import uuid

import streamlit as st

from database.db import get_db
from database.models import (AnalysisSession, RefineVerdict, RefinedSuggestion,
                              Requirement, Resume, Suggestion)
from services import ai_service

_VERDICT_CONFIG = {
    "approved": ("✅", "Approved",  "#28a745"),
    "improved": ("✏️", "Improved",  "#0d6efd"),
    "flagged":  ("⚠️", "Flagged",   "#dc3545"),
}


def _apply_refinement(refined_id: str, improved_text: str, sugg_id: str):
    with get_db() as db:
        r = db.query(RefinedSuggestion).filter_by(id=refined_id).first()
        if r:
            r.is_applied = True
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.edited_text = improved_text


def _delete_refinements(session_id: str):
    with get_db() as db:
        db.query(RefinedSuggestion).filter_by(session_id=session_id).delete()


def render():
    st.header("GPT-4 Refinement")
    st.caption(
        "GPT-4 independently reviews Claude's suggestions and either approves, "
        "improves, or flags each one. Apply the GPT-4 version to override Claude's wording."
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
        st.info("No analyses yet. Run one in the **Analyze** tab.")
        return

    current_id = st.session_state.get("current_session_id")
    ids = list(options.values())
    default_idx = ids.index(current_id) if current_id in ids else 0
    selected_label = st.selectbox("Analysis session", list(options.keys()), index=default_idx)
    selected_id = options[selected_label]
    st.session_state["current_session_id"] = selected_id

    # ── Load suggestions + existing refinements ───────────────────────────────
    with get_db() as db:
        session_row = db.query(AnalysisSession).filter_by(id=selected_id).first()
        jd_text = session_row.job_description if session_row else ""

        resumes = db.query(Resume).order_by(Resume.order).all()
        resume_text = resumes[0].content if resumes else ""

        accepted_suggs = (
            db.query(Suggestion)
            .filter_by(session_id=selected_id, is_selected=True)
            .all()
        )
        req_ids = list({s.requirement_id for s in accepted_suggs})
        req_map = {
            r.id: r.text
            for r in db.query(Requirement).filter(Requirement.id.in_(req_ids)).all()
        } if req_ids else {}

        sugg_data = [{
            "id": s.id,
            "type": s.type.value,
            "original_text": s.original_text,
            "suggested_text": s.suggested_text,
            "edited_text": s.edited_text,
            "section": s.section,
            "requirement_text": req_map.get(s.requirement_id, ""),
        } for s in accepted_suggs]

        existing_refinements = (
            db.query(RefinedSuggestion)
            .filter_by(session_id=selected_id)
            .all()
        )
        refined_map: dict[str, dict] = {
            r.suggestion_id: {
                "id": r.id,
                "verdict": r.verdict.value,
                "improved_text": r.improved_text,
                "critique": r.critique,
                "is_applied": r.is_applied,
            }
            for r in existing_refinements
        }

    if not sugg_data:
        st.warning(
            "No accepted suggestions found for this session. "
            "Go to the **Review** tab and accept suggestions first."
        )
        return

    st.metric("Accepted suggestions to review", len(sugg_data))

    # ── Run / Re-run ──────────────────────────────────────────────────────────
    has_refinements = bool(refined_map)
    btn_label = "Re-run GPT-4 Review" if has_refinements else "Run GPT-4 Review"

    col_btn, col_info = st.columns([2, 5])
    with col_btn:
        run = st.button(btn_label, type="primary")
    with col_info:
        if has_refinements:
            n_imp = sum(1 for r in refined_map.values() if r["verdict"] == "improved")
            n_flag = sum(1 for r in refined_map.values() if r["verdict"] == "flagged")
            n_ok = sum(1 for r in refined_map.values() if r["verdict"] == "approved")
            st.caption(f"Last run: ✅ {n_ok} approved  ✏️ {n_imp} improved  ⚠️ {n_flag} flagged")

    if run:
        if not jd_text or not resume_text:
            st.error("Missing resume or job description data.")
            st.stop()

        _delete_refinements(selected_id)
        refined_map = {}

        with st.status("GPT-4 is reviewing suggestions...", expanded=True) as status:
            try:
                result = ai_service.refine_suggestions_with_gpt4(
                    resume_text, jd_text, sugg_data
                )
                refinements = result.get("refinements", [])
                valid_ids = {s["id"] for s in sugg_data}
                valid_verdicts = {v.value for v in RefineVerdict}

                with get_db() as db:
                    for ref in refinements:
                        sid = ref.get("suggestion_id", "")
                        v = ref.get("verdict", "").lower()
                        if sid not in valid_ids or v not in valid_verdicts:
                            continue
                        rid = str(uuid.uuid4())
                        db.add(RefinedSuggestion(
                            id=rid,
                            session_id=selected_id,
                            suggestion_id=sid,
                            verdict=RefineVerdict(v),
                            improved_text=ref.get("improved_text"),
                            critique=ref.get("critique", ""),
                        ))
                        refined_map[sid] = {
                            "id": rid,
                            "verdict": v,
                            "improved_text": ref.get("improved_text"),
                            "critique": ref.get("critique", ""),
                            "is_applied": False,
                        }

                status.update(label="GPT-4 review complete!", state="complete")
            except EnvironmentError as e:
                status.update(label="Error", state="error")
                st.error(str(e))
                st.info("Set the `OPENAI_API_KEY` environment variable and restart.")
                st.stop()
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"GPT-4 call failed: {e}")
                st.stop()

        st.rerun()

    if not refined_map:
        return

    st.divider()

    # ── Display refinements grouped by verdict ────────────────────────────────
    for verdict_key in ("flagged", "improved", "approved"):
        icon, label, color = _VERDICT_CONFIG[verdict_key]
        items = [
            (s, refined_map[s["id"]])
            for s in sugg_data
            if s["id"] in refined_map and refined_map[s["id"]]["verdict"] == verdict_key
        ]
        if not items:
            continue

        st.subheader(f"{icon} {label} ({len(items)})")

        for sugg, ref in items:
            section = sugg.get("section") or "General"
            req_preview = sugg["requirement_text"][:80] + "..." if len(sugg["requirement_text"]) > 80 else sugg["requirement_text"]

            with st.expander(
                f"`{section}` — {req_preview}",
                expanded=(verdict_key != "approved"),
            ):
                st.markdown(f"<span style='color:{color};font-weight:bold'>{icon} {label}</span>", unsafe_allow_html=True)
                st.caption(ref["critique"])

                if verdict_key == "approved":
                    st.markdown("**Claude's suggestion** _(no changes needed)_")
                    st.info(sugg.get("edited_text") or sugg["suggested_text"])

                elif verdict_key == "improved":
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.markdown("**Claude's version**")
                        st.info(sugg.get("edited_text") or sugg["suggested_text"])
                    with col_b:
                        st.markdown("**GPT-4's version**")
                        st.success(ref["improved_text"] or "")

                    if not ref["is_applied"]:
                        if st.button(
                            "Apply GPT-4 version",
                            key=f"apply_{ref['id']}",
                            type="primary",
                        ):
                            _apply_refinement(ref["id"], ref["improved_text"], sugg["id"])
                            st.success("Applied — this version will be used in Generate.")
                            st.rerun()
                    else:
                        st.success("✓ GPT-4 version applied")

                elif verdict_key == "flagged":
                    st.markdown("**Claude's suggestion** _(may not help)_")
                    st.warning(sugg.get("edited_text") or sugg["suggested_text"])
                    st.caption(
                        "Consider unchecking this in the **Review** tab "
                        "or rewriting it manually."
                    )

        st.divider()
