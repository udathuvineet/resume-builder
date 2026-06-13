import uuid

import streamlit as st

from database.db import get_db
from database.models import (AnalysisSession, GPT4Suggestion, RefineVerdict,
                              RefinedSuggestion, Requirement, Resume,
                              Suggestion, SuggestionType)
from services import ai_service

_SUGGESTION_THRESHOLD = 0.8

_VERDICT_CONFIG = {
    "approved": ("✅", "Approved", "#28a745"),
    "improved": ("✏️", "Improved", "#0d6efd"),
    "flagged":  ("⚠️", "Flagged",  "#dc3545"),
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _toggle_gpt4_sugg(sugg_id: str, key: str):
    with get_db() as db:
        s = db.query(GPT4Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.is_selected = st.session_state[key]


def _save_gpt4_edit(sugg_id: str, key: str):
    with get_db() as db:
        s = db.query(GPT4Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.edited_text = st.session_state[key]


def _apply_refinement(refined_id: str, improved_text: str, sugg_id: str):
    with get_db() as db:
        r = db.query(RefinedSuggestion).filter_by(id=refined_id).first()
        if r:
            r.is_applied = True
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.edited_text = improved_text


def _delete_gpt4_suggestions(session_id: str):
    with get_db() as db:
        db.query(GPT4Suggestion).filter_by(session_id=session_id).delete()


def _delete_refinements(session_id: str):
    with get_db() as db:
        db.query(RefinedSuggestion).filter_by(session_id=session_id).delete()


# ── Main render ───────────────────────────────────────────────────────────────

def render():
    st.header("Refine")
    st.caption(
        "GPT-4o contributes its own independent suggestions for each gap, "
        "and separately reviews Claude's suggestions to approve, improve, or flag them."
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

    # ── Load session data ─────────────────────────────────────────────────────
    with get_db() as db:
        session_row = db.query(AnalysisSession).filter_by(id=selected_id).first()
        jd_text = session_row.job_description if session_row else ""

        resumes = db.query(Resume).order_by(Resume.order).all()
        resume_texts = [r.content for r in resumes]
        resume_text = resume_texts[0] if resume_texts else ""

        all_reqs = (
            db.query(Requirement)
            .filter_by(session_id=selected_id)
            .order_by(Requirement.match_score)
            .all()
        )
        gap_reqs = [
            {"id": r.id, "text": r.text, "match_score": r.match_score,
             "match_detail": r.match_detail, "category": r.category}
            for r in all_reqs if r.match_score < _SUGGESTION_THRESHOLD
        ]
        req_map = {r.id: r.text for r in all_reqs}

        # Claude's accepted suggestions (for the review section)
        accepted_claude = (
            db.query(Suggestion)
            .filter_by(session_id=selected_id, is_selected=True)
            .all()
        )
        claude_sugg_data = [{
            "id": s.id,
            "type": s.type.value,
            "original_text": s.original_text,
            "suggested_text": s.suggested_text,
            "edited_text": s.edited_text,
            "section": s.section,
            "requirement_text": req_map.get(s.requirement_id, ""),
            "requirement_id": s.requirement_id,
        } for s in accepted_claude]

        # Existing GPT-4o suggestions
        existing_gpt4 = (
            db.query(GPT4Suggestion)
            .filter_by(session_id=selected_id)
            .all()
        )
        gpt4_by_req: dict[str, list[dict]] = {}
        for s in existing_gpt4:
            gpt4_by_req.setdefault(s.requirement_id, []).append({
                "id": s.id,
                "type": s.type.value,
                "original_text": s.original_text,
                "suggested_text": s.suggested_text,
                "edited_text": s.edited_text,
                "is_selected": s.is_selected,
                "section": s.section,
            })

        # Existing refinements of Claude's suggestions
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

    if not gap_reqs and not claude_sugg_data:
        st.warning("No gap requirements or accepted suggestions found for this session.")
        return

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — GPT-4o's Own Suggestions
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("GPT-4o Suggestions")
    st.caption(
        "GPT-4o reads the same gaps Claude found and writes its own suggestions "
        "independently. Accept and edit them just like Claude's."
    )

    has_gpt4 = bool(gpt4_by_req)
    col_btn1, col_info1 = st.columns([2, 5])
    with col_btn1:
        run_gpt4 = st.button(
            "Re-generate GPT-4o Suggestions" if has_gpt4 else "Generate GPT-4o Suggestions",
            type="primary",
            key="btn_gpt4_sugg",
        )
    with col_info1:
        if has_gpt4:
            total = sum(len(v) for v in gpt4_by_req.values())
            selected = sum(
                1 for suggs in gpt4_by_req.values()
                for s in suggs if s["is_selected"]
            )
            st.caption(f"{total} suggestions generated, {selected} accepted")

    if run_gpt4:
        if not gap_reqs:
            st.info("No gaps to generate suggestions for.")
        elif not resume_text:
            st.error("No resume found.")
        else:
            _delete_gpt4_suggestions(selected_id)
            gpt4_by_req = {}
            with st.status("GPT-4o is generating suggestions...", expanded=True) as status:
                try:
                    result = ai_service.generate_suggestions_gpt4(gap_reqs, resume_texts)
                    by_req = result.get("suggestions_by_requirement", {})
                    valid_req_ids = {r["id"] for r in gap_reqs}
                    with get_db() as db:
                        for req_id, suggs in by_req.items():
                            if req_id not in valid_req_ids:
                                continue
                            for s in suggs:
                                sid = str(uuid.uuid4())
                                stype = s.get("type", "MODIFY").upper()
                                db.add(GPT4Suggestion(
                                    id=sid,
                                    session_id=selected_id,
                                    requirement_id=req_id,
                                    original_text=s.get("original_text"),
                                    suggested_text=s.get("suggested_text", ""),
                                    type=SuggestionType(stype if stype in ("MODIFY", "ADD") else "MODIFY"),
                                    section=s.get("section"),
                                ))
                    status.update(label="GPT-4o suggestions ready!", state="complete")
                except EnvironmentError as e:
                    status.update(label="Error", state="error")
                    st.error(str(e))
                    st.info("Add `OPENAI_API_KEY` to your environment variables.")
                    st.stop()
                except Exception as e:
                    status.update(label="Error", state="error")
                    st.error(f"GPT-4o call failed: {e}")
                    st.stop()
            st.rerun()

    if gpt4_by_req:
        for req in gap_reqs:
            req_suggs = gpt4_by_req.get(req["id"])
            if not req_suggs:
                continue
            score = req["match_score"]
            icon = "🔴" if score < 0.5 else "🟡"
            title = f"{icon} {req['text'][:90]}{'...' if len(req['text']) > 90 else ''} — {score:.0%}"
            with st.expander(title, expanded=(score < 0.5)):
                st.markdown(f"**{req['text']}**")
                st.caption(f"Category: `{req['category']}`  •  Score: {score:.0%}")
                st.markdown("---")
                for sugg in req_suggs:
                    sel_key  = f"g4sel_{sugg['id']}"
                    edit_key = f"g4edit_{sugg['id']}"
                    action   = "Modify" if sugg["type"] == "MODIFY" else "Add"

                    col_check, col_label = st.columns([1, 10])
                    is_checked = col_check.checkbox(
                        "", value=sugg["is_selected"], key=sel_key,
                        on_change=_toggle_gpt4_sugg, args=(sugg["id"], sel_key),
                        label_visibility="collapsed",
                    )
                    col_label.markdown(
                        f"**{action}** `{sugg.get('section') or 'resume'}`  \n"
                        f"{sugg['suggested_text']}"
                    )
                    if is_checked or st.session_state.get(sel_key, sugg["is_selected"]):
                        if sugg.get("original_text"):
                            st.caption(f"Replaces: *{sugg['original_text']}*")
                        st.text_area(
                            "Edit",
                            value=sugg.get("edited_text") or sugg["suggested_text"],
                            height=90,
                            key=edit_key,
                            label_visibility="collapsed",
                            on_change=_save_gpt4_edit,
                            args=(sugg["id"], edit_key),
                        )
                    st.markdown("")
    elif not run_gpt4:
        st.info("Click the button above to have GPT-4o generate its own suggestions.")

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — GPT-4o Review of Claude's Suggestions
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("GPT-4o Review of Claude's Suggestions")
    st.caption(
        "GPT-4o critiques every suggestion Claude made and either approves it, "
        "rewrites it, or flags it as unhelpful."
    )

    if not claude_sugg_data:
        st.info("No accepted Claude suggestions to review. Accept some in the **Review** tab first.")
    else:
        has_reviews = bool(refined_map)
        col_btn2, col_info2 = st.columns([2, 5])
        with col_btn2:
            run_review = st.button(
                "Re-run GPT-4o Review" if has_reviews else "Run GPT-4o Review",
                key="btn_gpt4_review",
            )
        with col_info2:
            if has_reviews:
                n_imp  = sum(1 for r in refined_map.values() if r["verdict"] == "improved")
                n_flag = sum(1 for r in refined_map.values() if r["verdict"] == "flagged")
                n_ok   = sum(1 for r in refined_map.values() if r["verdict"] == "approved")
                st.caption(f"✅ {n_ok} approved  ✏️ {n_imp} improved  ⚠️ {n_flag} flagged")

        if run_review:
            _delete_refinements(selected_id)
            refined_map = {}
            with st.status("GPT-4o reviewing Claude's suggestions...", expanded=True) as status:
                try:
                    result = ai_service.refine_suggestions_with_gpt4(
                        resume_text, jd_text, claude_sugg_data
                    )
                    refinements = result.get("refinements", [])
                    valid_ids = {s["id"] for s in claude_sugg_data}
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
                                "id": rid, "verdict": v,
                                "improved_text": ref.get("improved_text"),
                                "critique": ref.get("critique", ""),
                                "is_applied": False,
                            }
                    status.update(label="Review complete!", state="complete")
                except EnvironmentError as e:
                    status.update(label="Error", state="error")
                    st.error(str(e))
                    st.stop()
                except Exception as e:
                    status.update(label="Error", state="error")
                    st.error(f"GPT-4o call failed: {e}")
                    st.stop()
            st.rerun()

        if refined_map:
            for verdict_key in ("flagged", "improved", "approved"):
                icon, label, color = _VERDICT_CONFIG[verdict_key]
                items = [
                    (s, refined_map[s["id"]])
                    for s in claude_sugg_data
                    if s["id"] in refined_map and refined_map[s["id"]]["verdict"] == verdict_key
                ]
                if not items:
                    continue
                st.markdown(f"**{icon} {label} ({len(items)})**")
                for sugg, ref in items:
                    section = sugg.get("section") or "General"
                    preview = sugg["requirement_text"][:70] + "..." if len(sugg["requirement_text"]) > 70 else sugg["requirement_text"]
                    with st.expander(f"`{section}` — {preview}", expanded=(verdict_key != "approved")):
                        st.markdown(
                            f"<span style='color:{color};font-weight:bold'>{icon} {label}</span>",
                            unsafe_allow_html=True,
                        )
                        st.caption(ref["critique"])
                        if verdict_key == "approved":
                            st.info(sugg.get("edited_text") or sugg["suggested_text"])
                        elif verdict_key == "improved":
                            col_a, col_b = st.columns(2)
                            col_a.markdown("**Claude's version**")
                            col_a.info(sugg.get("edited_text") or sugg["suggested_text"])
                            col_b.markdown("**GPT-4o's version**")
                            col_b.success(ref["improved_text"] or "")
                            if not ref["is_applied"]:
                                if st.button("Apply GPT-4o version", key=f"apply_{ref['id']}", type="primary"):
                                    _apply_refinement(ref["id"], ref["improved_text"], sugg["id"])
                                    st.rerun()
                            else:
                                st.success("✓ GPT-4o version applied")
                        elif verdict_key == "flagged":
                            st.warning(sugg.get("edited_text") or sugg["suggested_text"])
                            st.caption("Consider unchecking this in the **Review** tab or rewriting it manually.")
                st.markdown("")
        elif not run_review:
            st.info("Click the button above to have GPT-4o review Claude's suggestions.")
