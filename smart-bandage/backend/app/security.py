"""JWT issuance/verification + password hashing (Phase 4, POST /auth/login)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import UserORM

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

# bcrypt itself caps secrets at 72 bytes; used directly (not via passlib,
# whose CryptContext version-probe breaks under bcrypt>=4.1 -- see
# https://github.com/pyca/bcrypt/issues/684).
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    truncated = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(truncated, bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    truncated = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(truncated, hashed.encode("ascii"))
    except ValueError:
        return False


def create_access_token(subject: str) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expires_minutes)
    payload = {"sub": subject, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expires_days)
    payload = {"sub": subject, "exp": expires_at, "typ": "refresh"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_refresh_token(token: str) -> str:
    """Verify a refresh token (POST /auth/refresh) and return its username.

    Refresh tokens are stateless JWTs, told apart from access tokens only by
    the "typ" claim -- there's no server-side revocation list, matching the
    rest of this dev-scoped auth setup. Rotation (the caller mints a new
    pair) just means a fresh sliding expiry, not that the old token stops
    working before it would otherwise have expired.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise _credentials_exception() from exc
    if payload.get("typ") != "refresh":
        raise _credentials_exception()
    username = payload.get("sub")
    if username is None:
        raise _credentials_exception()
    return username


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_username(token: Optional[str] = Depends(_oauth2_scheme)) -> str:
    if token is None:
        raise _credentials_exception()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        username = payload.get("sub")
        if username is None:
            raise _credentials_exception()
        return username
    except JWTError as exc:
        raise _credentials_exception() from exc


def ensure_seed_user(db: Session, username: str = "admin", password: str = "admin") -> None:
    """Dev convenience: seed one login so the dashboard has something to
    authenticate against before real user management exists."""
    existing = db.query(UserORM).filter(UserORM.username == username).first()
    if existing is None:
        db.add(UserORM(username=username, hashed_password=hash_password(password)))
        db.commit()
