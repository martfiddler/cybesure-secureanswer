"""
CybeSure SecureAnswer — Authentication
© CybeSure Ltd. All rights reserved.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import User, Organisation, UserRole, get_db

SECRET_KEY  = os.environ.get("JWT_SECRET", "cybesure-secureanswer-secret-change-in-production")
ALGORITHM   = "HS256"
TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(data: dict, expires_hours: int = TOKEN_EXPIRE_HOURS) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.utcnow() + timedelta(hours=expires_hours)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_role(*roles: UserRole):
    """Dependency factory — require one of the given roles."""
    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role: {', '.join(r.value for r in roles)}"
            )
        return current_user
    return checker


def get_org(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Organisation:
    org = db.query(Organisation).filter(Organisation.id == user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


def check_subscription(org: Organisation) -> None:
    """Raise if org cannot run more questionnaires."""
    if not org.is_active:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "subscription_expired",
                "message": "Your subscription has expired. Please renew to continue.",
                "contact": "support@cybesure.com"
            }
        )
    if org.remaining <= 0:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "limit_reached",
                "message": f"You have used all {org.total_limit} questionnaires in your subscription.",
                "remaining": 0,
                "topup_options": {
                    "single": {"qty": 1, "price_gbp": 300, "label": "1 additional questionnaire"},
                    "bundle": {"qty": 10, "price_gbp": 1000, "label": "10 additional questionnaires"}
                }
            }
        )
