"""
CybeSure SecureAnswer — Authentication
© CybeSure Ltd. All rights reserved.
"""
import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = os.environ.get("JWT_SECRET", "cybesure-secureanswer-secret-key-2025")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    """Hash password via SHA256 first to avoid bcrypt 72-byte limit."""
    prehash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return pwd_context.hash(prehash)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify password — pre-hash with SHA256 to match hash_password."""
    prehash = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    return pwd_context.verify(prehash, hashed)


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


def get_current_user(token: str = Depends(oauth2_scheme)):
    from database import get_db, User
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    db = next(get_db())
    user = db.query(User).filter(
        User.id == int(user_id), User.is_active == True
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_role(*roles):
    """Dependency factory — require one of the given roles."""
    def checker(token: str = Depends(oauth2_scheme)):
        from database import UserRole
        user = get_current_user(token)
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required: {', '.join(r.value for r in roles)}"
            )
        return user
    return checker


def check_subscription(org) -> None:
    """Raise HTTP 402 if org cannot run more questionnaires."""
    if not org:
        return
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
                "message": f"You have used all {org.total_limit} questionnaires.",
                "remaining": 0,
                "topup_options": {
                    "single": {"qty": 1, "price_gbp": 300, "label": "1 questionnaire"},
                    "bundle": {"qty": 10, "price_gbp": 1000, "label": "10 questionnaires"}
                }
            }
        )
