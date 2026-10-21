from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable, Sequence
from itertools import repeat
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from httpx import Response
from lark import LarkError
from pydantic import BaseModel
from sqlmodel import col, delete, select
from starlette import status

from academic_apis.api.utils import require_query_ownership
from academic_apis.api.utils.auth import get_current_admin, get_current_user
from academic_apis.db import Mode, Query, Task, User, db_session
from academic_apis.db.schemas import QueryInfo, SearchIngest, UserQueryPermission
from academic_apis.db.tokens import get_available_token_for_user
from academic_apis.grammars.scopus import QuerySyntaxError, query_to_indented_text
from academic_apis.utils import Wrapper, settings
from academic_apis.utils.types import AzureAccessLinks
from academic_apis.wrappers import WRAPPERS

if TYPE_CHECKING:
    from sqlmodel import Session

logger = logging.getLogger(__name__)
background_logger = logger.getChild("background")

route = APIRouter()

MIN_QUERY_LENGTH = 3


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
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You do not have access to any active auth token.",
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
        headers=dict(response.headers),
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
        background_logger.info(
            f"Dropping tasks for query_id={query_id} from database..."
        )
        session.exec(delete(Task).where(col(Task.query_id) == query_id))
        session.flush()

        background_logger.info(
            f"Dropping search-ingest trackers for query_id={query_id} from database..."
        )
        session.exec(delete(SearchIngest).where(col(SearchIngest.query_id) == query_id))

        background_logger.info(
            f"Dropping query permissions for query_id={query_id} from database..."
        )
        session.exec(
            delete(UserQueryPermission).where(
                col(UserQueryPermission.query_id) == query_id
            )
        )
        session.flush()

        background_logger.info(
            f"Dropping query with query_id={query_id} from database..."
        )
        session.exec(delete(Query).where(col(Query.query_id) == query_id))

        background_logger.info(f"Deleting files at {settings.responses_dir(query_id)}")
        shutil.rmtree(settings.responses_dir(query_id))
        background_logger.info(f"Deleting files at {settings.converted_dir(query_id)}")
        shutil.rmtree(settings.converted_dir(query_id))

        background_logger.info("Committing database")
        session.commit()


def _delete_query_logs(query_id: int) -> None:
    responses_dir = settings.responses_dir(query_id)
    for pattern in ("*.log", "*.err"):
        logger.info(f"Going to delete query logs for pattern {responses_dir}/{pattern}")
        for path in responses_dir.glob(pattern):
            path.unlink(missing_ok=True)


@route.delete("/")
async def delete_query(
    _current_admin: Annotated[User, Depends(get_current_admin)],
    query_id: int,
    background_tasks: BackgroundTasks,
) -> None:
    background_tasks.add_task(_delete_query, query_id)


@route.delete("/logs")
async def delete_query_logs(
    current_admin: Annotated[User, Depends(get_current_user)],
    query_id: int,
    background_tasks: BackgroundTasks,
) -> None:
    async with db_session(mode=Mode.WRITE) as session:  # type: Session
        require_query_ownership(session, current_admin, query_id)
    background_tasks.add_task(_delete_query_logs, query_id)


async def _submit_subqueries(
    query: Query,
    tasks: Iterable[tuple[str, dict[str, Any] | None]],
) -> None:

    from academic_apis.broker.tasks import retrieve_query

    background_logger.info(f"Queueing subquery tasks for query_id={query.query_id}...")

    n_subqueries = 0
    for subquery, params in tasks:
        background_logger.debug(f"Subquery: {subquery[-200:]}")
        await retrieve_query.kiq(
            query=subquery,
            wrapper=query.wrapper,
            query_id=query.query_id,
            params=params,
        )  # type: ignore[call-overload]
        n_subqueries += 1

    background_logger.info(
        f"Queued {n_subqueries:,} subquery tasks for query_id={query.query_id}!"
    )


async def _queue_subqueries(
    query: Query,
    py_start: int = 2010,
    py_end: int | None = None,
    py_steps: int = 2,
    use_py_filter: bool = False,
    use_firstauth_filter: bool = True,
) -> None:

    API = WRAPPERS[query.wrapper]  # noqa: N806
    await _submit_subqueries(
        query=query,
        tasks=zip(
            API.generate_subqueries(
                query.query,
                py_start=py_start,
                py_end=py_end,
                py_steps=py_steps,
                use_py_filter=use_py_filter,
                use_firstauth_filter=use_firstauth_filter,
            ),
            repeat(query.params),
            strict=False,
        ),
    )


