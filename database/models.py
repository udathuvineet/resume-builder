from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean,
    DateTime, LargeBinary, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()


class SessionStatus(str, enum.Enum):
    PENDING = "PENDING"
    ANALYZING = "ANALYZING"
    READY = "READY"
    GENERATING = "GENERATING"
    COMPLETE = "COMPLETE"


class SuggestionType(str, enum.Enum):
    MODIFY = "MODIFY"
    ADD = "ADD"


class AuditVerdict(str, enum.Enum):
    REPHRASE = "rephrase"
    REMOVE = "remove"


class RefineVerdict(str, enum.Enum):
    APPROVED = "approved"
    IMPROVED = "improved"
    FLAGGED = "flagged"


class UserProfile(Base):
    __tablename__ = "user_profile"
    id = Column(String, primary_key=True, default="default")
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    linkedin = Column(String, nullable=True)
    location = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SampleResume(Base):
    __tablename__ = "sample_resumes"
    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    pdf_data = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Resume(Base):
    __tablename__ = "resumes"
    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    pdf_data = Column(LargeBinary, nullable=False)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProjectsDocument(Base):
    __tablename__ = "projects_documents"
    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AnalysisSession(Base):
    __tablename__ = "analysis_sessions"
    id = Column(String, primary_key=True)
    job_description = Column(Text, nullable=False)
    status = Column(SAEnum(SessionStatus), default=SessionStatus.PENDING)
    overall_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    requirements = relationship("Requirement", back_populates="session", cascade="all, delete-orphan", order_by="Requirement.order")
    suggestions = relationship("Suggestion", back_populates="session", cascade="all, delete-orphan")
    audit_items = relationship("ContentAuditItem", back_populates="session", cascade="all, delete-orphan")


class Requirement(Base):
    __tablename__ = "requirements"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    category = Column(String, nullable=False)
    match_score = Column(Float, default=0.0)
    match_detail = Column(Text, nullable=True)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("AnalysisSession", back_populates="requirements")
    suggestions = relationship("Suggestion", back_populates="requirement", cascade="all, delete-orphan")


class Suggestion(Base):
    __tablename__ = "suggestions"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False)
    requirement_id = Column(String, ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    original_text = Column(Text, nullable=True)
    suggested_text = Column(Text, nullable=False)
    edited_text = Column(Text, nullable=True)
    is_selected = Column(Boolean, default=False)
    type = Column(SAEnum(SuggestionType), default=SuggestionType.MODIFY)
    section = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("AnalysisSession", back_populates="suggestions")
    requirement = relationship("Requirement", back_populates="suggestions")


class ContentAuditItem(Base):
    __tablename__ = "content_audit_items"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False)
    section = Column(String, nullable=True)
    text = Column(Text, nullable=False)
    verdict = Column(SAEnum(AuditVerdict), nullable=False)
    reason = Column(Text, nullable=False)
    is_dismissed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("AnalysisSession", back_populates="audit_items")


class RefinedSuggestion(Base):
    __tablename__ = "refined_suggestions"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False)
    suggestion_id = Column(String, ForeignKey("suggestions.id", ondelete="CASCADE"), nullable=False)
    verdict = Column(SAEnum(RefineVerdict), nullable=False)
    improved_text = Column(Text, nullable=True)
    critique = Column(Text, nullable=False)
    is_applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("AnalysisSession")
    suggestion = relationship("Suggestion")


class GPT4Suggestion(Base):
    __tablename__ = "gpt4_suggestions"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False)
    requirement_id = Column(String, ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    original_text = Column(Text, nullable=True)
    suggested_text = Column(Text, nullable=False)
    edited_text = Column(Text, nullable=True)
    is_selected = Column(Boolean, default=False)
    type = Column(SAEnum(SuggestionType), default=SuggestionType.MODIFY)
    section = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("AnalysisSession")
    requirement = relationship("Requirement")
