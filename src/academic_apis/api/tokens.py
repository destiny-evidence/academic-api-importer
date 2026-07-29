import logging
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Body
import sqlalchemy as sa
from academic_apis.db import Query, db_session, AccessToken
from academic_apis.db.schemas import AccessTokenPublic
from academic_apis.utils.types import Wrapper
from academic_apis.wrappers import WRAPPERS

if TYPE_CHECKING:
    from sqlmodel import Session  # noqa: F401

logger = logging.getLogger(__name__)

route = APIRouter()


@route.get("/")
async def submit_query() -> list[AccessTokenPublic]:
    async with db_session() as session:  # type: Session
        tokens = session.exec(
            sa.select(
                AccessToken.token_id, AccessToken.wrapper, AccessToken.comment, AccessToken.last_used, AccessToken.last_feedback, AccessToken.locked_until,
            ).where(AccessToken.active == True),
        ).mappings().all()
        return tokens
