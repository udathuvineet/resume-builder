import plotly.graph_objects as go
import streamlit as st

import uuid

from database.db import get_db
from database.models import (AnalysisSession, AuditVerdict, ContentAuditItem,
                              ProjectsDocument, Requirement, Resume,
                              Suggestion, SuggestionType)
from services import ai_service
from services.resume_generator import _label_lines


def _score_color(score: float) -> str:
    if score >= 0.8:
        return "#28a745"
    if score >= 0.5:
        return "#e6a817"
    return "#dc3545"


def _score_icon(score: float) -> str:
    if score >= 0.8:
        return "🟢"
    if score >= 0.5:
        return "🟡"
    return "🔴"


_CONTEXT_DOC_NAME = "accepted_additions.txt"


def _append_to_context(text: str, section: str, db):
    """Append an accepted ADD point to the shared Projects/Context document."""
    line = f"[{section}] {text.strip()}"
    doc = db.query(ProjectsDocument).filter_by(filename=_CONTEXT_DOC_NAME).first()
    if doc:
        doc.content = doc.content + "\n" + line
    else:
        db.add(ProjectsDocument(
            id=str(uuid.uuid4()),
            filename=_CONTEXT_DOC_NAME,
            content=line,
        ))


def _toggle_suggestion(sugg_id: str, key: str):
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.is_selected = st.session_state[key]
            if s.is_selected and s.type.value == "ADD":
                text = s.edited_text or s.suggested_text or ""
                _append_to_context(text, s.section or "General", db)


def _dismiss_audit(item_id: str):
    with get_db() as db:
        item = db.query(ContentAuditItem).filter_by(id=item_id).first()
        if item:
            item.is_dismissed = True


def _accept_audit_removal(item_id: str):
    with get_db() as db:
        item = db.query(ContentAuditItem).filter_by(id=item_id).first()
        if item:
            item.accepted_replacement = ""   # empty string = remove
            item.is_dismissed = True


def _accept_audit_rephrase(item_id: str, replacement: str):
    with get_db() as db:
        item = db.query(ContentAuditItem).filter_by(id=item_id).first()
        if item:
            item.accepted_replacement = replacement
            item.is_dismissed = True


def _undo_audit_action(item_id: str):
    with get_db() as db:
        item = db.query(ContentAuditItem).filter_by(id=item_id).first()
        if item:
            item.accepted_replacement = None
            item.is_dismissed = False


def _save_edit(sugg_id: str, key: str):
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.edited_text = st.session_state[key]


def _set_weave_original(sugg_id: str, key: str):
    val = st.session_state.get(key, "")
    if not val:
        return
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.original_text = val
            s.edited_text = None  # clear so AI suggestion is triggered fresh


def _clear_weave_original(sugg_id: str):
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.original_text = None
            s.edited_text = None


def _generate_improvement_for_req(req: dict, session_id: str, resume_texts: list[str]):
    """Generate and save improvement suggestions for an already-matching requirement."""
    result = ai_service.generate_suggestions([req], resume_texts)
    by_req = result.get("suggestions_by_requirement", {})
    with get_db() as db:
        for suggs in by_req.values():
            for s in suggs:
                db.add(Suggestion(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    requirement_id=req["id"],
                    original_text=s.get("original_text"),
                    suggested_text=s.get("suggested_text", ""),
                    type=SuggestionType(s.get("type", "MODIFY").upper()
                                        if s.get("type", "MODIFY").upper() in ("MODIFY", "ADD")
                                        else "MODIFY"),
                    section=s.get("section"),
                ))


def _save_ai_merge(sugg_id: str, merged: str):
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.edited_text = merged


def _parse_resume_bullets(resume_text: str) -> dict[str, list[str]]:
    """Return {SECTION_NAME: [full bullet strings]} with continuation lines joined."""
    result: dict[str, list[str]] = {}
    current = "General"
    prev_label = None
    for line, label in _label_lines(resume_text):
        s = " ".join(line.split())
        if not s:
            continue
        if label == "section":
            current = s
            prev_label = label
        elif label == "bullet":
            result.setdefault(current, []).append(s)
            prev_label = label
        elif label == "body":
            if prev_label in ("bullet", "body") and result.get(current):
                # Continuation of the previous bullet — join onto it
                result[current][-1] += " " + s
            else:
                result.setdefault(current, []).append(s)
            prev_label = label
        else:
            prev_label = label
    return result


