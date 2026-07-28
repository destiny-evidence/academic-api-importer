from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, TypeVar

import sqlalchemy as sa
from taskiq.abc.broker import AsyncBroker
from taskiq.abc.result_backend import AsyncResultBackend
from taskiq.depends.progress_tracker import TaskProgress, TaskState
from taskiq.events import TaskiqEvents
from taskiq.utils import maybe_awaitable

from academic_apis.db import Task, db_session
from academic_apis.utils.types import Mode, Wrapper, assert_int, assert_str

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from sqlmodel import Session  # noqa: F401
    from taskiq.abc.result_backend import TaskiqResult
    from taskiq.message import BrokerMessage

logger = logging.getLogger(__name__)

_ReturnType = TypeVar("_ReturnType")
_T = TypeVar("_T")


class OnDiskResultBackend(AsyncResultBackend[_ReturnType]):
    """
    Inmemory result backend.

    This resultbackend is intended to be used only
    with inmemory broker.

    It stores all results in a dict in memory.
    """

    def __init__(self, max_stored_results: int = 100) -> None:
        self.max_stored_results = max_stored_results
        self.results: OrderedDict[str, TaskiqResult[_ReturnType]] = OrderedDict()
        self.progress: OrderedDict[str, TaskProgress[Any]] = OrderedDict()

    async def set_result(self, task_id: str, result: TaskiqResult[_ReturnType]) -> None:
        """
        Sets result.

        This method is used to store result of an execution in a
        results dict. But also it removes previous results
        to keep memory footprint as low as possible.

        :param task_id: id of a task.
        :param result: result of an execution.
        """
        if (
            self.max_stored_results != -1
            and len(self.results) >= self.max_stored_results
        ):
            self.results.popitem(last=False)
        self.results[task_id] = result

    async def is_result_ready(self, task_id: str) -> bool:
        """
        Checks whether result is ready.

        Readiness means that result with this task_id is
        present in results dict.

        :param task_id: id of a task to check.
        :return: True if ready.
        """
        return task_id in self.results

    async def get_result(
        self,
        task_id: str,
        with_logs: bool = False,  # noqa: ARG002
    ) -> TaskiqResult[_ReturnType]:
        """
        Get result of a task.

        This method is used to get result
        from result dict.

        It throws exception in case if
        result dict doesn't have a value
        for task_id.

        :param task_id: id of a task.
        :param with_logs: this option is ignored.
        :return: result of a task execution.
        """
        return self.results[task_id]

    async def set_progress(
        self,
        task_id: str,
        progress: TaskProgress[dict[str, Any]],
    ) -> None:
        """
        Set progress of task execution.

        :param task_id: task id
        :param progress: task execution progress
        """
        async with db_session() as session:
            session.exec(
                sa.update(Task)
                .where(Task.task_id == task_id)
                .values(progress=progress.meta, status=progress.state),
            )
            session.commit()

    async def get_progress(
        self,
        task_id: str,
    ) -> TaskProgress[Any] | None:
        """
        Get progress of task execution.

        :param task_id: task id
        :return: progress or None
        """
        return self.progress.get(task_id)


class SQLiteBroker(AsyncBroker):
    def __init__(
        self,
        task_id_generator: Callable[[], str] | None = None,
        result_backend: AsyncResultBackend[_T] | None = None,
        queue_name: str = "taskiq",
        wrapper: Wrapper = Wrapper.SCOPUS,
        logger: logging.Logger | None = None,
        wait_loop_timeout: float = 1,
        listen_loop_timeout: float = 1,
    ) -> None:
        """
        Constructs a new broker.

        Inspiration: https://github.com/taskiq-python/taskiq-redis/blob/main/taskiq_redis/redis_broker.py#L104
        :param task_id_generator: custom task_id generator.
        :param result_backend: custom result backend.
        :param queue_name: name for a list in redis.
            Each worker opens its own connection. Therefore, this value has to be
            at least number of workers + 1
        """
        super().__init__(
            result_backend=result_backend,
            task_id_generator=task_id_generator,
        )
        self.queue_name = queue_name
        self.wrapper = wrapper
        self.logger = logger or logging.getLogger(__name__)
        self.wait_loop_timeout: float | int = wait_loop_timeout
        self.listen_loop_timeout: float | int = listen_loop_timeout

    async def kick(self, message: BrokerMessage) -> None:
        async with db_session(Mode.WRITE) as session:
            logger.debug(f"Kicking message to sqlite: {message}")
            queue_name = message.labels.pop("queue_name", None) or self.queue_name
            kwargs = json.loads(message.message)
            session.add(
                Task(
                    task_id=message.task_id,
                    task_name=message.task_name,
                    labels=message.labels,
                    queue_name=queue_name,
                    message=message.message,
                    query_id=assert_int(kwargs["kwargs"].get("query_id")),
                    subquery=assert_str(kwargs["kwargs"].get("query")),
                ),
            )
            session.commit()

    async def listen(self) -> AsyncGenerator[bytes]:
        while True:
            try:
                next_task: Task | None = None
                async with db_session() as session:  # type: Session  # session is a transaction
                    next_task = session.exec(
                        sa.select(Task)
                        .where(
                            Task.queue_name == self.queue_name,
                            sa.or_(
                                Task.status.not_in(
                                    [TaskState.SUCCESS, TaskState.STARTED]
                                ),
                                Task.status == None,  # noqa: E711
                            ),
                        )
                        .order_by(sa.func.coalesce(Task.latest_try, Task.created_at))
                        .limit(1),
                    ).scalar_one_or_none()
                    logger.debug(f"Checked queue, next task: {next_task}")
                    if next_task:
                        next_task.status = TaskState.STARTED
                        next_task.latest_try = dt.datetime.now(tz=dt.UTC)
                        session.commit()
                        logger.debug(f"Next task by broker: {next_task}")
                        session.refresh(next_task)
                        # session.expunge(next_task)
                if next_task:
                    yield next_task.message
            except ConnectionError as exc:
                self.logger.warning(f"Connection error: {exc}")
                continue
            await asyncio.sleep(self.listen_loop_timeout)

    async def wait_all(self) -> None:
        """
        Wait for all currently running tasks to complete.

        Useful when used in testing and you need to await all sent tasks
        before asserting results.
        """
        while True:
            async with db_session() as session:  # type: Session
                tasks = session.exec(
                    sa.select(Task.task_id, Task.task_name).where(
                        Task.status == "RUNNING",
                    ),
                ).all()
                if len(tasks) > 0:
                    self.logger.info(f"Waiting for {len(tasks)} to finish: {tasks}")
            await asyncio.sleep(self.wait_loop_timeout)

    async def startup(self) -> None:
        """Runs startup events for client and worker side."""
        async with db_session() as session:  # type: Session
            session.exec(
                sa.update(Task)
                .values(status=None)
                .where(Task.status != TaskState.SUCCESS),
            )

        for event in (TaskiqEvents.CLIENT_STARTUP, TaskiqEvents.WORKER_STARTUP):
            for handler in self.event_handlers.get(event, []):
                await maybe_awaitable(handler(self.state))

    async def shutdown(self) -> None:
        """Runs shutdown events for client and worker side."""
        for event in (TaskiqEvents.CLIENT_SHUTDOWN, TaskiqEvents.WORKER_SHUTDOWN):
            for handler in self.event_handlers.get(event, []):
                await maybe_awaitable(handler(self.state))
        # self.executor.shutdown()
