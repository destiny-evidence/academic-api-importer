from fastapi import HTTPException
from sqlmodel import Session
from starlette import status

from academic_apis.db import User
from academic_apis.db.permissions import has_query_ownership, has_query_permission


def require_query_ownership(
    session: Session,
    current_user: User,
    query_id: int,
) -> None:
    if not has_query_ownership(session=session, user=current_user, query_id=query_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions: only owners of a query can manage its permissions",
        )


def require_query_permission(
    session: Session,
    current_user: User,
    query_id: int,
    require_ownership: bool = False,
    admin_override: bool = True,
) -> None:
    if not has_query_permission(
        session=session,
        user=current_user,
        query_id=query_id,
        require_ownership=require_ownership,
        admin_override=admin_override,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions: Only admins, owners, or privileged users have permission to view or edit.",
        )
