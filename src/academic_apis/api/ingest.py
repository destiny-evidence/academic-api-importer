import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import desc, select
from starlette import status

from academic_apis.api.utils.auth import ensure_robot_authentication, get_current_admin
from academic_apis.db import db_session
from academic_apis.db.schemas import (
    Query,
    SearchIngest,
    User,
)
from academic_apis.utils import Mode, settings
from academic_apis.utils.azure import get_azure_links

logger = logging.getLogger(__name__)

route = APIRouter()

SUBQUERY_PREVIEW_LENGTH = 50
ELLIPSIS = "..."


@route.get("/")
async def list_ingests(
    _is_authorised: Annotated[bool, Depends(ensure_robot_authentication)],
    offset: int = 0,
    limit: int = 5,
    only_open: bool = False,
) -> Sequence[SearchIngest]:
    async with db_session() as session:
        stmt = (
            select(SearchIngest)
            .order_by(desc(SearchIngest.ingest_id))
            .limit(limit)
            .offset(offset)
        )
        if only_open:
            stmt = stmt.where(
                SearchIngest.is_queued == False,
                SearchIngest.is_queueing == False,
            )
        return session.exec(stmt).all()


@route.put("/queueing")
async def mark_queueing(
    _is_authorised: Annotated[bool, Depends(ensure_robot_authentication)],
    ingest_id: str,
) -> SearchIngest:
    async with db_session(mode=Mode.WRITE) as session:
        ingest = session.get(SearchIngest, ingest_id)
        if ingest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No info found for ingest_id={ingest_id}",
            )

        if ingest.is_queueing:
            # Already marked as queueing
            return ingest

        ingest.is_queueing = True
        ingest.is_queued = False  # explicitly (re-)set this
        session.commit()
        session.refresh(ingest)
        return ingest


@route.put("/queued")
async def mark_queued(
    _is_authorised: Annotated[bool, Depends(ensure_robot_authentication)],
    ingest_id: str,
) -> SearchIngest:
    async with db_session(mode=Mode.WRITE) as session:
        ingest = session.get(SearchIngest, ingest_id)
        if ingest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No info found for ingest_id={ingest_id}",
            )

        if not ingest.is_queueing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Trying to mark queued before queueing ingest_id={ingest_id}",
            )

        if ingest.is_queued:
            return ingest

        ingest.is_queued = True
        session.commit()
        session.refresh(ingest)
        return ingest


@route.put("/transfer")
async def add_ingest(
    _current_admin: Annotated[User, Depends(get_current_admin)],
    query_id: int,
) -> SearchIngest:
    async with db_session(mode=Mode.WRITE) as session:
        ingest = session.exec(
            select(SearchIngest).where(SearchIngest.query_id == query_id).limit(1)
        ).one_or_none()
        if ingest is not None:
            return ingest
        query = session.get(Query, query_id)
        if query is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No query found for query_id={query_id}",
            )
        blob_path = f"{settings.azure_storage_container_path}{query_id}"
        links = await get_azure_links(
            blob_path=blob_path,
            key_lifetime=settings.azure_storage_robot_key_lifetime,
        )
        ingest = SearchIngest(
            query_id=query_id,
            azure_link=links.responses_url,
        )
        session.add(ingest)
        session.commit()
        session.refresh(ingest)
        return ingest
