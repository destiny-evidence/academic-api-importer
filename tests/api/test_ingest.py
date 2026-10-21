"""
Tests for the ``/ingest`` endpoints of ``academic_apis.api.ingest``.

The endpoints serve the bulk-abstract-ingest robot: ``PUT /ingest/transfer``
registers a query's results for the transfer into the ingestion queue and hands
out a long-lived Azure link, ``PUT /ingest/queueing`` and ``PUT /ingest/queued``
report the progress of that transfer, and ``GET /ingest/`` lists what is tracked.

The rules covered here:

==========================  =======================  ==========================
                          robot endpoints          ``PUT /ingest/transfer``
==========================  =======================  ==========================
without credentials         401                      401
logged-in user              401                      403 (plain) / 200 (admin)
robot token                 200                      401
==========================  =======================  ==========================

The three robot endpoints authenticate with a token from
``settings.ods_robot_access_tokens``, sent in the ``api-token`` header; neither
an inactive nor a plain user login opens them, and an unset or empty token list
shuts them down even when a token is offered. ``PUT /ingest/transfer`` is the
mirror image: it demands a logged-in admin and ignores the robot token.

Authentication
- the robot endpoints only accept a token from ``settings.ods_robot_access_tokens``
  in the ``api-token`` header: missing, empty or wrong tokens, and an unset or
  empty configuration all leave every one of them closed; a user login does not
  open them, and ``PUT /ingest/transfer`` conversely ignores the robot token
- ``PUT /ingest/transfer`` demands a logged-in admin (anonymous, malformed and
  ghost logins are 401, inactive admins and plain users 403); cookie logins and
  admins without the query-submit permission are accepted

GET /ingest/
- newest ``ingest_id`` first (uuid6 hex grows with time), full records, default
  page of five, limit/offset paging incl. ``limit=0`` and offsets past the end,
  and ``only_open`` keeping only not yet started ingests

PUT /ingest/queueing and PUT /ingest/queued
- the ingest a token names must exist (404) and the ``ingest_id`` is required
  (422); queueing a fresh ingest flips both flags and is idempotent afterwards,
  rewinds a stale ``is_queued`` and leaves finished ingests alone
- marking queued before queueing is refused (400) and leaves the row untouched;
  doing it in the right order flips ``is_queued`` and stays idempotent

PUT /ingest/transfer
- unknown queries are refused (404), a missing or non-numeric ``query_id`` is
  unprocessable (422), and a failing Azure call leaves the endpoint broken (500)
- a new ingest stores the ``responses_url`` of a fresh link, asked for with the
  robot's key lifetime and the container-path-prefixed blob path
- a query that is transferred again returns that stored ingest, without a new
  Azure call
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from academic_apis.api.utils.auth import create_access_token
from academic_apis.db import Mode, User, db_session
from academic_apis.db.schemas import SearchIngest
from academic_apis.utils import settings
from academic_apis.utils.types import AzureAccessLinks

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from httpx2 import Response  # the project pins `httpx2`, which `TestClient` returns

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

#: the token tests configure in ``settings.ods_robot_access_tokens``; ``Header()``
#: maps the ``api_token`` parameter to the ``api-token`` HTTP header
ROBOT_TOKEN = "ingest-robot-token"
SECOND_ROBOT_TOKEN = "second-ingest-robot-token"

#: an ingest ID that no fixture creates
UNKNOWN_INGEST_ID = "no-such-ingest"

#: a query ID that no fixture creates (query IDs start at one)
UNKNOWN_QUERY_ID = 999

NO_ROBOT_AUTH = "Not authenticated"
NO_ADMIN_AUTH = "Insufficient privileges: admin access required"

#: the five columns a ``SearchIngest`` is serialised with
INGEST_FIELDS = ("ingest_id", "query_id", "azure_link", "is_queueing", "is_queued")

#: the fields of :class:`~academic_apis.utils.types.AzureAccessLinks`
LINK_FIELDS = ("download_url", "list_url", "responses_url", "log_url", "errors_url")

#: ``(method, path, params)`` triples of the robot endpoints, used by the
#: authentication tests; the IDs never exist, but the token is checked first
ENDPOINTS: dict[str, tuple[str, str, dict[str, str]]] = {
    "list": ("GET", "/ingest/", {}),
    "queueing": ("PUT", "/ingest/queueing", {"ingest_id": UNKNOWN_INGEST_ID}),
    "queued": ("PUT", "/ingest/queued", {"ingest_id": UNKNOWN_INGEST_ID}),
}

ALL_ENDPOINTS = pytest.mark.parametrize("endpoint", ENDPOINTS.values(), ids=ENDPOINTS)


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run one of the async database helpers from a synchronous test."""
    return asyncio.run(coroutine)


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


