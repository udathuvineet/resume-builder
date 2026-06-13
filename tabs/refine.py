import json
import uuid

import plotly.graph_objects as go
import streamlit as st

from database.db import get_db
from database.models import (AnalysisSession, GPT4MatchResult, GPT4Suggestion,
                              RefineVerdict, RefinedSuggestion, Requirement,
                              Resume, Suggestion, SuggestionType)
from services import ai_service
from services.resume_generator import _label_lines

_SUGGESTION_THRESHOLD = 0.8

_VERDICT_CONFIG = {
    "approved": ("✅", "Approved", "#28a745"),
    "improved": ("✏️", "Improved", "#0d6efd"),
    "flagged":  ("⚠️",  "Flagged",  "#dc3545"),
}


def _score_color(s: float) -> str:
    return "#28a745" if s >= 0.75 else ("#e6a817" if s >= 0.5 else "#dc3545")


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


def _set_gpt4_weave_original(sugg_id: str, key: str):
    val = st.session_state.get(key, "")
    if not val:
        return
    with get_db() as db:
        s = db.query(GPT4Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.original_text = val
            if not s.edited_text:
                s.edited_text = val


def _clear_gpt4_weave_original(sugg_id: str):
    with get_db() as db:
        s = db.query(GPT4Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.original_text = None
            s.edited_text = None


def _parse_resume_bullets(resume_text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current = "General"
    for line, label in _label_lines(resume_text):
        s = line.strip()
        if not s:
            continue
        if label == "section":
            current = s
        elif label in ("bullet", "body", "role"):
            result.setdefault(current, []).append(s)
    return result


def _bullets_for_section(resume_bullets: dict[str, list[str]], section: str) -> list[str]:
    sec_upper = (section or "").upper()
    for key in resume_bullets:
        if sec_upper in key.upper() or key.upper() in sec_upper:
            return resume_bullets[key]
    return [b for bullets in resume_bullets.values() for b in bullets]


def _apply_refinement(refined_id: str, improved_text: str, sugg_id: str):
    with get_db() as db:
        r = db.query(RefinedSuggestion).filter_by(id=refined_id).first()
        if r:
            r.is_applied = True
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.edited_text = improved_text


def _render_gpt4_suggestion(sugg: dict, resume_bullets: dict[str, list[str]]):
    sugg_id  = sugg["id"]
    sel_key  = f"g4sel_{sugg_id}"
    edit_key = f"g4edit_{sugg_id}"
    section  = sugg.get("section") or "General"
    is_add   = sugg["type"] == "ADD"

    col_check, col_label = st.columns([1, 10])
    action_label = "Add to" if is_add else "Modify"
    is_checked = col_check.checkbox(
        "", value=sugg["is_selected"], key=sel_key,
        on_change=_toggle_gpt4_sugg, args=(sugg_id, sel_key),
        label_visibility="collapsed",
    )
    col_label.markdown(
        f"**{action_label}** — `{section}`  \n"
        f"{sugg['suggested_text']}"
    )

    active = is_checked or st.session_state.get(sel_key, sugg["is_selected"])
    if not active:
        st.markdown("")
        return

    if is_add:
        mode_key = f"g4weave_mode_{sugg_id}"
        has_target = bool(sugg.get("original_text"))
        mode = st.radio(
            "Apply as",
            ["Add as new bullet point", "Weave into existing point"],
            index=1 if has_target else 0,
            key=mode_key,
            horizontal=True,
        )
        if mode == "Weave into existing point":
            bullets = _bullets_for_section(resume_bullets, section)
            if not bullets:
                st.caption("No existing bullets found in the resume to weave into.")
            else:
                target_key = f"g4weave_target_{sugg_id}"
                placeholder = "— pick a bullet to blend this into —"
                options = [placeholder] + bullets
                current_original = sugg.get("original_text") or ""
                current_idx = (bullets.index(current_original) + 1
                               if current_original in bullets else 0)
                st.selectbox(
                    "Existing point to weave into",
                    options=options,
                    index=current_idx,
                    key=target_key,
                    on_change=_set_gpt4_weave_original,
                    args=(sugg_id, target_key),
                )
                if has_target:
                    st.caption(f"Original: *{current_original}*")
                    st.text_area(
                        "Edit blended version",
                        value=sugg.get("edited_text") or current_original,
                        height=90, key=edit_key,
                        label_visibility="collapsed",
                        on_change=_save_gpt4_edit,
                        args=(sugg_id, edit_key),
                    )
                    st.caption("Edit the text above to blend the suggestion into the existing point.")
        else:
            if has_target:
                _clear_gpt4_weave_original(sugg_id)
            st.text_area(
                "Edit new bullet text",
                value=sugg.get("edited_text") or sugg["suggested_text"],
                height=90, key=edit_key,
                label_visibility="collapsed",
                on_change=_save_gpt4_edit,
                args=(sugg_id, edit_key),
            )
    else:
        if sugg.get("original_text"):
            st.caption(f"Replaces: *{sugg['original_text']}*")
        st.text_area(
            "Edit",
            value=sugg.get("edited_text") or sugg["suggested_text"],
            height=90, key=edit_key,
            label_visibility="collapsed",
            on_change=_save_gpt4_edit,
            args=(sugg_id, edit_key),
        )

    st.markdown("")


# ── Main render ───────────────────────────────────────────────────────────────

def render():
    st.header("Refine")
    st.caption(
        "Three sequential steps: GPT-4o first scores the resume independently, "
        "then reviews Claude's suggestions, then adds its own unique improvements."
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
    selected_label = st.selectbox("Analysis session", list(options.keys()), index=default_idx,
                                   key="session_select_refine")
    selected_id = options[selected_label]
    st.session_state["current_session_id"] = selected_id

    # ── Load all data up-front ────────────────────────────────────────────────
    with get_db() as db:
        session_row = db.query(AnalysisSession).filter_by(id=selected_id).first()
        jd_text = session_row.job_description if session_row else ""

        resumes = db.query(Resume).order_by(Resume.order).all()
        resume_texts = [r.content for r in resumes]
        resume_text = resume_texts[0] if resume_texts else ""
        resume_bullets = _parse_resume_bullets(resume_text)

        all_reqs = [
            {"id": r.id, "text": r.text, "match_score": r.match_score,
             "match_detail": r.match_detail, "category": r.category}
            for r in db.query(Requirement)
            .filter_by(session_id=selected_id)
            .order_by(Requirement.match_score)
            .all()
        ]
        req_map = {r["id"]: r["text"] for r in all_reqs}
        gap_reqs = [r for r in all_reqs if r["match_score"] < _SUGGESTION_THRESHOLD]

        # Claude's accepted suggestions
        claude_suggs = (
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
        } for s in claude_suggs]

        # Step 1 result
        _mr = db.query(GPT4MatchResult).filter_by(session_id=selected_id).first()
        match_result = {
            "overall_score": _mr.overall_score,
            "summary": _mr.summary,
            "requirements_json": _mr.requirements_json,
        } if _mr else None

        # Step 2 result
        refined_map: dict[str, dict] = {
            r.suggestion_id: {
                "id": r.id, "verdict": r.verdict.value,
                "improved_text": r.improved_text,
                "critique": r.critique, "is_applied": r.is_applied,
            }
            for r in db.query(RefinedSuggestion).filter_by(session_id=selected_id).all()
        }

        # Step 3 result
        gpt4_by_req: dict[str, list[dict]] = {}
        for s in db.query(GPT4Suggestion).filter_by(session_id=selected_id).all():
            gpt4_by_req.setdefault(s.requirement_id, []).append({
                "id": s.id, "type": s.type.value,
                "original_text": s.original_text,
                "suggested_text": s.suggested_text,
                "edited_text": s.edited_text,
                "is_selected": s.is_selected,
                "section": s.section,
            })

    step1_done = match_result is not None
    step2_done = bool(refined_map)
    step3_done = bool(gpt4_by_req)

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1 — GPT-4o Match Assessment
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### Step 1 — GPT-4o Match Assessment")
    st.caption("GPT-4o independently scores how well your resume matches the job description.")

    col_s1, col_s1info = st.columns([2, 5])
    with col_s1:
        run_step1 = st.button(
            "Re-run Step 1" if step1_done else "Run Step 1",
            key="btn_step1", type="primary",
        )
    with col_s1info:
        if step1_done:
            st.caption(f"GPT-4o score: **{match_result['overall_score']:.0%}** — click to re-run")

    if run_step1:
        if not resume_text or not jd_text:
            st.error("Missing resume or job description.")
            st.stop()
        with st.status("GPT-4o scoring the resume...", expanded=True) as status:
            try:
                result = ai_service.analyze_resume_gpt4(all_reqs, resume_text, jd_text)
                scores_by_req = {r["req_id"]: r for r in result.get("requirements", [])}
                with get_db() as db:
                    existing = db.query(GPT4MatchResult).filter_by(session_id=selected_id).first()
                    if existing:
                        db.delete(existing)
                    db.add(GPT4MatchResult(
                        id=str(uuid.uuid4()),
                        session_id=selected_id,
                        overall_score=float(result.get("overall_score", 0.0)),
                        summary=result.get("summary", ""),
                        requirements_json=json.dumps(scores_by_req),
                    ))
                status.update(label="Step 1 complete!", state="complete")
                step1_done = True
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

    if step1_done and match_result:
        scores_by_req = json.loads(match_result["requirements_json"])

        col_gauge, col_detail = st.columns([1, 2])
        with col_gauge:
            score = match_result["overall_score"]
            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=score * 100,
                number={"suffix": "%"},
                title={"text": "GPT-4o Score"},
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": _score_color(score)},
                    "steps": [
                        {"range": [0, 50],  "color": "#fff0f0"},
                        {"range": [50, 75], "color": "#fffbee"},
                        {"range": [75, 100],"color": "#f0fff4"},
                    ],
                },
            ))
            fig.update_layout(height=200, margin=dict(t=40, b=0, l=20, r=20))
            st.plotly_chart(fig, use_container_width=True)

        with col_detail:
            st.markdown(f"*{match_result['summary']}*")
            st.markdown("**Per-requirement scores**")
            for req in sorted(all_reqs, key=lambda r: scores_by_req.get(r["id"], {}).get("score", 1.0)):
                r_data = scores_by_req.get(req["id"], {})
                r_score = r_data.get("score", None)
                if r_score is None:
                    continue
                icon = "🟢" if r_score >= 0.75 else ("🟡" if r_score >= 0.5 else "🔴")
                with st.expander(f"{icon} {req['text'][:80]} — {r_score:.0%}", expanded=False):
                    st.caption(r_data.get("detail", ""))

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 2 — Review Claude's Suggestions
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### Step 2 — GPT-4o Reviews Claude's Suggestions")
    st.caption("GPT-4o approves, improves, or flags each suggestion Claude made.")

    if not step1_done:
        st.info("Complete Step 1 first.")
    elif not claude_sugg_data:
        st.info("No accepted Claude suggestions to review. Accept some in the **Review** tab first.")
    else:
        col_s2, col_s2info = st.columns([2, 5])
        with col_s2:
            run_step2 = st.button(
                "Re-run Step 2" if step2_done else "Run Step 2",
                key="btn_step2", type="primary",
            )
        with col_s2info:
            if step2_done:
                n_ok   = sum(1 for r in refined_map.values() if r["verdict"] == "approved")
                n_imp  = sum(1 for r in refined_map.values() if r["verdict"] == "improved")
                n_flag = sum(1 for r in refined_map.values() if r["verdict"] == "flagged")
                st.caption(f"✅ {n_ok} approved  ✏️ {n_imp} improved  ⚠️ {n_flag} flagged")

        if run_step2:
            with get_db() as db:
                db.query(RefinedSuggestion).filter_by(session_id=selected_id).delete()
            refined_map = {}
            with st.status("GPT-4o reviewing Claude's suggestions...", expanded=True) as status:
                try:
                    result = ai_service.refine_suggestions_with_gpt4(
                        resume_text, jd_text, claude_sugg_data
                    )
                    valid_ids = {s["id"] for s in claude_sugg_data}
                    valid_verdicts = {v.value for v in RefineVerdict}
                    with get_db() as db:
                        for ref in result.get("refinements", []):
                            sid = ref.get("suggestion_id", "")
                            v = ref.get("verdict", "").lower()
                            if sid not in valid_ids or v not in valid_verdicts:
                                continue
                            rid = str(uuid.uuid4())
                            db.add(RefinedSuggestion(
                                id=rid, session_id=selected_id,
                                suggestion_id=sid, verdict=RefineVerdict(v),
                                improved_text=ref.get("improved_text"),
                                critique=ref.get("critique", ""),
                            ))
                            refined_map[sid] = {
                                "id": rid, "verdict": v,
                                "improved_text": ref.get("improved_text"),
                                "critique": ref.get("critique", ""),
                                "is_applied": False,
                            }
                    status.update(label="Step 2 complete!", state="complete")
                    step2_done = True
                except Exception as e:
                    status.update(label="Error", state="error")
                    st.error(f"GPT-4o call failed: {e}")
                    st.stop()
            st.rerun()

        if step2_done and refined_map:
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
                    sec = sugg.get("section") or "General"
                    preview = sugg["requirement_text"][:70] + ("..." if len(sugg["requirement_text"]) > 70 else "")
                    with st.expander(f"`{sec}` — {preview}", expanded=(verdict_key != "approved")):
                        st.markdown(
                            f"<span style='color:{color};font-weight:bold'>{icon} {label}</span>",
                            unsafe_allow_html=True,
                        )
                        st.caption(ref["critique"])
                        if verdict_key == "approved":
                            st.info(sugg.get("edited_text") or sugg["suggested_text"])
                        elif verdict_key == "improved":
                            ca, cb = st.columns(2)
                            ca.markdown("**Claude's version**")
                            ca.info(sugg.get("edited_text") or sugg["suggested_text"])
                            cb.markdown("**GPT-4o's version**")
                            cb.success(ref["improved_text"] or "")
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

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 3 — GPT-4o's Own Unique Suggestions
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("### Step 3 — GPT-4o's Own Suggestions")
    st.caption("GPT-4o adds suggestions it thinks Claude missed. Duplicates are filtered out.")

    if not step2_done:
        st.info("Complete Step 2 first.")
    elif not gap_reqs:
        st.info("No gap requirements found for this session.")
    else:
        col_s3, col_s3info = st.columns([2, 5])
        with col_s3:
            run_step3 = st.button(
                "Re-run Step 3" if step3_done else "Run Step 3",
                key="btn_step3", type="primary",
            )
        with col_s3info:
            if step3_done:
                total = sum(len(v) for v in gpt4_by_req.values())
                selected_count = sum(
                    1 for suggs in gpt4_by_req.values() for s in suggs if s["is_selected"]
                )
                st.caption(f"{total} unique suggestions, {selected_count} accepted")

        if run_step3:
            with get_db() as db:
                db.query(GPT4Suggestion).filter_by(session_id=selected_id).delete()
            gpt4_by_req = {}
            with st.status("GPT-4o generating unique suggestions...", expanded=True) as status:
                try:
                    result = ai_service.generate_suggestions_gpt4(
                        gap_reqs, resume_texts, existing_suggestions=claude_sugg_data
                    )
                    valid_req_ids = {r["id"] for r in gap_reqs}
                    with get_db() as db:
                        for req_id, suggs in result.get("suggestions_by_requirement", {}).items():
                            if req_id not in valid_req_ids:
                                continue
                            for s in suggs:
                                stype = s.get("type", "MODIFY").upper()
                                sid = str(uuid.uuid4())
                                db.add(GPT4Suggestion(
                                    id=sid, session_id=selected_id,
                                    requirement_id=req_id,
                                    original_text=s.get("original_text"),
                                    suggested_text=s.get("suggested_text", ""),
                                    type=SuggestionType(stype if stype in ("MODIFY", "ADD") else "MODIFY"),
                                    section=s.get("section"),
                                ))
                                gpt4_by_req.setdefault(req_id, []).append({
                                    "id": sid, "type": stype,
                                    "original_text": s.get("original_text"),
                                    "suggested_text": s.get("suggested_text", ""),
                                    "edited_text": None, "is_selected": False,
                                    "section": s.get("section"),
                                })
                    status.update(label="Step 3 complete!", state="complete")
                    step3_done = True
                except Exception as e:
                    status.update(label="Error", state="error")
                    st.error(f"GPT-4o call failed: {e}")
                    st.stop()
            st.rerun()

        if step3_done and gpt4_by_req:
            st.caption("Accept the ones you want included in the generated resume.")
            for req in gap_reqs:
                suggs = gpt4_by_req.get(req["id"])
                if not suggs:
                    continue
                score = req["match_score"]
                icon = "🔴" if score < 0.5 else "🟡"
                with st.expander(
                    f"{icon} {req['text'][:90]}{'...' if len(req['text']) > 90 else ''} — {score:.0%}",
                    expanded=(score < 0.5),
                ):
                    st.markdown(f"**{req['text']}**")
                    st.caption(f"Category: `{req['category']}`  •  Score: {score:.0%}")
                    st.markdown("---")
                    for sugg in suggs:
                        _render_gpt4_suggestion(sugg, resume_bullets)
        elif step3_done:
            st.success("GPT-4o had nothing new to add — Claude's suggestions already cover the gaps well.")
