import io
import os
import sys
import json
import uuid
import gc
import re
import requests
import smtplib
import hashlib
import enum
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import List, Optional, Dict

import numpy as np
import pandas as pd
import pdfplumber
import faiss
import anthropic

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from docx import Document
from docx.shared import RGBColor, Pt
from openai import OpenAI
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

# ── Inline Auth & Database ────────────────────────────────────────────────────
AUTH_ENABLED = False
try:
    from sqlalchemy import (create_engine, Column, Integer, String, Boolean,
                            DateTime, Float, ForeignKey)
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker, relationship
    from passlib.context import CryptContext
    from jose import JWTError, jwt

    _DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./cybesure.db")
    if _DB_URL.startswith("postgres://"):
        _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)
    _connect_args = {"check_same_thread": False} if "sqlite" in _DB_URL else {}
    _engine = create_engine(_DB_URL, connect_args=_connect_args)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    _Base = declarative_base()

    SUBSCRIPTION_CONFIG = {
        "starter":   {"name":"Starter",    "limit":50,  "price_gbp":5000},
        "business":  {"name":"Business",   "limit":100, "price_gbp":8500},
        "corporate": {"name":"Corporate",  "limit":200, "price_gbp":15000},
        "enterprise":{"name":"Enterprise", "limit":500, "price_gbp":25000},
    }

    class Organisation(_Base):
        __tablename__ = "organisations"
        id                  = Column(Integer, primary_key=True, index=True)
        name                = Column(String(200), nullable=False)
        slug                = Column(String(100), unique=True, index=True)
        contact_email       = Column(String(200), nullable=False)
        created_at          = Column(DateTime, default=datetime.utcnow)
        tier                = Column(String(50), default="starter")
        status              = Column(String(50), default="trial")
        questionnaire_limit = Column(Integer, default=50)
        questionnaires_used = Column(Integer, default=0)
        topup_credits       = Column(Integer, default=0)
        subscription_start  = Column(DateTime, default=datetime.utcnow)
        subscription_end    = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=14))
        users               = relationship("User", back_populates="organisation")

        @property
        def total_limit(self): return self.questionnaire_limit + self.topup_credits
        @property
        def remaining(self): return max(0, self.total_limit - self.questionnaires_used)
        @property
        def is_active(self):
            return self.status in ("active","trial") and self.subscription_end > datetime.utcnow()

    class User(_Base):
        __tablename__ = "users"
        id              = Column(Integer, primary_key=True, index=True)
        org_id          = Column(Integer, ForeignKey("organisations.id"), nullable=False)
        email           = Column(String(200), unique=True, index=True, nullable=False)
        full_name       = Column(String(200), nullable=False)
        hashed_password = Column(String(200), nullable=False)
        role            = Column(String(50), default="contributor")
        is_active       = Column(Boolean, default=True)
        created_at      = Column(DateTime, default=datetime.utcnow)
        last_login      = Column(DateTime, nullable=True)
        organisation    = relationship("Organisation", back_populates="users")

    class QuestionnaireRun(_Base):
        __tablename__ = "questionnaire_runs"
        id             = Column(Integer, primary_key=True, index=True)
        org_id         = Column(Integer, ForeignKey("organisations.id"), nullable=False)
        user_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
        filename       = Column(String(500), nullable=True)
        question_count = Column(Integer, default=0)
        status         = Column(String(50), default="processing")
        created_at     = Column(DateTime, default=datetime.utcnow)

    _Base.metadata.create_all(bind=_engine)

    _JWT_SECRET = os.environ.get("JWT_SECRET", "cybesure-secureanswer-2025")
    _pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    _oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")

    def hash_password(p: str) -> str:
        pre = hashlib.sha256(p.encode()).hexdigest()
        return _pwd.hash(pre)

    def verify_password(plain: str, hashed: str) -> bool:
        pre = hashlib.sha256(plain.encode()).hexdigest()
        return _pwd.verify(pre, hashed)

    def make_token(data: dict) -> str:
        d = data.copy()
        d["exp"] = datetime.utcnow() + timedelta(hours=24)
        return jwt.encode(d, _JWT_SECRET, algorithm="HS256")

    def get_db():
        db = _SessionLocal()
        try: yield db
        finally: db.close()

    def get_auth_user(token: str = Depends(_oauth2)):
        try:
            payload = jwt.decode(token, _JWT_SECRET, algorithms=["HS256"])
            uid = payload.get("sub")
        except JWTError:
            raise HTTPException(401, "Invalid token")
        db = _SessionLocal()
        user = db.query(User).filter(User.id==int(uid), User.is_active==True).first()
        db.close()
        if not user: raise HTTPException(401, "User not found")
        return user

    AUTH_ENABLED = True
    print("Auth system initialised successfully")

except Exception as _auth_err:
    import traceback as _tb
    print(f"Auth not available: {_auth_err}")
    print(_tb.format_exc())
    async def get_db(): yield None
    def get_auth_user(): return None
    def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()
    def verify_password(p, h): return hash_password(p) == h
    def make_token(d): return "no-auth"

    def check_subscription(org): pass

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SESSIONS: dict = {}
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 400        # Smaller chunks = more precise retrieval
CHUNK_OVERLAP = 100     # More overlap = fewer gaps
TOP_K = 20              # Retrieve more chunks per question
EMBED_BATCH = 20
MAX_CHUNKS = 1200       # Allow more chunks = more document coverage
DOC_EXTENSIONS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv', '.txt'}

app = FastAPI(
    title="CybeSure SecureAnswer",
    description="© CybeSure Ltd. All rights reserved. SecureAnswer™ is a trademark of CybeSure Ltd.",
    version="1.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Powered-By"] = "CybeSure SecureAnswer"
    response.headers["X-Copyright"] = "© CybeSure Ltd. All rights reserved."
    response.headers["X-Data-Residency"] = "UK"
    response.headers["X-GDPR-Compliant"] = "true"
    return response

@app.on_event("startup")
async def startup():
    print(f"CybeSure SecureAnswer starting — AUTH_ENABLED={AUTH_ENABLED}")

# Include auth and billing routes if available
if AUTH_ENABLED and 'auth_router' in dir() and auth_router:
    app.include_router(auth_router, tags=["auth"])

# ── Inline Auth Routes ────────────────────────────────────────────────────────

