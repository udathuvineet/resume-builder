import uuid

import streamlit as st

from database.db import get_db
from database.models import ProjectsDocument, Resume, SampleResume
from services.pdf_parser import extract_text_from_pdf


def _delete(model_class, item_id: str):
    with get_db() as db:
        obj = db.query(model_class).filter_by(id=item_id).first()
        if obj:
            db.delete(obj)


def _swap_order(rid_a: str, rid_b: str):
    with get_db() as db:
        a = db.query(Resume).filter_by(id=rid_a).first()
        b = db.query(Resume).filter_by(id=rid_b).first()
        if a and b:
            a.order, b.order = b.order, a.order


def render():
    st.header("Document Library")
    st.caption("Upload the documents you want the AI to work with.")

    col1, col2, col3 = st.columns(3)

    # ── Resumes ──────────────────────────────────────────────────────────────
    with col1:
        st.subheader("Your Resumes")
        st.caption("PDFs to be optimized. First = primary.")

        uploaded = st.file_uploader(
            "Add resume PDF", type=["pdf"], key="resume_uploader",
            label_visibility="collapsed"
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
                st.success(f"Added {uploaded.name}")
                st.rerun()

        with get_db() as db:
            rows = db.query(Resume).order_by(Resume.order).all()
            resume_rows = [(r.id, r.filename, r.order) for r in rows]

        for i, (rid, fname, order) in enumerate(resume_rows):
            c1, c2, c3 = st.columns([4, 1, 1])
            label = fname if len(fname) <= 28 else fname[:25] + "..."
            c1.text(f"{'★ ' if i == 0 else ''}{label}")
            if c2.button("↑", key=f"up_{rid}", disabled=i == 0):
                prev_id = resume_rows[i - 1][0]
                _swap_order(rid, prev_id)
                st.rerun()
            if c3.button("🗑", key=f"dr_{rid}"):
                _delete(Resume, rid)
                st.rerun()

        if not resume_rows:
            st.info("No resumes yet.")

    # ── Sample Resumes ────────────────────────────────────────────────────────
    with col2:
        st.subheader("Sample Resumes")
        st.caption("Reference style for generation (optional).")

        uploaded_s = st.file_uploader(
            "Add sample PDF", type=["pdf"], key="sample_uploader",
            label_visibility="collapsed"
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
            st.success(f"Added {uploaded_s.name}")
            st.rerun()

        with get_db() as db:
            samples = [(s.id, s.filename) for s in db.query(SampleResume).all()]

        for sid, fname in samples:
            c1, c2 = st.columns([5, 1])
            c1.text(fname[:28] + ("..." if len(fname) > 28 else ""))
            if c2.button("🗑", key=f"ds_{sid}"):
                _delete(SampleResume, sid)
                st.rerun()

        if not samples:
            st.info("No sample resumes yet.")

    # ── Projects / Context ────────────────────────────────────────────────────
    with col3:
        st.subheader("Projects / Context")
        st.caption("Portfolios or extra context for the AI (optional).")

        uploaded_p = st.file_uploader(
            "Add context PDF", type=["pdf"], key="proj_uploader",
            label_visibility="collapsed"
        )
        if uploaded_p:
            pdf_bytes = uploaded_p.read()
            text = extract_text_from_pdf(pdf_bytes)
            with get_db() as db:
                db.add(ProjectsDocument(
                    id=str(uuid.uuid4()),
                    filename=uploaded_p.name,
                    content=text,
                ))
            st.success(f"Added {uploaded_p.name}")
            st.rerun()

        with get_db() as db:
            projects = [(p.id, p.filename) for p in db.query(ProjectsDocument).all()]

        for pid, fname in projects:
            c1, c2 = st.columns([5, 1])
            c1.text(fname[:28] + ("..." if len(fname) > 28 else ""))
            if c2.button("🗑", key=f"dp_{pid}"):
                _delete(ProjectsDocument, pid)
                st.rerun()

        if not projects:
            st.info("No project docs yet.")
