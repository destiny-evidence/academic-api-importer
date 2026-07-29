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
    first_result: datetime = Field(default=None, nullable=True)
    latest_result: datetime = Field(default=None, nullable=True)


class AccessToken(SQLModel, table=True):
    __tablename__ = "access_token"
    token_id: int | None = Field(default=None, primary_key=True)
    wrapper: Wrapper
    token: str
    proxy: str | None = None
    active: bool = True
    comment: str | None = None
    last_used: datetime | None = Field(default=None, nullable=True)
    locked_until: datetime = Field(
        default=None, nullable=True
    )  # when we get API feedback, put here when used up capacity is reset
    last_feedback: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("last_feedback", JSON, nullable=True),
    )


class Task(SQLModel, table=True):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    query_id: int
    queue_name: str
    subquery: str
    task_name: str
    message: bytes
    labels: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("labels", JSON, nullable=True),
    )
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
    latest_try: datetime = Field(default=None, nullable=True)
    finished_at: datetime = Field(default=None, nullable=True)