if AUTH_ENABLED:

    class RegisterRequest(BaseModel):
        org_name: str
        contact_email: str
        admin_name: str
        admin_email: str
        password: str

    class InviteUserRequest(BaseModel):
        email: str
        full_name: str
        role: str = "contributor"
        password: str

    @app.post("/auth/register")
    async def register(req: RegisterRequest, db=Depends(get_db)):
        if db is None:
            raise HTTPException(503, "Database not ready")
        if db.query(User).filter(User.email == req.admin_email).first():
            raise HTTPException(400, "Email already registered")
        slug = req.org_name.lower().replace(" ", "-")[:50]
        base = slug; i = 1
        while db.query(Organisation).filter(Organisation.slug == slug).first():
            slug = f"{base}-{i}"; i += 1
        org = Organisation(
            name=req.org_name, slug=slug, contact_email=req.contact_email,
            tier="starter", status="trial", questionnaire_limit=50,
            subscription_end=datetime.utcnow() + timedelta(days=14)
        )
        db.add(org); db.flush()
        admin = User(
            org_id=org.id, email=req.admin_email, full_name=req.admin_name,
            hashed_password=hash_password(req.password), role="admin", is_active=True
        )
        db.add(admin); db.commit()
        return {"message": "Organisation registered. 14-day trial activated.", "org_id": org.id}

    @app.post("/auth/login")
    async def login(form: OAuth2PasswordRequestForm = Depends(), db=Depends(get_db)):
        if db is None:
            raise HTTPException(503, "Database not ready")
        user = db.query(User).filter(User.email == form.username).first()
        if not user or not verify_password(form.password, user.hashed_password):
            raise HTTPException(401, "Incorrect email or password")
        if not user.is_active:
            raise HTTPException(403, "Account inactive")
        org = db.query(Organisation).filter(Organisation.id == user.org_id).first()
        user.last_login = datetime.utcnow(); db.commit()
        token = make_token({"sub": str(user.id), "org_id": user.org_id})
        cfg = SUBSCRIPTION_CONFIG.get(org.tier, {})
        return {
            "access_token": token, "token_type": "bearer",
            "user_id": user.id, "user_name": user.full_name,
            "user_role": user.role, "org_id": org.id, "org_name": org.name,
            "subscription_tier": org.tier,
            "tier_name": cfg.get("name", org.tier),
            "questionnaires_remaining": org.remaining,
            "subscription_expires": org.subscription_end.isoformat()
        }

    @app.get("/auth/me")
    async def get_me(user=Depends(get_auth_user), db=Depends(get_db)):
        org = db.query(Organisation).filter(Organisation.id == user.org_id).first()
        return {
            "user_id": user.id, "email": user.email, "full_name": user.full_name,
            "role": user.role, "org_id": org.id, "org_name": org.name,
            "tier": org.tier, "status": org.status,
            "remaining": org.remaining, "total_limit": org.total_limit,
            "used": org.questionnaires_used,
            "subscription_end": org.subscription_end.isoformat(),
            "is_active": org.is_active
        }

    @app.get("/org/dashboard")
    async def org_dashboard(user=Depends(get_auth_user), db=Depends(get_db)):
        org = db.query(Organisation).filter(Organisation.id == user.org_id).first()
        users_count = db.query(User).filter(User.org_id==org.id, User.is_active==True).count()
        recent = db.query(QuestionnaireRun).filter(
            QuestionnaireRun.org_id==org.id
        ).order_by(QuestionnaireRun.created_at.desc()).limit(10).all()
        cfg = SUBSCRIPTION_CONFIG.get(org.tier, {})
        days_left = max(0, (org.subscription_end - datetime.utcnow()).days)
        return {
            "org_name": org.name, "tier": org.tier, "tier_name": cfg.get("name", org.tier),
            "status": org.status, "is_active": org.is_active,
            "questionnaire_limit": org.questionnaire_limit,
            "questionnaires_used": org.questionnaires_used,
            "topup_credits": org.topup_credits, "remaining": org.remaining,
            "total_limit": org.total_limit,
            "subscription_end": org.subscription_end.isoformat(),
            "days_remaining": days_left, "users_count": users_count,
            "recent_runs": [{"id":r.id,"filename":r.filename,"status":r.status,
                            "created_at":r.created_at.isoformat()} for r in recent],
            "subscription_plans": SUBSCRIPTION_CONFIG,
            "topup_options": {"single":{"qty":1,"price_gbp":300},"bundle":{"qty":10,"price_gbp":1000}}
        }

    @app.post("/admin/users/invite")
    async def invite_user(req: InviteUserRequest, user=Depends(get_auth_user), db=Depends(get_db)):
        if user.role != "admin":
            raise HTTPException(403, "Admin access required")
        if db.query(User).filter(User.email == req.email).first():
            raise HTTPException(400, "Email already registered")
        new_user = User(
            org_id=user.org_id, email=req.email, full_name=req.full_name,
            hashed_password=hash_password(req.password), role=req.role, is_active=True
        )
        db.add(new_user); db.commit(); db.refresh(new_user)
        return {"message": f"User {req.full_name} added as {req.role}", "user_id": new_user.id}

    @app.get("/admin/users")
    async def list_users(user=Depends(get_auth_user), db=Depends(get_db)):
        if user.role != "admin":
            raise HTTPException(403, "Admin access required")
        users = db.query(User).filter(User.org_id == user.org_id).all()
        return [{"id":u.id,"email":u.email,"full_name":u.full_name,"role":u.role,
                 "is_active":u.is_active,"created_at":u.created_at.isoformat()} for u in users]

    @app.post("/billing/create-topup-session")
    async def create_topup(topup_type: str, user=Depends(get_auth_user), db=Depends(get_db)):
        if user.role != "admin":
            raise HTTPException(403, "Admin access required")
        options = {"single":{"qty":1,"price_gbp":300},"bundle":{"qty":10,"price_gbp":1000}}
        opt = options.get(topup_type)
        if not opt: raise HTTPException(400, "Invalid topup type")
        stripe_key = os.environ.get("STRIPE_SECRET_KEY","")
        if not stripe_key:
            raise HTTPException(503, "Payment not configured. Contact support@cybesure.com")
        try:
            import stripe
            stripe.api_key = stripe_key
            org = db.query(Organisation).filter(Organisation.id==user.org_id).first()
            app_url = os.environ.get("APP_URL","https://cybesure-qa-platform.onrender.com")
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price_data":{"currency":"gbp","product_data":{"name":f"CybeSure SecureAnswer — {opt['qty']} questionnaire(s)"},"unit_amount":int(opt['price_gbp']*100)},"quantity":1}],
                mode="payment",
                success_url=f"{app_url}/billing/success",
                cancel_url=f"{app_url}/billing/cancel",
                customer_email=user.email,
                metadata={"org_id":str(org.id),"topup_type":topup_type,"qty":str(opt['qty'])}
            )
            return {"checkout_url": session.url}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/billing/success", response_class=HTMLResponse)
    async def billing_success():
        return HTMLResponse("<html><body style='font-family:Arial;background:#0b1829;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'><div style='text-align:center'><div style='font-size:48px'>✅</div><h2 style='color:#00d4a0'>Payment Successful</h2><p style='color:#7a9cbf'>Credits added to your account.</p><a href='/' style='color:#00c8e0'>Return to SecureAnswer</a></div></body></html>")

    @app.get("/billing/cancel", response_class=HTMLResponse)
    async def billing_cancel():
        return HTMLResponse("<html><body style='font-family:Arial;background:#0b1829;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'><div style='text-align:center'><div style='font-size:48px'>❌</div><h2 style='color:#f5a623'>Payment Cancelled</h2><a href='/' style='color:#00c8e0'>Return to SecureAnswer</a></div></body></html>")

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>CybeSure SecureAnswer is running</h1>")

# ── questionnaire extraction ──────────────────────────────────────────────────

