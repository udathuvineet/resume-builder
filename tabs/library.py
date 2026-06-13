import uuid

import streamlit as st

from database.db import get_db
from database.models import ProjectsDocument, Resume, SampleResume
from services.pdf_parser import extract_text_from_docx, extract_text_from_pdf


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


def render():
    st.header("Document Library")
    st.caption("Upload the documents you want the AI to work with.")

    col1, col2, col3 = st.columns(3)

    # ── Resumes ──────────────────────────────────────────────────────────────
    with col1:
        st.subheader("Your Resumes")
        st.caption("PDFs to be optimized. First = primary.")

        uploaded = st.file_uploader(
            "Add resume PDF", type=["pdf"],
            key=_uploader_key("resume"),
            label_visibility="collapsed",
        )
        if uploaded:
            pdf_bytes = uploaded.read()
            text = extract_text_from_pdf(pdf_bytes)
            if not text:
                st.error("No text extracted — ensure the PDF is not a scanned image.")
            else:
                with get_db() as db:
                    count = db.query(Resume).count()
                    db.add(Resume(
                        id=str(uuid.uuid4()),
                        filename=uploaded.name,
                        content=text,
                        pdf_data=pdf_bytes,
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
                    prev_id = resume_rows[i - 1][0]
                    _swap_order(rid, prev_id)
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
        st.caption("Reference style for generation (optional).")

        uploaded_s = st.file_uploader(
            "Add sample PDF", type=["pdf"],
            key=_uploader_key("sample"),
            label_visibility="collapsed",
        )
        if uploaded_s:
            pdf_bytes = uploaded_s.read()
            text = extract_text_from_pdf(pdf_bytes)
            with get_db() as db:
                db.add(SampleResume(
                    id=str(uuid.uuid4()),
                    filename=uploaded_s.name,
                    content=text,
                    pdf_data=pdf_bytes,
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
        st.caption("Portfolios or extra context for the AI (optional).")

        uploaded_p = st.file_uploader(
            "Add context PDF or DOCX", type=["pdf", "docx"],
            key=_uploader_key("proj"),
            label_visibility="collapsed",
        )
        if uploaded_p:
            file_bytes = uploaded_p.read()
            if uploaded_p.name.endswith(".docx"):
                text = extract_text_from_docx(file_bytes)
            else:
                text = extract_text_from_pdf(file_bytes)
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
