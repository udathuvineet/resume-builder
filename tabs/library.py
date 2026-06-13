import uuid

import streamlit as st

from database.db import get_db
from database.models import ProjectsDocument, Resume, SampleResume, UserProfile
from services.pdf_parser import extract_text_from_docx, extract_text_from_pdf

ACCEPTED_TYPES = ["pdf", "docx"]


def _extract_text(uploaded_file) -> str:
    file_bytes = uploaded_file.read()
    if uploaded_file.name.lower().endswith(".docx"):
        return extract_text_from_docx(file_bytes)
    return extract_text_from_pdf(file_bytes)


def _delete(model_class, item_id: str) -> str | None:
    try:
        with get_db() as db:
            obj = db.query(model_class).filter(model_class.id == item_id).first()
            if obj:
                db.delete(obj)
        return None
    except Exception as e:
        return str(e)


def _delete_all(model_class) -> str | None:
    try:
        with get_db() as db:
            db.query(model_class).delete()
        return None
    except Exception as e:
        return str(e)


def _swap_order(rid_a: str, rid_b: str):
    with get_db() as db:
        a = db.query(Resume).filter(Resume.id == rid_a).first()
        b = db.query(Resume).filter(Resume.id == rid_b).first()
        if a and b:
            a.order, b.order = b.order, a.order


def _uploader_key(name: str) -> str:
    k = f"_uploader_gen_{name}"
    if k not in st.session_state:
        st.session_state[k] = 0
    return f"{name}_{st.session_state[k]}"


def _reset_uploader(name: str):
    st.session_state[f"_uploader_gen_{name}"] += 1


def _load_profile() -> dict:
    with get_db() as db:
        p = db.query(UserProfile).filter_by(id="default").first()
        if not p:
            return {"name": "", "email": "", "phone": "", "linkedin": "", "location": ""}
        return {"name": p.name or "", "email": p.email or "", "phone": p.phone or "",
                "linkedin": p.linkedin or "", "location": p.location or ""}


def _save_profile(name, email, phone, linkedin, location):
    with get_db() as db:
        p = db.query(UserProfile).filter_by(id="default").first()
        if not p:
            p = UserProfile(id="default")
            db.add(p)
        p.name = name
        p.email = email
        p.phone = phone
        p.linkedin = linkedin
        p.location = location


