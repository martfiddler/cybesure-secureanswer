from __future__ import annotations

import enum
import os
from datetime import datetime
from typing import Generator

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./secureanswer.db")
    # Render's managed Postgres URL may use the deprecated SQLAlchemy scheme.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


SQLALCHEMY_DATABASE_URL = _database_url()
connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    OWNER = "owner"
    MANAGER = "manager"
    USER = "user"


class Organisation(Base):
    __tablename__ = "organisations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    subscription_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    users = relationship("User", back_populates="organisation")
    questionnaire_runs = relationship("QuestionnaireRun", back_populates="organisation")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(
        Enum(UserRole, values_callable=lambda roles: [role.value for role in roles]),
        default=UserRole.USER,
        nullable=False,
    )
    is_active = Column(Boolean, default=True, nullable=False)
    subscription_active = Column(Boolean, default=True, nullable=False)
    subscription_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    organisation_id = Column(Integer, ForeignKey("organisations.id"), nullable=True)

    organisation = relationship("Organisation", back_populates="users")
    questionnaire_runs = relationship("QuestionnaireRun", back_populates="user")


class QuestionnaireRun(Base):
    __tablename__ = "questionnaire_runs"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    questionnaire_filename = Column(String(512), nullable=True)
    document_names = Column(Text, nullable=True)
    questions_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    organisation_id = Column(Integer, ForeignKey("organisations.id"), nullable=True)

    user = relationship("User", back_populates="questionnaire_runs")
    organisation = relationship("Organisation", back_populates="questionnaire_runs")


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
