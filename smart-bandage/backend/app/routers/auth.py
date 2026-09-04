"""POST /auth/login (Blueprint API §6 "Auth & devices")."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import UserORM
from backend.app.schemas import LoginRequest, RefreshRequest, Token
from backend.app.security import create_access_token, create_refresh_token, decode_refresh_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = db.query(UserORM).filter(UserORM.username == body.username).first()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    return Token(access_token=create_access_token(user.username), refresh_token=create_refresh_token(user.username))


@router.post("/refresh", response_model=Token)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> Token:
    """Silent re-auth for a device that already signed in once.

    frontend/src/api.ts calls this transparently whenever an access token
    401s, and rotates the refresh token on every call -- so a device used at
    least once within JWT_REFRESH_EXPIRES_DAYS never has to see the login
    screen again.
    """
    username = decode_refresh_token(body.refresh_token)
    user = db.query(UserORM).filter(UserORM.username == username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    return Token(access_token=create_access_token(user.username), refresh_token=create_refresh_token(user.username))
