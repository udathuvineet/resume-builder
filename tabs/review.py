import plotly.graph_objects as go
import streamlit as st

from database.db import get_db
from database.models import (AnalysisSession, AuditVerdict, ContentAuditItem,
                              Requirement, Resume, Suggestion)
from services.resume_generator import _label_lines


def _score_color(score: float) -> str:
    if score >= 0.75:
        return "#28a745"
    if score >= 0.5:
        return "#e6a817"
    return "#dc3545"


def _score_icon(score: float) -> str:
    if score >= 0.75:
        return "🟢"
    if score >= 0.5:
        return "🟡"
    return "🔴"


def _toggle_suggestion(sugg_id: str, key: str):
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.is_selected = st.session_state[key]


def _dismiss_audit(item_id: str):
    with get_db() as db:
        item = db.query(ContentAuditItem).filter_by(id=item_id).first()
        if item:
            item.is_dismissed = True


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
            if not s.edited_text:
                s.edited_text = val  # pre-fill editor with the chosen bullet


def _clear_weave_original(sugg_id: str):
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.original_text = None
            s.edited_text = None


def _parse_resume_bullets(resume_text: str) -> dict[str, list[str]]:
    """Return {SECTION_NAME: [bullet/body lines]} parsed from resume text."""
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
    """Return bullets for the closest-matching section, falling back to all."""
    sec_upper = (section or "").upper()
    for key in resume_bullets:
        if sec_upper in key.upper() or key.upper() in sec_upper:
            return resume_bullets[key]
    # fall back: every bullet across all sections
    return [b for bullets in resume_bullets.values() for b in bullets]


def _render_suggestion(sugg: dict, resume_bullets: dict[str, list[str]]):
    sugg_id  = sugg["id"]
    sel_key  = f"sel_{sugg_id}"
    edit_key = f"edit_{sugg_id}"
    section  = sugg.get("section") or "General"
    is_add   = sugg["type"] == "ADD"

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
                    on_change=_set_weave_original,
                    args=(sugg_id, target_key),
                )
                if has_target:
                    st.caption(f"Original: *{current_original}*")
                    st.text_area(
                        "Edit blended version",
                        value=sugg.get("edited_text") or current_original,
                        height=90,
                        key=edit_key,
                        label_visibility="collapsed",
                        on_change=_save_edit,
                        args=(sugg_id, edit_key),
                    )
                    st.caption("Edit the text above to blend the suggestion into the existing point.")
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
            f"{s.job_description[:70]}... [{s.status.value}]": s.id
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
    st.session_state["current_session_id"] = selected_id

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

        all_reqs_data = [{
            "id": r.id, "text": r.text, "category": r.category,
            "match_score": r.match_score, "match_detail": r.match_detail,
        } for r in all_reqs]

        suggs_by_req: dict[str, list[dict]] = {}
        for s in suggs:
            suggs_by_req.setdefault(s.requirement_id, []).append({
                "id": s.id,
                "type": s.type.value,
                "original_text": s.original_text,
                "suggested_text": s.suggested_text,
                "edited_text": s.edited_text,
                "is_selected": s.is_selected,
                "section": s.section,
            })

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
        } for a in audit_rows]

        # Primary resume for weave bullet list
        primary_resume = db.query(Resume).order_by(Resume.order).first()
        resume_text = primary_resume.content if primary_resume else ""

    resume_bullets = _parse_resume_bullets(resume_text)
    gap_reqs = [r for r in all_reqs_data if r["id"] in suggs_by_req]

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
        high = sum(1 for r in all_reqs_data if r["match_score"] >= 0.75)
        mid  = sum(1 for r in all_reqs_data if 0.5 <= r["match_score"] < 0.75)
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

    if not gap_reqs:
        st.success("No gaps found — your resume matches all extracted requirements well.")
        return

    st.subheader(f"Gaps & Suggestions ({len(gap_reqs)})")
    st.caption(
        "Check a suggestion to accept it. "
        "For additions, choose whether to add a new bullet or weave into an existing one."
    )

    for req in gap_reqs:
        req_suggs = suggs_by_req[req["id"]]
        score = req["match_score"]
        title = f"{_score_icon(score)} {req['text'][:90]}{'...' if len(req['text']) > 90 else ''} — {score:.0%}"

        with st.expander(title, expanded=(score < 0.5)):
            st.markdown(f"**{req['text']}**")
            st.caption(f"Category: `{req['category']}`  •  Score: {score:.0%}")
            st.markdown(f"*{req['match_detail']}*")
            st.markdown("---")
            for sugg in req_suggs:
                _render_suggestion(sugg, resume_bullets)

    # ── Content audit ─────────────────────────────────────────────────────────
    if audit_data:
        st.divider()
        st.subheader(f"Existing Content to Reconsider ({len(audit_data)})")
        st.caption(
            "These are bullets and statements already in your resume that may be "
            "hurting your match — either too generic or not relevant to this role."
        )

        _VERDICT_STYLE = {
            "remove":   ("🔴", "Remove",   "#dc3545"),
            "rephrase": ("🟡", "Rephrase", "#e6a817"),
        }

        by_section: dict[str, list[dict]] = {}
        for item in audit_data:
            by_section.setdefault(item["section"], []).append(item)

        for section, items in sorted(by_section.items()):
            with st.expander(f"**{section}** — {len(items)} item(s)", expanded=True):
                for item in items:
                    icon, label, color = _VERDICT_STYLE.get(
                        item["verdict"], ("⚪", item["verdict"].title(), "#888")
                    )
                    col_badge, col_content, col_dismiss = st.columns([1, 9, 1])
                    col_badge.markdown(
                        f"<span style='color:{color};font-weight:bold'>{icon} {label}</span>",
                        unsafe_allow_html=True,
                    )
                    col_content.markdown(f"**{item['text']}**")
                    col_content.caption(item["reason"])
                    if col_dismiss.button("✕", key=f"dismiss_{item['id']}",
                                          help="Dismiss this suggestion"):
                        _dismiss_audit(item["id"])
                        st.rerun()
                    st.markdown("")