def extract_questions_from_excel(data: bytes) -> list[str]:
    """
    Extract questions from Excel files (.xls and .xlsx).
    Handles: Row 1 = headers (Ref, Question, Answer)
    Questions are in the column headed 'Question'.
    """
    questions = []
    
    # Try multiple engines to handle both .xls and .xlsx
    engines_to_try = ['openpyxl', 'xlrd', None]
    
    for engine in engines_to_try:
        try:
            kwargs = {'engine': engine} if engine else {}
            xl = pd.ExcelFile(io.BytesIO(data), **kwargs)
            
            for sheet_name in xl.sheet_names:
                try:
                    read_kwargs = {'sheet_name': sheet_name}
                    if engine:
                        read_kwargs['engine'] = engine
                    
                    # Read with header
                    df = pd.read_excel(io.BytesIO(data), **read_kwargs)
                    
                    if df.empty:
                        continue

                    # Normalise column names
                    df.columns = [str(c).strip().lower() for c in df.columns]

                    # Strategy 1: Find column named 'question' or similar
                    q_col = None
                    for col in df.columns:
                        if col in ('question', 'questions', 'requirement', 
                                   'requirements', 'control', 'controls', 
                                   'ask', 'query', 'description', 'b'):
                            q_col = col
                            break

                    # Strategy 2: Second column if first is 'ref' or number
                    if q_col is None and len(df.columns) >= 2:
                        first_col = df.columns[0]
                        if first_col in ('ref', 'no', 'id', '#', 'num', 'number'):
                            q_col = df.columns[1]

                    # Strategy 3: Longest text column
                    if q_col is None:
                        best = 0
                        for col in df.columns:
                            vals = df[col].dropna().astype(str)
                            score = sum(1 for v in vals if len(v) > 15)
                            if score > best:
                                best = score
                                q_col = col

                    if q_col is not None:
                        for val in df[q_col].dropna():
                            text = str(val).strip()
                            if (text and len(text) > 3 and 
                                text.lower() not in ['nan', 'none', 'n/a', '-', 
                                                      'question', 'questions']):
                                questions.append(text)

                    # Fallback: all text cells > 10 chars
                    if not questions:
                        for col in df.columns:
                            for val in df[col].dropna():
                                text = str(val).strip()
                                if len(text) > 10 and text.lower() not in ['nan', 'none', 'n/a']:
                                    questions.append(text)

                except Exception as e:
                    print(f"Sheet {sheet_name} error: {e}")
                    # Try without header
                    try:
                        rk = {'sheet_name': sheet_name, 'header': None}
                        if engine:
                            rk['engine'] = engine
                        df2 = pd.read_excel(io.BytesIO(data), **rk)
                        header_row = 0
                        q_col_idx = 1
                        for i, row in df2.iterrows():
                            for j, val in enumerate(row):
                                if pd.notna(val) and 'question' in str(val).lower():
                                    header_row = i
                                    q_col_idx = j
                                    break
                        for i, row in df2.iterrows():
                            if i <= header_row:
                                continue
                            val = row.iloc[q_col_idx] if q_col_idx < len(row) else None
                            if pd.notna(val):
                                text = str(val).strip()
                                if text and len(text) > 3:
                                    questions.append(text)
                    except Exception:
                        pass

            if questions:
                break  # Stop trying engines once we have results
                
        except Exception as e:
            print(f"Engine {engine} failed: {e}")
            continue

    # Deduplicate
    seen = set()
    unique = []
    for q in questions:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def extract_questions_from_csv(data: bytes) -> list[str]:
    """Extract questions from CSV files."""
    questions = []
    try:
        for enc in ['utf-8', 'latin-1', 'cp1252']:
            try:
                df = pd.read_csv(io.BytesIO(data), encoding=enc)
                break
            except Exception:
                continue

        df.columns = [str(c).strip().lower() for c in df.columns]

        q_col = None
        for col in df.columns:
            if col in ('question', 'questions', 'requirement', 'requirements', 'control', 'description'):
                q_col = col
                break

        if q_col is None:
            best = 0
            for col in df.columns:
                vals = df[col].dropna().astype(str)
                score = sum(1 for v in vals if len(v) > 15)
                if score > best:
                    best = score
                    q_col = col

        if q_col:
            for val in df[q_col].dropna():
                text = str(val).strip()
                if text and len(text) > 3 and text.lower() not in ['nan', 'none', 'n/a']:
                    questions.append(text)

    except Exception as e:
        print(f"CSV parse error: {e}")
    return questions


def extract_questions_from_docx(data: bytes) -> list[str]:
    """Extract questions from Word documents."""
    questions = []
    try:
        doc = Document(io.BytesIO(data))
        for p in doc.paragraphs:
            text = p.text.strip()
            if text and len(text) > 3:
                questions.append(text)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text and len(text) > 3:
                        questions.append(text)
    except Exception as e:
        print(f"DOCX parse error: {e}")
    return questions


def extract_questions_from_pdf(data: bytes) -> list[str]:
    """Extract questions from PDF files."""
    questions = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    for line in text.split('\n'):
                        line = line.strip()
                        if line and len(line) > 5:
                            questions.append(line)
    except Exception as e:
        print(f"PDF parse error: {e}")
    return questions


