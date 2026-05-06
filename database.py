"""
CybeSure SecureAnswer — Database Models
© CybeSure Ltd. All rights reserved.
"""
import os
from datetime import datetime, timedelta
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean,
    DateTime, Float, ForeignKey, Text, Enum
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import enum

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./cybesure.db")
# Handle Render PostgreSQL URL format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Enums ─────────────────────────────────────────────────────────────────────

class SubscriptionTier(str, enum.Enum):
    STARTER   = "starter"    # Up to 50  — £5,000/yr
    BUSINESS  = "business"   # Up to 100 — £8,500/yr
    CORPORATE = "corporate"  # Up to 200 — £15,000/yr
    ENTERPRISE= "enterprise" # Up to 500 — £25,000/yr

class UserRole(str, enum.Enum):
    ADMIN       = "admin"       # Can upload questionnaires + documents, manage users
    CONTRIBUTOR = "contributor" # Can add comments to answers
    APPROVER    = "approver"    # Can approve/reject answers

class SubscriptionStatus(str, enum.Enum):
    ACTIVE    = "active"
    EXPIRED   = "expired"
    SUSPENDED = "suspended"
    TRIAL     = "trial"


# ── Subscription config ────────────────────────────────────────────────────────

SUBSCRIPTION_CONFIG = {
    SubscriptionTier.STARTER: {
        "name": "Starter",
        "limit": 50,
        "price_gbp": 5000,
        "description": "Up to 50 questionnaires per year"
    },
    SubscriptionTier.BUSINESS: {
        "name": "Business",
        "limit": 100,
        "price_gbp": 8500,
        "description": "Up to 100 questionnaires per year"
    },
    SubscriptionTier.CORPORATE: {
        "name": "Corporate",
        "limit": 200,
        "price_gbp": 15000,
        "description": "Up to 200 questionnaires per year"
    },
    SubscriptionTier.ENTERPRISE: {
        "name": "Enterprise",
        "limit": 500,
        "price_gbp": 25000,
        "description": "Up to 500 questionnaires per year"
    },
}

# Top-up pricing
TOPUP_CONFIG = {
    "single": {"qty": 1,  "price_gbp": 300,  "label": "1 questionnaire"},
    "bundle": {"qty": 10, "price_gbp": 1000, "label": "10 questionnaires"},
}


# ── Models ────────────────────────────────────────────────────────────────────

class Organisation(Base):
    __tablename__ = "organisations"

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(200), nullable=False)
    slug           = Column(String(100), unique=True, index=True)  # URL-safe identifier
    contact_email  = Column(String(200), nullable=False)
    created_at     = Column(DateTime, default=datetime.utcnow)

    # Subscription
    tier           = Column(Enum(SubscriptionTier), default=SubscriptionTier.STARTER)
    status         = Column(Enum(SubscriptionStatus), default=SubscriptionStatus.TRIAL)
    questionnaire_limit = Column(Integer, default=50)
    questionnaires_used = Column(Integer, default=0)
    topup_credits  = Column(Integer, default=0)  # Extra questionnaires purchased
    subscription_start = Column(DateTime, default=datetime.utcnow)
    subscription_end   = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=365))

    # Stripe
    stripe_customer_id     = Column(String(100), nullable=True)
    stripe_subscription_id = Column(String(100), nullable=True)

    users = relationship("User", back_populates="organisation")
    questionnaire_runs = relationship("QuestionnaireRun", back_populates="organisation")

    @property
    def total_limit(self):
        return self.questionnaire_limit + self.topup_credits

    @property
    def remaining(self):
        return max(0, self.total_limit - self.questionnaires_used)

    @property
    def is_active(self):
        return (
            self.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL)
            and self.subscription_end > datetime.utcnow()
        )


class User(Base):
    __tablename__ = "users"

    id             = Column(Integer, primary_key=True, index=True)
    org_id         = Column(Integer, ForeignKey("organisations.id"), nullable=False)
    email          = Column(String(200), unique=True, index=True, nullable=False)
    full_name      = Column(String(200), nullable=False)
    hashed_password= Column(String(200), nullable=False)
    role           = Column(Enum(UserRole), default=UserRole.CONTRIBUTOR)
    is_active      = Column(Boolean, default=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    last_login     = Column(DateTime, nullable=True)

    organisation = relationship("Organisation", back_populates="users")


class QuestionnaireRun(Base):
    __tablename__ = "questionnaire_runs"

    id             = Column(Integer, primary_key=True, index=True)
    org_id         = Column(Integer, ForeignKey("organisations.id"), nullable=False)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename       = Column(String(500), nullable=True)
    question_count = Column(Integer, default=0)
    status         = Column(String(50), default="processing")  # processing, complete, failed
    created_at     = Column(DateTime, default=datetime.utcnow)
    completed_at   = Column(DateTime, nullable=True)
    is_topup       = Column(Boolean, default=False)  # Was this from topup credits?

    organisation = relationship("Organisation", back_populates="questionnaire_runs")


class TopupPurchase(Base):
    __tablename__ = "topup_purchases"

    id                  = Column(Integer, primary_key=True, index=True)
    org_id              = Column(Integer, ForeignKey("organisations.id"), nullable=False)
    qty                 = Column(Integer, nullable=False)
    price_gbp           = Column(Float, nullable=False)
    stripe_payment_id   = Column(String(200), nullable=True)
    status              = Column(String(50), default="pending")
    created_at          = Column(DateTime, default=datetime.utcnow)


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
