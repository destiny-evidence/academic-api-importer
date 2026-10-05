from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Body, Depends, HTTPException
from lark import LarkError
from sqlmodel import col, select
from starlette import status

from academic_apis.api.utils.auth import get_current_user
from academic_apis.db import Query, User, db_session
from academic_apis.db.schemas import QueryInfo, UserQueryPermission
from academic_apis.db.tokens import get_available_token_for_user
from academic_apis.grammars.scopus import QuerySyntaxError, query_to_indented_text
from academic_apis.utils import Wrapper
from academic_apis.wrappers import WRAPPERS

if TYPE_CHECKING:
    from sqlmodel import Session  # noqa: F401

logger = logging.getLogger(__name__)

route = APIRouter()


@route.post("/test")
async def test_query(
    current_user: Annotated[User, Depends(get_current_user)],
    query: Annotated[QueryInfo, Body()],
) -> int:
    API = WRAPPERS[query.wrapper]  # noqa: N806
    token = await get_available_token_for_user(
        user_id=current_user.user_id, wrapper=query.wrapper
    )

    api = API(
        logger=logger,
        api_key=token.token,
        proxy=token.proxy,
        skip_token_logging=False,
    )
    n_results = await api.fetch_n_results(query=query.query, params=query.params)

    if n_results is None:
        raise HTTPException(
            status_code=status.HTTP_204_NO_CONTENT,
            detail=f"Did not receive a count from the {query.wrapper} API.",
        )

    return n_results


@route.post("/render")
async def render_query(
    _current_user: Annotated[User, Depends(get_current_user)],
    query: Annotated[QueryInfo, Body()],
) -> str:
    if query.wrapper == Wrapper.SCOPUS.value:
        try:
            return query_to_indented_text(query.query)
        except (LarkError, QuerySyntaxError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{e}",
            ) from e

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Formatting not available for {query.wrapper}.",
    )


@route.post("/")
async def submit_query(
    current_user: Annotated[User, Depends(get_current_user)],
    query: Annotated[QueryInfo, Body()],
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

    API = WRAPPERS[query.wrapper]  # noqa: N806

    async with db_session() as session:  # type: Session
        query_orm = Query(
            query=query.query,
            wrapper=query.wrapper,
        )
        session.add(query_orm)
        session.flush()
        session.refresh(query_orm)
        query_id: int = cast("int", query_orm.query_id)
        logger.info(f"Registered query as query_id={query_id}")
        session.commit()

    for subquery in API.generate_subqueries(
        query.query,
        py_start=py_start,
        py_end=py_end,
        py_steps=py_steps,
        use_py_filter=use_py_filter,
    ):
        logger.debug(f"Subquery: {subquery}")
        await retrieve_query.kiq(
            query=subquery, wrapper=query.wrapper, query_id=query_id
        )  # type: ignore[call-overload]

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