def _bullets_for_section(resume_bullets: dict[str, list[str]], section: str) -> list[str]:
    """Return bullets for the closest-matching section, falling back to all."""
    sec_upper = (section or "").upper()
    for key in resume_bullets:
        if sec_upper in key.upper() or key.upper() in sec_upper:
            return resume_bullets[key]
    # fall back: every bullet across all sections
    return [b for bullets in resume_bullets.values() for b in bullets]


_IMPACT_ICON = {"high": "🔴", "medium": "🟡", "low": "⚪"}
_EVIDENCE_ICON = {"direct": "🟢", "inferred": "🟡", "no_evidence": "🔴"}
_EVIDENCE_LABEL = {"direct": "Direct evidence", "inferred": "Inferred", "no_evidence": "⚠️ No evidence"}


def _render_suggestion(sugg: dict, resume_bullets: dict[str, list[str]]):
    sugg_id  = sugg["id"]
    sel_key  = f"sel_{sugg_id}"
    edit_key = f"edit_{sugg_id}"
    section  = sugg.get("section") or "General"
    is_add   = sugg["type"] == "ADD"
    ev       = sugg.get("evidence_type")
    impact   = sugg.get("impact")

    # ── Evidence / impact badges ──────────────────────────────────────────────
    badge_parts = []
    if impact:
        badge_parts.append(f"{_IMPACT_ICON.get(impact, '⚪')} {impact.capitalize()} impact")
    if ev:
        badge_parts.append(f"{_EVIDENCE_ICON.get(ev, '⚪')} {_EVIDENCE_LABEL.get(ev, ev)}")
    if badge_parts:
        st.caption(" · ".join(badge_parts))

    # ── No-evidence warning for ADD ───────────────────────────────────────────
    if is_add and ev == "no_evidence":
        st.warning(
            "⚠️ No evidence found in your resume. Only accept if you genuinely have this experience.",
            icon=None,
        )

    col_check, col_label = st.columns([1, 10])
    action_label = "Add to" if is_add else "Modify"
    is_checked = col_check.checkbox(
        "", value=sugg["is_selected"], key=sel_key,
        on_change=_toggle_suggestion, args=(sugg_id, sel_key),
        label_visibility="collapsed",
    )
    col_label.markdown(
        f"**{action_label}** — `{section}`  \n"
        f"{sugg['suggested_text']}"
    )

    # ── Gap + reasoning (collapsed) ───────────────────────────────────────────
    gap = sugg.get("gap_addressed")
    reasoning = sugg.get("reasoning")
    evidence_exp = sugg.get("evidence_explanation")
    if gap or reasoning or evidence_exp:
        with st.expander("💡 Why this change?", expanded=False):
            if gap:
                st.caption(f"**Addresses:** {gap}")
            if evidence_exp:
                st.caption(f"**Evidence:** {evidence_exp}")
            if reasoning:
                st.write(reasoning)

    active = is_checked or st.session_state.get(sel_key, sugg["is_selected"])
    if not active:
        st.markdown("")
        return

    if is_add:
        # ── Weave vs new-bullet toggle ────────────────────────────────────────
        mode_key = f"weave_mode_{sugg_id}"
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
                target_key = f"weave_target_{sugg_id}"
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
                    format_func=lambda x: x if x == placeholder else (
                        x[:90] + "…" if len(x) > 90 else x
                    ),
                    on_change=_set_weave_original,
                    args=(sugg_id, target_key),
                )
                if has_target:
                    col_ai, col_spacer = st.columns([2, 5])
                    if col_ai.button("✨ AI Suggest Integration", key=f"ai_merge_{sugg_id}"):
                        with st.spinner("Generating integrated version…"):
                            merged = ai_service.suggest_bullet_integration(
                                current_original, sugg["suggested_text"], section
                            )
                            _save_ai_merge(sugg_id, merged)
                        st.rerun()
                    st.text_area(
                        "Edit integrated version",
                        value=sugg.get("edited_text") or current_original,
                        height=90,
                        key=edit_key,
                        label_visibility="collapsed",
                        on_change=_save_edit,
                        args=(sugg_id, edit_key),
                    )
        else:
            # Switched back to "new bullet" — clear any saved weave target
            if has_target:
                _clear_weave_original(sugg_id)
            st.text_area(
                "Edit new bullet text",
                value=sugg.get("edited_text") or sugg["suggested_text"],
                height=90,
                key=edit_key,
                label_visibility="collapsed",
                on_change=_save_edit,
                args=(sugg_id, edit_key),
            )
    else:
        # MODIFY suggestion — standard flow
        if sugg.get("original_text"):
            st.caption(f"Replaces: *{sugg['original_text']}*")
        st.text_area(
            "Edit",
            value=sugg.get("edited_text") or sugg["suggested_text"],
            height=90,
            key=edit_key,
            label_visibility="collapsed",
            on_change=_save_edit,
            args=(sugg_id, edit_key),
        )

    st.markdown("")


