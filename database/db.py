import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from database.models import Base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./resume_builder.db")

# Railway provides postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _run_migrations():
    """Apply additive schema changes that create_all cannot handle on existing tables."""
    with engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text(
                "ALTER TABLE content_audit_items "
                "ADD COLUMN IF NOT EXISTS accepted_replacement TEXT"
            ))
        else:
            rows = conn.execute(text("PRAGMA table_info(content_audit_items)")).fetchall()
            existing = {r[1] for r in rows}
            if "accepted_replacement" not in existing:
                conn.execute(text(
                    "ALTER TABLE content_audit_items ADD COLUMN accepted_replacement TEXT"
                ))
        conn.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    try:
        _run_migrations()
    except Exception:
        pass  # table may not exist yet on first boot — create_all handles it


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
