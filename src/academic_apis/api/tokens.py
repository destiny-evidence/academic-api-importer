import logging
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import delete, select, tuple_
from starlette import status

from academic_apis.api.users import get_current_admin, get_current_user
from academic_apis.db import AccessToken, Mode, Wrapper, db_session
from academic_apis.db.schemas import AccessTokenPublic, User, UserTokenPermission

logger = logging.getLogger(__name__)

route = APIRouter()


@route.get("/list")
async def list_tokens(
    current_user: Annotated[User, Depends(get_current_user)],
    user_id: str | None = None,
) -> Sequence[AccessTokenPublic]:
    """
    Return the tokens the current user has access to.
    If the `current_user` is an admin, this lists all tokens.
    If `user_id` is set (and `current_user` is an admin), returns permissions for that user

    :param current_user:
    :param user_id: optional if the admin wants to see permissions of a specific user
    :return:
    """
    async with db_session() as session:
        stmt = select(AccessToken)

        user_id_filter: str | None = None
        if current_user.permission_admin and user_id is not None:
            user_id_filter = user_id
        elif not current_user.permission_admin:
            user_id_filter = current_user.user_id

        if user_id_filter is not None:
            stmt = stmt.join(UserTokenPermission).where(
                AccessToken.is_active == True,
                UserTokenPermission.user_id == user_id_filter,
            )

        return session.exec(stmt).all()


@route.post("/submit")
async def add_token(
    current_user: Annotated[User, Depends(get_current_user)],
    token: AccessToken,
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
    if not (
        (token.token or "").strip()
        and (token.comment or "").strip()
        and token.wrapper in Wrapper
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Required field missing!",
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
    current_user: Annotated[User, Depends(get_current_user)],
    token: AccessToken,
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

        # ``AccessToken`` is a table model, so ``wrapper`` arrives unvalidated.
        # Reject anything the enum does not define before it can reach the row.
        if "wrapper" in updates and updates["wrapper"] not in Wrapper:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Field 'wrapper' must be one of: "
                + ", ".join(wrapper.value for wrapper in Wrapper),
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
    current_admin: Annotated[User, Depends(get_current_admin)],
    token_id: str,
) -> str:
    """
    Delete existing API token from database.

    Only admin users can delete tokens.

    :param token_id:
    :param current_admin:
    :return: ID of deleted token
    :raises: NotFoundError if no matching token exists or other error
    """
    async with db_session(mode=Mode.WRITE) as session:
        stored = session.get(AccessToken, token_id)
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No API token found with token_id={token_id}",
            )
        # Remove all user permissions associated with this token
        permissions = session.exec(
            select(UserTokenPermission).where(
                UserTokenPermission.token_id == token_id,
            ),
        ).all()
        for permission in permissions:
            session.delete(permission)
        session.delete(stored)
        session.commit()

    logger.info(f"Deleted token {token_id} (by {current_admin.user_id})")
    return token_id


@route.post("/permissions")
async def edit_permissions(
    _current_admin: Annotated[User, Depends(get_current_admin)],
    permissions: list[UserTokenPermission],
) -> None:
    """
    Create existing permissions for users to use tokens.

    Only admin users can create or edit these permissions.
    Ignores already existing permissions.
    Updates update the existing permission if `is_owner` value changed.

    :param permissions: List of permissions to create or update
    :param _current_admin:
    """
    async with db_session(mode=Mode.WRITE) as session:
        for perm in permissions:
            session.merge(perm)
        session.commit()


@route.delete("/permissions")
async def delete_permissions(
    _current_admin: Annotated[User, Depends(get_current_admin)],
    permissions: list[UserTokenPermission],
) -> int:
    """
    Delete existing permissions for users to use tokens.

    Only admin users can delete these permissions.
    If (some) permissions don't exist, no error is thrown.

    :param permissions: List of permissions to delete
    :param _current_admin:
    :returns: number of affected rows
    """
    async with db_session(mode=Mode.WRITE) as session:
        stmt = delete(UserTokenPermission).where(
            tuple_(UserTokenPermission.user_id, UserTokenPermission.token_id).in_(
                [
                    (permission.user_id, permission.token_id)
                    for permission in permissions
                ]
            )
        )
        result = session.exec(stmt)
        session.commit()
        return result.rowcount


@route.get("/permission")
async def list_permissions(
    current_user: Annotated[User, Depends(get_current_user)],
    user_id: str | None = None,
) -> Sequence[UserTokenPermission]:
    """
    Return the token permissions the current user has.
    If `user_id` is set (and `current_user` is an admin), returns permissions for that `user_id`

    :param current_user:
    :param user_id: optional if the admin wants to see permissions of a specific user
    :return:
    """
    async with db_session() as session:
        if user_id is None:
            user_id = current_user.user_id
        elif not current_user.permission_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins are allowed to see other's permissions.",
            )
        if user_id is None:
            # Should never happen, but who knows...
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No `user_id` specified.",
            )

        stmt = select(UserTokenPermission).where(UserTokenPermission.user_id == user_id)

        return session.exec(stmt).all()