def render():
    st.header("Document Library")
    st.caption("Upload the documents you want the AI to work with.")

    # ── Profile ───────────────────────────────────────────────────────────────
    with st.expander("👤 Contact Profile", expanded=True):
        profile = _load_profile()
        c1, c2 = st.columns(2)
        name     = c1.text_input("Full name",     value=profile["name"],     key="prof_name")
        email    = c2.text_input("Email",          value=profile["email"],    key="prof_email")
        phone    = c1.text_input("Phone",          value=profile["phone"],    key="prof_phone")
        location = c2.text_input("Location",       value=profile["location"], key="prof_location",
                                 placeholder="City, State")
        linkedin = st.text_input("LinkedIn URL",   value=profile["linkedin"], key="prof_linkedin",
                                 placeholder="linkedin.com/in/yourname")
        if st.button("Save profile", key="save_profile"):
            _save_profile(name, email, phone, linkedin, location)
            st.success("Profile saved.")

    st.divider()
    col1, col2, col3 = st.columns(3)

    # ── Resumes ──────────────────────────────────────────────────────────────
    with col1:
        st.subheader("Your Resumes")
        st.caption("PDF or DOCX to be optimized. First = primary.")

        uploaded = st.file_uploader(
            "Add resume", type=ACCEPTED_TYPES,
            key=_uploader_key("resume"),
            label_visibility="collapsed",
        )
        if uploaded:
            text = _extract_text(uploaded)
            if not text:
                st.error("No text extracted — ensure the file is not a scanned image.")
            else:
                file_bytes = uploaded.getvalue()
                with get_db() as db:
                    count = db.query(Resume).count()
                    db.add(Resume(
                        id=str(uuid.uuid4()),
                        filename=uploaded.name,
                        content=text,
                        pdf_data=file_bytes,
                        order=count,
                    ))
                _reset_uploader("resume")
                st.rerun()

        with get_db() as db:
            rows = db.query(Resume).order_by(Resume.order).all()
            resume_rows = [(r.id, r.filename, r.order) for r in rows]

        if resume_rows:
            for i, (rid, fname, order) in enumerate(resume_rows):
                c1, c2, c3 = st.columns([4, 1, 1])
                label = fname if len(fname) <= 28 else fname[:25] + "..."
                c1.text(f"{'★ ' if i == 0 else ''}{label}")
                if c2.button("↑", key=f"up_{rid}", disabled=i == 0):
                    _swap_order(rid, resume_rows[i - 1][0])
                    st.rerun()
                if c3.button("🗑", key=f"dr_{rid}"):
                    err = _delete(Resume, rid)
                    if err:
                        st.error(f"Delete failed: {err}")
                    else:
                        st.rerun()

            if st.button("Clear all resumes", key="clear_resumes"):
                err = _delete_all(Resume)
                if err:
                    st.error(f"Clear failed: {err}")
                else:
                    st.rerun()
        else:
            st.info("No resumes yet.")

    # ── Sample Resumes ────────────────────────────────────────────────────────
    with col2:
        st.subheader("Sample Resumes")
        st.caption("PDF or DOCX reference style for generation (optional).")

        uploaded_s = st.file_uploader(
            "Add sample", type=ACCEPTED_TYPES,
            key=_uploader_key("sample"),
            label_visibility="collapsed",
        )
        if uploaded_s:
            text = _extract_text(uploaded_s)
            file_bytes = uploaded_s.getvalue()
            with get_db() as db:
                db.add(SampleResume(
                    id=str(uuid.uuid4()),
                    filename=uploaded_s.name,
                    content=text,
                    pdf_data=file_bytes,
                ))
            _reset_uploader("sample")
            st.rerun()

        with get_db() as db:
            samples = [(s.id, s.filename) for s in db.query(SampleResume).all()]

        if samples:
            for sid, fname in samples:
                c1, c2 = st.columns([5, 1])
                c1.text(fname[:28] + ("..." if len(fname) > 28 else ""))
                if c2.button("🗑", key=f"ds_{sid}"):
                    err = _delete(SampleResume, sid)
                    if err:
                        st.error(f"Delete failed: {err}")
                    else:
                        st.rerun()

            if st.button("Clear all samples", key="clear_samples"):
                err = _delete_all(SampleResume)
                if err:
                    st.error(f"Clear failed: {err}")
                else:
                    st.rerun()
        else:
            st.info("No sample resumes yet.")

    # ── Projects / Context ────────────────────────────────────────────────────
    with col3:
        st.subheader("Projects / Context")
        st.caption("PDF or DOCX portfolios or extra context for the AI (optional).")

        uploaded_p = st.file_uploader(
            "Add context doc", type=ACCEPTED_TYPES,
            key=_uploader_key("proj"),
            label_visibility="collapsed",
        )
        if uploaded_p:
            text = _extract_text(uploaded_p)
            with get_db() as db:
                db.add(ProjectsDocument(
                    id=str(uuid.uuid4()),
                    filename=uploaded_p.name,
                    content=text,
                ))
            _reset_uploader("proj")
            st.rerun()

        with get_db() as db:
            projects = [(p.id, p.filename) for p in db.query(ProjectsDocument).all()]

        if projects:
            for pid, fname in projects:
                c1, c2 = st.columns([5, 1])
                c1.text(fname[:28] + ("..." if len(fname) > 28 else ""))
                if c2.button("🗑", key=f"dp_{pid}"):
                    err = _delete(ProjectsDocument, pid)
                    if err:
                        st.error(f"Delete failed: {err}")
                    else:
                        st.rerun()

            if st.button("Clear all docs", key="clear_docs"):
                err = _delete_all(ProjectsDocument)
                if err:
                    st.error(f"Clear failed: {err}")
                else:
                    st.rerun()
        else:
            st.info("No project docs yet.")
