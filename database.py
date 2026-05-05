import enum
import os
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./secureanswer.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"


class Organisation(Base):
    __tablename__ = "organisations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    subscription_status = Column(String(50), nullable=False, default="trial")
    subscription_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    users = relationship("User", back_populates="organisation", cascade="all, delete-orphan")
    questionnaire_runs = relationship("QuestionnaireRun", back_populates="organisation", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.USER)
    is_active = Column(Boolean, nullable=False, default=True)
    organisation_id = Column(Integer, ForeignKey("organisations.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    organisation = relationship("Organisation", back_populates="users")
    questionnaire_runs = relationship("QuestionnaireRun", back_populates="user")


class QuestionnaireRun(Base):
    __tablename__ = "questionnaire_runs"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    questionnaire_filename = Column(String(512), nullable=True)
    document_names = Column(Text, nullable=True)
    questions_count = Column(Integer, nullable=False, default=0)
    answers_count = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="created")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    organisation_id = Column(Integer, ForeignKey("organisations.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="questionnaire_runs")
    organisation = relationship("Organisation", back_populates="questionnaire_runs")


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
