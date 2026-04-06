import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.database import DATA_DIR, get_db
from server.models import User
from server.security import decrypt_text, encrypt_text, generate_totp_secret, get_secret_from_env_or_vault


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
AGENT_TOKEN_EXPIRE_DAYS = int(os.getenv("AGENT_TOKEN_EXPIRE_DAYS", "30"))
STAGER_TOKEN_EXPIRE_MINUTES = int(os.getenv("STAGER_TOKEN_EXPIRE_MINUTES", "15"))

security = HTTPBearer()


def _persist_bootstrap_file(filename: str, value: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as bootstrap_file:
        bootstrap_file.write(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


SECRET_KEY = get_secret_from_env_or_vault("JWT_SECRET_KEY", "jwt_secret", lambda: secrets.token_urlsafe(48))
INITIAL_ADMIN_PASSWORD = get_secret_from_env_or_vault(
    "INITIAL_ADMIN_PASSWORD",
    "initial_admin_password",
    lambda: secrets.token_urlsafe(18),
)
_persist_bootstrap_file("initial_admin_password.txt", INITIAL_ADMIN_PASSWORD)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _create_token(data: dict, token_type: str, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload.update({
        "exp": datetime.utcnow() + expires_delta,
        "type": token_type,
    })
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        data,
        "access",
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        data,
        "refresh",
        expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )


def create_agent_token(agent_id: str, expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        {"sub": agent_id},
        "agent",
        expires_delta or timedelta(days=AGENT_TOKEN_EXPIRE_DAYS),
    )


def create_stager_token(expires_delta: Optional[timedelta] = None) -> str:
    return _create_token(
        {},
        "stager",
        expires_delta or timedelta(minutes=STAGER_TOKEN_EXPIRE_MINUTES),
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def authenticate_user(username: str, password: str, db: AsyncSession) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def get_or_create_default_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.username == "admin"))
    user = result.scalar_one_or_none()
    if user:
        if not user.mfa_secret:
            user.mfa_secret = encrypt_text(generate_totp_secret())
            await db.commit()
        return user

    user = User(
        username="admin",
        password_hash=hash_password(INITIAL_ADMIN_PASSWORD),
        is_admin=True,
        mfa_secret=encrypt_text(generate_totp_secret()),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def get_user_mfa_secret(user: User) -> Optional[str]:
    return decrypt_text(user.mfa_secret)


def require_token_type(token: str, expected_type: str) -> dict:
    payload = decode_token(token)
    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload
