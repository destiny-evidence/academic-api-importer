import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Integer
from sqlmodel import cast, col, func, select
from taskiq.depends.progress_tracker import TaskState

from academic_apis.api.utils import require_query_permission
from academic_apis.api.utils.auth import get_current_user
from academic_apis.db import db_session
from academic_apis.db.schemas import Query, Task, TaskPublic, User, UserQueryPermission

logger = logging.getLogger(__name__)

route = APIRouter()

SUBQUERY_LENGTH = 50


@route.get("/")
async def list_tasks(
    current_user: Annotated[User, Depends(get_current_user)],
    query_id: int | None = None,
    offset: int = 0,
    limit: int = 5,
    status: TaskState | None = None,
) -> Sequence[TaskPublic]:
    async with db_session() as session:
        require_query_permission(
            session=session,
            current_user=current_user,
            query_id=query_id,
            require_ownership=False,
        )

        stmt = (
            select(  # type: ignore[call-overload]
                Task.task_id,
                Task.status,
                Task.query_id,
                func.substr(Task.subquery, -SUBQUERY_LENGTH).label("subquery"),
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
        if not current_user.permission_admin:
            stmt = (
                stmt.join(Query)
                .join(UserQueryPermission)
                .where(UserQueryPermission.user_id == current_user.user_id)
            )

        return session.exec(stmt).all()  # type: ignore[no-any-return]


class QueryProgress(BaseModel):
    status: TaskState | None
    n_tasks: int
    n_results: int
    n_retrieved: int


@route.get("/summary")
async def task_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    query_id: int,
) -> Sequence[QueryProgress]:

    async with db_session() as session:
        require_query_permission(
            session=session,
            current_user=current_user,
            query_id=query_id,
            require_ownership=False,
        )
        stmt = (
            select(
                Task.status,
                func.count(col(Task.task_id)).label("n_tasks"),
                func.sum(
                    func.coalesce(
                        cast(Task.progress["n_results"].as_string(), Integer), 0
                    )
                ).label("n_results"),
                func.sum(
                    func.coalesce(
                        cast(Task.progress["n_retrieved"].as_string(), Integer), 0
                    )
                ).label("n_retrieved"),
            )
            .where(Task.query_id == query_id)
            .group_by(Task.status)
        )

        return [
            QueryProgress(
                status=None if status is None else TaskState(status),
                n_tasks=n_tasks,
                n_results=n_results,
                n_retrieved=n_retrieved,
            )
            for status, n_tasks, n_results, n_retrieved in session.exec(stmt).all()
        ]
