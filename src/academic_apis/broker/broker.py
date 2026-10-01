import importlib
from typing import Any

import typer
from taskiq import SimpleRetryMiddleware, TaskiqEvents

from academic_apis.utils import init_logging, settings

from .middleware_log import OutputCaptureMiddleware
from .sqlite_broker import OnDiskResultBackend, SQLiteBroker

app = typer.Typer()

results: OnDiskResultBackend[Any] = OnDiskResultBackend()
broker = (
    SQLiteBroker(
        queue_name="scopus",
        listen_loop_timeout=1,
        wait_loop_timeout=1,
        empty_loop_sleep=settings.empty_loop_sleep,
    )
    .with_middlewares(SimpleRetryMiddleware(default_retry_count=5))
    .with_result_backend(results)
    .with_middlewares(OutputCaptureMiddleware())
)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
def setup_logging(*args: Any) -> None:  # noqa: ANN401, ARG001
    init_logging()
    importlib.import_module("academic_apis.broker.tasks")


#
# # Never forget to call startup in the beginning.
# await broker.startup()
#
#
# # Send the task to the broker.
# task = await run_query_task.kiq(query="climate AND health", wrapper=Wrapper.SCOPUS)
#
# # Wait for the result.
# result = await task.wait_result(timeout=2)
#
# print(f"Task execution took: {result.execution_time} seconds.")
# if not result.is_err:
#     print(f"Returned value: {result.return_value}")
# else:
#     print("Error found while executing task.")
# await broker.shutdown()
