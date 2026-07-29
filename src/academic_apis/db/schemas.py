import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, text
from sqlmodel import Field, SQLModel
from taskiq.depends.progress_tracker import TaskState

from academic_apis.utils.types import Wrapper


class Query(SQLModel, table=True):
    query_id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None)
    query: str
    wrapper: Wrapper
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    first_result: datetime | None = Field(default=None, nullable=True)
    latest_result: datetime | None = Field(default=None, nullable=True)


class AccessTokenPublic(SQLModel):
    token_id: int | None = Field(default=None, primary_key=True)
    wrapper: Wrapper
    proxy: str | None = None
    active: bool = True
    comment: str | None = None
    last_used: datetime | None = Field(default=None, nullable=True)
    locked_until: datetime | None = Field(
        default=None,
        nullable=True,
    )  # when we get API feedback, put here when used up capacity is reset
    last_feedback: dict[str, Any] | None = Field(
        default_factory=dict,
        sa_column=Column("last_feedback", JSON, nullable=True),
    )


class AccessToken(AccessTokenPublic, table=True):
    __tablename__ = "access_token"
    token: str


class TaskPublic(SQLModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    query_id: int
    subquery: str
    num_retries: int = 0
    status: TaskState | None = None
    progress: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("progress", JSON, nullable=True),
    )
    execution_time: float | None = None
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    latest_try: datetime | None = Field(default=None, nullable=True)
    finished_at: datetime | None = Field(default=None, nullable=True)


class Task(TaskPublic, table=True):
    task_name: str
    queue_name: str
    message: bytes
    labels: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("labels", JSON, nullable=True),
    )