def extract_questions(filename: str, data: bytes) -> list[str]:
    """Route to correct extractor."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("xlsx", "xls"):
        return extract_questions_from_excel(data)
    elif ext == "csv":
        return extract_questions_from_csv(data)
    elif ext in ("docx", "doc"):
        return extract_questions_from_docx(data)
    elif ext == "pdf":
        return extract_questions_from_pdf(data)
    else:
        return [l.strip() for l in data.decode("utf-8", errors="ignore").split('\n') if l.strip() and len(l.strip()) > 5]

# ── document file parsing ─────────────────────────────────────────────────────

def parse_pdf(data: bytes) -> list[str]:
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t and t.strip():
                    pages.append(t[:4000])
    except Exception as e:
        print(f"PDF error: {e}")
    return pages

def parse_docx(data: bytes) -> list[str]:
    try:
        doc = Document(io.BytesIO(data))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text:
                    texts.append(row_text)
        return texts
    except Exception as e:
        print(f"DOCX error: {e}")
        return []

def parse_doc(data: bytes) -> list[str]:
    try:
        doc = Document(io.BytesIO(data))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text:
                    texts.append(row_text)
        if texts:
            return texts
    except Exception:
        pass
    try:
        text = data.decode('latin-1', errors='ignore')
        chunks = re.findall(r'[A-Za-z0-9\s\.\,\!\?\:\;\-\(\)\'\"]{20,}', text)
        readable = ' '.join(chunks)
        if readable.strip():
            return [readable[:6000]]
    except Exception:
        pass
    return []

def parse_excel_doc(data: bytes) -> list[str]:
    try:
        # Read all sheets
        xl = pd.ExcelFile(io.BytesIO(data))
        all_rows = []
        for sheet in xl.sheet_names:
            df = pd.read_excel(io.BytesIO(data), sheet_name=sheet)
            for _, row in df.iterrows():
                r = " | ".join(str(v) for v in row.values if pd.notna(v) and str(v).strip())
                if r.strip():
                    all_rows.append(r)
        return all_rows
    except Exception as e:
        print(f"Excel error: {e}")
        return []

def parse_csv_doc(data: bytes) -> list[str]:
    try:
        df = pd.read_csv(io.BytesIO(data))
        rows = []
        for _, row in df.iterrows():
            r = " | ".join(str(v) for v in row.values if pd.notna(v) and str(v).strip())
            if r.strip():
                rows.append(r)
        return rows
    except Exception as e:
        print(f"CSV error: {e}")
        return []

def parse_file(filename: str, data: bytes) -> list[str]:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "pdf": return parse_pdf(data)
    elif ext == "docx": return parse_docx(data)
    elif ext == "doc": return parse_doc(data)
    elif ext in ("xlsx", "xls"): return parse_excel_doc(data)
    elif ext == "csv": return parse_csv_doc(data)
    else: return [data.decode("utf-8", errors="ignore")[:5000]]

# ── URL fetching ──────────────────────────────────────────────────────────────

def is_document_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DOC_EXTENSIONS)

def fetch_document_from_url(url: str) -> tuple[str, bytes]:
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; CybeSure/1.0)', 'Accept': '*/*'}
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    filename = url.split('/')[-1].split('?')[0] or 'document'
    cd = resp.headers.get('Content-Disposition', '')
    if 'filename=' in cd:
        filename = cd.split('filename=')[-1].strip().strip('"\'')
    if '.' not in filename:
        ct = resp.headers.get('Content-Type', '')
        if 'pdf' in ct: filename += '.pdf'
        elif 'word' in ct: filename += '.docx'
        elif 'excel' in ct: filename += '.xlsx'
    return filename, resp.content

def discover_documents_from_page(url: str) -> list[str]:
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; CybeSure/1.0)'}
    try:
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch page {url}: {e}")
        return []
    soup = BeautifulSoup(resp.text, 'html.parser')
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    doc_urls = []
    for tag in soup.find_all('a', href=True):
        href = tag['href']
        if href.startswith('http'): full_url = href
        elif href.startswith('/'): full_url = base + href
        else: full_url = urljoin(url, href)
        if is_document_url(full_url):
            doc_urls.append(full_url)
    return list(set(doc_urls))

def fetch_all_from_url(url: str) -> list[tuple[str, bytes]]:
    results = []
    # Google Drive single file
    if 'drive.google.com/file' in url or ('docs.google.com' in url and '/d/' in url):
        file_id = None
        if '/d/' in url: file_id = url.split('/d/')[1].split('/')[0]
        elif 'id=' in url: file_id = url.split('id=')[1].split('&')[0]
        if file_id:
            try:
                fname, data = fetch_document_from_url(
                    f"https://drive.google.com/uc?export=download&id={file_id}")
                results.append((fname, data))
                return results
            except Exception as e:
                print(f"Google Drive download failed: {e}")
    # Direct document URL
    if is_document_url(url):
        try:
            fname, data = fetch_document_from_url(url)
            results.append((fname, data))
        except Exception as e:
            print(f"Failed to fetch {url}: {e}")
        return results
    # Page scan
    try:
        doc_urls = discover_documents_from_page(url)
        if doc_urls:
            for doc_url in doc_urls[:20]:
                try:
                    fname, data = fetch_document_from_url(doc_url)
                    results.append((fname, data))
                except Exception as e:
                    print(f"Failed {doc_url}: {e}")
        else:
            resp = requests.get(url, timeout=20)
            soup = BeautifulSoup(resp.text, 'html.parser')
            text = soup.get_text(separator='\n', strip=True)
            if text:
                results.append(('webpage.txt', text.encode('utf-8')))
    except Exception as e:
        print(f"Page scan failed: {e}")
    return results

# ── chunking ──────────────────────────────────────────────────────────────────

def simple_chunk(text: str) -> list[str]:
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end == len(words):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

# ── embeddings ────────────────────────────────────────────────────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    all_emb = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = openai_client.embeddings.create(
            input=texts[i:i+EMBED_BATCH], model=EMBEDDING_MODEL)
        all_emb.extend([r.embedding for r in resp.data])
        gc.collect()
    return all_emb

# ── retrieval ─────────────────────────────────────────────────────────────────

def expand_query(q: str) -> str:
    """Expand query with cyber security domain terms for better retrieval."""
    return (f"{q} policy procedure control implementation process "
            f"cyber security information security governance compliance "
            f"management system requirement standard evidence")

def retrieve(session_id: str, question: str) -> list[str]:
    """Multi-angle retrieval for maximum document coverage."""
    index, chunks, _ = SESSIONS[session_id]  # unpack 3-tuple

    queries = [
        expand_query(question),
        question,
        question.lower().replace('?','').replace('do you','').replace('how do you','').strip(),
    ]

    seen = set()
    results = []
    for query in queries:
        try:
            qv = np.array([embed_texts([query])[0]], dtype="float32")
            k = min(TOP_K, len(chunks))
            distances, idxs = index.search(qv, k)
            for dist, i in zip(distances[0], idxs[0]):
                c = chunks[i]
                if c not in seen:
                    seen.add(c)
                    results.append((dist, c))
        except Exception as e:
            print(f"Retrieval error: {e}")
            continue

    results.sort(key=lambda x: x[0])
    return [c for _, c in results[:TOP_K]]

# ── Claude ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior cyber security compliance expert writing comprehensive answers to a security questionnaire on behalf of an organisation.

You will be given extracts from the organisation's security policies and documents. Synthesise ALL relevant information across ALL provided extracts to write thorough, confident, professional answers — exactly as the organisation would respond to an auditor or customer due diligence questionnaire.

CRITICAL RULES:
- Always write as "We" — you ARE the organisation
- NEVER mention document names, policy titles, file names or "the documents"
- NEVER say what is missing, not covered, or not evidenced
- NEVER say "based on available documentation" or any hedging phrase
- NEVER reference "the extracts" or "the materials"
- Draw on ALL relevant content across every provided extract
- Be comprehensive — cover every relevant aspect, process, control and detail found
- Write with confidence and authority like a seasoned CISO responding to a customer
- Include specific details: tools, frequencies, roles, processes, technical controls

ANSWER STRUCTURE — always follow this exact 5-part pattern:

1. CONTROL STATEMENT — One confident opening sentence stating what we do
2. IMPLEMENTATION DETAIL — Comprehensive how: specific tools, processes, frequencies, technical controls, procedures, thresholds
3. GOVERNANCE & OWNERSHIP — Who owns and oversees this (specific roles/functions/teams)
4. STANDARDS ALIGNMENT — Frameworks this aligns to (only include if evidenced in the material: ISO 27001, NIST, GDPR, PCI DSS, Cyber Essentials etc)
5. EVIDENCE — Specific records, logs, reports, registers or artefacts that demonstrate this control

GOLD STANDARD EXAMPLE:
Q: "How do you control user access to systems?"
A: "We operate a role-based access control (RBAC) model aligned to least privilege principles across all systems and environments. Access is provisioned via a formal joiner-mover-leaver (JML) process integrated with HR workflows, with all access requests requiring documented approval from system owners and enforced through centralised identity management. Privileged access is strictly controlled with multi-factor authentication enforced for all administrative accounts, and standing privileged access is minimised through just-in-time provisioning. Access reviews are conducted quarterly with system owners required to validate user access appropriateness, and any discrepancies are remediated within defined SLAs. Ownership of access control sits with the Head of IT Operations with oversight from the Information Security function. This aligns with ISO 27001:2022 Annex A.5.15 / A.8.2, NIST AC family controls and PCI DSS v4 Requirement 7. Evidence includes access control policy records, IAM system audit logs, JML workflow records, quarterly access review reports and privilege access management logs."

Respond in JSON only — no markdown fences:
{
  "confidence": "Yes|No|Partial",
  "confidence_pct": 85,
  "explanation": "Comprehensive structured answer following the 5-part pattern — written confidently as the organisation with maximum detail",
  "sources": ["Specific control, process, tool or detail from the source material that supports this answer"]
}"""


IMPROVED_PROMPT = """You are a senior cyber security compliance consultant reviewing a draft answer to a security questionnaire question.

Your task is to take the existing answer and improve it to score 95%+ in a formal security assessment.

To achieve 95%+, the improved answer must:
1. Be more specific about technical controls, tools, configurations and thresholds
2. Reference additional relevant security frameworks and standards
3. Strengthen governance language with named roles and oversight structures  
4. Add more specific evidence types that would satisfy an auditor
5. Use more precise, quantified language (e.g. specific timeframes, frequency, SLAs)
6. Ensure every sentence adds audit value

RULES:
- Write as "We" — first person organisational voice
- NEVER mention documents or policy names
- NEVER say what is missing
- Build on the existing answer — do not replace it entirely
- Make it more detailed, more specific, more evidenced

Respond in JSON only:
{
  "improved_answer": "The enhanced answer that would score 95%+ in a formal assessment",
  "improvement_notes": "2-3 bullet points explaining what was strengthened and why it scores higher"
}"""


