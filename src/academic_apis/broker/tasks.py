import logging
from typing import Annotated, Any

from taskiq import AsyncResultBackend, Context, TaskiqDepends
from taskiq.depends.progress_tracker import TaskProgress, TaskState

from academic_apis.utils.types import Wrapper
from academic_apis.wrappers import WRAPPERS

from .broker import broker

logger = logging.getLogger(__name__)


@broker.task(retry=5)
async def retrieve_query(
    query: str,
    wrapper: Wrapper,
    query_id: int,
    context: Annotated[Context, TaskiqDepends()],
) -> list[dict[str, Any]]:
    API = WRAPPERS[wrapper]  # noqa: N806
    logger.debug(f"Working on query: {query_id}")
    logger.debug(f"Task context: {context.message}")
    task_id = context.message.task_id
    result_backend: AsyncResultBackend[dict[str, Any]] | None = (
        context.broker.result_backend
    )

    if result_backend:
        await result_backend.set_progress(
            task_id=task_id,
            progress=TaskProgress(
                state=TaskState.STARTED,
                meta={"status": "processing", "step": 1, "total": 10},
            ),
        )
    return [
        {
            "max": API.page_max,
            "wrp": API.wrapper,
            "qry": query,
            "qid": query_id,
        }
    ]
