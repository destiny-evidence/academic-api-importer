import datetime as dt
import logging
from dataclasses import asdict
from typing import Annotated, Any

from sqlmodel import col, or_, select
from taskiq import Context, TaskiqDepends
from taskiq.depends.progress_tracker import TaskProgress, TaskState

from academic_apis.db import AccessToken, db_session
from academic_apis.utils.types import Wrapper
from academic_apis.wrappers import WRAPPERS, OnLoopInfo

from .broker import broker


@broker.task
async def retrieve_query(
    query: str,
    wrapper: Wrapper,
    query_id: int,
    context: Annotated[Context, TaskiqDepends()],
) -> list[dict[str, Any]]:
    logger = logging.getLogger(
        f"query-retrieve | query={query_id} | task={context.message.task_id}",
    )

    API = WRAPPERS[wrapper]  # noqa: N806
    async with db_session() as session:
        token = session.exec(
            select(AccessToken)
            .where(
                AccessToken.wrapper == wrapper,
                col(AccessToken.active).is_(True),
                or_(
                    col(AccessToken.locked_until).is_(None),
                    col(AccessToken.locked_until)
                    <= dt.datetime.now(tz=dt.UTC).timestamp(),
                ),
            )
            .order_by(col(AccessToken.last_used))
        ).one()
        session.expunge(token)

    async def _on_loop(info: OnLoopInfo) -> None:
        result_backend = context.broker.result_backend
        if not result_backend:
            return

        await result_backend.set_progress(
            task_id=context.message.task_id,
            progress=TaskProgress(state=TaskState.STARTED, meta=asdict(info)),
        )
        api._on_loop = None  # noqa: SLF001
        return

    api = API(
        logger=logger,
        api_key=token.token,
        proxy=token.proxy,
        skip_token_logging=False,
        on_loop=_on_loop,
    )
    return [item async for item in api.fetch_raw(query=query)]
