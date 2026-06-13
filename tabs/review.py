import streamlit as st
import plotly.graph_objects as go

from database.db import get_db
from database.models import AnalysisSession, Requirement, Suggestion


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


def _save_edit(sugg_id: str, key: str):
    with get_db() as db:
        s = db.query(Suggestion).filter_by(id=sugg_id).first()
        if s:
            s.edited_text = st.session_state[key]


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

    selected_label = st.selectbox("Analysis session", list(options.keys()), index=default_idx)
    selected_id = options[selected_label]
    st.session_state["current_session_id"] = selected_id

    # ── Load data ─────────────────────────────────────────────────────────────
    with get_db() as db:
        session = db.query(AnalysisSession).filter_by(id=selected_id).first()
        if not session:
            st.error("Session not found.")
            return

        overall_score = session.overall_score or 0.0
        reqs = (
            db.query(Requirement)
            .filter_by(session_id=selected_id)
            .order_by(Requirement.match_score)
            .all()
        )
        suggs = db.query(Suggestion).filter_by(session_id=selected_id).all()

        reqs_data = [{
            "id": r.id, "text": r.text, "category": r.category,
            "match_score": r.match_score, "match_detail": r.match_detail,
        } for r in reqs]

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
        high = sum(1 for r in reqs_data if r["match_score"] >= 0.75)
        mid = sum(1 for r in reqs_data if 0.5 <= r["match_score"] < 0.75)
        low = sum(1 for r in reqs_data if r["match_score"] < 0.5)
        selected_count = sum(
            len([s for s in suggs if s["is_selected"]])
            for suggs in suggs_by_req.values()
        )
        st.metric("Strong matches 🟢", high)
        st.metric("Partial matches 🟡", mid)
        st.metric("Gaps 🔴", low)
        st.metric("Improvements selected", selected_count)

    st.divider()
    st.subheader("Requirements")
    st.caption("Toggle suggestions to accept them. Edit the text if needed, then click outside to save.")

    # ── Requirements list ─────────────────────────────────────────────────────
    for req in reqs_data:
        req_suggs = suggs_by_req.get(req["id"], [])
        score = req["match_score"]
        icon = _score_icon(score)
        title = f"{icon} {req['text'][:80]}{'...' if len(req['text']) > 80 else ''} — {score:.0%}"

        with st.expander(title, expanded=(score < 0.5)):
            st.markdown(f"**{req['text']}**")
            st.caption(f"Category: `{req['category']}`  •  Score: {score:.0%}")
            st.markdown(f"*{req['match_detail']}*")

            if req_suggs:
                st.markdown("---")
                st.markdown("**Suggested improvements:**")
                for sugg in req_suggs:
                    sel_key = f"sel_{sugg['id']}"
                    edit_key = f"edit_{sugg['id']}"
                    action = "Modify" if sugg["type"] == "MODIFY" else "Add"
                    preview = (sugg["suggested_text"] or "")[:70]

                    st.checkbox(
                        f"{action}: {preview}{'...' if len(sugg['suggested_text']) > 70 else ''}",
                        value=sugg["is_selected"],
                        key=sel_key,
                        on_change=_toggle_suggestion,
                        args=(sugg["id"], sel_key),
                    )

                    if st.session_state.get(sel_key, sugg["is_selected"]):
                        if sugg.get("original_text"):
                            st.caption(f"Replaces: *{sugg['original_text'][:100]}*")
                        st.text_area(
                            "Edit suggestion",
                            value=sugg.get("edited_text") or sugg["suggested_text"],
                            height=80,
                            key=edit_key,
                            label_visibility="collapsed",
                            on_change=_save_edit,
                            args=(sugg["id"], edit_key),
                        )
