import datetime as dt

from sqlmodel import col, or_, select

from academic_apis.db import AccessToken, db_session
from academic_apis.utils.types import Wrapper

from academic_apis.db.schemas import UserTokenPermission, Query


async def get_available_token(query_id: int, wrapper: Wrapper) -> AccessToken:
    async with db_session() as session:
        token = session.exec(
            select(AccessToken)
            .join(
                UserTokenPermission,
                onclause=UserTokenPermission.token_id == AccessToken.token_id,
            )
            .join(Query, onclause=Query.user_id == UserTokenPermission.user_id)
            .where(
                AccessToken.wrapper == wrapper,
                Query.query_id == query_id,
                col(AccessToken.is_active).is_(True),
                or_(
                    col(AccessToken.locked_until).is_(None),
                    col(AccessToken.locked_until)
                    <= dt.datetime.now(tz=dt.UTC).timestamp(),
                ),
            )
            .order_by(col(AccessToken.last_used))
        ).one()
        session.expunge(token)
    return token


async def get_available_token_for_user(user_id: str, wrapper: Wrapper) -> AccessToken:
    async with db_session() as session:
        token = session.exec(
            select(AccessToken)
            .join(
                UserTokenPermission,
                onclause=UserTokenPermission.token_id == AccessToken.token_id,
            )
            .where(
                AccessToken.wrapper == wrapper,
                UserTokenPermission.user_id == user_id,
                col(AccessToken.is_active).is_(True),
                or_(
                    col(AccessToken.locked_until).is_(None),
                    col(AccessToken.locked_until)
                    <= dt.datetime.now(tz=dt.UTC).timestamp(),
                ),
            )
            .order_by(col(AccessToken.last_used))
        ).one()
        session.expunge(token)
    return token
