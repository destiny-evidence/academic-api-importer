from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Body

from academic_apis.db import Query, db_session
from academic_apis.utils.types import Wrapper
from academic_apis.wrappers import WRAPPERS

if TYPE_CHECKING:
    from sqlmodel import Session  # noqa: F401

logger = logging.getLogger(__name__)

route = APIRouter()


@route.post("/query")
async def submit_query(
    wrapper: Wrapper,
    query: Annotated[str, Body()],
    py_start: int = 2010,
    py_end: int | None = None,
    py_steps: int = 2,
    use_py_filter: bool = False,
) -> int:
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
        await retrieve_query.kiq(query=subquery, wrapper=wrapper, query_id=query_id)

    return query_id
