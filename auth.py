from collections.abc import Callable
from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import User, UserRole, get_db


def get_current_user(db: Session = Depends(get_db)) -> Optional[User]:
    """Authentication placeholder until login routes are wired into the app."""
    return None


def require_role(*roles: UserRole | str) -> Callable[[Optional[User]], Optional[User]]:
    allowed_roles = {
        role.value if isinstance(role, UserRole) else str(role)
        for role in roles
    }

    def dependency(current_user: Optional[User] = Depends(get_current_user)) -> Optional[User]:
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        if allowed_roles and current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return dependency


def check_subscription(current_user: Optional[User] = Depends(get_current_user)) -> Optional[User]:
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    organisation = current_user.organisation
    if organisation and organisation.subscription_status not in {"active", "trial"}:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Active subscription required",
        )
    return current_user
