from sqlmodel import Session, select

from academic_apis.db import User
from academic_apis.db.schemas import UserQueryPermission


def has_query_permission(
    session: Session,
    user: User,
    query_id: int | None,
    require_ownership: bool = False,
    admin_override: bool = True,
) -> bool:
    # Only active users can have permissions (redundant check)
    if not user.is_active:
        return False

    # Admins get to do it all unless specified otherwise
    if admin_override and user.permission_admin:
        return True

    # Only admins get to query for all queries
    if query_id is None and not user.permission_admin:
        return False

    permission = session.exec(
        select(UserQueryPermission).where(
            UserQueryPermission.query_id == query_id,
            UserQueryPermission.user_id == user.user_id,
        ),
    ).first()
    return permission is not None and (not require_ownership or permission.is_owner)


def has_query_ownership(
    session: Session, user: User, query_id: int, admin_override: bool = True
) -> bool:
    return has_query_permission(
        session=session,
        user=user,
        query_id=query_id,
        admin_override=admin_override,
        require_ownership=True,
    )
