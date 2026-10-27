import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, TypeDecorator, text
from sqlalchemy.engine import Dialect
from sqlmodel import Field, SQLModel, UniqueConstraint
from sqlmodel.main import SQLModelConfig as ConfigDict
from taskiq.depends.progress_tracker import TaskState

from academic_apis.utils.types import AzureAccessLinks, Wrapper


class AzureAccessLinksType(TypeDecorator[AzureAccessLinks]):
    """Store AzureAccessLinks as JSON, restoring the model instance on load."""

    impl = JSON
    cache_ok = True

    def process_bind_param(
        self, value: AzureAccessLinks | dict[str, Any] | None, _dialect: Dialect
    ) -> dict[str, Any] | None:
        if isinstance(value, AzureAccessLinks):
            return value.model_dump()
        return value

    def process_result_value(
        self, value: dict[str, Any] | None, _dialect: Dialect
    ) -> AzureAccessLinks | None:
        if value is None:
            return None
        return AzureAccessLinks.model_validate(value)


class QueryInfo(SQLModel):
    query: str
    wrapper: Wrapper
    params: dict[str, Any] | None = Field(
        default_factory=dict,
        sa_column=Column("params", JSON, nullable=True),
    )


class Query(QueryInfo, table=True):
    # AUTOINCREMENT keeps SQLite from reusing the ROWID of a deleted row
    __table_args__ = {"sqlite_autoincrement": True}
    query_id: int | None = Field(default=None, primary_key=True)

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    is_public: bool = False
    first_result: datetime | None = Field(default=None, nullable=True)
    latest_result: datetime | None = Field(default=None, nullable=True)
    azure_link: AzureAccessLinks | None = Field(
        default=None,
        sa_column=Column("azure_link", AzureAccessLinksType(), nullable=True),
    )
    azure_progress: int | None = Field(default=None, nullable=True)
    transfer_link: str | None = Field(
        default=None,
        nullable=True,
        description="azure link with longer-lasting SAS token so other services can consume this",
    )


class AccessTokenPublic(SQLModel):
    model_config = ConfigDict(validate_default=True)
    token_id: str = Field(default_factory=lambda: uuid.uuid6().hex, primary_key=True)
    wrapper: Wrapper
    proxy: str | None = None
    is_active: bool = True
    comment: str | None = Field(default=None, nullable=False)
    last_used: datetime | None = Field(default=None, nullable=True)
    locked_until: datetime | None = Field(
        default=None,
        nullable=True,
    )  # if available, contains API feedback for when budget is reset
    last_feedback: dict[str, Any] | None = Field(
        default_factory=dict,
        sa_column=Column("last_feedback", JSON, nullable=True),
    )


class AccessToken(AccessTokenPublic, table=True):
    __tablename__ = "access_token"
    token: str


class TaskPublic(SQLModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid6().hex, primary_key=True)
    query_id: int
    subquery: str
    num_retries: int = 0
    status: TaskState | None = None
    progress: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("progress", JSON, nullable=False),
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
        sa_column=Column("labels", JSON, nullable=False),
    )


class UserPublicBase(SQLModel):
    user_id: str = Field(default_factory=lambda: uuid.uuid6().hex, primary_key=True)
    full_name: str
    institution: str


class UserPublic(UserPublicBase):
    email: str
    is_active: bool = False
    permission_admin: bool = False
    permission_submit_query: bool = False


class UserCreate(UserPublic):
    password_plain: str


class User(UserPublic, table=True):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="users_constraint_unique_email"),)
    password_hashed: str


class UserTokenPermission(SQLModel, table=True):
    __tablename__ = "m2m_user_token"
    user_id: str = Field(foreign_key="users.user_id", primary_key=True)
    token_id: str = Field(foreign_key="access_token.token_id", primary_key=True)
    is_owner: bool = False


class UserQueryPermission(SQLModel, table=True):
    __tablename__ = "m2m_user_query"
    user_id: str = Field(foreign_key="users.user_id", primary_key=True)
    query_id: int = Field(foreign_key="query.query_id", primary_key=True)
    is_owner: bool = False
