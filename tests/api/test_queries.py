"""
Tests for the ``/queries`` endpoints: submitting and listing queries, deleting them, and
managing who may see, manage and download them.

The rules covered here:

================================  ===========  =================  ================  ===============
                               anonymous     plain user        owner of a query     admin
================================  ===========  =================  ================  ===============
``POST /queries/test``            401          with an API key    with an API key   with an API key
``POST /queries/render``          401          allowed            allowed           allowed
``POST /queries/``                401          403                403               allowed
``GET /queries/``                 401          own queries only   own queries only  every query
``DELETE /queries/``              401          403                403               allowed
``DELETE /queries/logs``          401          403                allowed           allowed
``PUT /queries/permission``       401          403                allowed           allowed
``DELETE /queries/permission``    401          403                allowed           allowed
``GET /queries/permission``       401          403                allowed           allowed
``GET /queries/download``         401          403                403               allowed
``GET /queries/download/refresh`` 401          403                allowed           allowed
================================  ===========  =================  ================  ===============

Permissions are stored per query (``m2m_user_query``): an ``is_owner=True`` row makes a
user the owner of a query, which is what the permission endpoints and the log and link
endpoints check. Admins override ownership everywhere.

Work that the endpoints schedule as background tasks - deleting rows and files, queueing
subqueries, talking to Azure - is executed by ``TestClient`` before a request returns, so
the tests can assert on its effects directly.

Endpoints that behave differently from what their names or docstrings promise are covered
by tests marked ``KNOWN BUG``; those assert today's actual responses and have to be
updated together with the endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from academic_apis.api import queries as queries_module
from academic_apis.api.queries import _queue_subqueries
from academic_apis.api.utils.auth import create_access_token
from academic_apis.broker import tasks as broker_tasks
from academic_apis.db import Query, User
from academic_apis.utils import settings
from academic_apis.utils.types import AzureAccessLinks, Wrapper
from academic_apis.wrappers import WRAPPERS

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from httpx2 import Response  # the project pins `httpx2`, which `TestClient` returns

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

#: request bodies reused by several endpoints and the authentication tests
SCOPUS_BODY: dict[str, Any] = {"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"}

#: ``(method, path, json body)`` for every endpoint, used by the authentication tests.
ENDPOINTS: dict[str, tuple[str, str, Any]] = {
    "test": ("POST", "/queries/test", SCOPUS_BODY),
    "render": ("POST", "/queries/render", SCOPUS_BODY),
    "submit": ("POST", "/queries/", SCOPUS_BODY),
    "list": ("GET", "/queries/", None),
    "delete": ("DELETE", "/queries/?query_id=1", None),
    "delete-logs": ("DELETE", "/queries/logs?query_id=1", None),
    "permission-upsert": (
        "PUT",
        "/queries/permission",
        {"user_id": "whoever", "query_id": 1, "is_owner": False},
    ),
    "permission-drop": (
        "DELETE",
        "/queries/permission",
        {"user_id": "whoever", "query_id": 1, "is_owner": False},
    ),
    "permission-get": ("GET", "/queries/permission?query_id=1", None),
    "download": ("GET", "/queries/download?query_id=1", None),
    "download-refresh": ("GET", "/queries/download/refresh?query_id=1", None),
}

ALL_ENDPOINTS = pytest.mark.parametrize("endpoint", ENDPOINTS.values(), ids=ENDPOINTS)

#: ``(user_id, query_id, is_owner)``, as returned by ``stored_query_permissions``.
PermissionRow = tuple[str, int, bool]

#: the fields of :class:`~academic_apis.utils.types.AzureAccessLinks`
LINK_FIELDS = ("download_url", "list_url", "responses_url", "log_url", "errors_url")

#: a query the Scopus grammar accepts, and the rendering ``query_to_indented_text`` makes
VALID_SCOPUS_QUERY = 'TITLE-ABS-KEY("climate change") AND PUBYEAR AFT 2010'
RENDERED_SCOPUS_QUERY = 'TITLE-ABS-KEY (\n  "climate change"\n)\nAND PUBYEAR AFT 2010'

OWNER_ONLY_DETAIL = (
    "Not enough permissions: only owners of a query can manage its permissions"
)


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run one of the async functions under test from a synchronous test."""
    return asyncio.run(coroutine)


def auth_header(user: User) -> dict[str, str]:
    """Bearer header that logs the request in as ``user``."""
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def ghost_auth_header(user_id: str = "ghost-user") -> dict[str, str]:
    """Header signed with the right secret for a user missing from the database."""
    token = jwt.encode(
        {"sub": user_id, "email": "ghost@example.org", "permission_admin": True},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


def call(
    client: TestClient,
    endpoint: tuple[str, str, Any],
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Response:
    """Fire one of the ``ENDPOINTS`` entries, sending its body where one belongs."""
    method, path, body = endpoint
    if headers is not None:
        kwargs["headers"] = headers
    if body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs)


def permission(user: User | str, query_id: int, is_owner: bool) -> PermissionRow:
    """Expected permission row for ``user`` and ``query_id``."""
    user_id = user.user_id if isinstance(user, User) else user
    return user_id, query_id, is_owner


def permission_body(
    user: User | str, query_id: int, is_owner: bool = False
) -> dict[str, Any]:
    """Request body for the ``/queries/permission`` endpoints that write."""
    user_id = user.user_id if isinstance(user, User) else user
    return {"user_id": user_id, "query_id": query_id, "is_owner": is_owner}


def permission_rows(response: Response) -> list[PermissionRow]:
    """``(user_id, query_id, is_owner)`` triples returned by a permission endpoint."""
    return [
        (entry["user_id"], entry["query_id"], entry["is_owner"])
        for entry in response.json()
    ]


def listed_ids(response: Response) -> list[int]:
    """Query IDs returned by ``GET /queries/``."""
    return [entry["query_id"] for entry in response.json()]


def result_path(query_id: int, *parts: str) -> Path:
    """
    ``<data root>/results/<query_id>/...`` without creating anything on the way.

    ``settings.responses_dir`` and ``settings.converted_dir`` create their directories
    when called, which would hide exactly the side effects some tests look for.
    """
    return settings.result_dir.joinpath(str(query_id), *parts)


def blob(query_id: int) -> str:
    """The Azure blob path the endpoints build for a query."""
    return f"{settings.azure_storage_container_path}{query_id}"


def expected_links(query_id: int, version: int = 1) -> dict[str, str]:
    """Links the ``fake_azure`` fixture hands out for a query."""
    return {
        name: f"https://storage.example.org/{blob(query_id)}/{name}?v={version}"
        for name in LINK_FIELDS
    }


# --------------------------------------------------------------------------------------
# stand-ins for the outside world
# --------------------------------------------------------------------------------------


class FakeWrapperApi:
    """Stand-in for a real API wrapper class; records how ``/queries/test`` uses it."""

    response: httpx.Response | None
    inits: list[dict[str, Any]]
    calls: list[dict[str, Any]]

    def __init__(self, **kwargs: Any) -> None:
        type(self).inits.append(kwargs)

    async def get_first_response(self, **kwargs: Any) -> httpx.Response | None:
        type(self).calls.append(kwargs)
        return type(self).response


@pytest.fixture
def fake_wrapper_for(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Wrapper], type[FakeWrapperApi]]:
    """
    Install a recording wrapper class for a :class:`~academic_apis.utils.Wrapper`.

    The returned class starts out with a plain JSON ``200`` answer; tests assign to its
    ``response`` attribute to simulate anything else, and read ``inits`` and ``calls``.
    """

    def factory(wrapper: Wrapper = Wrapper.SCOPUS) -> type[FakeWrapperApi]:
        api = type(
            "FakeWrapperApi",
            (FakeWrapperApi,),
            {
                "response": httpx.Response(200, json={"meta": {"total-results": 1}}),
                "inits": [],
                "calls": [],
            },
        )
        monkeypatch.setitem(queries_module.WRAPPERS, wrapper, api)
        return api

    return factory


