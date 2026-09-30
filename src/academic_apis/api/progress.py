import logging
from collections.abc import Sequence

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlmodel import select
from taskiq.depends.progress_tracker import TaskState

from academic_apis.db import db_session
from academic_apis.db.schemas import Task, TaskPublic
from academic_apis.utils import settings

logger = logging.getLogger(__name__)

route = APIRouter()

templates = Jinja2Templates(directory="src/academic_apis/api/templates")

SUBQUERY_LENGTH = 50


@route.get("/")
async def list_tasks(
    query_id: int | None = None,
    offset: int = 0,
    limit: int = 5,
    status: TaskState | None = None,
) -> Sequence[TaskPublic]:
    async with db_session() as session:
        stmt = (
            select(  # type: ignore[call-overload]
                Task.task_id,
                Task.status,
                Task.query_id,
                func.substr(Task.subquery, -50).label("subquery"),
                Task.num_retries,
                Task.created_at,
                Task.latest_try,
                Task.finished_at,
                Task.progress,
                Task.execution_time,
            )
            .order_by(func.coalesce(Task.latest_try, Task.created_at))
            .limit(limit)
            .offset(offset)
        )
        if query_id is not None:
            stmt = stmt.where(Task.query_id == query_id)
        if status is not None:
            stmt = stmt.where(Task.status == status)

        return session.exec(stmt).all()  # type: ignore[no-any-return]


@route.get("/html", response_class=HTMLResponse)
async def list_tasks_html(
    request: Request,
    query_id: int | None = None,
    offset: int = 0,
    limit: int = 10,
    status: TaskState | None = None,
) -> HTMLResponse:
    tasks = await list_tasks(
        query_id=query_id, offset=offset, limit=limit, status=status
    )

    async with db_session() as session:
        count_stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.query_id == query_id if query_id is not None else False)
        )
        if status is not None:
            count_stmt = count_stmt.where(Task.status == status)
        total = session.exec(count_stmt).one()

    return templates.TemplateResponse(
        request,
        "progress.html",
        {
            "request": request,
            "tasks": tasks,
            "total": total,
            "query_id": query_id,
            "offset": offset,
            "limit": limit,
            "status": status,
            "settings": settings,
        },
    )
