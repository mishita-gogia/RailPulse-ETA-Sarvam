"""Authentication dependencies for FastAPI endpoints."""

from typing import Optional
from fastapi import Depends, HTTPException, status, Request

from app.services.auth_service import auth_service
from app.models.user_model import User


def _extract_token(request: Request) -> Optional[str]:
    """Extract token first from access_token cookie, then fallback to Authorization Bearer header."""
    token = request.cookies.get("access_token")
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        if bearer_token:
            return bearer_token
    return None


async def get_current_user(
    request: Request,
) -> User:
    """Extract and validate the current user from auth cookie or Authorization Bearer header."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = auth_service.decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = await auth_service.get_user_by_id(user_id=int(user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


async def get_optional_user(
    request: Request,
) -> Optional[User]:
    """Get current user if authenticated, otherwise return None.
    
    Use this for endpoints that work for both authenticated and unauthenticated users.
    """
    token = _extract_token(request)
    if not token:
        return None
    payload = auth_service.decode_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return await auth_service.get_user_by_id(user_id=int(user_id))


def require_role(*allowed_roles: str):
    """Dependency factory: require the current user to have one of the specified roles."""
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
    return role_checker


# Convenience dependencies
require_passenger = require_role("PASSENGER", "RAILWAY_STAFF")
require_staff = require_role("RAILWAY_STAFF")
