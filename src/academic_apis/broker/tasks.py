import logging
from dataclasses import asdict
from typing import Annotated, Any

from taskiq import Context, TaskiqDepends
from taskiq.depends.progress_tracker import TaskProgress, TaskState

from academic_apis.db.tokens import get_available_token
from academic_apis.utils.logging import DEFAULT_TASK_LOGGER_NAME
from academic_apis.utils.types import Wrapper
from academic_apis.wrappers import WRAPPERS, OnLoopInfo

from .broker import broker


@broker.task
async def retrieve_query(
    query: str,
    wrapper: Wrapper,
    query_id: int,
    params: dict[str, Any] | None,
    context: Annotated[Context, TaskiqDepends()],
) -> list[dict[str, Any]]:
    # Standardized logger path: academic_apis.task.retrieve_query
    logger = logging.getLogger(f"{DEFAULT_TASK_LOGGER_NAME}.retrieve_query")
    API = WRAPPERS[wrapper]  # noqa: N806
    token = await get_available_token(query_id=query_id, wrapper=wrapper)
    if not token:
        raise PermissionError(
            "No active API token available for the owner of the query."
        )

    async def _on_loop(info: OnLoopInfo) -> None:
        result_backend = context.broker.result_backend
        if not result_backend:
            return

        await result_backend.set_progress(
            task_id=context.message.task_id,
            progress=TaskProgress(state=TaskState.STARTED, meta=asdict(info)),
        )
        return

    api = API(
        logger=logger,
        api_key=token.token,
        proxy=token.proxy,
        skip_token_logging=False,
        on_loop=_on_loop,
    )

    return [item async for item in api.fetch_raw(query=query, params=params)]
