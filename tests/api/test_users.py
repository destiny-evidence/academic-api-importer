"""
Tests for the ``/users`` endpoints: logging in and out, profile visibility and
who may create or change user records.

The rules covered here:

=====================  ===========  ==============  ==============  =================
                     anonymous     user            inactive user   admin
=====================  ===========  ==============  ==============  =================
``POST /users/login``  open         token           403             -
``POST /users/logout`` 401          204             403             204
``GET /users/me``      401          own record      403             own record
``GET /users/``        401          403             403             all users
``GET /users/base``    401          all base data   403             all base data
``POST /users/``       401          403             403             create, 201
``PUT  /users/``       401          own record      403             any record
=====================  ===========  ==============  ==============  =================

Regular users keep control of their profile (``full_name``, ``institution``,
``email``, password), but may never change ``is_active`` or the permission
flags - not even about themselves. Admins may change everything, including
other users' privileged fields.

Endpoints that behave differently from what their docstrings promise are covered by
tests marked ``KNOWN BUG``; those assert today's actual responses and have to be
updated together with the endpoints.

Coverage of the endpoints in src/academic_apis/api/users.py:
- Authentication (shared): anonymous → 401, inactive users → 403, malformed/forged/wrong-secret JWTs, well-signed tokens for unknown users, cookie auth accepted like a bearer token, and admin rights cannot be forged in the JWT (verified against the database).
- POST /users/login: token + claims returned, session cookie set with the right attributes (HttpOnly, SameSite=lax, Path=/, Max-Age), wrong password and unknown email give the identical 401 (no account enumeration), inactive user → 403 but only when the password matches (password checked first), 422s for malformed bodies, no password/hash leakage.
- POST /users/logout: 204 with empty body, cookie cleared with matching attributes, works via bearer too, and documents that the JWT itself remains valid after logout.
- GET /users/me: exact own record, public fields only, no password hash.
- GET /users/: admins see everybody (incl. inactive) sorted by email; non-admins → 403.
- GET /users/base: every logged-in user sees all minimal records, sorted by full name, containing only user_id/full_name/institution (no email or hash).
- POST /users/: admin-only creation, 201, hashed password (verify_password round-trip), duplicate email → 409, client-chosen user_id ignored, permissions granted at creation work immediately, 422s for incomplete payloads.
- PUT /users/: self-service profile edits, 403 for foreign users (before the 404 lookup), privileged fields (is_active, permission_admin, permission_submit_query) blocked for non-admins with the whole update rolled back, unchanged privileged fields may be resent, admin promotion/deactivation/reactivation/demotion with immediate DB-driven effect, email changes incl. 409 conflicts, password changes (old stops working), and no password leakage in responses.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from academic_apis.api.utils.auth import create_access_token, verify_password
from academic_apis.db import User, db_session
from academic_apis.utils import settings

if TYPE_CHECKING:
    from httpx2 import Response  # the project pins `httpx2`, which `TestClient` returns

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

#: The password of every user created through the ``make_user`` fixtures.
DEFAULT_PASSWORD = "not-so-secret-password"

#: ``(method, path, json body)`` for every endpoint that requires a login.
AUTH_ENDPOINTS: dict[str, tuple[str, str, Any]] = {
    "logout": ("POST", "/users/logout", None),
    "me": ("GET", "/users/me", None),
    "list": ("GET", "/users/", None),
    "base": ("GET", "/users/base", None),
    "create": (
        "POST",
        "/users/",
        {
            "email": "someone@example.org",
            "full_name": "Someone New",
            "institution": "Nowhere University",
            "password_plain": "a-secret-password",
        },
    ),
    "update": ("PUT", "/users/", {"user_id": "whoever", "full_name": "Hacked"}),
}

ALL_AUTH_ENDPOINTS = pytest.mark.parametrize(
    "endpoint", AUTH_ENDPOINTS.values(), ids=AUTH_ENDPOINTS
)

#: The public fields that ``UserPublic`` exposes for every user.
PUBLIC_FIELDS = {
    "user_id",
    "full_name",
    "institution",
    "email",
    "is_active",
    "permission_admin",
    "permission_submit_query",
}


def auth_header(user: User) -> dict[str, str]:
    """Bearer header that logs the request in as ``user``."""
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def forged_admin_header(user: User) -> dict[str, str]:
    """Header signed with the right secret that falsely claims ``permission_admin``."""
    token = jwt.encode(
        {
            "sub": user.user_id,
            "email": user.email,
            "permission_admin": True,
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


def stored_user(user_id: str) -> dict[str, Any]:
    """Read a user straight from the database (an empty dict if it is gone)."""

    async def read() -> dict[str, Any]:
        async with db_session() as session:
            user = session.get(User, user_id)
            if user is None:
                return {}
            return {
                "user_id": user.user_id,
                "email": user.email,
                "full_name": user.full_name,
                "institution": user.institution,
                "is_active": user.is_active,
                "permission_admin": user.permission_admin,
                "permission_submit_query": user.permission_submit_query,
                "password_hashed": user.password_hashed,
            }

    return asyncio.run(read())


def all_emails() -> list[str]:
    """Every email in the database, sorted."""

    async def read() -> list[str]:
        async with db_session() as session:
            return sorted(user.email for user in session.exec(select(User)).all())

    return asyncio.run(read())


def login(
    client: TestClient,
    email: str,
    password: str = DEFAULT_PASSWORD,
) -> Response:
    """``POST /users/login`` for the given credentials."""
    return client.post("/users/login", json={"email": email, "password": password})


def create_payload(**overrides: object) -> dict[str, Any]:
    """Minimal valid body for ``POST /users/``; a ``None`` override removes a field."""
    body: dict[str, Any] = {
        "email": "newcomer@example.org",
        "full_name": "Nora Newcomer",
        "institution": "New University",
        "password_plain": "a-secret-password",
    }
    for key, value in overrides.items():
        if value is None:
            body.pop(key, None)
        else:
            body[key] = value
    return body


# --------------------------------------------------------------------------------------
# authentication (shared by all endpoints except /users/login)
# --------------------------------------------------------------------------------------


class TestAuthentication:
    """All endpoints except the login itself require a valid login."""

    @ALL_AUTH_ENDPOINTS
    def test_anonymous_requests_are_rejected(
        self, client: TestClient, endpoint: tuple[str, str, Any]
    ) -> None:
        method, path, body = endpoint
        response = client.request(
            method, path, **({} if body is None else {"json": body})
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Not authenticated"
        assert response.headers["www-authenticate"] == "Bearer"

    @ALL_AUTH_ENDPOINTS
    def test_inactive_users_are_rejected(
        self,
        client: TestClient,
        endpoint: tuple[str, str, Any],
        inactive_user: User,
    ) -> None:
        method, path, body = endpoint
        kwargs: dict[str, Any] = {"headers": auth_header(inactive_user)}
        if body is not None:
            kwargs["json"] = body
        response = client.request(method, path, **kwargs)
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
    @ALL_AUTH_ENDPOINTS
    def test_invalid_credentials_are_rejected(
        self,
        client: TestClient,
        endpoint: tuple[str, str, Any],
        header: dict[str, str],
    ) -> None:
        method, path, _ = endpoint
        assert client.request(method, path, headers=header).status_code == 401

    def test_a_token_signed_with_the_wrong_secret_is_rejected(
        self, client: TestClient, normal_user: User
    ) -> None:
        token = jwt.encode(
            {"sub": normal_user.user_id},
            "wrong-secret-key-which-is-also-long-enough",
        )
        response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Could not validate credentials"

    def test_well_signed_token_of_unknown_user_is_rejected(
        self, client: TestClient
    ) -> None:
        # Correctly signed, but this user does not exist in the database.
        token = jwt.encode(
            {"sub": "ghost-user", "email": "ghost@example.org"},
            settings.secret_key,
            algorithm=settings.jwt_algorithm,
        )
        response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["detail"] == "User not found"

    def test_admin_rights_cannot_be_forged_in_a_json_web_token(
        self, client: TestClient, normal_user: User
    ) -> None:
        # The header claims `permission_admin`, but rights are read from the database.
        response = client.get("/users/", headers=forged_admin_header(normal_user))
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )

    def test_cookie_login_is_accepted_like_a_bearer_token(
        self, client: TestClient, normal_user: User
    ) -> None:
        client.cookies.set(settings.cookie_name, create_access_token(normal_user))
        response = client.get("/users/me")
        assert response.status_code == 200
        assert response.json()["email"] == normal_user.email


# --------------------------------------------------------------------------------------
# POST /users/login
# --------------------------------------------------------------------------------------


class TestLogin:
    """``POST /users/login`` - password check and session cookie."""

    def test_correct_credentials_return_a_token(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = login(client, normal_user.email)
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"access_token", "token_type"}
        assert body["token_type"] == "bearer"
        assert body["access_token"]

    def test_the_returned_token_authenticates_the_user(
        self, client: TestClient, normal_user: User
    ) -> None:
        token = login(client, normal_user.email).json()["access_token"]
        client.cookies.clear()  # make sure the answer comes from the header
        response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["user_id"] == normal_user.user_id

    def test_the_token_carries_the_user_identity(
        self, client: TestClient, admin_user: User
    ) -> None:
        token = login(client, admin_user.email).json()["access_token"]
        claims = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        assert claims["sub"] == admin_user.user_id
        assert claims["email"] == admin_user.email
        assert claims["permission_admin"] is True

    def test_login_sets_a_session_cookie(
        self, client: TestClient, normal_user: User
    ) -> None:
        cookie = login(client, normal_user.email).headers["set-cookie"]
        assert cookie.startswith(f"{settings.cookie_name}=")
        lowered = cookie.lower()
        assert "httponly" in lowered
        assert "samesite=lax" in lowered
        assert "path=/" in lowered
        assert f"max-age={settings.cookie_lifetime * 60}" in lowered

    def test_the_cookie_authenticates_subsequent_requests(
        self, client: TestClient, normal_user: User
    ) -> None:
        login(client, normal_user.email)  # the TestClient keeps the returned cookie
        response = client.get("/users/me")
        assert response.status_code == 200
        assert response.json()["user_id"] == normal_user.user_id

    def test_wrong_password_is_unauthorized(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = login(client, normal_user.email, password="wrong-password")
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email or password"

    def test_an_unknown_email_gets_the_very_same_error(
        self, client: TestClient
    ) -> None:
        """The responses are identical on purpose, so that emails cannot be probed."""
        response = login(client, "nobody@example.org", password="whatever")
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email or password"

    def test_inactive_users_may_not_log_in(
        self, client: TestClient, inactive_user: User
    ) -> None:
        response = login(client, inactive_user.email)
        assert response.status_code == 403
        assert response.json()["detail"] == "Inactive user"

    def test_a_wrong_password_on_an_inactive_user_still_reports_401(
        self, client: TestClient, inactive_user: User
    ) -> None:
        # The password is checked first, so a failed login does not reveal that
        # the account exists (in contrast to the correct password -> 403).
        assert login(client, inactive_user.email, password="wrong").status_code == 401

    def test_an_admin_may_log_in_and_use_admin_endpoints(
        self, client: TestClient, admin_user: User
    ) -> None:
        login(client, admin_user.email)  # keeps the cookie in the client
        assert client.get("/users/").status_code == 200

    def test_the_password_or_its_hash_never_appear_in_the_response(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = login(client, normal_user.email)
        assert "argon2" not in response.text
        assert DEFAULT_PASSWORD not in response.text

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {"email": "not-an-email", "password": "x"}, id="invalid-email"
            ),
            pytest.param({"email": "a@example.org"}, id="missing-password"),
            pytest.param({"password": "x"}, id="missing-email"),
            pytest.param({}, id="empty-body"),
        ],
    )
    def test_malformed_login_requests_are_unprocessable(
        self, client: TestClient, payload: dict[str, Any]
    ) -> None:
        assert client.post("/users/login", json=payload).status_code == 422


# --------------------------------------------------------------------------------------
# POST /users/logout
# --------------------------------------------------------------------------------------


class TestLogout:
    """``POST /users/logout`` - removes the session cookie."""

    def test_logging_out_clears_the_cookie(
        self, client: TestClient, normal_user: User
    ) -> None:
        login(client, normal_user.email)
        response = client.post("/users/logout")
        assert response.status_code == 204
        assert response.content == b""
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{settings.cookie_name}=")
        assert "max-age=0" in cookie.lower()

    def test_the_cookie_attributes_match_the_login_cookie(
        self, client: TestClient, normal_user: User
    ) -> None:
        # A mismatch (e.g. in `path` or `samesite`) would leave the real cookie alone.
        login(client, normal_user.email)
        logout_cookie = client.post("/users/logout").headers["set-cookie"].lower()
        assert "httponly" in logout_cookie
        assert "samesite=lax" in logout_cookie
        assert "path=/" in logout_cookie

    def test_logout_works_with_a_bearer_token_too(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.post("/users/logout", headers=auth_header(normal_user))
        assert response.status_code == 204

    def test_a_logged_out_token_still_works_until_it_expires(
        self, client: TestClient, normal_user: User
    ) -> None:
        """Logout only clears the browser cookie; the JWT itself stays valid."""
        token = login(client, normal_user.email).json()["access_token"]
        client.post("/users/logout")
        client.cookies.clear()
        response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200


# --------------------------------------------------------------------------------------
# GET /users/me
# --------------------------------------------------------------------------------------


class TestMyInfo:
    """``GET /users/me`` - the caller's own record."""

    def test_a_user_sees_their_own_record(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.get("/users/me", headers=auth_header(normal_user))
        assert response.status_code == 200
        assert response.json() == {
            "user_id": normal_user.user_id,
            "email": normal_user.email,
            "full_name": "Norman User",
            "institution": "Test Institution",
            "is_active": True,
            "permission_admin": False,
            "permission_submit_query": False,
        }

    def test_the_password_hash_is_never_exposed(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get("/users/me", headers=auth_header(admin_user))
        assert set(response.json()) == PUBLIC_FIELDS
        assert "argon2" not in response.text

    def test_admins_get_their_own_record_with_their_flags(
        self, client: TestClient, admin_user: User
    ) -> None:
        body = client.get("/users/me", headers=auth_header(admin_user)).json()
        assert body["user_id"] == admin_user.user_id
        assert body["permission_admin"] is True


# --------------------------------------------------------------------------------------
# GET /users/
# --------------------------------------------------------------------------------------


class TestListUsers:
    """``GET /users/`` - admins see everybody."""

    def test_admin_sees_every_user_sorted_by_email(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        other_user: User,
        inactive_user: User,
    ) -> None:
        response = client.get("/users/", headers=auth_header(admin_user))
        assert response.status_code == 200
        assert [entry["email"] for entry in response.json()] == sorted(
            [
                admin_user.email,
                normal_user.email,
                other_user.email,
                inactive_user.email,
            ]
        )

    def test_inactive_users_are_listed_for_admins(
        self, client: TestClient, admin_user: User, inactive_user: User
    ) -> None:
        emails = [
            entry["email"]
            for entry in client.get("/users/", headers=auth_header(admin_user)).json()
        ]
        assert inactive_user.email in emails

    def test_entries_expose_public_fields_only(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get("/users/", headers=auth_header(admin_user))
        for entry in response.json():
            assert set(entry) == PUBLIC_FIELDS
        assert "argon2" not in response.text

    def test_non_admins_may_not_list_users(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.get("/users/", headers=auth_header(normal_user))
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )


# --------------------------------------------------------------------------------------
# GET /users/base
# --------------------------------------------------------------------------------------


class TestListUserBases:
    """``GET /users/base`` - minimal records for everybody who is logged in."""

    def test_any_user_sees_all_basic_records(
        self, client: TestClient, normal_user: User, admin_user: User
    ) -> None:
        response = client.get("/users/base", headers=auth_header(normal_user))
        assert response.status_code == 200
        assert sorted(entry["user_id"] for entry in response.json()) == sorted(
            [normal_user.user_id, admin_user.user_id]
        )

    def test_the_records_are_sorted_by_full_name(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.get("/users/base", headers=auth_header(normal_user))
        names = [entry["full_name"] for entry in response.json()]
        assert names == sorted(names)

    def test_only_the_columns_needed_to_pick_a_user_are_returned(
        self, client: TestClient, normal_user: User
    ) -> None:
        """``email`` and the password hash must not be loaded or returned."""
        response = client.get("/users/base", headers=auth_header(normal_user))
        for entry in response.json():
            assert set(entry) == {"user_id", "full_name", "institution"}
        assert "argon2" not in response.text
        assert normal_user.email not in response.text

    def test_inactive_users_appear_in_the_list(
        self, client: TestClient, normal_user: User, inactive_user: User
    ) -> None:
        entries = client.get("/users/base", headers=auth_header(normal_user)).json()
        assert inactive_user.user_id in [entry["user_id"] for entry in entries]


# --------------------------------------------------------------------------------------
# POST /users/
# --------------------------------------------------------------------------------------


class TestCreateUser:
    """``POST /users/`` - admins create new accounts."""

    def test_admin_can_create_a_user(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.post(
            "/users/", headers=auth_header(admin_user), json=create_payload()
        )
        assert response.status_code == 201
        created = response.json()
        assert created["email"] == "newcomer@example.org"
        assert created["full_name"] == "Nora Newcomer"
        assert created["institution"] == "New University"
        assert set(created) == PUBLIC_FIELDS
        assert all_emails() == sorted([admin_user.email, "newcomer@example.org"])

    def test_the_password_is_stored_as_a_hash(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.post(
            "/users/",
            headers=auth_header(admin_user),
            json=create_payload(is_active=True),
        )
        stored = stored_user(response.json()["user_id"])
        assert stored["password_hashed"] != "a-secret-password"
        assert "a-secret-password" not in stored["password_hashed"]
        assert verify_password("a-secret-password", stored["password_hashed"])

    def test_an_active_new_user_can_log_in(
        self, client: TestClient, admin_user: User
    ) -> None:
        client.post(
            "/users/",
            headers=auth_header(admin_user),
            json=create_payload(is_active=True),
        )
        assert (
            login(client, "newcomer@example.org", "a-secret-password").status_code
            == 200
        )

    def test_neither_plaintext_nor_hash_are_returned(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.post(
            "/users/", headers=auth_header(admin_user), json=create_payload()
        )
        assert set(response.json()) == PUBLIC_FIELDS
        assert "a-secret-password" not in response.text
        assert "argon2" not in response.text

    def test_permissions_and_status_can_be_granted_at_creation(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.post(
            "/users/",
            headers=auth_header(admin_user),
            json=create_payload(
                is_active=True, permission_admin=True, permission_submit_query=True
            ),
        )
        assert response.status_code == 201
        created = response.json()
        assert created["permission_admin"] is True
        assert created["permission_submit_query"] is True
        # The freshly created admin may act like one immediately.
        client.cookies.clear()
        login(client, "newcomer@example.org", "a-secret-password")
        assert client.get("/users/").status_code == 200

    def test_the_client_cannot_choose_the_user_id(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.post(
            "/users/",
            headers=auth_header(admin_user),
            json=create_payload(user_id="chosen-by-client"),
        )
        assert response.status_code == 201
        assert response.json()["user_id"] != "chosen-by-client"
        assert all_emails() == sorted([admin_user.email, "newcomer@example.org"])

    def test_a_duplicate_email_conflicts(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        response = client.post(
            "/users/",
            headers=auth_header(admin_user),
            json=create_payload(email=normal_user.email),
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "A user with this email already exists"
        # Nothing was added.
        assert all_emails() == sorted([admin_user.email, normal_user.email])

    def test_non_admins_may_not_create_users(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.post(
            "/users/", headers=auth_header(normal_user), json=create_payload()
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )
        assert all_emails() == [normal_user.email]

    def test_a_payload_without_is_active_creates_an_inactive_account(
        self, client: TestClient, admin_user: User
    ) -> None:
        """
        KNOWN BUG: ``is_active`` defaults to ``False`` in the request schema, so an
        admin who does not think to set it creates an account that cannot log in.
        """
        response = client.post(
            "/users/", headers=auth_header(admin_user), json=create_payload()
        )
        assert response.status_code == 201
        assert stored_user(response.json()["user_id"])["is_active"] is False
        assert (
            login(client, "newcomer@example.org", "a-secret-password").status_code
            == 403
        )

    def test_syntactically_invalid_emails_are_accepted_on_creation(
        self, client: TestClient, admin_user: User
    ) -> None:
        """
        KNOWN BUG: ``UserCreate.email`` is a plain ``str`` (only ``UserPublicBase``
        subclasses and ``LoginRequest`` use ``EmailStr``), so an unusable address is
        stored - and that account can never log in, because the login rejects it.
        """
        response = client.post(
            "/users/",
            headers=auth_header(admin_user),
            json=create_payload(email="not-an-email"),
        )
        assert response.status_code == 201
        assert login(client, "not-an-email", "a-secret-password").status_code == 422

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"full_name": None}, id="missing-full-name"),
            pytest.param({"institution": None}, id="missing-institution"),
            pytest.param({"email": None}, id="missing-email"),
            pytest.param({"password_plain": None}, id="missing-password"),
        ],
    )
    def test_incomplete_payloads_are_unprocessable(
        self, client: TestClient, admin_user: User, overrides: dict[str, Any]
    ) -> None:
        response = client.post(
            "/users/", headers=auth_header(admin_user), json=create_payload(**overrides)
        )
        assert response.status_code == 422
        assert all_emails() == [admin_user.email]


# --------------------------------------------------------------------------------------
# PUT /users/
# --------------------------------------------------------------------------------------


class TestUpdateUser:
    """``PUT /users/`` - self-service profile edits and admin overrides."""

    def test_a_user_may_change_their_own_profile(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id, "full_name": "M. User"},
        )
        assert response.status_code == 200
        assert response.json()["full_name"] == "M. User"
        stored = stored_user(normal_user.user_id)
        assert stored["full_name"] == "M. User"
        # Untouched fields keep their values (only what was sent is applied).
        assert stored["email"] == normal_user.email
        assert stored["institution"] == "Test Institution"
        assert stored["is_active"] is True

    def test_a_user_may_not_update_somebody_else(
        self, client: TestClient, normal_user: User, other_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": other_user.user_id, "full_name": "Hacked"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Not enough permissions: you may only update your own user"
        )
        assert stored_user(other_user.user_id)["full_name"] == "Olivia Other"

    def test_an_unknown_user_id_is_not_found_for_admins(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": "no-such-user", "full_name": "Ghost"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    def test_an_unknown_user_id_reports_forbidden_for_non_admins(
        self, client: TestClient, normal_user: User
    ) -> None:
        """The ownership check runs before the lookup, so 403 wins over 404."""
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": "no-such-user", "full_name": "Ghost"},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("permission_admin", True, id="become-admin"),
            pytest.param("permission_submit_query", True, id="grant-submit"),
            pytest.param("is_active", False, id="self-deactivate"),
        ],
    )
    def test_a_user_may_not_change_privileged_fields_about_themselves(
        self,
        client: TestClient,
        normal_user: User,
        field: str,
        value: object,
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id, field: value},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            f"Not enough permissions: cannot modify '{field}'"
        )
        assert stored_user(normal_user.user_id)[field] is not value

    def test_one_privileged_field_blocks_the_whole_update(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={
                "user_id": normal_user.user_id,
                "full_name": "Renamed",  # allowed ...
                "permission_admin": True,  # ... but this one is not
            },
        )
        assert response.status_code == 403
        # The rejected update is not applied at all, not even the allowed field.
        assert stored_user(normal_user.user_id)["full_name"] == "Norman User"

    def test_resending_the_unchanged_privileged_fields_is_accepted(
        self, client: TestClient, normal_user: User
    ) -> None:
        """
        Sending the whole record, as the web UI does, is accepted as long as the
        restricted fields keep their values.
        """
        stored = stored_user(normal_user.user_id)
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={
                "user_id": normal_user.user_id,
                "email": stored["email"],
                "full_name": "Renamed by Self",
                "institution": stored["institution"],
                "is_active": stored["is_active"],
                "permission_admin": stored["permission_admin"],
                "permission_submit_query": stored["permission_submit_query"],
            },
        )
        assert response.status_code == 200
        assert stored_user(normal_user.user_id)["full_name"] == "Renamed by Self"

    def test_an_admin_may_update_anybody_elses_profile(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": normal_user.user_id, "institution": "Promoted U"},
        )
        assert response.status_code == 200
        assert stored_user(normal_user.user_id)["institution"] == "Promoted U"

    def test_an_admin_may_grant_permissions(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": normal_user.user_id, "permission_admin": True},
        )
        assert response.status_code == 200
        assert response.json()["permission_admin"] is True
        assert stored_user(normal_user.user_id)["permission_admin"] is True

    def test_newly_granted_rights_work_immediately(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        # The old JWT still claims `permission_admin: False`; the database decides.
        client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": normal_user.user_id, "permission_admin": True},
        )
        assert (
            client.get("/users/", headers=auth_header(normal_user)).status_code == 200
        )

    def test_an_admin_may_deactivate_a_user(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": normal_user.user_id, "is_active": False},
        )
        assert response.status_code == 200
        assert stored_user(normal_user.user_id)["is_active"] is False
        # The deactivated user can neither use the API nor log in anymore.
        assert (
            client.get("/users/me", headers=auth_header(normal_user)).status_code == 403
        )
        assert login(client, normal_user.email).status_code == 403

    def test_an_admin_may_reactivate_an_inactive_user(
        self, client: TestClient, admin_user: User, inactive_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": inactive_user.user_id, "is_active": True},
        )
        assert response.status_code == 200
        assert stored_user(inactive_user.user_id)["is_active"] is True

    def test_an_admin_may_demote_themselves(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": admin_user.user_id, "permission_admin": False},
        )
        assert response.status_code == 200
        assert stored_user(admin_user.user_id)["permission_admin"] is False
        # ... and loses admin access with the very next request.
        assert client.get("/users/", headers=auth_header(admin_user)).status_code == 403

    def test_a_user_may_change_their_email(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id, "email": "renamed@example.org"},
        )
        assert response.status_code == 200
        assert stored_user(normal_user.user_id)["email"] == "renamed@example.org"

    def test_resending_ones_own_email_is_allowed(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id, "email": normal_user.email},
        )
        assert response.status_code == 200
        assert stored_user(normal_user.user_id)["email"] == normal_user.email

    def test_an_email_that_somebody_else_uses_conflicts(
        self, client: TestClient, normal_user: User, other_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id, "email": other_user.email},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "A user with this email already exists"
        # Nothing of the rejected update was applied.
        assert stored_user(normal_user.user_id)["email"] == normal_user.email

    def test_the_conflict_check_also_applies_to_admins(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": admin_user.user_id, "email": normal_user.email},
        )
        assert response.status_code == 409
        assert stored_user(admin_user.user_id)["email"] == admin_user.email

    def test_a_user_may_change_their_password(
        self, client: TestClient, normal_user: User
    ) -> None:
        old_hash = stored_user(normal_user.user_id)["password_hashed"]
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={
                "user_id": normal_user.user_id,
                "password_plain": "brand-new-password",
            },
        )
        assert response.status_code == 200
        stored = stored_user(normal_user.user_id)
        assert stored["password_hashed"] != old_hash
        assert verify_password("brand-new-password", stored["password_hashed"])
        # The new password logs in, the old one does not.
        assert login(client, normal_user.email, "brand-new-password").status_code == 200
        assert login(client, normal_user.email, DEFAULT_PASSWORD).status_code == 401

    def test_an_admin_may_reset_somebody_elses_password(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(admin_user),
            json={"user_id": normal_user.user_id, "password_plain": "reset-by-admin"},
        )
        assert response.status_code == 200
        assert verify_password(
            "reset-by-admin", stored_user(normal_user.user_id)["password_hashed"]
        )

    def test_an_empty_password_is_silently_ignored(
        self, client: TestClient, normal_user: User
    ) -> None:
        """An empty string is falsy, so the hash check `if password_plain` skips it."""
        old_hash = stored_user(normal_user.user_id)["password_hashed"]
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id, "password_plain": ""},
        )
        assert response.status_code == 200
        assert stored_user(normal_user.user_id)["password_hashed"] == old_hash

    def test_explicit_nulls_do_not_clear_the_profile(
        self, client: TestClient, normal_user: User
    ) -> None:
        """Fields sent as ``null`` are skipped, only truthy values are applied."""
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={
                "user_id": normal_user.user_id,
                "full_name": None,
                "institution": None,
            },
        )
        assert response.status_code == 200
        stored = stored_user(normal_user.user_id)
        assert stored["full_name"] == "Norman User"
        assert stored["institution"] == "Test Institution"

    def test_an_update_without_any_changes_is_harmless(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id},
        )
        assert response.status_code == 200
        assert response.json()["full_name"] == "Norman User"

    def test_the_password_never_leaks_through_the_response(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.put(
            "/users/",
            headers=auth_header(normal_user),
            json={"user_id": normal_user.user_id, "password_plain": "rotated-secret"},
        )
        assert response.status_code == 200
        assert "rotated-secret" not in response.text
        assert "argon2" not in response.text
        assert set(response.json()) == PUBLIC_FIELDS

    def test_invalid_update_bodies_are_unprocessable(
        self, client: TestClient, normal_user: User
    ) -> None:
        bodies: list[dict[str, Any]] = [
            {"full_name": "No Id"},  # `user_id` is required
            {"user_id": None, "full_name": "Null Id"},
            {"user_id": normal_user.user_id, "email": "not-an-email"},
        ]
        for body in bodies:
            response = client.put(
                "/users/", headers=auth_header(normal_user), json=body
            )
            assert response.status_code == 422, body
        assert stored_user(normal_user.user_id)["full_name"] == "Norman User"