def render():
    st.header("Review")

    # ── Session selector ──────────────────────────────────────────────────────
    with get_db() as db:
        sessions = (
            db.query(AnalysisSession)
            .order_by(AnalysisSession.created_at.desc())
            .all()
        )
        options = {
            f"{s.created_at.strftime('%m/%d %H:%M') if s.created_at else '?'} | {s.job_description[:55]}...": s.id
            for s in sessions
        }

    if not options:
        st.info("No analyses yet. Run one in the **Analyze** tab.")
        return

    current_id = st.session_state.get("current_session_id")
    ids = list(options.values())
    default_idx = ids.index(current_id) if current_id in ids else 0

    selected_label = st.selectbox(
        "Analysis session", list(options.keys()), index=default_idx,
        key="session_select_review",
    )
    selected_id = options[selected_label]
    prev_id = st.session_state.get("current_session_id")
    st.session_state["current_session_id"] = selected_id
    if selected_id != prev_id:
        for _k in ["session_select_refine", "session_select_generate"]:
            st.session_state.pop(_k, None)
        for _k in ["generated_resume", "generated_resume_source", "generated_sugg_data", "generated_req_map"]:
            st.session_state.pop(_k, None)

    # ── Load data ─────────────────────────────────────────────────────────────
    with get_db() as db:
        session = db.query(AnalysisSession).filter_by(id=selected_id).first()
        if not session:
            st.error("Session not found.")
            return

        overall_score = session.overall_score or 0.0
        all_reqs = (
            db.query(Requirement)
            .filter_by(session_id=selected_id)
            .order_by(Requirement.match_score)
            .all()
        )
        suggs = db.query(Suggestion).filter_by(session_id=selected_id).all()

        # Exclude sentinel requirements from metrics
        all_reqs_data = [{
            "id": r.id, "text": r.text, "category": r.category,
            "match_score": r.match_score, "match_detail": r.match_detail,
        } for r in all_reqs if r.category != "sentinel"]

        req_map = {r.id: r.text for r in all_reqs if r.category != "sentinel"}

        # Section-based grouping for all suggestions (works for old and new sessions)
        suggs_by_section: dict[str, list[dict]] = {}
        for s in suggs:
            sec = s.section or "General"
            suggs_by_section.setdefault(sec, []).append({
                "id": s.id,
                "type": s.type.value,
                "original_text": s.original_text,
                "suggested_text": s.suggested_text,
                "edited_text": s.edited_text,
                "is_selected": s.is_selected,
                "section": s.section,
                "gap_addressed": s.gap_addressed or req_map.get(s.requirement_id, ""),
                "evidence_type": s.evidence_type,
                "evidence_explanation": s.evidence_explanation,
                "reasoning": s.reasoning,
                "impact": s.impact,
            })

        # Keep suggs_by_req for selected-count metric
        suggs_by_req: dict[str, list[dict]] = {}
        for s in suggs:
            suggs_by_req.setdefault(s.requirement_id, []).append({"is_selected": s.is_selected})

        jd_text = session.job_description or ""

        audit_rows = (
            db.query(ContentAuditItem)
            .filter_by(session_id=selected_id, is_dismissed=False)
            .order_by(ContentAuditItem.section)
            .all()
        )
        audit_data = [{
            "id": a.id,
            "section": a.section or "General",
            "text": a.text,
            "verdict": a.verdict.value,
            "reason": a.reason,
            "relevance": a.relevance,
            "evidence_type": a.evidence_type,
            "evidence_explanation": a.evidence_explanation,
            "suggested_action": a.suggested_action,
        } for a in audit_rows]

        # Items already actioned (dismissed with an accepted_replacement)
        accepted_audit_rows = (
            db.query(ContentAuditItem)
            .filter(
                ContentAuditItem.session_id == selected_id,
                ContentAuditItem.is_dismissed == True,
                ContentAuditItem.accepted_replacement != None,  # noqa: E711
            )
            .order_by(ContentAuditItem.section)
            .all()
        )
        accepted_audit_data = [{
            "id": a.id,
            "section": a.section or "General",
            "text": a.text,
            "verdict": a.verdict.value,
            "accepted_replacement": a.accepted_replacement,
        } for a in accepted_audit_rows]

        resumes = db.query(Resume).order_by(Resume.order).all()
        resume_texts = [r.content for r in resumes]
        resume_text = resume_texts[0] if resume_texts else ""

    resume_bullets = _parse_resume_bullets(resume_text)
    # Threshold aligned with suggestion generation in analyze.py (0.8)
    gap_reqs     = [r for r in all_reqs_data if r["match_score"] < 0.8]
    matched_reqs = [r for r in all_reqs_data if r["match_score"] >= 0.8]

    # ── Score summary ─────────────────────────────────────────────────────────
    col_gauge, col_metrics = st.columns([1, 2])

    with col_gauge:
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=overall_score * 100,
            number={"suffix": "%"},
            title={"text": "Match Score"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": _score_color(overall_score)},
                "steps": [
                    {"range": [0, 50], "color": "#fff0f0"},
                    {"range": [50, 75], "color": "#fffbee"},
                    {"range": [75, 100], "color": "#f0fff4"},
                ],
            },
        ))
        fig.update_layout(height=220, margin=dict(t=40, b=0, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)

    with col_metrics:
        high = sum(1 for r in all_reqs_data if r["match_score"] >= 0.8)
        mid  = sum(1 for r in all_reqs_data if 0.5 <= r["match_score"] < 0.8)
        low  = sum(1 for r in all_reqs_data if r["match_score"] < 0.5)
        selected_count = sum(
            1 for suggs_list in suggs_by_req.values()
            for s in suggs_list if s["is_selected"]
        )
        st.metric("Strong matches 🟢", high)
        st.metric("Partial matches 🟡", mid)
        st.metric("Gaps 🔴", low)
        st.metric("Improvements selected", selected_count)

    st.divider()

    # ── Requirements breakdown (collapsed) ────────────────────────────────────
    gap_reqs     = [r for r in all_reqs_data if r["match_score"] < 0.8]
    matched_reqs = [r for r in all_reqs_data if r["match_score"] >= 0.8]
    with st.expander(
        f"📊 Requirements breakdown — {len(gap_reqs)} below threshold · {len(matched_reqs)} strong",
        expanded=False,
    ):
        for req in sorted(all_reqs_data, key=lambda r: r["match_score"]):
            score = req["match_score"]
            st.markdown(f"{_score_icon(score)} **{req['text'][:100]}** — {score:.0%}")
            st.caption(f"`{req['category']}` — {req['match_detail']}")

    # ── Suggested changes by section ──────────────────────────────────────────
    all_sugg_count = sum(len(v) for v in suggs_by_section.values())
    _IMPACT_ORDER = {"high": 0, "medium": 1, "low": 2}

    if suggs_by_section:
        st.subheader(f"Suggested Changes ({all_sugg_count})")
        st.caption(
            "High-impact suggestions appear first within each section. "
            "Check a suggestion to accept it. For additions, weave into an existing bullet or add new."
        )

        for section in sorted(suggs_by_section.keys()):
            section_suggs = sorted(
                suggs_by_section[section],
                key=lambda s: (_IMPACT_ORDER.get(s.get("impact"), 3), 0 if s["type"] == "MODIFY" else 1),
            )
            mod_n = sum(1 for s in section_suggs if s["type"] == "MODIFY")
            add_n = sum(1 for s in section_suggs if s["type"] == "ADD")
            parts = []
            if mod_n:
                parts.append(f"{mod_n} modification{'s' if mod_n > 1 else ''}")
            if add_n:
                parts.append(f"{add_n} addition{'s' if add_n > 1 else ''}")

            with st.expander(f"**{section}** — {', '.join(parts)}", expanded=True):
                for sugg in section_suggs:
                    _render_suggestion(sugg, resume_bullets)
                    st.markdown("---")
    else:
        st.success("No suggestions generated — your resume looks well-aligned with this role.")

    # ── Content audit ─────────────────────────────────────────────────────────
    if audit_data or accepted_audit_data:
        st.divider()
        pending_count = len(audit_data)
        accepted_count = len(accepted_audit_data)
        st.subheader(f"Existing Content to Reconsider ({pending_count} pending)")
        st.caption(
            "These are bullets and statements in your resume that may be "
            "hurting your match — either too generic or not relevant to this role. "
            "Remove or rephrase them to strengthen the fit."
        )

        # suggested_action overrides old verdict for new sessions
        _ACTION_STYLE = {
            "remove":  ("🔴", "Remove",   "#dc3545"),
            "shorten": ("🟡", "Shorten",  "#e6a817"),
            "merge":   ("🟡", "Merge",    "#e6a817"),
            "rephrase":("🟡", "Rephrase", "#e6a817"),  # backward compat
        }

        if audit_data:
            by_section: dict[str, list[dict]] = {}
            for item in audit_data:
                by_section.setdefault(item["section"], []).append(item)

            for section, items in sorted(by_section.items()):
                with st.expander(f"**{section}** — {len(items)} item(s)", expanded=True):
                    for item in items:
                        action = item.get("suggested_action") or item["verdict"]
                        icon, label, color = _ACTION_STYLE.get(
                            action, ("⚪", action.title(), "#888")
                        )
                        # Header row: action badge + relevance
                        badge_parts = [f"<span style='color:{color};font-weight:bold'>{icon} {label}</span>"]
                        rel = item.get("relevance")
                        if rel:
                            rel_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(rel, "⚪")
                            badge_parts.append(f"<span style='color:#888'>{rel_icon} {rel.capitalize()} relevance</span>")
                        ev = item.get("evidence_type")
                        if ev:
                            badge_parts.append(
                                f"<span style='color:#888'>{_EVIDENCE_ICON.get(ev,'⚪')} {_EVIDENCE_LABEL.get(ev,ev)}</span>"
                            )
                        st.markdown(" · ".join(badge_parts), unsafe_allow_html=True)

                        st.markdown(f"**{item['text']}**")

                        reason_parts = [item["reason"]]
                        if item.get("evidence_explanation"):
                            reason_parts.append(f"*Evidence: {item['evidence_explanation']}*")
                        st.caption("  \n".join(reason_parts))

                        rephrase_key = f"rephrase_draft_{item['id']}"

                        if action == "remove":
                            col_rm, col_dis, _ = st.columns([2, 2, 6])
                            if col_rm.button("🗑 Remove from resume",
                                             key=f"rm_{item['id']}",
                                             use_container_width=True):
                                _accept_audit_removal(item["id"])
                                st.rerun()
                            if col_dis.button("✕ Dismiss", key=f"dismiss_{item['id']}",
                                              use_container_width=True):
                                _dismiss_audit(item["id"])
                                st.rerun()

                        else:  # shorten / merge / rephrase
                            btn_label = {"shorten": "✂️ Generate shortened", "merge": "🔀 Generate merged"}.get(
                                action, "✏️ Generate rephrase"
                            )
                            col_rp, col_dis, _ = st.columns([2, 2, 6])
                            if col_rp.button(btn_label, key=f"rp_{item['id']}",
                                             use_container_width=True):
                                with st.spinner("Generating revision…"):
                                    draft = ai_service.rephrase_bullet_for_jd(
                                        item["text"], jd_text, item["section"]
                                    )
                                    st.session_state[rephrase_key] = draft
                                st.rerun()
                            if col_dis.button("✕ Dismiss", key=f"dismiss_{item['id']}",
                                              use_container_width=True):
                                _dismiss_audit(item["id"])
                                st.rerun()

                            if rephrase_key in st.session_state:
                                edited = st.text_area(
                                    "Revised version (edit if needed)",
                                    value=st.session_state[rephrase_key],
                                    height=80,
                                    key=f"rp_edit_{item['id']}",
                                )
                                if st.button("✅ Accept revision",
                                             key=f"rp_accept_{item['id']}"):
                                    _accept_audit_rephrase(item["id"], edited)
                                    st.session_state.pop(rephrase_key, None)
                                    st.rerun()

                        st.markdown("")

        if accepted_audit_data:
            with st.expander(
                f"✅ Accepted audit actions ({accepted_count}) — click to review or undo",
                expanded=False,
            ):
                for item in accepted_audit_data:
                    icon, label, color = _VERDICT_STYLE.get(
                        item["verdict"], ("⚪", item["verdict"].title(), "#888")
                    )
                    rep = item["accepted_replacement"]
                    if rep == "":
                        action_str = "🗑 **Marked for removal**"
                    else:
                        action_str = f"✏️ **Rephrase accepted:** {rep}"

                    col_info, col_undo = st.columns([9, 1])
                    with col_info:
                        st.caption(f"{icon} {label} — original: *{item['text'][:120]}*")
                        st.markdown(action_str)
                    if col_undo.button("↩", key=f"undo_{item['id']}",
                                       help="Undo — put back in pending list"):
                        _undo_audit_action(item["id"])
                        st.rerun()
                    st.markdown("")