def ask_claude(question: str, chunks: list[str]) -> dict:
    """Generate comprehensive answer plus an improved 95%+ version."""
    context = "\n\n---\n\n".join(chunks)

    # Step 1: Generate primary answer
    msg = claude_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Organisation's security policy extracts:\n\n{context}\n\n"
                f"---\n\n"
                f"Security questionnaire question: {question}\n\n"
                f"Write a comprehensive, detailed answer as the organisation. "
                f"Use ALL relevant information from every extract above. "
                f"Follow the 5-part structure precisely. "
                f"Be specific about processes, tools, frequencies and controls. "
                f"Respond in JSON only."
            )
        }]
    )
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    result = json.loads(text)
    if "confidence_pct" not in result:
        result["confidence_pct"] = {"Yes": 90, "Partial": 65, "No": 15}.get(
            result.get("confidence", "Partial"), 50)

    # Step 2: Generate improved 95%+ version
    try:
        improved_msg = claude_client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1200,
            system=IMPROVED_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"Current answer:\n{result.get('explanation', '')}\n\n"
                    f"Additional context from organisation's documents:\n{context[:3000]}\n\n"
                    f"Improve this answer to score 95%+. Respond in JSON only."
                )
            }]
        )
        imp_text = improved_msg.content[0].text.strip().replace("```json","").replace("```","").strip()
        imp_data = json.loads(imp_text)
        result["improved_answer"] = imp_data.get("improved_answer", "")
        result["improvement_notes"] = imp_data.get("improvement_notes", "")
    except Exception as e:
        print(f"Improved answer error: {e}")
        result["improved_answer"] = ""
        result["improvement_notes"] = ""

    return result

# ── index builder ─────────────────────────────────────────────────────────────

# Answer cache: session_id -> {question_text: answer_dict}
ANSWER_CACHE: dict = {}

# Approval store: token -> approval_record
APPROVALS: dict = {}

# Email config from environment
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
APP_URL   = os.environ.get("APP_URL", "https://cybesure-qa-platform.onrender.com")

def build_index(raw_texts: list[str], session_id: str, doc_names: list[str] = None) -> dict:
    chunks = []
    for text in raw_texts:
        if text.strip():
            chunks.extend(simple_chunk(text))
    if not chunks:
        raise HTTPException(400, "Could not extract any text from the provided documents.")
    if len(chunks) > MAX_CHUNKS:
        chunks = chunks[:MAX_CHUNKS]
    embeddings = embed_texts(chunks)
    vectors = np.array(embeddings, dtype="float32")
    del embeddings
    gc.collect()
    index = faiss.IndexFlatL2(vectors.shape[1])
    index.add(vectors)
    del vectors
    gc.collect()
    # Store index, chunks AND document names
    SESSIONS[session_id] = (index, chunks, doc_names or [])
    # Initialise answer cache for this session
    ANSWER_CACHE[session_id] = {}
    return {"session_id": session_id, "chunks_created": len(chunks)}

# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "CybeSure SecureAnswer",
        "copyright": "© CybeSure Ltd. All rights reserved.",
        "data_residency": "UK",
        "gdpr_compliant": True,
        "data_retention": "none — in-memory processing only"
    }

@app.post("/upload/questionnaire")
async def upload_questionnaire(
    file: UploadFile = File(...),
    request: Request = None,
    db=Depends(get_db)
):
    # Check subscription if auth enabled
    if AUTH_ENABLED and db:
        try:
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if token:
                from auth import decode_token
                payload = decode_token(token)
                user_id = int(payload.get("sub", 0))
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    org = db.query(Organisation).filter(Organisation.id == user.org_id).first()
                    if org:
                        check_subscription(org)
                        org.questionnaires_used += 1
                        run = QuestionnaireRun(
                            org_id=org.id, user_id=user.id,
                            filename=file.filename, status="processing"
                        )
                        db.add(run)
                        db.commit()
        except Exception as e:
            print(f"Auth check skipped: {e}")

    data = await file.read()
    import base64
    original_b64 = base64.b64encode(data).decode()
    questions_text = extract_questions(file.filename, data)

    if not questions_text:
        raise HTTPException(
            400,
            f"No questions found in '{file.filename}'. "
            f"Expected a column named 'Question' in your Excel file."
        )

    questions = [
        {"id": i, "text": q.strip(), "category": None}
        for i, q in enumerate(questions_text)
        if q.strip() and len(q.strip()) > 3
    ]

    return {
        "questions": questions,
        "total": len(questions),
        "filename": file.filename,
        "original_file": original_b64
    }

@app.post("/upload/documents")
async def upload_documents(
    files: List[UploadFile] = File(...),
    session_id: str = None,
    db=Depends(get_db)
):
    if not session_id:
        session_id = str(uuid.uuid4())
    raw, failed, doc_names = [], [], []
    for f in files:
        try:
            data = await f.read()
            parsed = parse_file(f.filename, data)
            if parsed:
                raw.extend(parsed)
                doc_names.append(f.filename)
            else:
                failed.append(f.filename)
            del data
            gc.collect()
        except Exception as e:
            print(f"Failed {f.filename}: {e}")
            failed.append(f.filename)
    if not raw:
        raise HTTPException(400, "Could not extract text from any documents.")
    result = build_index(raw, session_id, doc_names)
    result["files_processed"] = len(files) - len(failed)
    result["files_failed"] = failed
    result["document_names"] = doc_names
    return result

class UrlRequest(BaseModel):
    urls: List[str]
    session_id: Optional[str] = None

@app.post("/upload/documents-url")
async def upload_documents_url(req: UrlRequest):
    session_id = req.session_id or str(uuid.uuid4())
    raw, files_processed, files_failed, doc_names = [], 0, [], []
    for url in req.urls:
        try:
            file_pairs = fetch_all_from_url(url)
            if not file_pairs:
                files_failed.append(url)
                continue
            for fname, data in file_pairs:
                try:
                    parsed = parse_file(fname, data)
                    if parsed:
                        raw.extend(parsed)
                        files_processed += 1
                        doc_names.append(fname)
                    else:
                        files_failed.append(fname)
                except Exception as e:
                    print(f"Parse error {fname}: {e}")
                    files_failed.append(fname)
                del data
                gc.collect()
        except Exception as e:
            print(f"URL error {url}: {e}")
            files_failed.append(url)
    if not raw:
        raise HTTPException(
            400,
            "Could not fetch or read documents from the provided URLs. "
            "For Google Drive: share each file individually with 'Anyone with link can view' "
            "and paste the individual file share link."
        )
    result = build_index(raw, session_id, doc_names)
    result["files_processed"] = files_processed
    result["files_failed"] = files_failed
    result["documents_found"] = doc_names
    return result


# ── OneTrust / portal integration ────────────────────────────────────────────

class PortalRequest(BaseModel):
    portal_url: str
    session_id: str
    questions: List[Question]

@app.post("/portal/onetrust")
async def answer_onetrust(req: PortalRequest):
    """
    Fetch a questionnaire from a OneTrust or similar portal URL,
    extract questions, answer them, and return results ready to paste back.
    Supports OneTrust, Whistic, SecurityScorecard, and generic portal links.
    """
    if req.session_id not in SESSIONS:
        raise HTTPException(404, "Session not found. Upload documents first.")

    # Fetch the portal page
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; CybeSure/1.0)',
            'Accept': 'text/html,application/json,*/*'
        }
        resp = requests.get(req.portal_url, headers=headers, timeout=30)
        resp.raise_for_status()

        # Try JSON response first (API-based portals)
        try:
            portal_data = resp.json()
            # Extract questions from common portal formats
            portal_questions = []
            if isinstance(portal_data, list):
                for item in portal_data:
                    q = item.get('question') or item.get('text') or item.get('name')
                    if q:
                        portal_questions.append(q)
            elif isinstance(portal_data, dict):
                items = portal_data.get('questions') or portal_data.get('items') or []
                for item in items:
                    q = item.get('question') or item.get('text') or item.get('name')
                    if q:
                        portal_questions.append(q)
        except Exception:
            # HTML page — extract questions from text
            soup = BeautifulSoup(resp.text, 'html.parser')
            portal_questions = []
            # Look for question-like elements
            for tag in soup.find_all(['p', 'li', 'td', 'label', 'h3', 'h4']):
                text = tag.get_text(strip=True)
                if text and len(text) > 10 and ('?' in text or len(text) > 30):
                    portal_questions.append(text)

    except Exception as e:
        raise HTTPException(400, f"Could not fetch portal URL: {str(e)}")

    # Answer all questions
    _, _, doc_names = SESSIONS[req.session_id]
    cache = ANSWER_CACHE.get(req.session_id, {})
    results = []

    questions_to_answer = req.questions if req.questions else [
        Question(id=i, text=q) for i, q in enumerate(portal_questions)
    ]

    for q in questions_to_answer:
        if q.text in cache:
            results.append(cache[q.text])
            continue
        chunks = retrieve(req.session_id, q.text)
        r = ask_claude(q.text, chunks)
        result = {
            "question_id": q.id,
            "question": q.text,
            "confidence": r.get("confidence", "Partial"),
            "confidence_pct": r.get("confidence_pct", 50),
            "explanation": r.get("explanation", ""),
            "sources": r.get("sources", []),
            "document_names": ", ".join(doc_names)
        }
        cache[q.text] = result
        results.append(result)
        gc.collect()

    ANSWER_CACHE[req.session_id] = cache
    return {
        "portal_url": req.portal_url,
        "results": results,
        "total": len(results),
        "ready_to_paste": True,
        "instructions": "Copy each 'explanation' field and paste into the corresponding question field in your portal."
    }

class Question(BaseModel):
    id: int
    text: str
    category: Optional[str] = None

class AnswerRequest(BaseModel):
    session_id: str
    questions: List[Question]

@app.post("/answer")
async def answer(
    req: AnswerRequest,
    db=Depends(get_db)
):
    if req.session_id not in SESSIONS:
        raise HTTPException(404, "Session not found. Upload documents first.")

    _, _, doc_names = SESSIONS[req.session_id]
    doc_names_str = ", ".join(doc_names) if doc_names else ""
    cache = ANSWER_CACHE.get(req.session_id, {})

    results = []
    for q in req.questions:
        if q.text in cache:
            results.append(cache[q.text])
            continue

        chunks = retrieve(req.session_id, q.text)
        r = ask_claude(q.text, chunks)

        result = {
            "question_id": q.id,
            "question": q.text,
            "category": q.category,
            "confidence": r.get("confidence", "Partial"),
            "confidence_pct": r.get("confidence_pct", 50),
            "explanation": r.get("explanation", ""),
            "improved_answer": r.get("improved_answer", ""),
            "improvement_notes": r.get("improvement_notes", ""),
            "sources": r.get("sources", []),
            "document_names": doc_names_str,
            "approval_status": "pending",
            "approved_by": None,
            "approval_token": str(uuid.uuid4())
        }
        cache[q.text] = result
        results.append(result)
        gc.collect()

    ANSWER_CACHE[req.session_id] = cache
    return {"results": results, "total": len(results)}


# ── Approval workflow ─────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html_body: str) -> bool:
    """Send email via SMTP. Returns True if sent successfully."""
    if not SMTP_USER or not SMTP_PASS:
        print("Email not configured — SMTP_USER/SMTP_PASS env vars missing")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


class ApprovalRequest(BaseModel):
    session_id: str
    question_id: int
    question: str
    answer: str
    improved_answer: str
    approver_email: str
    approver_name: str
    requester_name: Optional[str] = "CybeSure SecureAnswer"


class ApprovalDecision(BaseModel):
    token: str
    decision: str  # "approved" or "rejected"
    comments: Optional[str] = ""


