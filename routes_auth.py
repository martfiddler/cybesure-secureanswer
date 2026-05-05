from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    authenticate_user,
    create_access_token,
    get_current_user,
    get_password_hash,
)
from database import Organisation, User, UserRole, get_db


router = APIRouter(prefix="/auth", tags=["auth"])


class OrganisationOut(BaseModel):
    id: int
    name: str
    subscription_status: str

    class Config:
        from_attributes = True


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    organisation: OrganisationOut

    class Config:
        from_attributes = True


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=1, max_length=255)
    organisation_name: str = Field(..., min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class OrganisationUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)


def _token_for_user(user: User) -> str:
    expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_access_token({"sub": str(user.id)}, expires_delta=expires)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="Email is already registered")

    organisation = (
        db.query(Organisation)
        .filter(Organisation.name == payload.organisation_name.strip())
        .first()
    )
    if organisation is None:
        organisation = Organisation(name=payload.organisation_name.strip())
        db.add(organisation)
        db.flush()

    user_count = (
        db.query(User)
        .filter(User.organisation_id == organisation.id)
        .count()
    )
    role = UserRole.ADMIN if user_count == 0 else UserRole.USER
    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        hashed_password=get_password_hash(payload.password),
        role=role,
        organisation_id=organisation.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return TokenResponse(access_token=_token_for_user(user), user=user)


@router.post("/login", response_model=TokenResponse)
def login_json(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=_token_for_user(user), user=user)


@router.post("/token", response_model=TokenResponse)
def login_form(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=_token_for_user(user), user=user)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/organisation", response_model=OrganisationOut)
def get_organisation(current_user: User = Depends(get_current_user)):
    return current_user.organisation


@router.patch("/organisation", response_model=OrganisationOut)
def update_organisation(
    payload: OrganisationUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only admins can update organisations")

    organisation = current_user.organisation
    if payload.name is not None:
        organisation.name = payload.name.strip()

    db.add(organisation)
    db.commit()
    db.refresh(organisation)
    return organisation