@pytest.fixture
def fake_wrapper(
    fake_wrapper_for: Callable[[Wrapper], type[FakeWrapperApi]],
) -> type[FakeWrapperApi]:
    """The stand-in used for the Scopus wrapper, the one most tests exercise."""
    return fake_wrapper_for(Wrapper.SCOPUS)


@dataclass
class FakeAzure:
    """Records the Azure calls the download endpoints make."""

    #: ``("links" | "sync" | "upload", ...)`` in call order
    calls: list[tuple[Any, ...]] = field(default_factory=list)
    link_requests: list[str] = field(default_factory=list)
    syncs: list[tuple[str, str]] = field(default_factory=list)
    uploads: list[tuple[str, str, bool]] = field(default_factory=list)
    version: int = 0

    def links_for(self, blob_path: str) -> AzureAccessLinks:
        """Fresh links for a blob path; every call hands out a new version."""
        self.version += 1
        return AzureAccessLinks(
            **{
                name: f"https://storage.example.org/{blob_path}/{name}?v={self.version}"
                for name in LINK_FIELDS
            }
        )


@pytest.fixture
def fake_azure(monkeypatch: pytest.MonkeyPatch) -> FakeAzure:
    """Replace the Azure helpers, which the endpoints import at call time."""
    azure = FakeAzure()

    async def get_azure_links(blob_path: str) -> AzureAccessLinks:
        azure.link_requests.append(blob_path)
        azure.calls.append(("links", blob_path))
        return azure.links_for(blob_path)

    async def sync_directory(source_dir: str, target_dir: str) -> None:
        azure.calls.append(("sync", source_dir, target_dir))
        azure.syncs.append((source_dir, target_dir))

    async def upload_concatenated_files(
        source_pattern: str,
        target_file: str,
        overwrite: bool = False,
        include_file_headers: bool = False,
    ) -> None:
        azure.calls.append(("upload", source_pattern, target_file, include_file_headers))
        azure.uploads.append((source_pattern, target_file, include_file_headers))

    monkeypatch.setattr("academic_apis.utils.azure.get_azure_links", get_azure_links)
    monkeypatch.setattr("academic_apis.utils.azure.sync_directory", sync_directory)
    monkeypatch.setattr(
        "academic_apis.utils.azure.upload_concatenated_files", upload_concatenated_files
    )
    return azure


@dataclass
class FakeQueue:
    """Records what ``_queue_subqueries`` hands to the task broker."""

    queued: list[dict[str, Any]] = field(default_factory=list)

    async def kiq(self, **kwargs: Any) -> str:
        self.queued.append(kwargs)
        return "queued-task-id"


@pytest.fixture
def fake_queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    """Replace the taskiq task that ``_queue_subqueries`` imports at call time."""
    queue = FakeQueue()
    monkeypatch.setattr(broker_tasks, "retrieve_query", queue)
    return queue


def transient_query(
    query: str = "TITLE-ABS-KEY(x)",
    wrapper: Wrapper = Wrapper.SCOPUS,
    query_id: int = 42,
) -> Query:
    """A ``Query`` that never touched a session, for calling the background helpers."""
    return Query(query=query, wrapper=wrapper, query_id=query_id)


# --------------------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------------------