@route.post("/")
async def submit_query(
    current_user: Annotated[User, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    query: Annotated[QueryInfo, Body()],
    queries: Annotated[list[QueryInfo], Body()] | None = None,
    py_start: int = 2010,
    py_end: int | None = None,
    py_steps: int = 2,
    use_py_filter: bool = False,
    use_firstauth_filter: bool = True,
) -> int:
    """
    Submit a query to be queued for workers to fetch.

    In case a list of queries is provided, this will not use the wrapper-specific
    sub-query generator but interpret each `QueryInfo` as a dedicated task. These
    are queued as tasks directly, i.e. without generating subqueries first.
    The first query is registered as the parent query the tasks belong to.

    :param current_user:
    :param query: the query of which to generate sub-queries to submit as tasks to the queue;
                  if `queries` are set, using this only for reference
    :param queries: the queries to submit as tasks to the queue
    :param py_start:
    :param py_end:
    :param py_steps:
    :param use_py_filter:
    :param use_firstauth_filter:
    :param background_tasks:
    :return: the query_id of the registered parent query
    """
    if not current_user.permission_submit_query or not current_user.permission_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have the permission to submit queries.",
        )
    if queries is not None and len(queries) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided sub-query (task) list is empty!",
        )
    if any(len(q.query) < MIN_QUERY_LENGTH for q in ((queries or []) + [query])):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Query too short!"
        )

    if (
        len({q.wrapper for q in ((queries or []) + [query])}) > 1
        or query.wrapper not in WRAPPERS
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested queries use different or invalid wrappers!",
        )

    token = await get_available_token_for_user(
        user_id=current_user.user_id,
        wrapper=query.wrapper,
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You do not have access to any active auth token.",
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

        if queries is not None:
            logger.info(
                f"Submitting {len(queries) + 1:,} sub-queries directly to worker queue for query {query_id}"
            )
            background_tasks.add_task(
                _submit_subqueries,
                query=query_orm,
                tasks=((q.query, q.params) for q in ((queries or []) + [query])),
            )

        session.add(
            UserQueryPermission(
                user_id=current_user.user_id,
                query_id=query_id,
                is_owner=True,
            )
        )
        session.commit()
        session.refresh(query_orm)

    if queries is None:
        logger.info(
            f"Submitting generated sub-queries to worker queue for query {query_id}"
        )
        background_tasks.add_task(
            _queue_subqueries,
            query=query_orm,
            py_start=py_start,
            py_end=py_end,
            py_steps=py_steps,
            use_py_filter=use_py_filter,
            use_firstauth_filter=use_firstauth_filter,
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
        # Both body fields are primary keys, so SQLModel makes them optional and an
        # incomplete body reaches this point. Handing a NULL primary key to `session.get`
        # would make SQLAlchemy warn that such an "identity cannot load any object"; the
        # lookup is skipped instead, which answers the very same 404.
        if (
            permission.query_id is None
            or session.get(Query, permission.query_id) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No query found with query_id={permission.query_id}",
            )
        if permission.user_id is None or session.get(User, permission.user_id) is None:
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


async def update_azure_link(
    current_user: User, query_id: int
) -> tuple[str, AzureAccessLinks]:
    from academic_apis.utils.azure import get_azure_links

    async with db_session() as session:  # type: Session
        require_query_ownership(
            session=session, current_user=current_user, query_id=query_id
        )
        blob_path = f"{settings.azure_storage_container_path}{query_id}"
        links = await get_azure_links(blob_path)
        query = session.get(Query, query_id)
        if query:
            query.azure_link = links
            session.commit()
        return blob_path, links


@route.get("/download")
async def download_query(
    current_admin: Annotated[User, Depends(get_current_admin)],
    query_id: int,
    background_tasks: BackgroundTasks,
    concatenated: bool = False,
) -> AzureAccessLinks:
    from academic_apis.utils.azure import sync_directory, upload_concatenated_files

    blob_path, links = await update_azure_link(
        current_user=current_admin,
        query_id=query_id,
    )

    if concatenated:
        background_tasks.add_task(
            upload_concatenated_files,
            source_pattern=f"{settings.result_dir}/{query_id}/responses/*.jsonl",
            target_file=f"{blob_path}/responses.jsonl",
        )
        background_tasks.add_task(
            upload_concatenated_files,
            source_pattern=f"{settings.result_dir}/{query_id}/responses/*.err",
            target_file=f"{blob_path}/responses.err",
            include_file_headers=True,
        )
        background_tasks.add_task(
            upload_concatenated_files,
            source_pattern=f"{settings.result_dir}/{query_id}/responses/*.log",
            target_file=f"{blob_path}/responses.log",
            include_file_headers=True,
        )
    else:
        background_tasks.add_task(
            sync_directory,
            source_dir=str(settings.result_dir / str(query_id)),
            target_dir=blob_path,
        )

    return links


@route.get("/download/refresh")
async def refresh_download_links(
    current_user: Annotated[User, Depends(get_current_user)],
    query_id: int,
) -> AzureAccessLinks:
    _, links = await update_azure_link(
        current_user=current_user,
        query_id=query_id,
    )

    return links
