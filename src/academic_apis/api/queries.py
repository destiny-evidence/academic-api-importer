from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import col, select
from starlette import status

from academic_apis.api.utils.auth import get_current_user
from academic_apis.db import Query, User, db_session
from academic_apis.db.schemas import UserQueryPermission
from academic_apis.utils.types import Wrapper
from academic_apis.wrappers import WRAPPERS

if TYPE_CHECKING:
    from sqlmodel import Session  # noqa: F401

logger = logging.getLogger(__name__)

route = APIRouter()


@route.post("/test")
async def test_query(
    wrapper: Wrapper,
    query: Annotated[str, Body(media_type="text/plain")],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    raise NotImplementedError(
        "Once implemented, this will take an available token and fetch the first page of this query"
    )


@route.post("/")
async def submit_query(
    wrapper: Wrapper,
    query: Annotated[str, Body(media_type="text/plain")],
    current_user: Annotated[User, Depends(get_current_user)],
    py_start: int = 2010,
    py_end: int | None = None,
    py_steps: int = 2,
    use_py_filter: bool = False,
) -> int:
    if not current_user.permission_submit_query or not current_user.permission_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have the permission to submit queries.",
        )

    from academic_apis.broker.tasks import retrieve_query

    API = WRAPPERS[wrapper]  # noqa: N806

    async with db_session() as session:  # type: Session
        query_orm = Query(
            query=query,
            wrapper=wrapper,
        )
        session.add(query_orm)
        session.flush()
        session.refresh(query_orm)
        query_id: int = cast("int", query_orm.query_id)
        logger.info(f"Registered query as query_id={query_id}")
        session.commit()

    for subquery in API.generate_subqueries(
        query,
        py_start=py_start,
        py_end=py_end,
        py_steps=py_steps,
        use_py_filter=use_py_filter,
    ):
        logger.debug(f"Subquery: {subquery}")
        await retrieve_query.kiq(query=subquery, wrapper=wrapper, query_id=query_id)  # type: ignore[call-overload]

    return query_id


@route.get("/")
async def list_queries(
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sequence[Query]:
    async with db_session() as session:  # type: Session
        stmt = select(Query).order_by(col(Query.created_at).desc())
        if not current_user.permission_admin:
            stmt = stmt.join(UserQueryPermission).where(
                UserQueryPermission.user_id == current_user.user_id
            )

        return session.exec(stmt).all()
