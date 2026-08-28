from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, cast, Sequence

from fastapi import APIRouter, Body, Cookie, HTTPException
from sqlmodel import select
from starlette import status
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.templating import Jinja2Templates

from academic_apis.db import Query, db_session
from academic_apis.utils import settings
from academic_apis.utils.types import Wrapper
from academic_apis.wrappers import WRAPPERS


if TYPE_CHECKING:
    from sqlmodel import Session  # noqa: F401

logger = logging.getLogger(__name__)

route = APIRouter()


@route.post("/query")
async def submit_query(
    wrapper: Wrapper,
    query: Annotated[str, Body(media_type="text/plain")],
    py_start: int = 2010,
    py_end: int | None = None,
    py_steps: int = 2,
    use_py_filter: bool = False,
    aai_admin_token: Annotated[str | None, Cookie()] = None,
) -> int:
    if not aai_admin_token or aai_admin_token not in settings.admin_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing auth cookie",
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


templates = Jinja2Templates(directory="src/academic_apis/api/templates")


@route.get("/")
async def list_queries() -> Sequence[Query]:
    async with db_session() as session:  # type: Session
        return session.exec(select(Query)).all()


@route.get("/html", response_class=HTMLResponse)
async def list_queries_html(request: Request) -> HTMLResponse:
    queries = await list_queries()

    return templates.TemplateResponse(
        request,
        "queries.html",
        {
            "request": request,
            "queries": queries,
            "settings": settings,
        },
    )
