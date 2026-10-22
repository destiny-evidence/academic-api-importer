from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Cookie, Depends, Header, HTTPException
from starlette import status

from academic_apis.db import User, db_session
from academic_apis.utils import settings

logger = logging.getLogger(__name__)


password_hasher = PasswordHasher()


def hash_password(password_plain: str) -> str:
    return password_hasher.hash(password_plain)


def verify_password(password_plain: str, password_hashed: str) -> bool:
    # argon2 `verify()` returns True or raises:
    # `VerificationError` on invalid password, `InvalidHashError` on corrupted hash format
    try:
        return password_hasher.verify(password_hashed, password_plain)
    except VerificationError, InvalidHashError:
        return False


def create_access_token(user: User) -> str:
    issued_at = datetime.now(UTC)
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "permission_admin": user.permission_admin,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.cookie_lifetime),
    }
    return jwt.encode(
        payload,
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )


async def get_current_user(
    aai_user_token: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = aai_user_token
    if not token and authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer":
            token = credentials

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    async with db_session() as session:
        user = session.get(User, user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return user


async def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.permission_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges: admin access required",
        )
    return current_user


def _has_robot_access(api_token: str | None) -> bool:
    return bool(
        settings.ods_robot_access_tokens
        and api_token
        and api_token in settings.ods_robot_access_tokens
    )


def ensure_robot_authentication(
    api_token: Annotated[str | None, Header()] = None,
) -> bool:
    if not _has_robot_access(api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return True


async def ensure_robot_or_admin_authentication(
    api_token: Annotated[str | None, Header()] = None,
    aai_user_token: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> bool:
    """Assert that we have valid robot authentication or an admin user."""
    if _has_robot_access(api_token):
        return True
    if aai_user_token or authorization:
        current_user = await get_current_user(aai_user_token, authorization)
        await get_current_admin(current_user)
        return True
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


if __name__ == "__main__":
    pw = input("Password to hash: ")
    print(hash_password(pw))  # noqa: T201