@app.post("/approval/request")
async def request_approval(req: ApprovalRequest, background_tasks: BackgroundTasks):
    """Send an approval request email to a manager or SME."""
    token = hashlib.sha256(f"{req.session_id}-{req.question_id}-{uuid.uuid4()}".encode()).hexdigest()[:32]
    
    # Store approval record
    APPROVALS[token] = {
        "token": token,
        "session_id": req.session_id,
        "question_id": req.question_id,
        "question": req.question,
        "answer": req.answer,
        "improved_answer": req.improved_answer,
        "approver_email": req.approver_email,
        "approver_name": req.approver_name,
        "requester_name": req.requester_name,
        "status": "pending",
        "comments": "",
        "requested_at": datetime.utcnow().isoformat(),
        "decided_at": None
    }

    approve_url = f"{APP_URL}/approval/decide?token={token}&decision=approved"
    reject_url  = f"{APP_URL}/approval/decide?token={token}&decision=rejected"
    review_url  = f"{APP_URL}/approval/review/{token}"

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto">
      <div style="background:#0f2035;padding:24px 32px;border-bottom:3px solid #00c8e0">
        <h1 style="color:#fff;font-size:20px;margin:0">Cybe<span style="color:#00c8e0">Sure</span> SecureAnswer</h1>
        <p style="color:#7a9cbf;font-size:13px;margin:4px 0 0">AI Security Questionnaire System</p>
      </div>
      <div style="padding:32px;background:#f9fafb;border:1px solid #e2e8f0">
        <h2 style="color:#0f2035;font-size:18px;margin:0 0 8px">Answer Approval Request</h2>
        <p style="color:#4a6a8a;font-size:14px;margin:0 0 24px">
          <strong>{req.requester_name}</strong> is requesting your approval for the following questionnaire answer.
        </p>

        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:4px;padding:20px;margin-bottom:20px">
          <p style="font-size:12px;color:#4a6a8a;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin:0 0 8px">Question</p>
          <p style="color:#0f2035;font-size:15px;font-weight:600;margin:0 0 20px">{req.question}</p>

          <p style="font-size:12px;color:#4a6a8a;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin:0 0 8px">Proposed Answer</p>
          <p style="color:#334155;font-size:14px;line-height:1.7;margin:0 0 20px;padding:12px;background:#f8fafc;border-left:3px solid #00c8e0">{req.answer}</p>

          {"<p style='font-size:12px;color:#4a6a8a;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin:0 0 8px'>Enhanced Answer (95%+ Score)</p><p style='color:#334155;font-size:14px;line-height:1.7;margin:0 0 8px;padding:12px;background:#f0fdf4;border-left:3px solid #00d4a0'>" + req.improved_answer + "</p>" if req.improved_answer else ""}
        </div>

        <div style="display:flex;gap:12px;margin-bottom:24px">
          <a href="{approve_url}" style="display:inline-block;background:#00c8e0;color:#0f2035;padding:12px 28px;text-decoration:none;font-weight:700;font-size:14px;border-radius:3px">
            ✓ Approve Answer
          </a>
          <a href="{reject_url}" style="display:inline-block;background:#fff;color:#e84855;padding:12px 28px;text-decoration:none;font-weight:700;font-size:14px;border-radius:3px;border:2px solid #e84855">
            ✗ Reject / Request Changes
          </a>
        </div>

        <p style="color:#7a9cbf;font-size:12px">
          Or <a href="{review_url}" style="color:#00c8e0">view full details and add comments</a> before deciding.
        </p>
      </div>
      <div style="padding:16px 32px;background:#0f2035;text-align:center">
        <p style="color:#4a6a8a;font-size:11px;margin:0">CybeSure SecureAnswer — AI Compliance Engine | cybesure.com</p>
      </div>
    </div>
    """

    background_tasks.add_task(
        send_email,
        req.approver_email,
        f"Answer Approval Required: {req.question[:60]}...",
        html
    )

    return {
        "token": token,
        "status": "pending",
        "message": f"Approval request sent to {req.approver_email}",
        "review_url": review_url
    }


@app.get("/approval/review/{token}", response_class=HTMLResponse)
async def review_approval(token: str):
    """Show approval review page for approver."""
    if token not in APPROVALS:
        return HTMLResponse("<h2>Approval request not found or expired.</h2>", status_code=404)
    
    rec = APPROVALS[token]
    approve_url = f"{APP_URL}/approval/decide?token={token}&decision=approved"
    reject_url  = f"{APP_URL}/approval/decide?token={token}&decision=rejected"
    
    status_colour = {"approved": "#00d4a0", "rejected": "#e84855", "pending": "#f5a623"}.get(rec["status"], "#f5a623")
    
    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>CybeSure — Answer Approval</title>
      <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Open+Sans:wght@400;500&display=swap" rel="stylesheet">
      <style>
        body {{font-family:'Open Sans',sans-serif;background:#0b1829;color:#dce8f5;margin:0;padding:0}}
        .header {{background:#0f2035;border-bottom:3px solid #00c8e0;padding:20px 40px;display:flex;align-items:center;gap:12px}}
        .logo {{font-family:'Montserrat',sans-serif;font-size:22px;font-weight:700;color:#fff}}
        .logo em {{color:#00c8e0;font-style:normal}}
        .container {{max-width:800px;margin:40px auto;padding:0 24px}}
        .card {{background:#0f2035;border:1px solid #1e3a5a;padding:28px;margin-bottom:20px;border-radius:4px}}
        .label {{font-size:11px;font-weight:700;color:#7a9cbf;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px}}
        .question {{font-size:18px;font-weight:600;color:#fff;line-height:1.5;margin-bottom:24px}}
        .answer-box {{background:#152a42;border-left:3px solid #00c8e0;padding:16px;font-size:14px;line-height:1.8;color:#dce8f5;border-radius:2px;margin-bottom:16px}}
        .improved-box {{background:#0d2a1f;border-left:3px solid #00d4a0;padding:16px;font-size:14px;line-height:1.8;color:#dce8f5;border-radius:2px}}
        .status-badge {{display:inline-block;padding:4px 14px;border-radius:2px;font-size:12px;font-weight:700;background:{status_colour}22;color:{status_colour};border:1px solid {status_colour}55;margin-bottom:20px}}
        .btn-approve {{display:inline-block;background:#00c8e0;color:#0f2035;padding:12px 32px;text-decoration:none;font-weight:700;font-size:14px;border-radius:3px;margin-right:12px;font-family:'Montserrat',sans-serif}}
        .btn-reject {{display:inline-block;background:transparent;color:#e84855;padding:12px 32px;text-decoration:none;font-weight:700;font-size:14px;border-radius:3px;border:2px solid #e84855;font-family:'Montserrat',sans-serif}}
        .comments {{width:100%;background:#152a42;border:1px solid #1e3a5a;color:#dce8f5;padding:12px;font-size:14px;border-radius:3px;margin-bottom:16px;min-height:80px;font-family:'Open Sans',sans-serif}}
        .decided {{background:#1a2f4a;border:1px solid #2d5a80;padding:20px;border-radius:4px;text-align:center}}
      </style>
    </head>
    <body>
    <div class="header">
      <div class="logo">Cybe<em>Sure</em></div>
      <span style="color:#7a9cbf;font-size:14px;margin-left:8px">Answer Approval Review</span>
    </div>
    <div class="container">
      <div class="card">
        <div class="status-badge">{rec['status'].upper()}</div>
        <div class="label">Question</div>
        <div class="question">{rec['question']}</div>
        <div class="label">Proposed Answer</div>
        <div class="answer-box">{rec['answer']}</div>
        {"<div class='label'>Enhanced Answer (95%+ Score)</div><div class='improved-box'>" + rec['improved_answer'] + "</div>" if rec.get('improved_answer') else ""}
      </div>
      {"<div class='decided'><p style='font-size:18px;font-weight:600;color:" + status_colour + "'>" + rec['status'].upper() + "</p><p style='color:#7a9cbf'>" + (rec.get('comments') or 'No comments') + "</p></div>" if rec['status'] != 'pending' else f"""
      <div class="card">
        <div class="label">Your Decision</div>
        <textarea class="comments" id="comments" placeholder="Add any comments or requested changes (optional)..."></textarea>
        <a href="{approve_url}" class="btn-approve" onclick="addComments(this,'approved')">✓ Approve</a>
        <a href="{reject_url}" class="btn-reject" onclick="addComments(this,'rejected')">✗ Reject / Request Changes</a>
      </div>
      <script>
        function addComments(el, decision) {{
          const c = document.getElementById('comments').value;
          el.href = `{APP_URL}/approval/decide?token={token}&decision=${{decision}}&comments=${{encodeURIComponent(c)}}`;
        }}
      </script>
      """}
    </div>
    </body>
    </html>
    """)


@app.get("/approval/decide")
async def decide_approval(token: str, decision: str, comments: str = "", background_tasks: BackgroundTasks = None):
    """Process approval decision and notify requester."""
    if token not in APPROVALS:
        return HTMLResponse("<h2>Approval request not found or expired.</h2>", status_code=404)
    
    if decision not in ("approved", "rejected"):
        raise HTTPException(400, "Decision must be 'approved' or 'rejected'")
    
    rec = APPROVALS[token]
    rec["status"] = decision
    rec["comments"] = comments
    rec["decided_at"] = datetime.utcnow().isoformat()

    status_colour = "#00d4a0" if decision == "approved" else "#e84855"
    status_word = "Approved" if decision == "approved" else "Rejected"

    # Send notification back to requester
    notify_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto">
      <div style="background:#0f2035;padding:24px 32px;border-bottom:3px solid #00c8e0">
        <h1 style="color:#fff;font-size:20px;margin:0">Cybe<span style="color:#00c8e0">Sure</span> SecureAnswer</h1>
      </div>
      <div style="padding:32px;background:#f9fafb;border:1px solid #e2e8f0">
        <h2 style="color:{status_colour};font-size:20px;margin:0 0 8px">Answer {status_word}</h2>
        <p style="color:#4a6a8a;font-size:14px;margin:0 0 20px">
          <strong>{rec['approver_name']}</strong> has <strong>{decision}</strong> the answer to:
        </p>
        <p style="color:#0f2035;font-weight:600;font-size:15px;margin:0 0 16px">{rec['question']}</p>
        {f"<div style='background:#fff3cd;border:1px solid #ffc107;padding:12px 16px;border-radius:3px;margin-bottom:16px'><p style='font-weight:700;color:#856404;margin:0 0 4px'>Comments from approver:</p><p style='color:#856404;margin:0'>{comments}</p></div>" if comments else ""}
        <p style="color:#4a6a8a;font-size:13px">Log in to CybeSure SecureAnswer to view and update this answer.</p>
      </div>
    </div>
    """

    if background_tasks:
        background_tasks.add_task(
            send_email,
            SMTP_USER,  # notify the system/requester
            f"Answer {status_word}: {rec['question'][:60]}...",
            notify_html
        )

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head>
      <title>CybeSure — Decision Recorded</title>
      <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Open+Sans:wght@400;500&display=swap" rel="stylesheet">
      <style>
        body {{font-family:'Open Sans',sans-serif;background:#0b1829;color:#dce8f5;margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh}}
        .box {{background:#0f2035;border:1px solid #1e3a5a;padding:48px;border-radius:4px;text-align:center;max-width:480px}}
        .logo {{font-family:'Montserrat',sans-serif;font-size:22px;font-weight:700;color:#fff;margin-bottom:24px}}
        .logo em {{color:#00c8e0;font-style:normal}}
        .status {{font-size:48px;margin-bottom:16px}}
        h2 {{color:{status_colour};font-family:'Montserrat',sans-serif;margin:0 0 12px}}
        p {{color:#7a9cbf;font-size:14px;line-height:1.6}}
      </style>
    </head>
    <body>
      <div class="box">
        <div class="logo">Cybe<em>Sure</em></div>
        <div class="status">{"✅" if decision == "approved" else "❌"}</div>
        <h2>{status_word}</h2>
        <p>Your decision has been recorded{f' with comments: <em>{comments}</em>' if comments else ''}.</p>
        <p style="margin-top:16px;color:#4a6a8a;font-size:12px">You can close this window.</p>
      </div>
    </body>
    </html>
    """)


