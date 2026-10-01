from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr
from sqlmodel import select
from starlette import status

from academic_apis.db import Mode, User, UserCreate, UserPublic, db_session
from academic_apis.utils import settings

from .utils.auth import (
    create_access_token,
    get_current_admin,
    get_current_user,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)
route = APIRouter()

# Fields that regular users may never change, even about themselves
RESTRICTED_FIELDS = ("is_active", "permission_admin", "permission_submit_query")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    institution: str | None = None
    is_active: bool | None = None
    permission_admin: bool | None = None
    permission_submit_query: bool | None = None
    password_plain: str | None = None


@route.post("/login", response_model=Token)
async def login(login_data: LoginRequest, response: Response) -> Token:
    async with db_session() as session:
        user = session.exec(
            select(User).where(User.email == login_data.email),
        ).first()

    if user is None or not verify_password(login_data.password, user.password_hashed):
        logger.warning(f"Failed login attempt for email={login_data.email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )

    token = create_access_token(user)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True if using HTTPS
        max_age=settings.cookie_lifetime * 60,
    )
    logger.info(f"User {user.user_id} logged in")
    return Token(access_token=token)


@route.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    # Attributes must match those used when setting the cookie, or the
    # browser will not remove it.
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True if using HTTPS
    )
    logger.info(f"User {current_user.user_id} logged out")


@route.get("/", response_model=list[UserPublic])
async def list_users(
    current_user: Annotated[  # noqa: ARG001
        User,
        Depends(get_current_admin),
    ],
) -> list[UserPublic]:
    async with db_session() as session:
        users = session.exec(
            select(User).order_by(User.email),
        ).all()
    return [UserPublic.model_validate(user) for user in users]


@route.post("/", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_user(
    _current_user: Annotated[User, Depends(get_current_admin)],
    user_create: UserCreate,
) -> UserPublic:
    async with db_session(mode=Mode.WRITE) as session:
        existing = session.exec(
            select(User).where(User.email == user_create.email),
        ).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists",
            )

        user = User(
            email=user_create.email,
            full_name=user_create.full_name,
            institution=user_create.institution,
            is_active=user_create.is_active,
            permission_admin=user_create.permission_admin,
            permission_submit_query=user_create.permission_submit_query,
            password_hashed=hash_password(user_create.password_plain),
        )
        session.add(user)
        session.flush()
        session.commit()
        session.refresh(user)
        user_public = UserPublic.model_validate(user)

    logger.info(f"Created user {user_public.user_id} (by admin)")
    return user_public


@route.put("/{user_id}", response_model=UserPublic)
async def update_user(
    current_user: Annotated[User, Depends(get_current_user)],
    user_id: str,
    user_update: UserUpdate,
) -> UserPublic:
    if user_id != current_user.user_id and not current_user.permission_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions: you may only update your own user",
        )

    async with db_session(mode=Mode.WRITE) as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        updates: dict[str, Any] = user_update.model_dump(exclude_unset=True)

        # Non-admins keep control of their profile data, but may not change
        # privileged fields - about themselves or anybody else.
        if not current_user.permission_admin:
            for field in RESTRICTED_FIELDS:
                if field in updates and updates[field] != getattr(user, field):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Not enough permissions: cannot modify '{field}'",
                    )

        email = updates.pop("email", None)
        if email is not None and email != user.email:
            existing = session.exec(
                select(User).where(User.email == email),
            ).first()
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A user with this email already exists",
                )
            user.email = email

        password_plain = updates.pop("password_plain", None)
        if password_plain:
            user.password_hashed = hash_password(password_plain)

        for field, value in updates.items():
            if value is not None:
                setattr(user, field, value)

        session.add(user)
        session.flush()
        session.commit()
        session.refresh(user)
        user_public = UserPublic.model_validate(user)

    logger.info(f"Updated user {user_public.user_id} (by {current_user.user_id})")
    return user_public