def call(
    client: TestClient,
    endpoint: tuple[str, str, dict[str, str]],
    headers: dict[str, str] | None = None,
) -> Response:
    """Fire one of the ``ENDPOINTS`` entries."""
    method, path, params = endpoint
    return client.request(method, path, params=params or None, headers=headers)


def blob(query_id: int) -> str:
    """The Azure blob path the transfer endpoint builds for a query."""
    return f"{settings.azure_storage_container_path}{query_id}"


def link(blob_path: str, name: str, version: int = 1) -> str:
    """One of the links ``fake_azure`` hands out for a blob path."""
    return f"https://storage.example.org/{blob_path}/{name}?v={version}"


def ingested_ids(response: Response) -> list[str]:
    """Ingest IDs returned by ``GET /ingest/``, in listing order."""
    return [entry["ingest_id"] for entry in response.json()]


def as_record(
    ingest_id: str, query_id: int, is_queueing: bool, is_queued: bool
) -> dict[str, Any]:
    """Expected API record for a stored ingest with the default Azure link."""
    return {
        "ingest_id": ingest_id,
        "query_id": query_id,
        "azure_link": "https://storage.example.org/ingest/responses",
        "is_queueing": is_queueing,
        "is_queued": is_queued,
    }


# --------------------------------------------------------------------------------------
# stand-ins for the outside world
# --------------------------------------------------------------------------------------


@dataclass
class FakeAzure:
    """Records the calls ``PUT /ingest/transfer`` makes; hands out versioned links."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    version: int = 0

    def links_for(self, blob_path: str) -> AzureAccessLinks:
        """Fresh links for a blob path; every call hands out a new version."""
        self.version += 1
        return AzureAccessLinks(
            **{name: link(blob_path, name, self.version) for name in LINK_FIELDS},
        )


@pytest.fixture
def fake_azure(monkeypatch: pytest.MonkeyPatch) -> FakeAzure:
    """Replace the Azure helper that ``ingest.py`` imported at module level."""
    azure = FakeAzure()

    async def get_azure_links(
        blob_path: str, key_lifetime: int | None = None
    ) -> AzureAccessLinks:
        azure.calls.append({"blob_path": blob_path, "key_lifetime": key_lifetime})
        return azure.links_for(blob_path)

    monkeypatch.setattr("academic_apis.api.ingest.get_azure_links", get_azure_links)
    return azure


@pytest.fixture
def azure_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every Azure call fail, as a dead storage endpoint would."""

    async def get_azure_links(
        blob_path: str,  # noqa: ARG001
        key_lifetime: int | None = None,  # noqa: ARG001
    ) -> AzureAccessLinks:
        msg = "Azure is having a bad day"
        raise RuntimeError(msg)

    monkeypatch.setattr("academic_apis.api.ingest.get_azure_links", get_azure_links)


