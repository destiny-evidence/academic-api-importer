"""
Shared fixtures for the API tests.

The application reads its configuration once, at import time, and derives the sqlite
database and file-lock paths from it. The test environment therefore has to be set up
*before* any application module is imported, which is why the imports below are not at
the top of this file.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

# Keep the tests away from `.config/secret.env` and the real `data/oracle.db`: a throwaway
# data root, its own database file and a secret key long enough for HS256 (the default is
# shorter than what `jose` accepts).
_data_root = Path(tempfile.mkdtemp(prefix="academic-apis-tests-"))
os.environ["AAI_CONF_FILE"] = os.devnull
os.environ["AAI_DATA_ROOT"] = str(_data_root)
os.environ["AAI_DB_FILE"] = str(_data_root / "academic_apis_tests.db")
os.environ["AAI_SECRET_KEY"] = "test-secret-key-long-enough-for-hs256"
# The Azure storage settings are required, but the tests never talk to Azure.
os.environ["AAI_AZURE_STORAGE_CONNECTION_STRING"] = (
    "DefaultEndpointsProtocol=https;AccountName=fake;AccountKey=fake;EndpointSuffix=core.windows.net"
)
os.environ["AAI_AZURE_STORAGE_ACCOUNT_NAME"] = "fake"
os.environ["AAI_AZURE_STORAGE_ACCOUNT_KEY"] = "fake"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel, select  # noqa: E402

from academic_apis.api import api  # noqa: E402
from academic_apis.api.utils.auth import hash_password  # noqa: E402
from academic_apis.db import (  # noqa: E402
    AccessToken,
    Mode,
    User,
    db_session,
    make_engine,
)
from academic_apis.db.schemas import UserTokenPermission  # noqa: E402
from academic_apis.utils.types import Wrapper  # noqa: E402

DEFAULT_PASSWORD = "not-so-secret-password"


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """Recreate all tables for every test, so tests cannot see each other's data."""
    engine = make_engine()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


@pytest.fixture
def client() -> TestClient:
    """
    Client for the API.

    Deliberately used without its context manager: that keeps the lifespan handler
    from running, which would start up the taskiq broker. With
    ``raise_server_exceptions=False`` the endpoints that currently break show up as
    500 responses instead of raising inside the test.
    """
    return TestClient(api, raise_server_exceptions=False)


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run one of the async database helpers from a synchronous fixture."""
    return asyncio.run(coroutine)


@pytest.fixture
def make_user() -> Callable[..., User]:
    """Factory that stores a :class:`~academic_apis.db.User` and returns it."""

    def factory(
        *,
        email: str | None = None,
        permission_admin: bool = False,
        permission_submit_query: bool = False,
        is_active: bool = True,
        full_name: str = "Test User",
        institution: str = "Test Institution",
        password: str = DEFAULT_PASSWORD,
    ) -> User:
        async def create() -> User:
            async with db_session(mode=Mode.WRITE) as session:
                user = User(
                    email=email or f"user-{uuid4().hex[:8]}@example.org",
                    full_name=full_name,
                    institution=institution,
                    is_active=is_active,
                    permission_admin=permission_admin,
                    permission_submit_query=permission_submit_query,
                    password_hashed=hash_password(password),
                )
                session.add(user)
                session.commit()
                # Detach with loaded attributes: otherwise they expire on commit and
                # cannot be read after the session is closed.
                session.refresh(user)
                session.expunge(user)
            return user

        return _run(create())

    return factory


@pytest.fixture
def make_token() -> Callable[..., str]:
    """
    Factory that stores an :class:`~academic_apis.db.AccessToken` and returns its ID.

    ``owner`` makes that user the owner of the token, ``users`` takes ``(user,
    is_owner)`` pairs; both create ``UserTokenPermission`` rows. ``token_id`` allows
    storing a token with a predictable ID.
    """

    def factory(
        *,
        wrapper: Wrapper = Wrapper.SCOPUS,
        token: str = "api-key-value",
        comment: str | None = "test token",
        is_active: bool = True,
        proxy: str | None = None,
        owner: User | None = None,
        users: Sequence[tuple[User, bool]] = (),
        token_id: str | None = None,
    ) -> str:
        async def create() -> str:
            async with db_session(mode=Mode.WRITE) as session:
                new_token = AccessToken(
                    wrapper=wrapper,
                    token=token,
                    comment=comment,
                    is_active=is_active,
                    proxy=proxy,
                )
                if token_id is not None:
                    new_token.token_id = token_id
                session.add(new_token)
                session.flush()
                grants: list[tuple[User, bool]] = (
                    [(owner, True)] if owner is not None else []
                ) + list(users)
                for user, is_owner in grants:
                    session.add(
                        UserTokenPermission(
                            user_id=user.user_id,
                            token_id=new_token.token_id,
                            is_owner=is_owner,
                        ),
                    )
                session.commit()
                session.refresh(new_token)
                return str(new_token.token_id)

        return _run(create())

    return factory


@pytest.fixture
def stored_token() -> Callable[[str], dict[str, Any]]:
    """Read a token straight from the database (an empty dict if it is gone)."""

    def factory(token_id: str) -> dict[str, Any]:
        async def read() -> dict[str, Any]:
            async with db_session() as session:
                token = session.get(AccessToken, token_id)
                if token is None:
                    return {}
                return {
                    "token_id": token.token_id,
                    "wrapper": token.wrapper,
                    "token": token.token,
                    "comment": token.comment,
                    "proxy": token.proxy,
                    "is_active": token.is_active,
                }

        return _run(read())

    return factory


@pytest.fixture
def stored_permissions() -> Callable[..., list[tuple[str, str, bool]]]:
    """Read ``UserTokenPermission`` rows, optionally filtered by user and/or token."""

    def factory(
        *, user_id: str | None = None, token_id: str | None = None
    ) -> list[tuple[str, str, bool]]:
        async def read() -> list[tuple[str, str, bool]]:
            async with db_session() as session:
                stmt = select(UserTokenPermission)
                if user_id is not None:
                    stmt = stmt.where(UserTokenPermission.user_id == user_id)
                if token_id is not None:
                    stmt = stmt.where(UserTokenPermission.token_id == token_id)
                return [
                    (permission.user_id, permission.token_id, permission.is_owner)
                    for permission in session.exec(stmt).all()
                ]

        return _run(read())

    return factory


# Users that are used by most tests. They only exist to keep the tests short; tests
# that need a different constellation use `make_user` and `make_token` directly.


@pytest.fixture
def admin_user(make_user: Callable[..., User]) -> User:
    return make_user(
        email="admin@example.org",
        full_name="Ada Admin",
        permission_admin=True,
        permission_submit_query=True,
    )


@pytest.fixture
def normal_user(make_user: Callable[..., User]) -> User:
    return make_user(email="normal@example.org", full_name="Norman User")


@pytest.fixture
def other_user(make_user: Callable[..., User]) -> User:
    return make_user(email="other@example.org", full_name="Olivia Other")


@pytest.fixture
def inactive_user(make_user: Callable[..., User]) -> User:
    return make_user(
        email="inactive@example.org",
        full_name="Ivan Inactive",
        is_active=False,
        permission_admin=True,
    )


@pytest.fixture
def owned_token(make_token: Callable[..., str], normal_user: User) -> str:
    """A token that `normal_user` owns; the only token in such tests."""
    return make_token(owner=normal_user, comment="token owned by normal_user")
