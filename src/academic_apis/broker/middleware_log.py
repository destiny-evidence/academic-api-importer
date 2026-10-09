import contextvars
import logging
import typing
from pathlib import Path
from typing import Any

from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult

from academic_apis.utils.config import settings
from academic_apis.utils.logging import DEFAULT_TASK_LOGGER_NAME

# Context variable tracking the active task_id in the current async execution context
current_task_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_task_id", default=None
)


class _TaskFieldsFilter(logging.Filter):
    """Filters out log records from other concurrent tasks or unbound framework logs."""

    def __init__(self, task_id: str, task_name: str) -> None:
        super().__init__()
        self.task_id = task_id
        self.task_name = task_name

    def filter(self, record: logging.LogRecord) -> bool:
        active_task_id = current_task_id_var.get()

        # Record explicitly tagged with a task_id
        record_task_id = getattr(record, "task_id", None)
        if record_task_id is not None and record_task_id != self.task_id:
            return False

        # Strictly filter out records not bound to this task's context
        if active_task_id != self.task_id:
            return False

        # Attach metadata required by log formatters
        record.task_id = self.task_id
        record.task_name = self.task_name
        return True


class TaskLogFileMiddleware(TaskiqMiddleware):
    """Captures task execution logs into isolated, task-specific log files."""

    def _resolve_query_id(self, message: TaskiqMessage) -> int:
        """Extracts query_id from task kwargs, positionals, or labels."""
        if "query_id" in message.kwargs:
            return int(message.kwargs["query_id"])

        if message.args and isinstance(message.args[0], int):
            return message.args[0]

        if "query_id" in message.labels:
            return int(message.labels["query_id"])

        raise ValueError(
            f"Missing required 'query_id' parameter for task {message.task_name} ({message.task_id})"
        )

    @typing.override
    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        query_id = self._resolve_query_id(message)
        task_id = message.task_id

        token = current_task_id_var.set(task_id)

        log_path: Path = settings.task_log(query_id, task_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        is_retry = log_path.exists() and log_path.stat().st_size > 0
        if is_retry:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n\n--- TASK RESTARTED ---\n\n")

        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")

        task_logger = logging.getLogger(DEFAULT_TASK_LOGGER_NAME)
        file_handler.setLevel(task_logger.level or logging.DEBUG)

        # Formatter using task metadata format
        task_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s [%(task_id)s | %(task_name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(task_formatter)

        task_filter = _TaskFieldsFilter(task_id, message.task_name)
        file_handler.addFilter(task_filter)

        task_logger.addHandler(file_handler)

        # Extract simple function name for logger hierarchy (e.g. academic_apis.task.retrieve_query)
        clean_name = message.task_name.rsplit(":", 1)[-1]
        logger = logging.getLogger(f"{DEFAULT_TASK_LOGGER_NAME}.{clean_name}")

        if is_retry:
            logger.info("Executing task retry attempt...")
        else:
            logger.info("Executing task initial attempt...")

        message.labels["_log_file_state"] = {
            "handler": file_handler,
            "filter": task_filter,
            "context_token": token,
            "log_path": log_path,
            "task_logger": task_logger,
        }

        return message

    @typing.override
    def post_execute(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
    ) -> None:
        state = message.labels.pop("_log_file_state", None)
        if not state:
            return

        file_handler: logging.FileHandler = state["handler"]
        task_filter: _TaskFieldsFilter = state["filter"]
        token: contextvars.Token[str | None] = state["context_token"]
        log_path: Path = state["log_path"]
        task_logger: logging.Logger = state["task_logger"]

        task_logger.removeHandler(file_handler)
        file_handler.removeFilter(task_filter)

        file_handler.flush()
        file_handler.close()

        current_task_id_var.reset(token)

        try:
            result.log = log_path.read_text(encoding="utf-8")
        except OSError:
            result.log = ""
