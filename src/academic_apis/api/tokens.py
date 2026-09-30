import logging
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from starlette import status

from academic_apis.api.users import get_current_admin, get_current_user
from academic_apis.db import AccessToken, Mode, db_session
from academic_apis.db.schemas import AccessTokenPublic, User, UserTokenPermission


logger = logging.getLogger(__name__)

route = APIRouter()


def _parse_token_id(token_id: str) -> int:
    """Convert token ID string to integer, raising a 400 error on malformed values."""
    try:
        return int(token_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid token ID: '{token_id}' is not an integer",
        ) from exc


@route.get("/list")
async def list_tokens(
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sequence[AccessTokenPublic]:
    async with db_session() as session:
        stmt = select(AccessToken)

        if not current_user.permission_admin:
            stmt = stmt.join(UserTokenPermission).where(
                AccessToken.is_active == True,
                UserTokenPermission.user_id == current_user.user_id,
            )

        return session.exec(stmt).all()


@route.post("/submit")
async def add_token(
    token: AccessToken,
    current_user: Annotated[User, Depends(get_current_user)],
) -> str:
    """
    Add new API token to database.

    Any user can submit a (hopefully) valid API token (`AccessToken`).
    The endpoint ensures that key parameters are set: `wrapper`, `comment`, and `token`.
    This also automatically adds an `UserTokenPermission` entry with `is_owner=True`.

    :param token:
    :param current_user:
    :return: ID of added token
    """
    if not token.token.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Field 'token' must be set to add a new API token",
        )
    if not (token.comment or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Field 'comment' must be set to add a new API token",
        )

    async with db_session(mode=Mode.WRITE) as session:
        new_token = AccessToken(
            wrapper=token.wrapper,
            token=token.token,
            comment=token.comment,
            proxy=token.proxy,
        )
        session.add(new_token)
        session.flush()
        session.refresh(new_token)
        token_id = str(new_token.token_id)
        session.add(
            UserTokenPermission(
                user_id=current_user.user_id,
                token_id=token_id,
                is_owner=True,
            ),
        )
        session.commit()

    logger.info(
        f"User {current_user.user_id} submitted new token {token_id} ({token.wrapper})"
    )
    return token_id


@route.post("/update")
async def update_token(
    token: AccessToken,
    current_user: Annotated[User, Depends(get_current_user)],
) -> str:
    """
    Update existing API token in database.

    Any user can update the status (`is_active`) of their own token (`UserTokenPermission.is_owner == True`).
    Admin users can edit all fields of all tokens.
    Only fields that are explicitly set in the request body are modified.

    :param token:
    :param current_user:
    :return: ID of updated token
    :raises: NotFoundError if no matching token exitsts or other error
    """
    if token.token_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Field 'token_id' must be set to update an API token",
        )

    async with db_session(mode=Mode.WRITE) as session:
        stored = session.get(AccessToken, token.token_id)
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No API token found with token_id={token.token_id}",
            )

        updates: dict[str, Any] = token.model_dump(
            exclude_unset=True,
            exclude={"token_id"},
        )

        # Non-admins may only toggle the status of tokens they own.
        if not current_user.permission_admin:
            permission = session.exec(
                select(UserTokenPermission).where(
                    UserTokenPermission.user_id == current_user.user_id,
                    UserTokenPermission.token_id == str(token.token_id),
                ),
            ).first()
            if permission is None or not permission.is_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not enough permissions: you may only update your own tokens",
                )
            for field, value in updates.items():
                if field != "is_active" and value != getattr(stored, field):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Not enough permissions: cannot modify '{field}'",
                    )

        for field, value in updates.items():
            setattr(stored, field, value)

        session.add(stored)
        session.flush()
        session.commit()
        session.refresh(stored)
        token_id = str(stored.token_id)

    logger.info(f"Updated token {token_id} (by {current_user.user_id})")
    return token_id


@route.delete("/delete")
async def delete_token(
    token_id: str,
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> str:
    """
    Delete existing API token from database.

    Only admin users can delete tokens.

    :param token_id:
    :param current_admin:
    :return: ID of deleted token
    :raises: NotFoundError if no matching token exitsts or other error
    """
    key = _parse_token_id(token_id)
    async with db_session(mode=Mode.WRITE) as session:
        stored = session.get(AccessToken, key)
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No API token found with token_id={token_id}",
            )
        # Remove all user permissions associated with this token
        permissions = session.exec(
            select(UserTokenPermission).where(
                UserTokenPermission.token_id == str(key),
            ),
        ).all()
        for permission in permissions:
            session.delete(permission)
        session.delete(stored)
        session.commit()

    logger.info(f"Deleted token {token_id} (by {current_admin.user_id})")
    return str(key)


@route.post("/permission")
async def edit_permission(
    permissions: list[UserTokenPermission],
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> str:
    """
    Create existing permissions for users to use tokens.

    Only admin users can create or edit these permissions.
    Ignores already existing permissions.
    Updates update the existing permission if `is_owner` value changed.

    :param permissions: List of permissions to create or update
    :param current_admin:
    :return: number of created or upated permissions
    """
    changed = 0
    async with db_session(mode=Mode.WRITE) as session:
        for permission in permissions:
            if session.get(User, permission.user_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No user found with user_id='{permission.user_id}'",
                )
            key = _parse_token_id(permission.token_id)
            if session.get(AccessToken, key) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No API token found with token_id='{permission.token_id}'",
                )

            stored = session.exec(
                select(UserTokenPermission).where(
                    UserTokenPermission.user_id == permission.user_id,
                    UserTokenPermission.token_id == str(key),
                ),
            ).first()
            if stored is None:
                session.add(
                    UserTokenPermission(
                        user_id=permission.user_id,
                        token_id=str(key),
                        is_owner=permission.is_owner,
                    ),
                )
                changed += 1
            elif stored.is_owner != permission.is_owner:
                stored.is_owner = permission.is_owner
                session.add(stored)
                changed += 1
        session.commit()

    logger.info(
        f"Created/updated {changed} token permission(s) (by {current_admin.user_id})"
    )
    return str(changed)


@route.delete("/permission")
async def delete_permission(
    permissions: list[UserTokenPermission],
    current_admin: Annotated[User, Depends(get_current_admin)],
) -> str:
    """
    Delete existing permissions for users to use tokens.

    Only admin users can delete these permissions.
    If (some) permissions don't exist, no error is thrown.

    :param permissions: List of permissions to delete
    :param current_admin:
    :return: number of deleted permissions
    """
    deleted = 0
    async with db_session(mode=Mode.WRITE) as session:
        for permission in permissions:
            stored = session.exec(
                select(UserTokenPermission).where(
                    UserTokenPermission.user_id == permission.user_id,
                    UserTokenPermission.token_id == permission.token_id,
                ),
            ).first()
            if stored is not None:
                session.delete(stored)
                deleted += 1
        session.commit()

    logger.info(f"Deleted {deleted} token permission(s) (by {current_admin.user_id})")
    return str(deleted)
