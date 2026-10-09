from __future__ import annotations

import asyncio
import datetime as dt
import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import orjson as json
from sqlmodel import col, func, or_, select, update
from taskiq.abc.broker import AsyncBroker
from taskiq.abc.result_backend import AsyncResultBackend, TaskiqResult
from taskiq.depends.progress_tracker import TaskProgress, TaskState
from taskiq.events import TaskiqEvents
from taskiq.utils import maybe_awaitable

from academic_apis.db import Task, db_session
from academic_apis.utils import settings
from academic_apis.utils.types import Mode, Wrapper, assert_int, assert_str

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from taskiq.message import BrokerMessage

broker_logger = logging.getLogger(__name__)

_ReturnType = TypeVar("_ReturnType")
_T = TypeVar("_T")


async def get_task(task_id: str) -> Task:
    async with db_session() as session:
        task: Task = session.exec(
            select(Task).where(Task.task_id == task_id),
        ).one()
        session.expunge(task)
    return task


class OnDiskResultBackend(AsyncResultBackend[_ReturnType]):
    @classmethod
    def result_file(cls, query_id: int, task_id: str) -> Path:
        return (settings.responses_dir(query_id) / task_id).with_suffix(".jsonl")

    @classmethod
    def log_file(cls, query_id: int, task_id: str) -> Path:
        return (settings.responses_dir(query_id) / task_id).with_suffix(".log")

    @classmethod
    def err_file(cls, query_id: int, task_id: str) -> Path:
        return (settings.responses_dir(query_id) / task_id).with_suffix(".err")

    async def set_result(self, task_id: str, result: TaskiqResult[_ReturnType]) -> None:
        """
        Sets result.

        This method is used to store result of an execution in a
        results dict. But also it removes previous results
        to keep memory footprint as low as possible.

        :param task_id: id of a task.
        :param result: result of an execution.
        """
        async with db_session() as session:
            task = session.exec(select(Task).where(Task.task_id == task_id)).one()
            broker_logger.info(
                f"Found task: {task.model_dump(exclude={'subquery', 'message'})}"
            )
            result_file = self.result_file(task_id=task_id, query_id=task.query_id)
            log_file = self.log_file(task_id=task_id, query_id=task.query_id)
            exc_file = self.err_file(task_id=task_id, query_id=task.query_id)

            task.execution_time = result.execution_time
            task.labels = (task.labels | {}) | (result.labels | {})
            task.labels.pop("_capture_state", None)
            task.labels.pop("_log_file_state", None)

            if result.is_err:
                task.num_retries += 1
                if task.num_retries > settings.max_retries:
                    task.status = TaskState.FAILURE
                else:
                    task.status = TaskState.RETRY
                task.latest_try = dt.datetime.now(tz=dt.UTC)
            else:
                task.status = TaskState.SUCCESS
                task.finished_at = dt.datetime.now(tz=dt.UTC)

            session.commit()

            if result.error:
                with exc_file.open("wb") as exc_fp:
                    pickle.dump(result.error, exc_fp)
            if result.log:
                with log_file.open("w") as log_fp:
                    log_fp.write(result.log)
            if result.return_value:
                records = cast("list[dict[str, Any]]", result.return_value)
                with result_file.open("wb") as result_fp:
                    [result_fp.write(json.dumps(record) + b"\n") for record in records]

    async def is_result_ready(self, task_id: str) -> bool:
        """
        Checks whether result is ready.

        Readiness means that result file exists.

        :param task_id: id of a task to check.
        :return: True if ready.
        """
        task = await get_task(task_id)
        return self.result_file(query_id=task.query_id, task_id=task_id).exists()

    async def get_result(
        self,
        task_id: str,
        with_logs: bool = False,
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
        task = await get_task(task_id)

        log: str | None = None
        log_file = self.log_file(query_id=task.query_id, task_id=task_id)
        if with_logs and log_file.exists():
            with log_file.open() as log_fp:
                log = log_fp.read()

        exc: BaseException | None = None
        err_file = self.err_file(query_id=task.query_id, task_id=task_id)
        if err_file.exists():
            with err_file.open("rb") as err_fp:
                exc = pickle.load(err_fp)  # noqa: S301

        result: list[dict[str, Any]] = []
        result_file = self.result_file(query_id=task.query_id, task_id=task_id)
        if result_file.exists():
            with result_file.open() as file:
                result = [json.loads(line) for line in file]

        return TaskiqResult(
            is_err=exc is not None,
            log=log,
            execution_time=task.execution_time or -1.0,
            labels=task.labels,
            error=exc,
            return_value=cast("_ReturnType", result),
        )

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
                update(Task)
                .where(col(Task.task_id) == task_id)
                .values(progress=progress.meta, status=progress.state),
            )
            session.commit()

    async def get_progress(
        self,
        task_id: str,
    ) -> TaskProgress[dict[str, Any]] | None:
        """
        Get progress of task execution.

        :param task_id: task id
        :return: progress or None
        """
        task = await get_task(task_id)
        if task.status is None:
            return None
        return TaskProgress(state=task.status, meta=task.progress)


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
        empty_loop_sleep: float = 1,
    ) -> None:
        """
        Constructs a new broker.

        Inspiration: https://github.com/taskiq-python/taskiq-redis/blob/main/taskiq_redis/redis_broker.py#L104
        :param task_id_generator: custom task_id generator.
        :param result_backend: custom result backend.
        :param empty_loop_sleep: Number of seconds to sleep when the current worker loop iteration found no tasks
        :param listen_loop_timeout: Number of seconds to sleep between worker loop iterations
        :param wait_loop_timeout: Number of seconds to wait between status checks on shutdown
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
        self.logger = logger or broker_logger
        self.wait_loop_timeout: float | int = wait_loop_timeout
        self.listen_loop_timeout: float | int = listen_loop_timeout
        self.empty_loop_sleep: float | int = empty_loop_sleep

    async def kick(self, message: BrokerMessage) -> None:
        async with db_session(Mode.WRITE) as session:
            self.logger.debug(f"Kicking message to sqlite: {message}")
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
                async with db_session(
                    Mode.WRITE
                ) as session:  # session is a transaction
                    next_task = session.exec(
                        select(Task)
                        .where(
                            Task.queue_name == self.queue_name,
                            or_(
                                col(Task.status).not_in(
                                    [TaskState.SUCCESS, TaskState.STARTED],
                                ),
                                col(Task.status).is_(None),
                            ),
                            or_(
                                col(Task.num_retries).is_(None),
                                Task.num_retries <= settings.max_retries,
                            ),
                        )
                        .order_by(func.coalesce(Task.latest_try, Task.created_at))
                        .limit(1)
                    ).one_or_none()
                    if next_task:
                        next_task.status = TaskState.STARTED
                        next_task.latest_try = dt.datetime.now(tz=dt.UTC)
                        session.commit()
                        session.refresh(next_task)
                        self.logger.debug(
                            f"Next task by broker: {next_task.model_dump_json(exclude={'message', 'subquery'})}"
                        )
                        # session.expunge(next_task)

                if next_task and next_task.message:
                    yield next_task.message
                else:
                    self.logger.info(
                        f"No task in queue, going to sleep for {self.empty_loop_sleep} seconds."
                    )
                    await asyncio.sleep(self.empty_loop_sleep)
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
            async with db_session() as session:
                tasks = session.exec(
                    select(Task.task_id, Task.task_name).where(
                        Task.status == TaskState.STARTED.value
                    ),
                ).all()
                if len(tasks) > 0:
                    self.logger.info(f"Waiting for {len(tasks)} to finish: {tasks}")
            await asyncio.sleep(self.wait_loop_timeout)

    async def startup(self) -> None:
        """Runs startup events for client and worker side."""
        async with db_session(Mode.WRITE) as session:
            session.exec(
                update(Task)
                .values(status=None)
                .where(col(Task.status) != TaskState.SUCCESS.value),
            )
            session.commit()

        for event in (TaskiqEvents.CLIENT_STARTUP, TaskiqEvents.WORKER_STARTUP):
            for handler in self.event_handlers.get(event, []):
                await maybe_awaitable(handler(self.state))

    async def shutdown(self) -> None:
        """Runs shutdown events for client and worker side."""
        for event in (TaskiqEvents.CLIENT_SHUTDOWN, TaskiqEvents.WORKER_SHUTDOWN):
            for handler in self.event_handlers.get(event, []):
                await maybe_awaitable(handler(self.state))
        # self.executor.shutdown()
