import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import Organisation, User, UserRole, get_db


SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
PASSWORD_HASH_ITERATIONS = int(os.environ.get("PASSWORD_HASH_ITERATIONS", "210000"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        algorithm, iterations, salt, expected = hashed_password.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        calculated = _pbkdf2_hash(plain_password, salt, int(iterations))
        return hmac.compare_digest(calculated, expected)
    except (TypeError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    salt = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("=")
    digest = _pbkdf2_hash(password, salt, PASSWORD_HASH_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def _pbkdf2_hash(password: str, salt: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        iterations,
    )
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.email == email.lower()).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_role(*roles: UserRole | str):
    allowed = {role.value if isinstance(role, UserRole) else role for role in roles}

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        user_role = (
            current_user.role.value
            if isinstance(current_user.role, UserRole)
            else str(current_user.role)
        )
        if user_role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return dependency


def check_subscription(current_user: User = Depends(get_current_user)) -> User:
    organisation: Optional[Organisation] = current_user.organisation
    if organisation is None:
        raise HTTPException(status_code=403, detail="Organisation not found")

    status_value = (organisation.subscription_status or "").lower()
    if status_value in {"active", "trial"}:
        expires_at = organisation.subscription_expires_at
        if expires_at is None or expires_at > datetime.utcnow():
            return current_user

    raise HTTPException(status_code=402, detail="Subscription inactive or expired")
