"""
Tests for the ``/tasks`` endpoints of ``academic_apis.api.progress``.

``GET /tasks/`` lists individual tasks, ``GET /tasks/summary`` aggregates the tasks
of one query per status.

The rules covered here:

=======================  ==========  =====================  ==================================
                       anonymous    user without query     permitted user / admin
                                     permission
=======================  ==========  =====================  ==================================
``GET /tasks/``          401         403                    admins: all tasks / others: 500
``GET /tasks/summary``   401         403                    aggregates of that query
=======================  ==========  =====================  ==================================

Admins bypass the permission check and may list tasks across all queries. Everyone
else needs a ``UserQueryPermission`` row for the query they name (ownership is *not*
required, but omitting ``query_id`` is admin-only). Non-admins pass the permission
check but then hit a broken query - see the ``KNOWN BUG`` test below.

Endpoints that behave differently from what their docstrings promise are covered by
tests marked ``KNOWN BUG``; those assert today's actual responses and have to be
updated together with the endpoints.

GET /tasks/ (list_tasks)
- Authentication: anonymous/inactive/malformed-JWT/ghost-user (both endpoints)
- Query permissions: admins bypass the check; non-admins need a UserQueryPermission row (ownership not required); listing across all queries is admin-only; unknown query IDs behave per role
- Filtering: query_id, status (all four TaskState values, combinations, invalid values → 422)
- Paging: default limit of 5, limit/offset pages, offset past the end
- Ordering: coalesce(latest_try, created_at) ascending (a late-retried task sorts correctly)
- The substr(..., -50) subquery trimming (short / exactly-50 / longer-keeps-the-tail)
- Only public TaskPublic fields leak — task_name, queue_name, message, labels stay hidden; full-record comparison including the mixed naive/aware datetime serialization (latest_try comes back with a Z suffix, created_at without)
GET /tasks/summary (task_summary)
- query_id required (422), permission behavior, empty result for task-less queries
- Grouping by status including the NULL-status group; isolation between queries
- The coalesce(cast(progress->>...)) aggregation: missing/null/{} progress → 0, numeric strings counted, floats truncated (2.9 → 2), garbage ("abc") → 0 — all verified against actual SQLite behavior before asserting
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from fastapi.testclient import TestClient
from taskiq.depends.progress_tracker import TaskState

from academic_apis.api.progress import SUBQUERY_LENGTH
from academic_apis.api.utils.auth import create_access_token
from academic_apis.db import Mode, User, db_session
from academic_apis.db.schemas import Query, Task, UserQueryPermission
from academic_apis.utils import settings
from academic_apis.utils.types import Wrapper

if TYPE_CHECKING:
    from collections.abc import Callable

    from httpx2 import Response  # the project pins `httpx2`, which `TestClient` returns

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

#: ``(id, path)`` pairs of both endpoints, used by the authentication tests.
ENDPOINTS: dict[str, str] = {
    "list": "/tasks/",
    "summary": "/tasks/summary?query_id=1",
}

ALL_ENDPOINTS = pytest.mark.parametrize("path", ENDPOINTS.values(), ids=ENDPOINTS)

#: A query ID that no fixture creates.
UNKNOWN_QUERY_ID = 999

NO_PERMISSION = (
    "Not enough permissions: Only admins, owners, or privileged users "
    "have permission to view or edit."
)


def auth_header(user: User) -> dict[str, str]:
    """Bearer header that logs the request in as ``user``."""
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def ghost_auth_header() -> dict[str, str]:
    """Header signed with the right secret for a user missing from the database."""
    token = jwt.encode(
        {"sub": "ghost-user", "email": "ghost@example.org", "permission_admin": True},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


def ts(day: int, hour: int = 0) -> datetime:
    """
    Deterministic timestamp in January 2026.

    Aware on purpose: the ``latest_try`` / ``finished_at`` columns refuse naive values.
    """
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def created_iso(day: int, hour: int = 0) -> str:
    """How ``created_at`` comes back from the API (the column drops the timezone)."""
    return ts(day, hour).strftime("%Y-%m-%dT%H:%M:%S")


def utc_iso(day: int, hour: int = 0) -> str:
    """How ``latest_try`` / ``finished_at`` come back from the API (UTC, ``Z`` suffix)."""
    return ts(day, hour).isoformat().replace("+00:00", "Z")


def listed_ids(response: Response) -> list[str]:
    """Task IDs returned by ``GET /tasks/``, in listing order."""
    return [entry["task_id"] for entry in response.json()]


def summary_rows(response: Response) -> dict[str | None, dict[str, Any]]:
    """Rows of ``GET /tasks/summary``, keyed by their (possibly ``None``) status."""
    return {entry["status"]: entry for entry in response.json()}


# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def make_query() -> Callable[..., Query]:
    """Factory that stores a :class:`~academic_apis.db.schemas.Query` and returns it."""

    def factory(
        *,
        query: str = "test query",
        wrapper: Wrapper = Wrapper.SCOPUS,
        params: dict[str, Any] | None = None,
    ) -> Query:
        async def create() -> Query:
            async with db_session(mode=Mode.WRITE) as session:
                new_query = Query(query=query, wrapper=wrapper, params=params or {})
                session.add(new_query)
                session.commit()
                # Detach with loaded attributes so `query_id` survives the session.
                session.refresh(new_query)
                session.expunge(new_query)
            return new_query

        return asyncio.run(create())

    return factory


@pytest.fixture
def make_task() -> Callable[..., str]:
    """Factory that stores a :class:`~academic_apis.db.schemas.Task`; returns its ID."""

    def factory(
        query_id: int,
        *,
        task_id: str | None = None,
        status: str | None = None,
        subquery: str = "sub",
        progress: dict[str, Any] | None = None,
        num_retries: int = 0,
        created_at: datetime | None = None,
        latest_try: datetime | None = None,
        finished_at: datetime | None = None,
        execution_time: float | None = None,
    ) -> str:
        async def create() -> str:
            async with db_session(mode=Mode.WRITE) as session:
                task = Task(
                    task_name="run_query",
                    queue_name="queries",
                    message=b"serialized-payload",
                    labels={"hidden": "internal"},
                    query_id=query_id,
                    subquery=subquery,
                    status=TaskState(status) if isinstance(status, str) else status,
                    progress=progress or {},
                    num_retries=num_retries,
                    created_at=created_at or ts(1),
                    latest_try=latest_try,
                    finished_at=finished_at,
                    execution_time=execution_time,
                )
                if task_id is not None:
                    task.task_id = task_id
                session.add(task)
                session.flush()
                new_id = str(task.task_id)
                session.commit()
            return new_id

        return asyncio.run(create())

    return factory


@pytest.fixture
def grant_query_permission() -> Callable[..., None]:
    """Store a ``UserQueryPermission`` row linking a user and a query."""

    def factory(user: User, query_id: int, is_owner: bool = False) -> None:
        async def create() -> None:
            async with db_session(mode=Mode.WRITE) as session:
                session.add(
                    UserQueryPermission(
                        user_id=user.user_id,
                        query_id=query_id,
                        is_owner=is_owner,
                    )
                )
                session.commit()

        asyncio.run(create())

    return factory


@pytest.fixture
def query(make_query: Callable[..., Query]) -> Query:
    """The query most tests attach their tasks to."""
    return make_query()


# --------------------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------------------


class TestAuthentication:
    """All endpoints require a valid login, no matter the permission level."""

    @ALL_ENDPOINTS
    def test_anonymous_requests_are_rejected(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"
        assert response.headers["www-authenticate"] == "Bearer"

    @ALL_ENDPOINTS
    def test_inactive_users_are_rejected(
        self, client: TestClient, path: str, inactive_user: User
    ) -> None:
        response = client.get(path, headers=auth_header(inactive_user))
        assert response.status_code == 403
        assert response.json()["detail"] == "Inactive user"

    @pytest.mark.parametrize(
        "header",
        [
            pytest.param({"Authorization": "Bearer"}, id="bearer-without-credentials"),
            pytest.param({"Authorization": "Bearer not-a-jwt"}, id="malformed-jwt"),
            pytest.param({"Authorization": "Basic Zm9vOmJhcg=="}, id="wrong-scheme"),
            pytest.param({"Authorization": ""}, id="empty-header"),
        ],
    )
    @ALL_ENDPOINTS
    def test_invalid_credentials_are_rejected(
        self, client: TestClient, path: str, header: dict[str, str]
    ) -> None:
        assert client.get(path, headers=header).status_code == 401

    @ALL_ENDPOINTS
    def test_well_signed_token_of_unknown_user_is_rejected(
        self, client: TestClient, path: str
    ) -> None:
        # Correctly signed, but this user does not exist in the database.
        response = client.get(path, headers=ghost_auth_header())
        assert response.status_code == 401
        assert response.json()["detail"] == "User not found"


# --------------------------------------------------------------------------------------
# permissions shared by both endpoints
# --------------------------------------------------------------------------------------


class TestQueryPermissions:
    """Who may look at tasks: admins always, everybody else only with a permission row."""

    def test_non_admin_may_not_list_across_queries(
        self, client: TestClient, query: Query, normal_user: User
    ) -> None:
        response = client.get("/tasks/", headers=auth_header(normal_user))
        assert response.status_code == 403
        assert response.json()["detail"] == NO_PERMISSION

    def test_user_without_permission_cannot_list(
        self, client: TestClient, query: Query, other_user: User
    ) -> None:
        response = client.get(
            f"/tasks/?query_id={query.query_id}", headers=auth_header(other_user)
        )
        assert response.status_code == 403
        assert response.json()["detail"] == NO_PERMISSION

    def test_user_without_permission_cannot_get_the_summary(
        self, client: TestClient, query: Query, other_user: User
    ) -> None:
        response = client.get(
            f"/tasks/summary?query_id={query.query_id}", headers=auth_header(other_user)
        )
        assert response.status_code == 403
        assert response.json()["detail"] == NO_PERMISSION

    def test_unknown_query_is_forbidden_for_non_admins(
        self, client: TestClient, normal_user: User
    ) -> None:
        # No permission row can exist for a query that does not exist.
        for path in (
            f"/tasks/?query_id={UNKNOWN_QUERY_ID}",
            f"/tasks/summary?query_id={UNKNOWN_QUERY_ID}",
        ):
            assert client.get(path, headers=auth_header(normal_user)).status_code == 403

    def test_summary_requires_ownership_not_at_all(
        self,
        client: TestClient,
        query: Query,
        normal_user: User,
        grant_query_permission: Callable[..., None],
    ) -> None:
        # A plain member (is_owner=False) passes `require_ownership=False`.
        grant_query_permission(normal_user, query.query_id, is_owner=False)
        response = client.get(
            f"/tasks/summary?query_id={query.query_id}",
            headers=auth_header(normal_user),
        )
        assert response.status_code == 200

    def test_owner_may_get_the_summary(
        self,
        client: TestClient,
        query: Query,
        normal_user: User,
        grant_query_permission: Callable[..., None],
    ) -> None:
        grant_query_permission(normal_user, query.query_id, is_owner=True)
        response = client.get(
            f"/tasks/summary?query_id={query.query_id}",
            headers=auth_header(normal_user),
        )
        assert response.status_code == 200

    def test_admin_may_list_any_query_without_a_permission_row(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        make_task(query.query_id, task_id="hidden")
        response = client.get(
            f"/tasks/?query_id={query.query_id}", headers=auth_header(admin_user)
        )
        assert listed_ids(response) == ["hidden"]

    def test_admin_may_summarize_any_query_without_a_permission_row(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        make_task(query.query_id, status="SUCCESS")
        response = client.get(
            f"/tasks/summary?query_id={query.query_id}", headers=auth_header(admin_user)
        )
        assert summary_rows(response)["SUCCESS"]["n_tasks"] == 1

    def test_admin_gets_an_empty_summary_for_an_unknown_query(
        self, client: TestClient, admin_user: User
    ) -> None:
        # The permission check only asks for admins, so unknown IDs answer 200 ...
        response = client.get(
            f"/tasks/summary?query_id={UNKNOWN_QUERY_ID}",
            headers=auth_header(admin_user),
        )
        assert response.status_code == 200
        assert response.json() == []


# --------------------------------------------------------------------------------------
# GET /tasks/
# --------------------------------------------------------------------------------------


class TestListTasks:
    """``GET /tasks/`` - listing, filtering, paging and trimming (as an admin)."""

    @pytest.fixture
    def two_queries(self, make_query: Callable[..., Query]) -> tuple[Query, Query]:
        return make_query(query="first"), make_query(query="second")

    def test_admin_sees_the_tasks_of_all_queries(
        self,
        client: TestClient,
        admin_user: User,
        two_queries: tuple[Query, Query],
        make_task: Callable[..., str],
    ) -> None:
        first, second = two_queries
        make_task(first.query_id, task_id="a1", created_at=ts(1))
        make_task(second.query_id, task_id="b1", created_at=ts(2))
        make_task(first.query_id, task_id="a2", created_at=ts(3))
        assert sorted(
            listed_ids(client.get("/tasks/", headers=auth_header(admin_user)))
        ) == ["a1", "a2", "b1"]

    def test_admin_can_filter_by_query_id(
        self,
        client: TestClient,
        admin_user: User,
        two_queries: tuple[Query, Query],
        make_task: Callable[..., str],
    ) -> None:
        first, second = two_queries
        make_task(first.query_id, task_id="a1")
        make_task(second.query_id, task_id="b1")
        response = client.get(
            f"/tasks/?query_id={first.query_id}", headers=auth_header(admin_user)
        )
        assert listed_ids(response) == ["a1"]

    def test_admin_gets_an_empty_list_for_an_unknown_query(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get(
            f"/tasks/?query_id={UNKNOWN_QUERY_ID}", headers=auth_header(admin_user)
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_at_most_five_tasks_are_returned_by_default(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        for day in range(1, 8):  # seven tasks
            make_task(query.query_id, task_id=f"t{day}", created_at=ts(day))
        assert listed_ids(client.get("/tasks/", headers=auth_header(admin_user))) == [
            "t1",
            "t2",
            "t3",
            "t4",
            "t5",
        ]

    def test_limit_and_offset_page_through_the_tasks(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        for day in range(1, 8):
            make_task(query.query_id, task_id=f"t{day}", created_at=ts(day))
        base = f"/tasks/?query_id={query.query_id}"
        assert listed_ids(
            client.get(f"{base}&limit=3&offset=0", headers=auth_header(admin_user))
        ) == ["t1", "t2", "t3"]
        assert listed_ids(
            client.get(f"{base}&limit=3&offset=3", headers=auth_header(admin_user))
        ) == ["t4", "t5", "t6"]
        assert listed_ids(
            client.get(f"{base}&limit=3&offset=6", headers=auth_header(admin_user))
        ) == ["t7"]

    def test_an_offset_past_the_end_returns_nothing(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        make_task(query.query_id, created_at=ts(1))
        response = client.get("/tasks/?offset=10", headers=auth_header(admin_user))
        assert response.json() == []

    @pytest.mark.parametrize("status", ["STARTED", "RETRY", "SUCCESS", "FAILURE"])
    def test_only_tasks_with_the_requested_status_are_returned(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
        status: str,
    ) -> None:
        for day, state in enumerate(
            ["STARTED", "RETRY", "SUCCESS", "FAILURE", None], start=1
        ):
            make_task(
                query.query_id,
                task_id=(state or "none").lower(),
                status=state,
                created_at=ts(day),
            )
        response = client.get(
            f"/tasks/?query_id={query.query_id}&status={status}",
            headers=auth_header(admin_user),
        )
        assert listed_ids(response) == [status.lower()]

    def test_status_filter_combines_with_the_query_filter(
        self,
        client: TestClient,
        admin_user: User,
        two_queries: tuple[Query, Query],
        make_task: Callable[..., str],
    ) -> None:
        first, second = two_queries
        make_task(first.query_id, task_id="first-success", status="SUCCESS")
        make_task(first.query_id, task_id="first-started", status="STARTED")
        make_task(second.query_id, task_id="second-success", status="SUCCESS")
        response = client.get(
            f"/tasks/?query_id={first.query_id}&status=SUCCESS",
            headers=auth_header(admin_user),
        )
        assert listed_ids(response) == ["first-success"]

    @pytest.mark.parametrize(
        "status", ["BOGUS", "success"], ids=["unknown", "lowercase"]
    )
    def test_an_unknown_status_is_unprocessable(
        self, client: TestClient, admin_user: User, status: str
    ) -> None:
        response = client.get(
            f"/tasks/?status={status}", headers=auth_header(admin_user)
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "enum"

    def test_a_non_numeric_query_id_is_unprocessable(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get(
            "/tasks/?query_id=not-a-number", headers=auth_header(admin_user)
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["query", "query_id"]

    @pytest.mark.parametrize(
        ("subquery", "expected"),
        [
            pytest.param("short", "short", id="shorter-than-50"),
            pytest.param("x" * 50, "x" * 50, id="exactly-50"),
            pytest.param(
                "head-" + "m" * 55 + "-tail",
                ("head-" + "m" * 55 + "-tail")[-50:],
                id="longer-keeps-the-tail",
            ),
        ],
    )
    def test_the_subquery_is_trimmed_to_its_last_characters(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
        subquery: str,
        expected: str,
    ) -> None:
        make_task(query.query_id, subquery=subquery)
        (listed,) = client.get(
            f"/tasks/?query_id={query.query_id}", headers=auth_header(admin_user)
        ).json()
        assert listed["subquery"] == expected
        assert len(expected) <= SUBQUERY_LENGTH
        if len(subquery) > SUBQUERY_LENGTH:  # the *last* characters are kept
            assert expected.endswith("-tail")
            assert not expected.startswith("head-")

    def test_tasks_are_ordered_by_latest_try_then_created_at(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        # Sort key is `coalesce(latest_try, created_at)`, ascending: `late` tried on
        # the 3rd and comes first even though it was created on the 1st.
        make_task(query.query_id, task_id="plain", created_at=ts(5))
        make_task(query.query_id, task_id="late", created_at=ts(1), latest_try=ts(3))
        make_task(query.query_id, task_id="newest", created_at=ts(10))
        assert listed_ids(client.get("/tasks/", headers=auth_header(admin_user))) == [
            "late",
            "plain",
            "newest",
        ]

    def test_only_public_fields_are_returned(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        make_task(query.query_id)
        (listed,) = client.get(
            f"/tasks/?query_id={query.query_id}", headers=auth_header(admin_user)
        ).json()
        assert set(listed) == {
            "task_id",
            "status",
            "query_id",
            "subquery",
            "num_retries",
            "created_at",
            "latest_try",
            "finished_at",
            "progress",
            "execution_time",
        }
        # The internal task fields stay in the database.
        for hidden in ("task_name", "queue_name", "message", "labels", "hidden"):
            assert hidden not in str(listed)

    def test_a_finished_task_is_returned_in_full(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        make_task(
            query.query_id,
            task_id="t1",
            status="SUCCESS",
            subquery="SELECT something",
            num_retries=2,
            progress={"n_results": 10, "n_retrieved": 42},
            execution_time=1.5,
            created_at=ts(2),
            latest_try=ts(3),
            finished_at=ts(4),
        )
        (listed,) = client.get(
            f"/tasks/?query_id={query.query_id}", headers=auth_header(admin_user)
        ).json()
        assert listed == {
            "task_id": "t1",
            "query_id": query.query_id,
            "subquery": "SELECT something",
            "num_retries": 2,
            "status": "SUCCESS",
            "progress": {"n_results": 10, "n_retrieved": 42},
            "execution_time": 1.5,
            "created_at": created_iso(2),
            "latest_try": utc_iso(3),
            "finished_at": utc_iso(4),
        }

    def test_listing_as_a_permitted_non_admin_fails(
        self,
        client: TestClient,
        query: Query,
        normal_user: User,
        grant_query_permission: Callable[..., None],
        make_task: Callable[..., str],
    ) -> None:
        """
        KNOWN BUG: the query that restricts tasks to permitted queries cannot be built.

        The ``select`` lists plain columns, so SQLAlchemy does not know how to join
        ``Query`` onto it (``InvalidRequestError``). Users without permission still
        get their 403, but every permitted non-admin request answers 500 - listing
        works for admins only.
        """
        grant_query_permission(normal_user, query.query_id, is_owner=True)
        make_task(query.query_id)
        response = client.get(
            f"/tasks/?query_id={query.query_id}", headers=auth_header(normal_user)
        )
        assert response.status_code == 500


# --------------------------------------------------------------------------------------
# GET /tasks/summary
# --------------------------------------------------------------------------------------


class TestTaskSummary:
    """``GET /tasks/summary`` - counting tasks and summing up their progress."""

    def test_the_query_id_has_to_be_specified(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get("/tasks/summary", headers=auth_header(admin_user))
        assert response.status_code == 422
        detail = response.json()["detail"][0]
        assert detail["loc"] == ["query", "query_id"]
        assert detail["type"] == "missing"

    def test_a_query_without_tasks_has_no_rows(
        self, client: TestClient, admin_user: User, query: Query
    ) -> None:
        response = client.get(
            f"/tasks/summary?query_id={query.query_id}", headers=auth_header(admin_user)
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_tasks_are_grouped_by_status(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        make_task(
            query.query_id,
            status="SUCCESS",
            progress={"n_results": 10, "n_retrieved": 42},
        )
        make_task(
            query.query_id,
            status="SUCCESS",
            progress={"n_results": 5, "n_retrieved": 8},
        )
        make_task(query.query_id, status="STARTED", progress={"n_results": 3})
        make_task(query.query_id)  # status None

        rows = summary_rows(
            client.get(
                f"/tasks/summary?query_id={query.query_id}",
                headers=auth_header(admin_user),
            )
        )
        assert rows == {
            None: {"status": None, "n_tasks": 1, "n_results": 0, "n_retrieved": 0},
            "STARTED": {
                "status": "STARTED",
                "n_tasks": 1,
                "n_results": 3,
                "n_retrieved": 0,
            },
            "SUCCESS": {
                "status": "SUCCESS",
                "n_tasks": 2,
                "n_results": 15,
                "n_retrieved": 50,
            },
        }

    def test_tasks_of_other_queries_are_not_counted(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_query: Callable[..., Query],
        make_task: Callable[..., str],
    ) -> None:
        foreign = make_query(query="foreign")
        make_task(query.query_id, status="SUCCESS", progress={"n_results": 1})
        make_task(foreign.query_id, status="SUCCESS", progress={"n_results": 1000})
        rows = summary_rows(
            client.get(
                f"/tasks/summary?query_id={query.query_id}",
                headers=auth_header(admin_user),
            )
        )
        assert rows["SUCCESS"]["n_tasks"] == 1
        assert rows["SUCCESS"]["n_results"] == 1

    def test_missing_progress_values_count_as_zero(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        # Empty, absent and null progress values all contribute zeros instead of null.
        make_task(query.query_id, status="SUCCESS")
        make_task(query.query_id, status="SUCCESS", progress={"n_results": None})
        rows = summary_rows(
            client.get(
                f"/tasks/summary?query_id={query.query_id}",
                headers=auth_header(admin_user),
            )
        )
        assert rows["SUCCESS"] == {
            "status": "SUCCESS",
            "n_tasks": 2,
            "n_results": 0,
            "n_retrieved": 0,
        }

    def test_numeric_strings_and_floats_in_progress_are_cast(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        make_task: Callable[..., str],
    ) -> None:
        # The JSON values are read as strings and CAST to integers: "7" counts,
        # floats are truncated and non-numeric text counts as zero.
        make_task(
            query.query_id,
            status="SUCCESS",
            progress={"n_results": "7", "n_retrieved": "abc"},
        )
        make_task(query.query_id, status="SUCCESS", progress={"n_results": 2.9})
        rows = summary_rows(
            client.get(
                f"/tasks/summary?query_id={query.query_id}",
                headers=auth_header(admin_user),
            )
        )
        assert rows["SUCCESS"] == {
            "status": "SUCCESS",
            "n_tasks": 2,
            "n_results": 9,
            "n_retrieved": 0,
        }

    def test_a_member_sees_the_same_numbers_as_an_admin(
        self,
        client: TestClient,
        admin_user: User,
        query: Query,
        normal_user: User,
        grant_query_permission: Callable[..., None],
        make_task: Callable[..., str],
    ) -> None:
        make_task(
            query.query_id,
            status="SUCCESS",
            progress={"n_results": 4, "n_retrieved": 9},
        )
        grant_query_permission(normal_user, query.query_id, is_owner=False)
        admin_body = client.get(
            f"/tasks/summary?query_id={query.query_id}",
            headers=auth_header(admin_user),
        ).json()
        member_body = client.get(
            f"/tasks/summary?query_id={query.query_id}",
            headers=auth_header(normal_user),
        ).json()
        assert (
            member_body
            == admin_body
            == [{"status": "SUCCESS", "n_tasks": 1, "n_results": 4, "n_retrieved": 9}]
        )