# --------------------------------------------------------------------------------------
# fixtures for the system under test
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_robot_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Robot access is off by default; tests opt in through ``enable_robots``."""
    monkeypatch.setattr(settings, "ods_robot_access_tokens", None)


@pytest.fixture
def enable_robots(monkeypatch: pytest.MonkeyPatch) -> Callable[..., dict[str, str]]:
    """
    Configure robot tokens and return the header that sends one of them.

    Called without arguments, the setting becomes an empty set, which is a
    different state than the unset default the autouse fixture leaves behind.
    """

    def factory(*tokens: str, send: str | None = None) -> dict[str, str]:
        monkeypatch.setattr(settings, "ods_robot_access_tokens", set(tokens))
        token = send if send is not None else (tokens[0] if tokens else ROBOT_TOKEN)
        return {"api-token": token}

    return factory


@pytest.fixture
def make_ingest() -> Callable[..., str]:
    """
    Factory that stores a :class:`~academic_apis.db.schemas.SearchIngest`.

    Rows are written straight to the database, as they are after a transfer has
    been committed; ``ingest_id`` keeps listing orders deterministic.
    """

    def factory(
        *,
        query_id: int,
        ingest_id: str | None = None,
        azure_link: str = "https://storage.example.org/ingest/responses",
        is_queueing: bool = False,
        is_queued: bool = False,
    ) -> str:
        async def create() -> str:
            async with db_session(mode=Mode.WRITE) as session:
                ingest = SearchIngest(
                    query_id=query_id,
                    azure_link=azure_link,
                    is_queueing=is_queueing,
                    is_queued=is_queued,
                )
                if ingest_id is not None:
                    ingest.ingest_id = ingest_id
                session.add(ingest)
                session.commit()
                return str(ingest.ingest_id)

        return _run(create())

    return factory


@pytest.fixture
def stored_ingests() -> Callable[[], list[dict[str, Any]]]:
    """All rows of ``search_ingest`` as plain records, ordered by ``ingest_id``."""

    def factory() -> list[dict[str, Any]]:
        async def read() -> list[dict[str, Any]]:
            async with db_session() as session:
                return [
                    {
                        "ingest_id": ingest.ingest_id,
                        "query_id": ingest.query_id,
                        "azure_link": ingest.azure_link,
                        "is_queueing": ingest.is_queueing,
                        "is_queued": ingest.is_queued,
                    }
                    for ingest in session.exec(
                        select(SearchIngest).order_by(SearchIngest.ingest_id),
                    ).all()
                ]

        return _run(read())

    return factory


@pytest.fixture
def stored_ingest(
    stored_ingests: Callable[[], list[dict[str, Any]]],
) -> Callable[[str], dict[str, Any]]:
    """Read one ingest straight from the database (an empty dict if it is gone)."""

    def factory(ingest_id: str) -> dict[str, Any]:
        for record in stored_ingests():
            if record["ingest_id"] == ingest_id:
                return record
        return {}

    return factory


# --------------------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------------------


class TestRobotAuthentication:
    """The three robot endpoints only open for a configured ``api-token``."""

    @ALL_ENDPOINTS
    def test_requests_without_the_header_are_rejected(
        self, client: TestClient, endpoint: tuple[str, str, dict[str, str]]
    ) -> None:
        response = call(client, endpoint)
        assert response.status_code == 401
        assert response.json()["detail"] == NO_ROBOT_AUTH
        # unlike the user endpoints, this one does not advertise a scheme
        assert "www-authenticate" not in response.headers

    @ALL_ENDPOINTS
    @pytest.mark.parametrize(
        "sent",
        [
            pytest.param("", id="empty-header"),
            pytest.param("not-a-robot-token", id="wrong-token"),
        ],
    )
    def test_unusable_tokens_are_rejected(
        self,
        client: TestClient,
        endpoint: tuple[str, str, dict[str, str]],
        enable_robots: Callable[..., dict[str, str]],
        sent: str,
    ) -> None:
        headers = enable_robots(ROBOT_TOKEN, send=sent)
        assert call(client, endpoint, headers=headers).status_code == 401

    @ALL_ENDPOINTS
    @pytest.mark.parametrize(
        "configured",
        [
            pytest.param(None, id="unset"),
            pytest.param(set(), id="empty-set"),
        ],
    )
    def test_without_configured_tokens_even_the_right_token_fails(
        self,
        client: TestClient,
        endpoint: tuple[str, str, dict[str, str]],
        monkeypatch: pytest.MonkeyPatch,
        configured: set[str] | None,
    ) -> None:
        monkeypatch.setattr(settings, "ods_robot_access_tokens", configured)
        response = call(client, endpoint, headers={"api-token": ROBOT_TOKEN})
        assert response.status_code == 401

    @ALL_ENDPOINTS
    def test_a_user_login_is_no_substitute(
        self,
        client: TestClient,
        endpoint: tuple[str, str, dict[str, str]],
        admin_user: User,
    ) -> None:
        # the token list is not consulted at all, so this fails with 401 even
        # though an admin is logged in
        response = call(client, endpoint, headers=auth_header(admin_user))
        assert response.status_code == 401

    @pytest.mark.parametrize("token", [ROBOT_TOKEN, SECOND_ROBOT_TOKEN])
    def test_every_configured_token_opens_every_endpoint(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        token: str,
    ) -> None:
        headers = enable_robots(ROBOT_TOKEN, SECOND_ROBOT_TOKEN)
        ingest_id = make_ingest(query_id=make_query())
        assert client.get("/ingest/", headers=headers).status_code == 200
        assert (
            client.put(
                "/ingest/queueing", params={"ingest_id": ingest_id}, headers=headers
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/ingest/queued", params={"ingest_id": ingest_id}, headers=headers
            ).status_code
            == 200
        )


class TestTransferAuthentication:
    """``PUT /ingest/transfer`` demands a logged-in admin, robot tokens fail."""

    def test_anonymous_requests_are_rejected(
        self, client: TestClient, make_query: Callable[..., int]
    ) -> None:
        response = client.put("/ingest/transfer", params={"query_id": make_query()})
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        "header",
        [
            pytest.param({"Authorization": "Bearer"}, id="bearer-without-credentials"),
            pytest.param({"Authorization": "Bearer not-a-jwt"}, id="malformed-jwt"),
            pytest.param({"Authorization": "Basic Zm9vOmJhcg=="}, id="wrong-scheme"),
            pytest.param({"Authorization": ""}, id="empty-header"),
        ],
    )
    def test_invalid_credentials_are_rejected(
        self,
        client: TestClient,
        make_query: Callable[..., int],
        header: dict[str, str],
    ) -> None:
        response = client.put(
            "/ingest/transfer", params={"query_id": make_query()}, headers=header
        )
        assert response.status_code == 401

    def test_well_signed_token_of_unknown_user_is_rejected(
        self, client: TestClient, make_query: Callable[..., int]
    ) -> None:
        response = client.put(
            "/ingest/transfer",
            params={"query_id": make_query()},
            headers=ghost_auth_header(),
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "User not found"

    def test_inactive_admins_are_rejected(
        self,
        client: TestClient,
        inactive_user: User,
        make_query: Callable[..., int],
    ) -> None:
        response = client.put(
            "/ingest/transfer",
            params={"query_id": make_query()},
            headers=auth_header(inactive_user),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Inactive user"

    def test_plain_users_are_refused(
        self,
        client: TestClient,
        normal_user: User,
        owned_query: int,
        fake_azure: FakeAzure,
    ) -> None:
        response = client.put(
            "/ingest/transfer",
            params={"query_id": owned_query},
            headers=auth_header(normal_user),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == NO_ADMIN_AUTH
        assert fake_azure.calls == []

    def test_the_robot_token_does_not_open_the_transfer_endpoint(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
    ) -> None:
        headers = enable_robots(ROBOT_TOKEN)
        response = client.put(
            "/ingest/transfer", params={"query_id": make_query()}, headers=headers
        )
        assert response.status_code == 401

    def test_the_submit_permission_is_not_required(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
    ) -> None:
        """Only ``permission_admin`` matters, the query flag plays no part here."""
        admin = make_user(
            email="admin-without-submit@example.org", permission_admin=True
        )
        response = client.put(
            "/ingest/transfer",
            params={"query_id": make_query()},
            headers=auth_header(admin),
        )
        assert response.status_code == 200

    def test_cookie_login_is_accepted_like_a_bearer_token(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
    ) -> None:
        client.cookies.set(settings.cookie_name, create_access_token(admin_user))
        response = client.put("/ingest/transfer", params={"query_id": make_query()})
        assert response.status_code == 200


# --------------------------------------------------------------------------------------
# GET /ingest/
# --------------------------------------------------------------------------------------


class TestListIngests:
    """``GET /ingest/`` - the robot looking at what the transfer endpoint tracked."""

    def test_an_empty_table_lists_nothing(
        self, client: TestClient, enable_robots: Callable[..., dict[str, str]]
    ) -> None:
        headers = enable_robots(ROBOT_TOKEN)
        response = client.get("/ingest/", headers=headers)
        assert response.status_code == 200
        assert response.json() == []

    def test_stored_ingests_are_returned_in_full(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
    ) -> None:
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id, is_queueing=True)
        response = client.get("/ingest/", headers=enable_robots(ROBOT_TOKEN))
        assert response.json() == [as_record(ingest_id, query_id, True, False)]

    def test_larger_ids_are_listed_first(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
    ) -> None:
        """Ordered descending: the uuid6 hex IDs the endpoint generates sort by time."""
        for index in (1, 2, 3):
            make_ingest(query_id=make_query(), ingest_id=f"ing-{index:02d}")
        response = client.get("/ingest/", headers=enable_robots(ROBOT_TOKEN))
        assert ingested_ids(response) == ["ing-03", "ing-02", "ing-01"]

    def test_the_default_page_holds_five_items(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
    ) -> None:
        for index in range(1, 8):
            make_ingest(query_id=make_query(), ingest_id=f"ing-{index:02d}")
        response = client.get("/ingest/", headers=enable_robots(ROBOT_TOKEN))
        assert ingested_ids(response) == [
            "ing-07",
            "ing-06",
            "ing-05",
            "ing-04",
            "ing-03",
        ]

    def test_limit_and_offset_page_through_the_list(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
    ) -> None:
        for index in range(1, 8):
            make_ingest(query_id=make_query(), ingest_id=f"ing-{index:02d}")
        headers = enable_robots(ROBOT_TOKEN)
        response = client.get(
            "/ingest/", headers=headers, params={"limit": 2, "offset": 2}
        )
        assert ingested_ids(response) == ["ing-05", "ing-04"]
        tail = client.get("/ingest/", headers=headers, params={"limit": 2, "offset": 6})
        assert ingested_ids(tail) == ["ing-01"]
        assert (
            ingested_ids(client.get("/ingest/", headers=headers, params={"limit": 0}))
            == []
        )
        assert (
            ingested_ids(client.get("/ingest/", headers=headers, params={"offset": 7}))
            == []
        )

    def test_only_open_keeps_ingests_that_never_started(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
    ) -> None:
        """``only_open`` hides anything that started queueing or claims to be queued."""
        make_ingest(query_id=make_query(), ingest_id="open-a")
        make_ingest(query_id=make_query(), ingest_id="queueing-b", is_queueing=True)
        make_ingest(
            query_id=make_query(),
            ingest_id="queued-c",
            is_queueing=True,
            is_queued=True,
        )
        make_ingest(query_id=make_query(), ingest_id="odd-d", is_queued=True)
        headers = enable_robots(ROBOT_TOKEN)
        assert ingested_ids(client.get("/ingest/", headers=headers)) == [
            "queueing-b",
            "queued-c",
            "open-a",
            "odd-d",
        ]
        assert ingested_ids(
            client.get("/ingest/", headers=headers, params={"only_open": True})
        ) == ["open-a"]


# --------------------------------------------------------------------------------------
# PUT /ingest/queueing
# --------------------------------------------------------------------------------------


class TestMarkQueueing:
    """``PUT /ingest/queueing`` - the robot announces that it started transferring."""

    def test_an_unknown_ingest_is_not_found(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
    ) -> None:
        response = client.put(
            "/ingest/queueing",
            params={"ingest_id": UNKNOWN_INGEST_ID},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == (
            f"No info found for ingest_id={UNKNOWN_INGEST_ID}"
        )

    def test_the_ingest_id_is_required(
        self, client: TestClient, enable_robots: Callable[..., dict[str, str]]
    ) -> None:
        response = client.put("/ingest/queueing", headers=enable_robots(ROBOT_TOKEN))
        assert response.status_code == 422

    def test_a_fresh_ingest_turns_queueing(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id)
        response = client.put(
            "/ingest/queueing",
            params={"ingest_id": ingest_id},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.status_code == 200
        assert response.json() == as_record(ingest_id, query_id, True, False)
        assert stored_ingest(ingest_id) == as_record(ingest_id, query_id, True, False)

    def test_an_ingest_of_another_query_keeps_its_state(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        marked_id = make_ingest(query_id=make_query(), ingest_id="ing-mark")
        other_query = make_query()
        untouched_id = make_ingest(query_id=other_query, ingest_id="ing-keep")
        client.put(
            "/ingest/queueing",
            params={"ingest_id": marked_id},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert stored_ingest(untouched_id) == as_record(
            "ing-keep", other_query, False, False
        )

    def test_a_queued_flag_is_reset_on_the_way_back(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        """A re-transfer of a finished ingest starts over: queueing on, queued off."""
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id, is_queueing=False, is_queued=True)
        response = client.put(
            "/ingest/queueing",
            params={"ingest_id": ingest_id},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.json() == as_record(ingest_id, query_id, True, False)
        assert stored_ingest(ingest_id) == as_record(ingest_id, query_id, True, False)

    def test_marking_queueing_twice_changes_nothing(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id, is_queueing=True)
        response = client.put(
            "/ingest/queueing",
            params={"ingest_id": ingest_id},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.status_code == 200
        assert response.json() == as_record(ingest_id, query_id, True, False)
        assert stored_ingest(ingest_id) == as_record(ingest_id, query_id, True, False)

    def test_a_queued_ingest_is_returned_as_it_is(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        """A late queueing report finds the ingest finished and does not rewind it."""
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id, is_queueing=True, is_queued=True)
        response = client.put(
            "/ingest/queueing",
            params={"ingest_id": ingest_id},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.json() == as_record(ingest_id, query_id, True, True)
        assert stored_ingest(ingest_id) == as_record(ingest_id, query_id, True, True)


# --------------------------------------------------------------------------------------
# PUT /ingest/queued
# --------------------------------------------------------------------------------------


class TestMarkQueued:
    """``PUT /ingest/queued`` - the robot reports that the transfer finished."""

    def test_an_unknown_ingest_is_not_found(
        self, client: TestClient, enable_robots: Callable[..., dict[str, str]]
    ) -> None:
        response = client.put(
            "/ingest/queued",
            params={"ingest_id": UNKNOWN_INGEST_ID},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == (
            f"No info found for ingest_id={UNKNOWN_INGEST_ID}"
        )

    def test_the_ingest_id_is_required(
        self, client: TestClient, enable_robots: Callable[..., dict[str, str]]
    ) -> None:
        response = client.put("/ingest/queued", headers=enable_robots(ROBOT_TOKEN))
        assert response.status_code == 422

    def test_queued_before_queueing_is_refused(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id)
        response = client.put(
            "/ingest/queued",
            params={"ingest_id": ingest_id},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"Trying to mark queued before queueing ingest_id={ingest_id}"
        )
        assert stored_ingest(ingest_id) == as_record(ingest_id, query_id, False, False)

    def test_a_freshly_queued_ingest_is_returned_as_it_is(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
    ) -> None:
        """An ingest already marked as queued hits the short circuit and is untouched."""
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id, is_queueing=True, is_queued=True)
        response = client.put(
            "/ingest/queued",
            params={"ingest_id": ingest_id},
            headers=enable_robots(ROBOT_TOKEN),
        )
        assert response.status_code == 200
        assert response.json() == as_record(ingest_id, query_id, True, True)

    def test_a_queueing_ingest_turns_queued(
        self,
        client: TestClient,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        query_id = make_query()
        ingest_id = make_ingest(query_id=query_id, is_queueing=True)
        headers = enable_robots(ROBOT_TOKEN)
        response = client.put(
            "/ingest/queued", params={"ingest_id": ingest_id}, headers=headers
        )
        assert response.status_code == 200
        assert response.json() == as_record(ingest_id, query_id, True, True)
        assert stored_ingest(ingest_id) == as_record(ingest_id, query_id, True, True)
        # the robot reporting again hits the short circuit and changes nothing
        retry = client.put(
            "/ingest/queued", params={"ingest_id": ingest_id}, headers=headers
        )
        assert retry.json() == as_record(ingest_id, query_id, True, True)
        assert stored_ingest(ingest_id) == as_record(ingest_id, query_id, True, True)


# --------------------------------------------------------------------------------------
# PUT /ingest/transfer
# --------------------------------------------------------------------------------------


class TestAddIngest:
    """``PUT /ingest/transfer`` - an admin registers a query for the robot."""

    def test_an_unknown_query_is_not_found(
        self,
        client: TestClient,
        admin_user: User,
        fake_azure: FakeAzure,
    ) -> None:
        response = client.put(
            "/ingest/transfer",
            params={"query_id": UNKNOWN_QUERY_ID},
            headers=auth_header(admin_user),
        )
        assert response.status_code == 404
        assert (
            response.json()["detail"]
            == f"No query found for query_id={UNKNOWN_QUERY_ID}"
        )
        assert fake_azure.calls == []

    @pytest.mark.parametrize(
        "query_id",
        [
            pytest.param(None, id="missing-query-id"),
            pytest.param("not-a-number", id="non-numeric-query-id"),
        ],
    )
    def test_an_unusable_query_id_is_unprocessable(
        self,
        client: TestClient,
        admin_user: User,
        fake_azure: FakeAzure,
        query_id: str | None,
    ) -> None:
        arguments: dict[str, Any] = (
            {} if query_id is None else {"params": {"query_id": query_id}}
        )
        response = client.put(
            "/ingest/transfer", headers=auth_header(admin_user), **arguments
        )
        assert response.status_code == 422
        assert fake_azure.calls == []

    def test_a_fresh_ingest_is_built_from_the_azure_links(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        query_id = make_query()
        response = client.put(
            "/ingest/transfer",
            params={"query_id": query_id},
            headers=auth_header(admin_user),
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == set(INGEST_FIELDS)
        assert re.fullmatch(r"[0-9a-f]{32}", body["ingest_id"])
        assert body["query_id"] == query_id
        # the robot gets the link to the responses of the query
        assert body["azure_link"] == link(blob(query_id), "responses_url")
        assert body["is_queueing"] is False
        assert body["is_queued"] is False
        # asked for with the long robot lifetime and the path the results live under
        assert fake_azure.calls == [
            {
                "blob_path": blob(query_id),
                "key_lifetime": settings.azure_storage_robot_key_lifetime,
            },
        ]
        # what the endpoint hands back is the row it stored
        assert stored_ingest(body["ingest_id"]) == body

    def test_the_container_path_prefix_reaches_the_blob_path(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        query_id = make_query()
        monkeypatch.setattr(settings, "azure_storage_container_path", "base-folder/")
        response = client.put(
            "/ingest/transfer",
            params={"query_id": query_id},
            headers=auth_header(admin_user),
        )
        assert response.status_code == 200
        assert response.json()["azure_link"] == link(
            f"base-folder/{query_id}", "responses_url"
        )
        assert fake_azure.calls == [
            {
                "blob_path": f"base-folder/{query_id}",
                "key_lifetime": settings.azure_storage_robot_key_lifetime,
            },
        ]

    def test_a_query_that_is_tracked_already_returns_that_ingest(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        make_ingest: Callable[..., str],
        fake_azure: FakeAzure,
    ) -> None:
        """Re-registering is idempotent as long as the row is actually there."""
        query_id = make_query()
        make_ingest(query_id=query_id, ingest_id="already-there")
        response = client.put(
            "/ingest/transfer",
            params={"query_id": query_id},
            headers=auth_header(admin_user),
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == set(INGEST_FIELDS)
        assert body["ingest_id"] == "already-there"
        assert body["azure_link"] == "https://storage.example.org/ingest/responses"
        assert fake_azure.calls == []

    def test_the_same_query_is_transferred_only_once(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
        stored_ingests: Callable[[], list[dict[str, Any]]],
    ) -> None:
        """The second call deduplicates on ``query_id`` and skips Azure entirely."""
        query_id = make_query()
        first = client.put(
            "/ingest/transfer",
            params={"query_id": query_id},
            headers=auth_header(admin_user),
        )
        assert first.status_code == 200
        second = client.put(
            "/ingest/transfer",
            params={"query_id": query_id},
            headers=auth_header(admin_user),
        )
        assert second.status_code == 200
        assert second.json() == first.json()
        assert len(fake_azure.calls) == 1
        assert len(stored_ingests()) == 1

    def test_a_transferred_ingest_can_run_the_whole_lifecycle(
        self,
        client: TestClient,
        admin_user: User,
        enable_robots: Callable[..., dict[str, str]],
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
        stored_ingest: Callable[[str], dict[str, Any]],
    ) -> None:
        """Transfer, queueing and queued work hand in hand on the stored row."""
        query_id = make_query()
        transfer = client.put(
            "/ingest/transfer",
            params={"query_id": query_id},
            headers=auth_header(admin_user),
        )
        assert transfer.status_code == 200
        ingest_id = transfer.json()["ingest_id"]
        robot = enable_robots(ROBOT_TOKEN)
        assert ingested_ids(client.get("/ingest/", headers=robot)) == [ingest_id]
        too_early = client.put(
            "/ingest/queued", params={"ingest_id": ingest_id}, headers=robot
        )
        assert too_early.status_code == 400  # queueing has to be reported first
        client.put("/ingest/queueing", params={"ingest_id": ingest_id}, headers=robot)
        done = client.put(
            "/ingest/queued", params={"ingest_id": ingest_id}, headers=robot
        )
        assert done.status_code == 200
        expected = {
            "ingest_id": ingest_id,
            "query_id": query_id,
            "azure_link": link(blob(query_id), "responses_url"),
            "is_queueing": True,
            "is_queued": True,
        }
        assert done.json() == expected
        assert stored_ingest(ingest_id) == expected

    def test_a_failing_azure_call_breaks_the_endpoint(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        azure_outage: None,
    ) -> None:
        response = client.put(
            "/ingest/transfer",
            params={"query_id": make_query()},
            headers=auth_header(admin_user),
        )
        assert response.status_code == 500
