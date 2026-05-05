from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth import create_access_token, get_current_user, hash_password, verify_password
from database import Organisation, User, UserRole, get_db


router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    organisation_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    role: str
    organisation_id: Optional[int] = None
    subscription_active: bool


def serialize_user(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value if isinstance(user.role, UserRole) else str(user.role),
        organisation_id=user.organisation_id,
        subscription_active=(
            user.organisation.subscription_active if user.organisation else True
        ),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
    email = req.email.lower()
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    organisation = None
    if req.organisation_name:
        organisation = Organisation(name=req.organisation_name)
        db.add(organisation)
        db.flush()

    # First registered user is treated as an admin for simple standalone deployments.
    has_users = db.query(User.id).first() is not None
    role = UserRole.USER if has_users else UserRole.ADMIN

    user = User(
        email=email,
        full_name=req.full_name,
        hashed_password=hash_password(req.password),
        role=role,
        organisation_id=organisation.id if organisation else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(user)
    return TokenResponse(access_token=access_token, expires_in=60 * 60 * 24)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == req.email.lower()).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is disabled")

    access_token = create_access_token(user)
    return TokenResponse(access_token=access_token, expires_in=60 * 60 * 24)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return serialize_user(current_user)
