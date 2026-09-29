"""Authentication API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status, Response

from app.services.auth_service import auth_service
from app.models.auth_schemas import (
    RegisterRequest,
    LoginRequest,
    UserResponse,
    AuthResponse,
    MessageResponse,
)
from app.api.auth_deps import get_current_user
from app.models.user_model import User
from app.config import settings

router = APIRouter()


def _user_response(user: User) -> UserResponse:
    """Create a safe user response (no password hash)."""
    return UserResponse(
        id=user.id,
        name=user.name,
        phone=user.phone,
        role=user.role,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


def _set_auth_cookie(response: Response, token: str) -> None:
    """Set the JWT token as an HttpOnly cookie."""
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="none",
        secure=True,  # Required for cross-origin HTTPS frontend/API requests
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    response: Response,
):
    """Register a new passenger account."""
    # Check for existing user
    existing = await auth_service.get_user_by_phone(phone=data.phone)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this phone number already exists",
        )

    # Create user - explicitly enforce PASSENGER role
    user = await auth_service.create_user(
        name=data.name,
        phone=data.phone,
        password=data.password,
        role="PASSENGER",
    )

    # Create token and set cookie
    token = auth_service.create_access_token(user.id, user.phone, user.role)
    _set_auth_cookie(response, token)

    return AuthResponse(
        message="Registration successful",
        user=_user_response(user),
        access_token=token,
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    data: LoginRequest,
    response: Response,
):
    """Login with phone and password."""
    user = await auth_service.get_user_by_phone(phone=data.phone)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password",
        )

    if not auth_service.verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password",
        )

    token = auth_service.create_access_token(user.id, user.phone, user.role)
    _set_auth_cookie(response, token)

    return AuthResponse(
        message="Login successful",
        user=_user_response(user),
        access_token=token,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response):
    """Logout - clear the auth cookie."""
    response.delete_cookie(
        key="access_token",
        path="/",
        httponly=True,
        samesite="none",
        secure=True,
    )
    return MessageResponse(message="Logged out successfully")


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's information."""
    return _user_response(current_user)
