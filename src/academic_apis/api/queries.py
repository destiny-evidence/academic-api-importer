from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Body, Depends, HTTPException, BackgroundTasks
from httpx import Response
from lark import LarkError
from pydantic import BaseModel
from sqlmodel import col, select, delete
from starlette import status

from academic_apis.api.utils import require_query_ownership
from academic_apis.api.utils.auth import get_current_user, get_current_admin
from academic_apis.db import Mode, Query, User, db_session, Task
from academic_apis.db.schemas import QueryInfo, UserQueryPermission
from academic_apis.db.tokens import get_available_token_for_user
from academic_apis.grammars.scopus import QuerySyntaxError, query_to_indented_text
from academic_apis.utils import Wrapper, settings
from academic_apis.wrappers import WRAPPERS

if TYPE_CHECKING:
    from sqlmodel import Session

logger = logging.getLogger(__name__)

route = APIRouter()


class TestResult(BaseModel):
    body: dict[str, Any]
    headers: dict[str, Any]
    status: int


@route.post("/test")
async def test_query(
    current_user: Annotated[User, Depends(get_current_user)],
    query: Annotated[QueryInfo, Body()],
) -> TestResult:
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
    response: Response | None = await api.get_first_response(
        query=query.query, params=query.params
    )

    if response is None:
        raise HTTPException(
            status_code=status.HTTP_204_NO_CONTENT,
            detail=f"Did not receive a response from the {query.wrapper} API.",
        )
    try:
        body = response.json()
    except Exception as e:  # noqa: BLE001
        body = {
            "text": response.text,
            "error": str(e),
        }
    return TestResult(
        body=body,
        headers=response.headers,
        status=response.status_code,
    )


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


async def _delete_query(query_id: int) -> None:
    async with db_session(mode=Mode.WRITE) as session:  # type: Session
        # Drop Task (subqueries) from database
        session.exec(delete(Task).where(Task.query_id == query_id))
        session.flush()
        # Drop Permissions from database
        session.exec(
            delete(UserQueryPermission).where(UserQueryPermission.query_id == query_id)
        )
        session.flush()
        # Drop Query from database
        session.exec(delete(Query).where(Query.query_id == query_id))

        # Delete data from disk
        shutil.rmtree(settings.responses_dir(query_id))
        shutil.rmtree(settings.converted_dir(query_id))

        # Commit changes
        session.commit()


@route.delete("/")
async def delete_query(
    _current_admin: Annotated[User, Depends(get_current_admin)],
    query_id: int,
    background_tasks: BackgroundTasks,
) -> None:
    background_tasks.add_task(_delete_query, query_id)


async def _queue_subqueries(
    query: Query,
    py_start: int = 2010,
    py_end: int | None = None,
    py_steps: int = 2,
    use_py_filter: bool = False,
) -> None:

    from academic_apis.broker.tasks import retrieve_query

    logger.debug(f"Queueing subquery tasks for query_id={query.query_id}")

    API = WRAPPERS[query.wrapper]  # noqa: N806

    for subquery in API.generate_subqueries(
        query.query,
        py_start=py_start,
        py_end=py_end,
        py_steps=py_steps,
        use_py_filter=use_py_filter,
    ):
        logger.debug(f"Subquery: {subquery[-200:]}")
        await retrieve_query.kiq(
            query=subquery, wrapper=query.wrapper, query_id=query.query_id
        )  # type: ignore[call-overload]


@route.post("/")
async def submit_query(
    current_user: Annotated[User, Depends(get_current_user)],
    query: Annotated[QueryInfo, Body()],
    background_tasks: BackgroundTasks,
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

    async with db_session(mode=Mode.WRITE) as session:  # type: Session
        query_orm = Query(
            query=query.query,
            wrapper=query.wrapper,
        )
        session.add(query_orm)
        session.flush()
        session.refresh(query_orm)
        query_id: int = cast("int", query_orm.query_id)
        logger.info(f"Registered query as query_id={query_id}")
        session.add(
            UserQueryPermission(
                user_id=current_user.user_id,
                query_id=query_id,
                is_owner=True,
            )
        )
        session.commit()

    background_tasks.add_task(
        _queue_subqueries,
        query=query_orm,
        py_start=py_start,
        py_end=py_end,
        py_steps=py_steps,
        use_py_filter=use_py_filter,
    )

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


def _permissions_for_query(
    session: Session,
    query_id: int,
) -> Sequence[UserQueryPermission]:
    return session.exec(
        select(UserQueryPermission).where(
            UserQueryPermission.query_id == query_id,
        ),
    ).all()


@route.put("/permission")
async def upsert_permission(
    current_user: Annotated[User, Depends(get_current_user)],
    permission: Annotated[UserQueryPermission, Body()],
) -> Sequence[UserQueryPermission]:
    """
    Create or update a user's permission to access a query.

    Admin users can create permissions for anyone and any query.
    All other users need an owner permission (`is_owner=True`) for the query
    themselves; only then they may grant permissions to others and change
    existing ones (e.g. pass on the ownership).
    The primary key is `(user_id, query_id)`, so an existing permission is
    updated instead of duplicated.

    :param current_user:
    :param permission: the permission to create or update
    :return: all permissions stored for `permission.query_id` afterwards
    """
    async with db_session(mode=Mode.WRITE) as session:  # type: Session
        if session.get(Query, permission.query_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No query found with query_id={permission.query_id}",
            )
        if session.get(User, permission.user_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No user found with user_id={permission.user_id}",
            )

        require_query_ownership(session, current_user, permission.query_id)

        session.merge(permission)
        session.commit()

        return _permissions_for_query(session, permission.query_id)


@route.delete("/permission")
async def drop_permission(
    current_user: Annotated[User, Depends(get_current_user)],
    permission: Annotated[UserQueryPermission, Body()],
) -> Sequence[UserQueryPermission]:
    """
    Delete a user's permission to access a query.

    Admin users can delete permissions for any query.
    All other users need an owner permission (`is_owner=True`) for that query
    in the database to remove someone else's permission.

    :param current_user:
    :param permission: the permission to delete (identified by user_id + query_id)
    :return: all permissions stored for `permission.query_id` afterwards
    """
    async with db_session(mode=Mode.WRITE) as session:  # type: Session
        require_query_ownership(session, current_user, permission.query_id)

        stored = session.get(
            UserQueryPermission,
            (permission.user_id, permission.query_id),
        )
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No permission found for user_id={permission.user_id} "
                    f"on query_id={permission.query_id}"
                ),
            )
        session.delete(stored)
        session.commit()

        return _permissions_for_query(session, permission.query_id)


@route.get("/permission")
async def get_permissions(
    current_user: Annotated[User, Depends(get_current_user)],
    query_id: int,
) -> Sequence[UserQueryPermission]:
    """
    Read permissions for a query

    :param current_user:
    :param query_id:
    :return: all permissions stored for `query_id`
    """
    async with db_session() as session:  # type: Session
        require_query_ownership(session, current_user, query_id)
        return _permissions_for_query(session=session, query_id=query_id)