@app.get("/approval/status/{token}")
async def approval_status(token: str):
    """Check the status of an approval request."""
    if token not in APPROVALS:
        raise HTTPException(404, "Approval token not found")
    rec = APPROVALS[token]
    return {
        "token": token,
        "question": rec["question"],
        "status": rec["status"],
        "approver": rec["approver_name"],
        "comments": rec.get("comments", ""),
        "requested_at": rec["requested_at"],
        "decided_at": rec.get("decided_at")
    }

class ResultItem(BaseModel):
    question_id: int
    question: str
    category: Optional[str] = None
    confidence: str
    confidence_pct: Optional[int] = 50
    explanation: str
    improved_answer: Optional[str] = ""
    improvement_notes: Optional[str] = ""
    sources: List[str]
    document_names: Optional[str] = ""
    approval_status: Optional[str] = "pending"
    approved_by: Optional[str] = None
    approval_token: Optional[str] = None

class ExportRequest(BaseModel):
    results: List[ResultItem]
    format: str
    original_file: Optional[str] = None  # base64 of original questionnaire

@app.post("/export")
async def export(req: ExportRequest):
    fmt = req.format.lower()
    results = req.results

    if fmt == "json":
        content = json.dumps([r.dict() for r in results], indent=2)
        return StreamingResponse(io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.json"})

    if fmt == "excel":
        import base64
        from openpyxl import load_workbook
        from openpyxl.styles import Alignment, PatternFill, Font
        from openpyxl.utils import get_column_letter

        # If we have the original file, populate it directly
        if req.original_file:
            try:
                original_bytes = base64.b64decode(req.original_file)
                wb = load_workbook(io.BytesIO(original_bytes))
                ws = wb.active

                # Find the Question column and Answer column
                headers = {}
                for col in range(1, ws.max_column + 2):
                    cell = ws.cell(row=1, column=col)
                    if cell.value:
                        headers[str(cell.value).strip().lower()] = col

                q_col = headers.get('question', 2)
                ans_col = headers.get('answer', 3)

                # If Answer column doesn't exist, add it
                if 'answer' not in headers:
                    ans_col = ws.max_column + 1
                    ws.cell(row=1, column=ans_col, value="Answer").font = Font(bold=True)

                # Add Confidence column
                conf_col = ans_col + 1
                if ws.cell(row=1, column=conf_col).value is None:
                    ws.cell(row=1, column=conf_col, value="Confidence").font = Font(bold=True)

                # Add Confidence % column
                pct_col = conf_col + 1
                if ws.cell(row=1, column=pct_col).value is None:
                    ws.cell(row=1, column=pct_col, value="Confidence %").font = Font(bold=True)

                # Add Policy Sources column
                src_col = pct_col + 1
                if ws.cell(row=1, column=src_col).value is None:
                    ws.cell(row=1, column=src_col, value="Policy Sources").font = Font(bold=True)

                # Build a map of question text -> result
                result_map = {r.question.strip(): r for r in results}

                # Fill in answers row by row
                for row in range(2, ws.max_row + 1):
                    q_cell = ws.cell(row=row, column=q_col)
                    if not q_cell.value:
                        continue
                    q_text = str(q_cell.value).strip()
                    if q_text in result_map:
                        r = result_map[q_text]
                        # Answer
                        ans_cell = ws.cell(row=row, column=ans_col)
                        ans_cell.value = r.explanation
                        ans_cell.alignment = Alignment(wrap_text=True, vertical='top')
                        # Confidence
                        conf_cell = ws.cell(row=row, column=conf_col)
                        conf_cell.value = r.confidence
                        # Colour code confidence
                        if r.confidence == "Yes":
                            conf_cell.fill = PatternFill("solid", fgColor="C6EFCE")
                        elif r.confidence == "No":
                            conf_cell.fill = PatternFill("solid", fgColor="FFC7CE")
                        else:
                            conf_cell.fill = PatternFill("solid", fgColor="FFEB9C")
                        # Confidence %
                        ws.cell(row=row, column=pct_col).value = f"{r.confidence_pct}%"
                        # Policy sources
                        ws.cell(row=row, column=src_col).value = r.document_names or " | ".join(r.sources)
                        ws.cell(row=row, column=src_col).alignment = Alignment(wrap_text=True, vertical='top')

                # Set column widths
                ws.column_dimensions[get_column_letter(ans_col)].width = 80
                ws.column_dimensions[get_column_letter(src_col)].width = 40

                buf = io.BytesIO()
                wb.save(buf)
                buf.seek(0)
                return StreamingResponse(buf,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=secureanswer_completed.xlsx"})

            except Exception as e:
                print(f"Original file export error: {e}")
                # Fall through to standard export

        # Standard export if no original file
        rows = []
        for r in results:
            rows.append({
                "Ref": r.question_id + 1,
                "Question": r.question,
                "Answer": r.explanation,
                "Confidence": r.confidence,
                "Confidence %": f"{r.confidence_pct}%",
                "Policy Sources": r.document_names or " | ".join(r.sources)
            })
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="SecureAnswer Results")
            ws = w.sheets["SecureAnswer Results"]
            from openpyxl.styles import Alignment
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(wrap_text=True, vertical='top')
            ws.column_dimensions['C'].width = 80
            ws.column_dimensions['F'].width = 40
        buf.seek(0)
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.xlsx"})

    if fmt == "word":
        doc = Document()
        doc.add_heading("CybeSure SecureAnswer — Compliance Results", 0)
        p = doc.add_paragraph()
        p.add_run(f"© {datetime.utcnow().year} CybeSure Ltd. All rights reserved. SecureAnswer™ is a trademark of CybeSure Ltd.").italic = True
        p.runs[0].font.size = __import__('docx').shared.Pt(8)
        p.runs[0].font.color.rgb = RGBColor(150, 150, 150)
        doc.add_paragraph("")
        for r in results:
            doc.add_heading(f"Q{r.question_id+1}: {r.question[:100]}", level=2)
            p = doc.add_paragraph()
            run = p.add_run(f"Confidence: {r.confidence} ({r.confidence_pct}%)")
            run.font.color.rgb = (RGBColor(0,160,100) if r.confidence=="Yes"
                                  else RGBColor(200,0,0) if r.confidence=="No"
                                  else RGBColor(200,130,0))
            run.bold = True
            doc.add_paragraph(r.explanation)
            if r.document_names:
                doc.add_paragraph(f"Policy sources: {r.document_names}", style="Intense Quote")
            doc.add_paragraph("")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.docx"})

    if fmt == "pdf":
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        story = [Paragraph("CybeSure SecureAnswer — Compliance Results", styles["Title"]),
                 Paragraph(f"© {datetime.utcnow().year} CybeSure Ltd. All rights reserved. SecureAnswer™ is a trademark of CybeSure Ltd.", styles["Normal"]),
                 Spacer(1, 20)]
        for r in results:
            story.append(Paragraph(f"<b>Q{r.question_id+1}:</b> {r.question}", styles["Heading2"]))
            story.append(Paragraph(f"<b>Confidence:</b> {r.confidence} ({r.confidence_pct}%)", styles["Normal"]))
            story.append(Paragraph(r.explanation, styles["Normal"]))
            if r.document_names:
                story.append(Paragraph(f"<i>Sources: {r.document_names}</i>", styles["Normal"]))
            story.append(Spacer(1, 12))
        doc.build(story)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.pdf"})