class TestAuthentication:
    """All endpoints require a valid login, no matter the permission level."""

    @ALL_ENDPOINTS
    def test_anonymous_requests_are_rejected(
        self, client: TestClient, endpoint: tuple[str, str, Any]
    ) -> None:
        response = call(client, endpoint)
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"
        assert response.headers["www-authenticate"] == "Bearer"

    @ALL_ENDPOINTS
    def test_inactive_users_are_rejected(
        self,
        client: TestClient,
        endpoint: tuple[str, str, Any],
        inactive_user: User,
    ) -> None:
        response = call(client, endpoint, headers=auth_header(inactive_user))
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
        self,
        client: TestClient,
        endpoint: tuple[str, str, Any],
        header: dict[str, str],
    ) -> None:
        assert call(client, endpoint, headers=header).status_code == 401

    def test_well_signed_token_of_unknown_user_is_rejected(
        self, client: TestClient
    ) -> None:
        # Correctly signed, but this user does not exist in the database.
        response = client.get("/queries/", headers=ghost_auth_header())
        assert response.status_code == 401
        assert response.json()["detail"] == "User not found"

    def test_admin_rights_cannot_be_forged_in_a_json_web_token(
        self, client: TestClient
    ) -> None:
        # The header claims `permission_admin`, but rights are read from the database.
        response = client.delete(
            "/queries/?query_id=1", headers=ghost_auth_header("fake-admin")
        )
        assert response.status_code == 401

    def test_cookie_login_is_accepted_like_a_bearer_token(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        client.cookies.set(settings.cookie_name, create_access_token(normal_user))
        assert listed_ids(client.get("/queries/")) == [owned_query]


# --------------------------------------------------------------------------------------
# POST /queries/test
# --------------------------------------------------------------------------------------


class TestTestQuery:
    """``POST /queries/test`` - running a query once with the user's own API key."""

    def test_the_api_response_is_returned_verbatim(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        fake_wrapper: type[FakeWrapperApi],
    ) -> None:
        answer = httpx.Response(
            200,
            json={"entries": [{"eid": "1"}], "meta": {"total-results": 1}},
            headers={"x-request-id": "abc123"},
        )
        fake_wrapper.response = answer
        response = client.post(
            "/queries/test",
            headers=auth_header(normal_user),
            json={"query": VALID_SCOPUS_QUERY, "wrapper": "scopus"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "body": {"entries": [{"eid": "1"}], "meta": {"total-results": 1}},
            "headers": dict(answer.headers),
            "status": 200,
        }

    def test_the_users_api_key_and_proxy_are_used(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        fake_wrapper: type[FakeWrapperApi],
    ) -> None:
        user = make_user(email="tester@example.org")
        make_token(
            owner=user,
            token="secret-scopus-key",
            proxy="socks5://proxy.example.org:1080",
        )
        client.post(
            "/queries/test",
            headers=auth_header(user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        (init,) = fake_wrapper.inits
        assert init["api_key"] == "secret-scopus-key"
        assert init["proxy"] == "socks5://proxy.example.org:1080"
        assert init["skip_token_logging"] is False
        assert isinstance(init["logger"], logging.Logger)

    def test_query_and_parameters_are_passed_to_the_api(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        fake_wrapper: type[FakeWrapperApi],
    ) -> None:
        client.post(
            "/queries/test",
            headers=auth_header(normal_user),
            json={
                "query": "AUTH(authdir_id(12345678900))",
                "wrapper": "scopus",
                "params": {"view": "FULL", "cursor": "*"},
            },
        )
        (recorded,) = fake_wrapper.calls
        assert recorded == {
            "query": "AUTH(authdir_id(12345678900))",
            "params": {"view": "FULL", "cursor": "*"},
        }

    def test_the_api_key_itself_is_never_returned(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        fake_wrapper: type[FakeWrapperApi],
    ) -> None:
        response = client.post(
            "/queries/test",
            headers=auth_header(normal_user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert response.status_code == 200
        # the key that was used for the call does not appear anywhere in the response
        assert "api-key-value" not in response.text

    def test_an_answer_of_another_wrapper_is_tested_too(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        fake_wrapper_for: Callable[[Wrapper], type[FakeWrapperApi]],
    ) -> None:
        user = make_user(email="pubmed-tester@example.org")
        make_token(owner=user, wrapper=Wrapper.SCOPUS, token="scopus-key")
        make_token(owner=user, wrapper=Wrapper.PUBMED, token="pubmed-key")
        api = fake_wrapper_for(Wrapper.PUBMED)
        response = client.post(
            "/queries/test",
            headers=auth_header(user),
            json={"query": "cancer[titles]", "wrapper": "pubmed"},
        )
        assert response.status_code == 200
        assert api.inits[0]["api_key"] == "pubmed-key"

    def test_a_response_without_json_is_reported_as_text(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        fake_wrapper: type[FakeWrapperApi],
    ) -> None:
        fake_wrapper.response = httpx.Response(
            429, text="Too Many Requests", headers={"retry-after": "1"}
        )
        response = client.post(
            "/queries/test",
            headers=auth_header(normal_user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert response.status_code == 200
        reported = response.json()
        assert reported["status"] == 429
        assert reported["body"]["text"] == "Too Many Requests"
        assert "Expecting value" in reported["body"]["error"]
        assert reported["headers"]["retry-after"] == "1"

    def test_a_missing_answer_is_reported_as_no_content(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        fake_wrapper: type[FakeWrapperApi],
    ) -> None:
        fake_wrapper.response = None
        response = client.post(
            "/queries/test",
            headers=auth_header(normal_user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert response.status_code == 204
        assert response.json()["detail"] == (
            "Did not receive a response from the scopus API."
        )

    def test_inactive_and_foreign_tokens_leave_the_user_without_a_key(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        fake_wrapper: type[FakeWrapperApi],
    ) -> None:
        """Neither an inactive token nor one for another database is usable."""
        user = make_user(email="stale-key@example.org")
        make_token(owner=user, wrapper=Wrapper.PUBMED, token="not-the-scopus-key")
        make_token(
            owner=user, wrapper=Wrapper.SCOPUS, is_active=False, token="inactive-key"
        )
        response = client.post(
            "/queries/test",
            headers=auth_header(user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        # KNOWN BUG: see `test_a_user_without_an_api_key_fails`.
        assert response.status_code == 500
        assert fake_wrapper.inits == []

    def test_a_user_without_an_api_key_fails(
        self, client: TestClient, make_user: Callable[..., User]
    ) -> None:
        """
        KNOWN BUG: ``get_available_token_for_user`` raises ``NoResultFound``.

        The endpoint should answer that no API key is available for this wrapper, but the
        unhandled SQLAlchemy error turns into a 500.
        """
        outsider = make_user(email="keyless@example.org")
        response = client.post(
            "/queries/test",
            headers=auth_header(outsider),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert response.status_code == 500

    def test_a_wrapper_without_implementation_fails(
        self, client: TestClient, normal_user: User, owned_token: str
    ) -> None:
        """
        KNOWN BUG: ``Wrapper`` lists more databases than ``WRAPPERS`` implements.

        ``openalex`` is a valid value of the enum, so the body passes validation, but the
        lookup in ``WRAPPERS`` raises a ``KeyError``.
        """
        assert "openalex" not in WRAPPERS
        response = client.post(
            "/queries/test",
            headers=auth_header(normal_user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "openalex"},
        )
        assert response.status_code == 500

    @pytest.mark.parametrize(
        ("payload", "location"),
        [
            pytest.param(
                {"query": "TITLE-ABS-KEY(x)", "wrapper": "google-scholar"},
                ["body", "wrapper"],
                id="unknown-wrapper",
            ),
            pytest.param({"wrapper": "scopus"}, ["body", "query"], id="missing-query"),
            pytest.param(
                {"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus", "params": "view"},
                ["body", "params"],
                id="params-not-an-object",
            ),
        ],
    )
    def test_unusable_bodies_are_rejected_before_any_call(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        fake_wrapper: type[FakeWrapperApi],
        payload: dict[str, Any],
        location: list[str],
    ) -> None:
        response = client.post(
            "/queries/test", headers=auth_header(normal_user), json=payload
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == location
        assert fake_wrapper.inits == []


# --------------------------------------------------------------------------------------
# POST /queries/render
# --------------------------------------------------------------------------------------


class TestRenderQuery:
    """``POST /queries/render`` - pretty-printing a Scopus query."""

    def test_a_scopus_query_is_returned_indented(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.post(
            "/queries/render",
            headers=auth_header(normal_user),
            json={"query": VALID_SCOPUS_QUERY, "wrapper": "scopus"},
        )
        assert response.status_code == 200
        assert response.json() == RENDERED_SCOPUS_QUERY

    def test_rendering_is_idempotent(self, client: TestClient, normal_user: User) -> None:
        headers = auth_header(normal_user)
        first = client.post(
            "/queries/render",
            headers=headers,
            json={"query": VALID_SCOPUS_QUERY, "wrapper": "scopus"},
        ).json()
        second = client.post(
            "/queries/render",
            headers=headers,
            json={"query": first, "wrapper": "scopus"},
        ).json()
        assert second == first

    def test_rendering_needs_neither_an_api_key_nor_a_stored_query(
        self, client: TestClient, make_user: Callable[..., User]
    ) -> None:
        user = make_user(email="no-tokens@example.org")
        response = client.post(
            "/queries/render",
            headers=auth_header(user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        ("query", "hint"),
        [
            pytest.param("TITLE-ABS-KEY(", "The query ended too early.", id="open-group"),
            pytest.param(
                "TITLE-ABS-KEY(a AND (b)", "ended too early", id="missing-paren"
            ),
            pytest.param("", "The query ended too early.", id="empty-query"),
            pytest.param(
                "AUTHOR-KEYWORD(solar)",
                '"AUTHOR-KEYWORD" is not a valid code here',
                id="misspelt-field",
            ),
            pytest.param(
                "TITLE-ABS-KEY(x) OR OR TITLE-ABS-KEY(y)",
                "Syntax error at line 1, column 21",
                id="dangling-operator",
            ),
        ],
    )
    def test_a_syntax_error_is_reported_as_bad_request(
        self, client: TestClient, normal_user: User, query: str, hint: str
    ) -> None:
        response = client.post(
            "/queries/render",
            headers=auth_header(normal_user),
            json={"query": query, "wrapper": "scopus"},
        )
        assert response.status_code == 400
        assert hint in response.json()["detail"]

    def test_the_error_message_points_at_the_problem(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.post(
            "/queries/render",
            headers=auth_header(normal_user),
            json={"query": "AUTHOR-KEYWORD(solar)", "wrapper": "scopus"},
        )
        detail = response.json()["detail"]
        assert "Did you mean AUTHKEY" in detail
        assert "Expected one of" in detail

    @pytest.mark.parametrize(
        ("wrapper", "detail"),
        [
            pytest.param(
                "pubmed", "Formatting not available for Wrapper.PUBMED.", id="pubmed"
            ),
            pytest.param(
                "openalex",
                "Formatting not available for Wrapper.OPENALEX.",
                id="openalex",
            ),
        ],
    )
    def test_other_wrappers_are_refused(
        self, client: TestClient, normal_user: User, wrapper: str, detail: str
    ) -> None:
        """
        KNOWN BUG: the message leaks the enum representation, not the wrapper's name.

        Users pick "pubmed" in the web UI, so an error mentioning ``Wrapper.PUBMED`` is
        harder to recognise than the value they sent.
        """
        response = client.post(
            "/queries/render",
            headers=auth_header(normal_user),
            json={"query": "cancer[titles]", "wrapper": wrapper},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == detail

    def test_an_unknown_wrapper_is_unprocessable(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.post(
            "/queries/render",
            headers=auth_header(normal_user),
            json={"query": "x", "wrapper": "bogus"},
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "enum"


# --------------------------------------------------------------------------------------
# POST /queries/
# --------------------------------------------------------------------------------------


class TestSubmitQuery:
    """``POST /queries/`` - registering a query and queueing its subqueries."""

    def test_an_admin_may_submit_a_query(
        self,
        client: TestClient,
        admin_user: User,
        stored_query: Callable[[int], dict[str, Any]],
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        response = client.post(
            "/queries/",
            headers=auth_header(admin_user),
            json={"query": VALID_SCOPUS_QUERY, "wrapper": "scopus"},
        )
        assert response.status_code == 200
        query_id = response.json()
        assert isinstance(query_id, int)
        stored = stored_query(query_id)
        assert stored["query"] == VALID_SCOPUS_QUERY
        assert stored["wrapper"] == Wrapper.SCOPUS
        # the submitter automatically becomes the owner of the new query
        assert stored_query_permissions(query_id=query_id) == [
            permission(admin_user, query_id, True)
        ]

    def test_the_query_is_listed_for_its_submitter(
        self, client: TestClient, admin_user: User
    ) -> None:
        query_id = client.post(
            "/queries/",
            headers=auth_header(admin_user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        ).json()
        assert listed_ids(client.get("/queries/", headers=auth_header(admin_user))) == [
            query_id
        ]

    def test_nobody_else_sees_the_new_query(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        client.post(
            "/queries/",
            headers=auth_header(admin_user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert listed_ids(client.get("/queries/", headers=auth_header(normal_user))) == []
        assert stored_query_permissions(user_id=normal_user.user_id) == []

    def test_a_plain_user_is_refused(
        self, client: TestClient, normal_user: User, owned_token: str
    ) -> None:
        response = client.post(
            "/queries/",
            headers=auth_header(normal_user),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "You do not have the permission to submit queries."
        )

    def test_an_admin_without_the_submit_permission_is_refused(
        self, client: TestClient, make_user: Callable[..., User]
    ) -> None:
        admin = make_user(email="admin-without-submit@example.org", permission_admin=True)
        assert (
            client.post(
                "/queries/",
                headers=auth_header(admin),
                json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
            ).status_code
            == 403
        )

    def test_the_submit_permission_alone_is_not_enough(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        """
        KNOWN BUG: the condition is ``not submit or not admin``, so it demands both.

        A dedicated query submitter who is not an admin is rejected, which makes the
        ``permission_submit_query`` flag pointless.
        """
        submitter = make_user(
            email="dedicated-submitter@example.org", permission_submit_query=True
        )
        response = client.post(
            "/queries/",
            headers=auth_header(submitter),
            json={"query": "TITLE-ABS-KEY(x)", "wrapper": "scopus"},
        )
        assert response.status_code == 403
        assert stored_query_permissions(user_id=submitter.user_id) == []

    def test_no_subqueries_are_queued(
        self,
        client: TestClient,
        admin_user: User,
        fake_queue: FakeQueue,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        """
        KNOWN BUG: the background task dies on the detached ``Query`` instance.

        ``submit_query`` hands the ORM object to ``BackgroundTasks`` after closing the
        session it belongs to, so reading ``query.query_id`` in ``_queue_subqueries``
        raises ``DetachedInstanceError``. The endpoint still reports the new ID - and
        nothing is ever retrieved.
        """
        response = client.post(
            "/queries/",
            headers=auth_header(admin_user),
            json={"query": VALID_SCOPUS_QUERY, "wrapper": "scopus"},
            params={"py_start": 2019, "py_end": 2021, "py_steps": 1},
        )
        assert response.status_code == 200
        assert stored_query(response.json()) != {}
        assert fake_queue.queued == []

    def test_query_parameters_are_not_persisted(
        self,
        client: TestClient,
        admin_user: User,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        """Unlike ``/queries/test``, a ``params`` object here is dropped silently."""
        query_id = client.post(
            "/queries/",
            headers=auth_header(admin_user),
            json={
                "query": "TITLE-ABS-KEY(x)",
                "wrapper": "scopus",
                "params": {"view": "METADATA_AND_REF"},
            },
        ).json()
        assert stored_query(query_id)["params"] == {}

    def test_an_empty_query_text_is_accepted(
        self,
        client: TestClient,
        admin_user: User,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        """
        KNOWN BUG: nothing validates the query itself, so an empty one is registered and
        the year splitting produces subqueries that Scopus will reject.
        """
        query_id = client.post(
            "/queries/",
            headers=auth_header(admin_user),
            json={"query": "", "wrapper": "scopus"},
        ).json()
        assert stored_query(query_id)["query"] == ""

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"query": "x", "wrapper": "google-scholar"}, id="unknown-wrapper"),
            pytest.param({"wrapper": "scopus"}, id="missing-query"),
            pytest.param(None, id="no-body"),
        ],
    )
    def test_unusable_bodies_store_nothing(
        self,
        client: TestClient,
        admin_user: User,
        fake_queue: FakeQueue,
        stored_query_permissions: Callable[..., list[PermissionRow]],
        payload: dict[str, Any] | None,
    ) -> None:
        response = client.post(
            "/queries/",
            headers=auth_header(admin_user),
            **({} if payload is None else {"json": payload}),
        )
        assert response.status_code == 422
        assert stored_query_permissions(user_id=admin_user.user_id) == []
        assert fake_queue.queued == []


# --------------------------------------------------------------------------------------
# _queue_subqueries (the background task of POST /queries/)
# --------------------------------------------------------------------------------------


class TestQueueSubqueries:
    """
    ``academic_apis.api.queries._queue_subqueries``.

    Called directly here: going through ``POST /queries/`` cannot work at all because of
    the detached instance bug recorded in ``TestSubmitQuery``.
    """

    def test_a_query_without_filters_becomes_one_task(self, fake_queue: FakeQueue) -> None:
        _run(
            _queue_subqueries(
                transient_query("TITLE-ABS-KEY(  climate  )"),
                use_py_filter=False,
                use_firstauth_filter=False,
            )
        )
        assert fake_queue.queued == [
            {
                "query": "( TITLE-ABS-KEY( climate ) )",
                "wrapper": Wrapper.SCOPUS,
                "query_id": 42,
            }
        ]

    def test_the_author_filter_splits_the_query_676_ways(self, fake_queue: FakeQueue) -> None:
        _run(
            _queue_subqueries(
                transient_query("TITLE-ABS-KEY( climate )"),
                use_py_filter=False,
                use_firstauth_filter=True,
            )
        )
        assert len(fake_queue.queued) == 26 * 26
        assert fake_queue.queued[0]["query"] == (
            "( TITLE-ABS-KEY( climate ) ) AND FIRSTAUTH ( aa* )"
        )
        assert fake_queue.queued[-1]["query"] == (
            "( TITLE-ABS-KEY( climate ) ) AND FIRSTAUTH ( zz* )"
        )

    def test_the_publication_year_filter_creates_year_windows(
        self, fake_queue: FakeQueue
    ) -> None:
        _run(
            _queue_subqueries(
                transient_query("TITLE-ABS-KEY( climate )"),
                py_start=2019,
                py_end=2022,
                py_steps=1,
                use_py_filter=True,
                use_firstauth_filter=False,
            )
        )
        subqueries = [entry["query"] for entry in fake_queue.queued]
        assert len(subqueries) == 5
        assert subqueries[0].endswith("AND ( PUBYEAR BEF 2019 )")
        assert subqueries[-1].endswith("AND ( PUBYEAR AFT 2021 )")
        assert all("( PUBYEAR" in subquery for subquery in subqueries)

    def test_the_end_year_defaults_to_the_current_year(self, fake_queue: FakeQueue) -> None:
        this_year = datetime.now(tz=UTC).year
        _run(
            _queue_subqueries(
                transient_query("TITLE-ABS-KEY( climate )"),
                py_start=this_year - 2,
                py_steps=1,
                use_py_filter=True,
                use_firstauth_filter=False,
            )
        )
        assert len(fake_queue.queued) > 1
        assert fake_queue.queued[-1]["query"].endswith(
            f"AND ( PUBYEAR AFT {this_year - 1} )"
        )

    def test_every_task_carries_the_query_id_and_wrapper(self, fake_queue: FakeQueue) -> None:
        query = transient_query("TITLE-ABS-KEY( climate )", query_id=7)
        _run(
            _queue_subqueries(
                query, use_py_filter=False, use_firstauth_filter=True
            )
        )
        assert {entry["query_id"] for entry in fake_queue.queued} == {7}
        assert {entry["wrapper"] for entry in fake_queue.queued} == {Wrapper.SCOPUS}

    def test_an_inverted_year_range_fails(self, fake_queue: FakeQueue) -> None:
        with pytest.raises(AssertionError, match="strictly smaller"):
            _run(_queue_subqueries(transient_query(), py_start=2025, py_end=2020))
        assert fake_queue.queued == []


# --------------------------------------------------------------------------------------
# GET /queries/
# --------------------------------------------------------------------------------------


class TestListQueries:
    """``GET /queries/`` - everyone lists what they may see, newest first."""

    def test_an_admin_sees_queries_they_have_no_permission_for(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        owned_query: int,
    ) -> None:
        unpermitted = make_query()
        assert sorted(
            listed_ids(client.get("/queries/", headers=auth_header(admin_user)))
        ) == [owned_query, unpermitted]

    def test_queries_are_ordered_newest_first(
        self, client: TestClient, admin_user: User, make_query: Callable[..., int]
    ) -> None:
        now = datetime.now(tz=UTC).replace(tzinfo=None)
        oldest = make_query(query="old", created_at=now - timedelta(days=30))
        middle = make_query(query="middle", created_at=now - timedelta(days=10))
        newest = make_query(query="new", created_at=now)
        assert listed_ids(client.get("/queries/", headers=auth_header(admin_user))) == [
            newest,
            middle,
            oldest,
        ]

    def test_a_user_without_permissions_sees_nothing(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        outsider = make_user(email="outsider-list@example.org")
        make_query()
        assert listed_ids(client.get("/queries/", headers=auth_header(outsider))) == []

    def test_users_see_only_their_own_queries(
        self,
        client: TestClient,
        normal_user: User,
        owned_query: int,
        make_query: Callable[..., int],
        make_user: Callable[..., User],
    ) -> None:
        make_query(query="foreign", owner=make_user(email="someone-else@example.org"))
        assert listed_ids(client.get("/queries/", headers=auth_header(normal_user))) == [
            owned_query
        ]

    def test_a_permission_without_ownership_is_enough_to_list(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        member = make_user(email="member-list@example.org")
        owner = make_user(email="owner-list@example.org")
        query_id = make_query(owner=owner, users=[(member, False)])
        assert listed_ids(client.get("/queries/", headers=auth_header(member))) == [
            query_id
        ]

    def test_all_queries_of_a_user_are_listed(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        user = make_user(email="collector@example.org")
        owned = make_query(query="owned", owner=user)
        shared = make_query(query="shared", users=[(user, False)])
        make_query(query="foreign", owner=make_user(email="other-collector@example.org"))
        assert sorted(listed_ids(client.get("/queries/", headers=auth_header(user)))) == [
            owned,
            shared,
        ]

    def test_the_full_record_is_returned(
        self,
        client: TestClient,
        normal_user: User,
        make_query: Callable[..., int],
    ) -> None:
        created_at = datetime(2026, 1, 2, 3, 4, 5)
        query_id = make_query(
            query=VALID_SCOPUS_QUERY, owner=normal_user, created_at=created_at
        )
        (listed,) = client.get("/queries/", headers=auth_header(normal_user)).json()
        assert listed["query_id"] == query_id
        assert listed["query"] == VALID_SCOPUS_QUERY
        assert listed["wrapper"] == "scopus"
        assert listed["created_at"] == created_at.isoformat()
        assert listed["is_public"] is False
        assert listed["azure_link"] is None

    def test_the_list_carries_no_secrets(
        self, client: TestClient, normal_user: User, make_token: Callable[..., str]
    ) -> None:
        make_token(owner=normal_user, token="a-secret-api-key")
        response = client.get("/queries/", headers=auth_header(normal_user))
        assert "a-secret-api-key" not in response.text


# --------------------------------------------------------------------------------------
# DELETE /queries/
# --------------------------------------------------------------------------------------


class TestDeleteQuery:
    """``DELETE /queries/`` - admins wipe queries, their tasks and their files."""

    @pytest.mark.parametrize("role", ["owner", "member", "outsider"])
    def test_only_admins_may_delete(
        self,
        client: TestClient,
        role: str,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        actor = make_user(email=f"{role}-delete@example.org")
        query_id = (
            make_query(owner=actor)
            if role == "owner"
            else make_query(
                owner=make_user(email=f"owner-delete-{role}@example.org"),
                users=[(actor, role == "member")],
            )
        )
        response = client.delete(
            f"/queries/?query_id={query_id}", headers=auth_header(actor)
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )
        assert stored_query(query_id) != {}

    def test_an_admin_deletes_a_foreign_query(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        owned_query: int,
        stored_query: Callable[[int], dict[str, Any]],
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        response = client.delete(
            f"/queries/?query_id={owned_query}", headers=auth_header(admin_user)
        )
        assert response.status_code == 200
        assert response.json() is None
        assert stored_query(owned_query) == {}
        assert stored_query_permissions(query_id=owned_query) == []
        assert stored_query_permissions(user_id=normal_user.user_id) == []

    def test_the_tasks_of_the_query_are_deleted_too(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
        make_task: Callable[..., str],
        stored_task_ids: Callable[[int], list[str]],
    ) -> None:
        doomed = make_query()
        spared = make_query()
        make_task(query_id=doomed, subquery="first")
        make_task(query_id=doomed, subquery="second")
        spared_task = make_task(query_id=spared)

        client.delete(f"/queries/?query_id={doomed}", headers=auth_header(admin_user))

        assert stored_task_ids(doomed) == []
        assert stored_task_ids(spared) == [spared_task]

    def test_the_files_of_the_query_are_deleted_too(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
    ) -> None:
        query_id = make_query()
        responses = result_path(query_id, "responses")
        converted = result_path(query_id, "converted")
        responses.mkdir(parents=True)
        converted.mkdir(parents=True)
        (responses / "1.jsonl").write_text("{}\n")
        (converted / "1.parquet").write_text("binary")

        client.delete(f"/queries/?query_id={query_id}", headers=auth_header(admin_user))

        assert not responses.exists()
        assert not converted.exists()

    def test_other_queries_keep_their_files(
        self,
        client: TestClient,
        admin_user: User,
        make_query: Callable[..., int],
    ) -> None:
        doomed = make_query()
        spared = make_query()
        for query_id in (doomed, spared):
            responses = result_path(query_id, "responses")
            responses.mkdir(parents=True)
            (responses / "1.jsonl").write_text("{}\n")

        client.delete(f"/queries/?query_id={doomed}", headers=auth_header(admin_user))

        assert result_path(spared, "responses", "1.jsonl").is_file()

    def test_a_deleted_query_disappears_from_everyones_list(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        member = make_user(email="member-disappears@example.org")
        query_id = make_query(owner=normal_user, users=[(member, False)])
        assert listed_ids(client.get("/queries/", headers=auth_header(member))) == [
            query_id
        ]
        client.delete(f"/queries/?query_id={query_id}", headers=auth_header(admin_user))
        assert listed_ids(client.get("/queries/", headers=auth_header(member))) == []
        assert listed_ids(client.get("/queries/", headers=auth_header(normal_user))) == []
        assert listed_ids(client.get("/queries/", headers=auth_header(admin_user))) == []

    def test_an_unknown_query_id_is_accepted(
        self,
        client: TestClient,
        admin_user: User,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        """
        KNOWN BUG: the endpoint does not look at the database, so deleting what is not
        there is reported as a success.
        """
        response = client.delete(
            "/queries/?query_id=4242", headers=auth_header(admin_user)
        )
        assert response.status_code == 200
        assert stored_query(4242) == {}

    def test_deleting_an_unknown_query_leaves_a_directory_behind(
        self, client: TestClient, admin_user: User
    ) -> None:
        """
        KNOWN BUG: ``settings.responses_dir`` creates directories when called, so the
        cleanup of a query that never existed still creates its result folder.
        """
        assert not result_path(4243).exists()
        client.delete("/queries/?query_id=4243", headers=auth_header(admin_user))
        assert result_path(4243).is_dir()

    @pytest.mark.parametrize(
        ("query_id", "status_code"),
        [
            pytest.param("not-a-number", 422, id="not-a-number"),
            pytest.param("12.5", 422, id="float"),
            pytest.param("", 422, id="empty"),
        ],
    )
    def test_the_query_id_has_to_be_a_number(
        self, client: TestClient, admin_user: User, query_id: str, status_code: int
    ) -> None:
        response = client.delete(
            f"/queries/?query_id={query_id}", headers=auth_header(admin_user)
        )
        assert response.status_code == status_code

    def test_the_query_id_has_to_be_specified(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.delete("/queries/", headers=auth_header(admin_user))
        assert response.status_code == 422


# --------------------------------------------------------------------------------------
# DELETE /queries/logs
# --------------------------------------------------------------------------------------


class TestDeleteQueryLogs:
    """
    ``DELETE /queries/logs`` - owners throw away log and error files.

    Despite its parameter name (``current_admin``) the endpoint only requires a login
    plus ownership of the query.
    """

    @staticmethod
    def _write_logs(query_id: int) -> None:
        responses = result_path(query_id, "responses")
        responses.mkdir(parents=True, exist_ok=True)
        for name in ("4711.log", "4711.err", "4712.log", "4711.jsonl", "notes.txt"):
            (responses / name).write_text("content")

    def test_an_owner_deletes_logs_and_error_files(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        self._write_logs(owned_query)
        response = client.delete(
            "/queries/logs",
            headers=auth_header(normal_user),
            params={"query_id": owned_query},
        )
        assert response.status_code == 200
        assert sorted(
            path.name for path in result_path(owned_query, "responses").iterdir()
        ) == ["4711.jsonl", "notes.txt"]

    def test_the_converted_files_are_kept(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        self._write_logs(owned_query)
        converted = result_path(owned_query, "converted")
        converted.mkdir(parents=True, exist_ok=True)
        (converted / "1.parquet").write_text("binary")
        client.delete(
            "/queries/logs",
            headers=auth_header(normal_user),
            params={"query_id": owned_query},
        )
        assert [path.name for path in converted.iterdir()] == ["1.parquet"]

    def test_the_logs_of_other_queries_are_kept(
        self,
        client: TestClient,
        normal_user: User,
        owned_query: int,
        make_query: Callable[..., int],
    ) -> None:
        self._write_logs(owned_query)
        other = make_query()
        self._write_logs(other)
        client.delete(
            "/queries/logs",
            headers=auth_header(normal_user),
            params={"query_id": owned_query},
        )
        assert sorted(path.name for path in result_path(other, "responses").iterdir()) == [
            "4711.err",
            "4711.jsonl",
            "4711.log",
            "4712.log",
            "notes.txt",
        ]

    def test_a_user_without_the_permission_cannot_delete_logs(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        outsider = make_user(email="outsider-logs@example.org")
        query_id = make_query(owner=make_user(email="owner-logs@example.org"))
        self._write_logs(query_id)
        response = client.delete(
            "/queries/logs", headers=auth_header(outsider), params={"query_id": query_id}
        )
        assert response.status_code == 403
        assert response.json()["detail"] == OWNER_ONLY_DETAIL
        assert (result_path(query_id, "responses") / "4711.log").exists()

    def test_a_member_without_ownership_cannot_delete_logs(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        member = make_user(email="member-logs@example.org")
        query_id = make_query(
            owner=make_user(email="owner-2-logs@example.org"), users=[(member, False)]
        )
        self._write_logs(query_id)
        assert (
            client.delete(
                "/queries/logs",
                headers=auth_header(member),
                params={"query_id": query_id},
            ).status_code
            == 403
        )
        assert (result_path(query_id, "responses") / "4712.log").exists()

    def test_an_admin_needs_no_permission_for_the_query(
        self, client: TestClient, admin_user: User, make_query: Callable[..., int]
    ) -> None:
        query_id = make_query()
        self._write_logs(query_id)
        assert (
            client.delete(
                "/queries/logs",
                headers=auth_header(admin_user),
                params={"query_id": query_id},
            ).status_code
            == 200
        )
        assert not (result_path(query_id, "responses") / "4711.err").exists()

    def test_a_query_without_logs_is_harmless(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        response = client.delete(
            "/queries/logs",
            headers=auth_header(normal_user),
            params={"query_id": owned_query},
        )
        assert response.status_code == 200

    def test_an_unknown_query_id_is_rejected_for_non_admins(
        self, client: TestClient, normal_user: User
    ) -> None:
        assert (
            client.delete(
                "/queries/logs",
                headers=auth_header(normal_user),
                params={"query_id": 4242},
            ).status_code
            == 403
        )

    def test_the_query_id_has_to_be_specified(
        self, client: TestClient, normal_user: User
    ) -> None:
        assert (
            client.delete("/queries/logs", headers=auth_header(normal_user)).status_code
            == 422
        )


# --------------------------------------------------------------------------------------
# PUT /queries/permission
# --------------------------------------------------------------------------------------


class TestUpsertPermission:
    """``PUT /queries/permission`` - owners share their queries, admins share all."""

    def test_the_owner_grants_access_to_another_user(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        response = client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query),
        )
        assert response.status_code == 200
        assert sorted(permission_rows(response)) == sorted(
            [
                permission(normal_user, owned_query, True),
                permission(other_user, owned_query, False),
            ]
        )
        assert sorted(stored_query_permissions(query_id=owned_query)) == sorted(
            [
                permission(normal_user, owned_query, True),
                permission(other_user, owned_query, False),
            ]
        )

    def test_a_granted_user_can_now_list_the_query(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
    ) -> None:
        assert listed_ids(client.get("/queries/", headers=auth_header(other_user))) == []
        client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query),
        )
        assert listed_ids(client.get("/queries/", headers=auth_header(other_user))) == [
            owned_query
        ]

    def test_an_existing_permission_is_updated_not_duplicated(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        for is_owner in (False, False, True):
            client.put(
                "/queries/permission",
                headers=auth_header(normal_user),
                json=permission_body(other_user, owned_query, is_owner=is_owner),
            )
        assert stored_query_permissions(query_id=owned_query) == [
            permission(normal_user, owned_query, True),
            permission(other_user, owned_query, True),
        ]

    def test_the_owner_may_pass_ownership_on(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query, is_owner=True),
        )
        response = client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(normal_user, owned_query, is_owner=False),
        )
        assert response.status_code == 200
        assert sorted(stored_query_permissions(query_id=owned_query)) == sorted(
            [
                permission(normal_user, owned_query, False),
                permission(other_user, owned_query, True),
            ]
        )
        # losing ownership also takes away the right to manage permissions
        assert (
            client.put(
                "/queries/permission",
                headers=auth_header(normal_user),
                json=permission_body(other_user, owned_query),
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/queries/permission",
                headers=auth_header(other_user),
                params={"query_id": owned_query},
            ).status_code
            == 200
        )

    def test_an_admin_grants_access_to_a_query_they_do_not_own(
        self,
        client: TestClient,
        admin_user: User,
        other_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        response = client.put(
            "/queries/permission",
            headers=auth_header(admin_user),
            json=permission_body(other_user, owned_query),
        )
        assert response.status_code == 200
        # the admin did not invent a permission for themselves
        assert stored_query_permissions(user_id=admin_user.user_id) == []
        assert permission(other_user, owned_query, False) in stored_query_permissions(
            query_id=owned_query
        )

    def test_a_user_without_ownership_cannot_grant_anything(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        outsider = make_user(email="outsider-grant@example.org")
        query_id = make_query(owner=make_user(email="owner-grant@example.org"))
        response = client.put(
            "/queries/permission",
            headers=auth_header(outsider),
            json=permission_body(outsider, query_id, is_owner=True),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == OWNER_ONLY_DETAIL
        assert stored_query_permissions(query_id=query_id) == []

    def test_a_member_cannot_promote_themselves(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        member = make_user(email="self-promoter@example.org")
        query_id = make_query(
            owner=make_user(email="real-owner@example.org"), users=[(member, False)]
        )
        assert (
            client.put(
                "/queries/permission",
                headers=auth_header(member),
                json=permission_body(member, query_id, is_owner=True),
            ).status_code
            == 403
        )
        assert stored_query_permissions(user_id=member.user_id) == [
            permission(member, query_id, False)
        ]

    def test_an_unknown_query_is_not_found(
        self, client: TestClient, normal_user: User, other_user: User
    ) -> None:
        response = client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, 4242),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "No query found with query_id=4242"

    def test_an_unknown_user_is_not_found(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        response = client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body("ghost-user", owned_query),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "No user found with user_id=ghost-user"

    def test_the_existence_checks_come_before_the_ownership_check(
        self, client: TestClient, normal_user: User
    ) -> None:
        """
        An unknown target is reported as 404 even without any permission (and the query
        is checked before the user), which reveals which query and user IDs exist.
        """
        response = client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body("ghost-user", 4242),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "No query found with query_id=4242"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"query_id": 1, "is_owner": False}, id="missing-user"),
            pytest.param({"user_id": "whoever", "is_owner": False}, id="missing-query"),
            pytest.param(
                {"user_id": "whoever", "query_id": "not-a-number"},
                id="query-not-a-number",
            ),
        ],
    )
    def test_incomplete_bodies_are_unprocessable(
        self,
        client: TestClient,
        normal_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
        body: dict[str, Any],
    ) -> None:
        assert (
            client.put(
                "/queries/permission", headers=auth_header(normal_user), json=body
            ).status_code
            == 422
        )
        assert stored_query_permissions(query_id=owned_query) == [
            permission(normal_user, owned_query, True)
        ]

    def test_a_body_without_the_is_owner_flag_grants_read_access(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json={"user_id": other_user.user_id, "query_id": owned_query},
        )
        assert permission(other_user, owned_query, False) in stored_query_permissions(
            query_id=owned_query
        )


# --------------------------------------------------------------------------------------
# DELETE /queries/permission
# --------------------------------------------------------------------------------------


class TestDropPermission:
    """``DELETE /queries/permission`` - taking access away again."""

    def test_the_owner_revokes_a_permission(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query),
        )
        response = client.request(
            "DELETE",
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query),
        )
        assert response.status_code == 200
        assert permission_rows(response) == [permission(normal_user, owned_query, True)]
        assert stored_query_permissions(query_id=owned_query) == [
            permission(normal_user, owned_query, True)
        ]

    def test_a_revoked_user_cannot_list_the_query_anymore(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
    ) -> None:
        client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query),
        )
        assert listed_ids(client.get("/queries/", headers=auth_header(other_user))) == [
            owned_query
        ]
        client.request(
            "DELETE",
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query),
        )
        assert listed_ids(client.get("/queries/", headers=auth_header(other_user))) == []

    def test_an_admin_revokes_a_permission_of_a_foreign_query(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        response = client.request(
            "DELETE",
            "/queries/permission",
            headers=auth_header(admin_user),
            json=permission_body(normal_user, owned_query),
        )
        assert response.status_code == 200
        assert response.json() == []
        assert stored_query_permissions(query_id=owned_query) == []

    def test_the_owner_may_revoke_their_own_ownership(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        client.put(
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query, is_owner=True),
        )
        client.request(
            "DELETE",
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(normal_user, owned_query),
        )
        assert stored_query_permissions(query_id=owned_query) == [
            permission(other_user, owned_query, True)
        ]

    def test_a_user_without_ownership_cannot_revoke(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
        stored_query_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        member = make_user(email="member-revoke@example.org")
        victim = make_user(email="victim-revoke@example.org")
        query_id = make_query(
            owner=make_user(email="owner-revoke@example.org"),
            users=[(member, False), (victim, False)],
        )
        response = client.request(
            "DELETE",
            "/queries/permission",
            headers=auth_header(member),
            json=permission_body(victim, query_id),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == OWNER_ONLY_DETAIL
        assert len(stored_query_permissions(query_id=query_id)) == 3

    def test_an_unknown_permission_is_not_found(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_query: int,
    ) -> None:
        response = client.request(
            "DELETE",
            "/queries/permission",
            headers=auth_header(normal_user),
            json=permission_body(other_user, owned_query),
        )
        assert response.status_code == 404
        assert response.json()["detail"] == (
            f"No permission found for user_id={other_user.user_id} "
            f"on query_id={owned_query}"
        )

    def test_the_permission_of_an_unknown_user_simply_does_not_exist(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        """Unlike ``PUT``, ``DELETE`` does not check that the user exists at all."""
        assert (
            client.request(
                "DELETE",
                "/queries/permission",
                headers=auth_header(normal_user),
                json=permission_body("ghost-user", owned_query),
            ).status_code
            == 404
        )

    def test_a_user_without_ownership_gets_a_permission_error_not_404(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        """The ownership check runs first, so outsiders cannot probe permission rows."""
        outsider = make_user(email="outsider-drop@example.org")
        query_id = make_query(owner=make_user(email="owner-drop@example.org"))
        assert (
            client.request(
                "DELETE",
                "/queries/permission",
                headers=auth_header(outsider),
                json=permission_body(outsider, query_id),
            ).status_code
            == 403
        )

    def test_the_body_has_to_name_both_user_and_query(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        assert (
            client.request(
                "DELETE",
                "/queries/permission",
                headers=auth_header(normal_user),
                json={"user_id": normal_user.user_id},
            ).status_code
            == 422
        )


# --------------------------------------------------------------------------------------
# GET /queries/permission
# --------------------------------------------------------------------------------------


class TestGetPermissions:
    """``GET /queries/permission`` - owners and admins read the permission list."""

    def test_the_owner_sees_every_permission_of_their_query(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        owner = make_user(email="owner-read@example.org")
        member = make_user(email="member-read@example.org")
        query_id = make_query(owner=owner, users=[(member, False)])
        response = client.get(
            "/queries/permission",
            headers=auth_header(owner),
            params={"query_id": query_id},
        )
        assert response.status_code == 200
        assert sorted(permission_rows(response)) == sorted(
            [
                permission(owner, query_id, True),
                permission(member, query_id, False),
            ]
        )

    def test_an_admin_sees_permissions_without_having_one(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        query_id = make_query(owner=make_user(email="owner-read-2@example.org"))
        response = client.get(
            "/queries/permission",
            headers=auth_header(admin_user),
            params={"query_id": query_id},
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_a_granted_user_may_not_read_the_permissions(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        """
        KNOWN BUG: reading is restricted to owners, so a user who was granted access
        cannot tell who else has it - unlike ``GET /tokens/permission``, which reports a
        caller's own permissions to everybody.
        """
        member = make_user(email="member-read-3@example.org")
        query_id = make_query(
            owner=make_user(email="owner-read-3@example.org"), users=[(member, False)]
        )
        response = client.get(
            "/queries/permission",
            headers=auth_header(member),
            params={"query_id": query_id},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == OWNER_ONLY_DETAIL

    def test_a_query_without_permissions_reads_as_empty_for_admins(
        self, client: TestClient, admin_user: User, make_query: Callable[..., int]
    ) -> None:
        query_id = make_query()
        assert (
            client.get(
                "/queries/permission",
                headers=auth_header(admin_user),
                params={"query_id": query_id},
            ).json()
            == []
        )

    def test_an_unknown_query_is_not_reported_as_missing(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        """
        KNOWN BUG: the ownership check cannot tell a missing query from an unpermitted
        one, so admins get an empty list and everybody else a 403.
        """
        assert (
            client.get(
                "/queries/permission",
                headers=auth_header(admin_user),
                params={"query_id": 4242},
            ).json()
            == []
        )
        assert (
            client.get(
                "/queries/permission",
                headers=auth_header(normal_user),
                params={"query_id": 4242},
            ).status_code
            == 403
        )

    def test_the_query_id_has_to_be_specified(
        self, client: TestClient, normal_user: User
    ) -> None:
        assert (
            client.get("/queries/permission", headers=auth_header(normal_user)).status_code
            == 422
        )


# --------------------------------------------------------------------------------------
# GET /queries/download
# --------------------------------------------------------------------------------------


class TestDownloadQuery:
    """``GET /queries/download`` - admins hand out Azure links and trigger the upload."""

    def test_a_plain_user_is_refused(
        self, client: TestClient, normal_user: User, owned_query: int
    ) -> None:
        response = client.get(
            "/queries/download",
            headers=auth_header(normal_user),
            params={"query_id": owned_query},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )

    def test_an_admin_gets_the_links_of_a_query(
        self,
        client: TestClient,
        admin_user: User,
        owned_query: int,
        fake_azure: FakeAzure,
    ) -> None:
        response = client.get(
            "/queries/download",
            headers=auth_header(admin_user),
            params={"query_id": owned_query},
        )
        assert response.status_code == 200
        assert response.json() == expected_links(owned_query)
        assert fake_azure.link_requests == [blob(owned_query)]

    def test_the_links_are_stored_on_the_query(
        self,
        client: TestClient,
        admin_user: User,
        owned_query: int,
        fake_azure: FakeAzure,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        client.get(
            "/queries/download",
            headers=auth_header(admin_user),
            params={"query_id": owned_query},
        )
        assert stored_query(owned_query)["azure_link"] == expected_links(owned_query)

    def test_the_whole_result_directory_is_synced(
        self,
        client: TestClient,
        admin_user: User,
        owned_query: int,
        fake_azure: FakeAzure,
    ) -> None:
        client.get(
            "/queries/download",
            headers=auth_header(admin_user),
            params={"query_id": owned_query},
        )
        assert fake_azure.syncs == [
            (str(settings.result_dir / str(owned_query)), blob(owned_query))
        ]
        assert fake_azure.uploads == []

    def test_concatenated_uploads_three_files(
        self,
        client: TestClient,
        admin_user: User,
        owned_query: int,
        fake_azure: FakeAzure,
    ) -> None:
        response = client.get(
            "/queries/download",
            headers=auth_header(admin_user),
            params={"query_id": owned_query, "concatenated": True},
        )
        assert response.status_code == 200
        prefix = f"{settings.result_dir}/{owned_query}/responses"
        assert fake_azure.uploads == [
            (f"{prefix}/*.jsonl", f"{blob(owned_query)}/responses.jsonl", False),
            (f"{prefix}/*.err", f"{blob(owned_query)}/responses.err", True),
            (f"{prefix}/*.log", f"{blob(owned_query)}/responses.log", True),
        ]
        # the plain directory sync is not scheduled on top of the concatenation
        assert fake_azure.syncs == []

    def test_a_query_of_another_user_is_downloaded_too(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
    ) -> None:
        query_id = make_query(owner=make_user(email="owner-download@example.org"))
        assert (
            client.get(
                "/queries/download",
                headers=auth_header(admin_user),
                params={"query_id": query_id},
            ).status_code
            == 200
        )
        assert fake_azure.link_requests == [blob(query_id)]

    def test_an_unknown_query_is_not_reported_as_missing(
        self,
        client: TestClient,
        admin_user: User,
        fake_azure: FakeAzure,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        """
        KNOWN BUG: links are generated for any ID and simply not stored, because
        ``update_azure_link`` silently skips a missing ``Query`` row.
        """
        response = client.get(
            "/queries/download",
            headers=auth_header(admin_user),
            params={"query_id": 4242},
        )
        assert response.status_code == 200
        assert response.json() == expected_links(4242)
        assert stored_query(4242) == {}
        assert fake_azure.link_requests == ["4242"]

    def test_an_anonymous_caller_is_refused(
        self, client: TestClient, owned_query: int
    ) -> None:
        assert (
            client.get("/queries/download", params={"query_id": owned_query}).status_code
            == 401
        )

    def test_the_query_id_has_to_be_specified(
        self, client: TestClient, admin_user: User
    ) -> None:
        assert (
            client.get("/queries/download", headers=auth_header(admin_user)).status_code
            == 422
        )


# --------------------------------------------------------------------------------------
# GET /queries/download/refresh
# --------------------------------------------------------------------------------------


class TestRefreshDownloadLinks:
    """``GET /queries/download/refresh`` - owners ask Azure for valid links again."""

    def test_the_owner_gets_fresh_links(
        self,
        client: TestClient,
        normal_user: User,
        owned_query: int,
        fake_azure: FakeAzure,
    ) -> None:
        response = client.get(
            "/queries/download/refresh",
            headers=auth_header(normal_user),
            params={"query_id": owned_query},
        )
        assert response.status_code == 200
        assert response.json() == expected_links(owned_query, version=1)
        # refreshing only asks for links, it does not upload anything
        assert fake_azure.calls == [("links", blob(owned_query))]

    def test_refreshing_updates_the_stored_links(
        self,
        client: TestClient,
        normal_user: User,
        owned_query: int,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        for version in (1, 2):
            response = client.get(
                "/queries/download/refresh",
                headers=auth_header(normal_user),
                params={"query_id": owned_query},
            )
            assert response.json() == expected_links(owned_query, version=version)
        assert stored_query(owned_query)["azure_link"] == expected_links(
            owned_query, version=2
        )

    def test_an_admin_may_refresh_too(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
    ) -> None:
        query_id = make_query(owner=make_user(email="owner-refresh@example.org"))
        assert (
            client.get(
                "/queries/download/refresh",
                headers=auth_header(admin_user),
                params={"query_id": query_id},
            ).status_code
            == 200
        )

    def test_a_user_without_the_permission_is_refused(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_query: Callable[..., int],
        fake_azure: FakeAzure,
    ) -> None:
        outsider = make_user(email="outsider-refresh@example.org")
        query_id = make_query(owner=make_user(email="owner-refresh-2@example.org"))
        response = client.get(
            "/queries/download/refresh",
            headers=auth_header(outsider),
            params={"query_id": query_id},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == OWNER_ONLY_DETAIL
        assert fake_azure.link_requests == []

    def test_an_unknown_query_still_produces_links(
        self,
        client: TestClient,
        admin_user: User,
        stored_query: Callable[[int], dict[str, Any]],
    ) -> None:
        """KNOWN BUG: same as ``GET /queries/download`` - no existence check."""
        response = client.get(
            "/queries/download/refresh",
            headers=auth_header(admin_user),
            params={"query_id": 4242},
        )
        assert response.status_code == 200
        assert response.json() == expected_links(4242)
        assert stored_query(4242) == {}

    def test_the_query_id_has_to_be_specified(
        self, client: TestClient, normal_user: User
    ) -> None:
        assert (
            client.get(
                "/queries/download/refresh", headers=auth_header(normal_user)
            ).status_code
            == 422
        )
