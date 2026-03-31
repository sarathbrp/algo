"""Auth router — /auth/login, /auth/register, /auth/google, and /auth/me."""
from __future__ import annotations

import os
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, EmailStr

from src.api.deps import CurrentUser, DbSession
from src.auth import create_token, hash_password, verify_password
from src.db.repos import user_repo

_GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleLoginRequest(BaseModel):
    credential: str  # Google ID token from the frontend


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfile(BaseModel):
    id: str
    email: str
    role: str
    paper: bool

    model_config = {"from_attributes": True}


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, session: DbSession) -> TokenResponse:
    if user_repo.get_by_email(session, body.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    if len(body.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters",
        )
    user_id = uuid4().hex[:16]
    user = user_repo.create(
        session,
        user_id=user_id,
        email=body.email,
        hashed_password=hash_password(body.password),
    )
    session.flush()
    token = create_token(user.id, user.role.value)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: DbSession) -> TokenResponse:
    user = user_repo.get_by_email(session, body.email)
    if user is None or user.hashed_password is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_token(user.id, user.role.value)
    return TokenResponse(access_token=token)


class AppConfig(BaseModel):
    google_client_id: str


@router.get("/config", response_model=AppConfig)
def config() -> AppConfig:
    return AppConfig(google_client_id=_GOOGLE_CLIENT_ID)


@router.post("/google", response_model=TokenResponse)
def google_login(body: GoogleLoginRequest, session: DbSession) -> TokenResponse:
    if not _GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Google login is not configured")
    try:
        payload = google_id_token.verify_oauth2_token(
            body.credential, google_requests.Request(), _GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")

    email = payload.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google account has no email")

    user = user_repo.get_by_email(session, email)
    if user is None:
        user = user_repo.create(session, user_id=uuid4().hex[:16], email=email, hashed_password=None)
        session.flush()

    token = create_token(user.id, user.role.value)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserProfile)
def me(current_user: CurrentUser) -> UserProfile:
    return UserProfile.model_validate(current_user)
