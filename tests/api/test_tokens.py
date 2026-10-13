"""
Tests for the ``/tokens`` endpoints: who may see, create, edit and delete tokens.

The rules covered here:

=============================  ============  ========================  ======================
                             anonymous      user without permission   user with permission
                                                                       (owner of the token)
=============================  ============  ========================  ======================
``GET /tokens/list``           401           only permitted tokens     only permitted tokens
``GET /tokens/permission``     401           own permissions only      own permissions only
``POST /tokens/submit``        401           allowed, becomes owner    allowed, becomes owner
``POST /tokens/update``        401           403                       only ``is_active``
``DELETE /tokens/delete``      403           403                       403
``POST   /tokens/permissions`` 403           403                       403
``DELETE /tokens/permissions`` 403           403                       403
=============================  ============  ========================  ======================

Admins can do everything an owner can and, in addition, change all fields of all
tokens, delete tokens, and manage the permissions of other users.

Endpoints that behave differently from what their docstrings promise are covered by
tests marked ``KNOWN BUG``; those assert today's actual responses and have to be
updated together with the endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jwt
import pytest
from fastapi.testclient import TestClient

from academic_apis.api.utils.auth import create_access_token
from academic_apis.db import User
from academic_apis.utils import settings
from academic_apis.utils.types import Wrapper

if TYPE_CHECKING:
    from collections.abc import Callable

    from httpx2 import Response  # the project pins `httpx2`, which `TestClient` returns

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------

#: ``(method, path, json body)`` for every endpoint, used by the authentication tests.
ENDPOINTS: dict[str, tuple[str, str, Any]] = {
    "list": ("GET", "/tokens/list", None),
    "permission": ("GET", "/tokens/permission", None),
    "submit": (
        "POST",
        "/tokens/submit",
        {"wrapper": "scopus", "token": "api-key", "comment": "some comment"},
    ),
    "update": ("POST", "/tokens/update", {"token_id": "1", "is_active": False}),
    "delete": ("DELETE", "/tokens/delete?token_id=1", None),
    "permissions-create": (
        "POST",
        "/tokens/permissions",
        [{"user_id": "whoever", "token_id": "whichever", "is_owner": False}],
    ),
    "permissions-delete": (
        "DELETE",
        "/tokens/permissions",
        [{"user_id": "whoever", "token_id": "whichever", "is_owner": False}],
    ),
}

ALL_ENDPOINTS = pytest.mark.parametrize("endpoint", ENDPOINTS.values(), ids=ENDPOINTS)

#: ``(user_id, token_id, is_owner)``, as returned by the ``stored_permissions`` fixture.
PermissionRow = tuple[str, str, bool]

#: what ``POST /tokens/submit`` answers for every unusable required field; the endpoint
#: checks ``token``, ``comment`` and ``wrapper`` in one condition and names none of them
MISSING_FIELD_DETAIL = "Required field missing!"

#: what ``POST /tokens/submit`` answers for every unusable required field; the endpoint
#: checks ``token``, ``comment`` and ``wrapper`` in one condition and does not name one
MISSING_FIELD_DETAIL = "Required field missing!"


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


def permission(user: User, token_id: str, is_owner: bool) -> PermissionRow:
    """Expected permission row for ``user`` and ``token_id``."""
    return user.user_id, token_id, is_owner


def listed_ids(response: Response) -> list[str]:
    """Token IDs returned by ``/tokens/list``."""
    return [entry["token_id"] for entry in response.json()]


def permission_rows(response: Response) -> list[tuple[str, bool]]:
    """``(token_id, is_owner)`` pairs returned by ``/tokens/permission``."""
    return [(entry["token_id"], entry["is_owner"]) for entry in response.json()]


def permission_body(
    user: User | str, token_id: str, is_owner: bool = False
) -> list[Any]:
    """Request body for the ``/tokens/permissions`` endpoints."""
    return [
        {
            "user_id": user.user_id if isinstance(user, User) else user,
            "token_id": token_id,
            "is_owner": is_owner,
        }
    ]


def user_with_role(
    role: str,
    tag: str,
    make_user: Callable[..., User],
    make_token: Callable[..., str],
) -> tuple[User, str]:
    """
    Create a token plus a user who is its ``owner``, a ``member`` with permission,
    or a completely unrelated ``outsider``; returns ``(user, token_id)``.
    """
    actor = make_user(email=f"{tag}-{role}@example.org")
    if role == "outsider":
        return actor, make_token(
            owner=make_user(email=f"{tag}-owner-{role}@example.org")
        )
    if role == "member":
        return actor, make_token(
            owner=make_user(email=f"{tag}-owner-{role}@example.org"),
            users=[(actor, False)],
        )
    return actor, make_token(owner=actor)


# --------------------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------------------


class TestAuthentication:
    """All endpoints require a valid login, no matter the permission level."""

    @ALL_ENDPOINTS
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

    @ALL_ENDPOINTS
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
    @ALL_ENDPOINTS
    def test_invalid_credentials_are_rejected(
        self,
        client: TestClient,
        endpoint: tuple[str, str, Any],
        header: dict[str, str],
    ) -> None:
        method, path, _ = endpoint
        assert client.request(method, path, headers=header).status_code == 401

    def test_well_signed_token_of_unknown_user_is_rejected(
        self, client: TestClient
    ) -> None:
        # Correctly signed, but this user does not exist in the database.
        response = client.get("/tokens/list", headers=ghost_auth_header())
        assert response.status_code == 401
        assert response.json()["detail"] == "User not found"

    def test_admin_rights_cannot_be_forged_in_a_json_web_token(
        self, client: TestClient
    ) -> None:
        # The header claims `permission_admin`, but rights are read from the database.
        response = client.delete(
            "/tokens/delete?token_id=1", headers=ghost_auth_header("fake-admin")
        )
        assert response.status_code == 401

    def test_cookie_login_is_accepted_like_a_bearer_token(
        self, client: TestClient, normal_user: User, owned_token: str
    ) -> None:
        client.cookies.set(settings.cookie_name, create_access_token(normal_user))
        assert listed_ids(client.get("/tokens/list")) == [owned_token]

    def test_the_api_key_itself_is_never_returned(
        self, client: TestClient, normal_user: User, owned_token: str
    ) -> None:
        (listed,) = client.get("/tokens/list", headers=auth_header(normal_user)).json()
        assert listed["token_id"] == owned_token
        assert "token" not in listed
        assert "api-key-value" not in str(listed)


# --------------------------------------------------------------------------------------
# GET /tokens/list
# --------------------------------------------------------------------------------------


class TestListTokens:
    """``GET /tokens/list``"""

    def test_owner_sees_their_own_token(
        self, client: TestClient, normal_user: User, owned_token: str
    ) -> None:
        response = client.get("/tokens/list", headers=auth_header(normal_user))
        assert response.status_code == 200
        assert listed_ids(response) == [owned_token]

    def test_user_without_permission_sees_nothing(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        owned_token: str,
    ) -> None:
        outsider = make_user(email="outsider-list@example.org")
        assert (
            listed_ids(client.get("/tokens/list", headers=auth_header(outsider))) == []
        )

    def test_permission_without_ownership_lists_the_token(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        member = make_user(email="member-list@example.org")
        token_id = make_token(users=[(member, False)])
        assert listed_ids(client.get("/tokens/list", headers=auth_header(member))) == [
            token_id
        ]

    def test_only_permitted_and_active_tokens_are_listed(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="several-list@example.org")
        owned = make_token(owner=user)
        shared = make_token(users=[(user, False)])
        make_token()  # no permission for `user`
        make_token(owner=user, is_active=False)  # permitted, but not active
        assert listed_ids(client.get("/tokens/list", headers=auth_header(user))) == [
            owned,
            shared,
        ]

    def test_inactive_tokens_are_hidden_from_non_admins(
        self, client: TestClient, normal_user: User, make_token: Callable[..., str]
    ) -> None:
        make_token(owner=normal_user, is_active=False)
        assert (
            listed_ids(client.get("/tokens/list", headers=auth_header(normal_user)))
            == []
        )

    def test_admin_sees_every_token_including_unpermitted_and_inactive(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        visible = make_token(owner=make_user(email="someone@example.org"))
        unpermitted = make_token()
        inactive = make_token(is_active=False)
        assert sorted(
            listed_ids(client.get("/tokens/list", headers=auth_header(admin_user)))
        ) == sorted([visible, unpermitted, inactive])

    def test_admin_can_filter_by_user_id(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        other_user: User,
        owned_token: str,
        make_token: Callable[..., str],
    ) -> None:
        foreign = make_token(owner=other_user)
        response = client.get(
            f"/tokens/list?user_id={normal_user.user_id}",
            headers=auth_header(admin_user),
        )
        assert listed_ids(response) == [owned_token]
        assert foreign not in listed_ids(response)

    def test_the_user_id_filter_cannot_be_used_by_non_admins(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        owned_token: str,
        make_token: Callable[..., str],
    ) -> None:
        # No error is raised, but the scope cannot be widened: a non-admin always
        # sees their own tokens, never somebody else's.
        foreign = make_token(owner=other_user)
        response = client.get(
            f"/tokens/list?user_id={other_user.user_id}",
            headers=auth_header(normal_user),
        )
        assert response.status_code == 200
        assert listed_ids(response) == [owned_token]
        assert foreign not in listed_ids(response)

    def test_filter_for_an_unknown_user_is_empty_for_admins(
        self, client: TestClient, admin_user: User, owned_token: str
    ) -> None:
        assert (
            listed_ids(
                client.get(
                    "/tokens/list?user_id=nobody", headers=auth_header(admin_user)
                )
            )
            == []
        )

    def test_deactivated_tokens_disappear_for_their_owner(
        self, client: TestClient, normal_user: User, owned_token: str
    ) -> None:
        client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={"token_id": owned_token, "is_active": False},
        )
        assert (
            listed_ids(client.get("/tokens/list", headers=auth_header(normal_user)))
            == []
        )


# --------------------------------------------------------------------------------------
# POST /tokens/submit
# --------------------------------------------------------------------------------------


class TestSubmitToken:
    """``POST /tokens/submit`` - creating tokens."""

    def test_any_user_may_submit_a_token(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        stored_token: Callable[[str], dict[str, Any]],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        user = make_user(email="submitter@example.org")
        response = client.post(
            "/tokens/submit",
            headers=auth_header(user),
            json={
                "wrapper": "scopus",
                "token": "my-secret-api-key",
                "comment": "my key",
                "proxy": "http://proxy.example.org:8080",
            },
        )
        assert response.status_code == 200
        token_id = response.json()
        assert stored_token(token_id) == {
            "token_id": token_id,
            "wrapper": Wrapper.SCOPUS,
            "token": "my-secret-api-key",
            "comment": "my key",
            "proxy": "http://proxy.example.org:8080",
            "is_active": True,
        }
        # The submitter automatically becomes the owner of the new token.
        assert stored_permissions(token_id=token_id) == [
            permission(user, token_id, True)
        ]

    def test_admin_may_submit_a_token_too(
        self,
        client: TestClient,
        admin_user: User,
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        token_id = client.post(
            "/tokens/submit",
            headers=auth_header(admin_user),
            json={"wrapper": "openalex", "token": "admin-key", "comment": "admin key"},
        ).json()
        assert stored_permissions(token_id=token_id) == [
            permission(admin_user, token_id, True)
        ]

    def test_new_token_is_immediately_visible_to_its_submitter(
        self, client: TestClient, normal_user: User, owned_token: str
    ) -> None:
        token_id = client.post(
            "/tokens/submit",
            headers=auth_header(normal_user),
            json={"wrapper": "scopus", "token": "second-key", "comment": "second"},
        ).json()
        assert listed_ids(
            client.get("/tokens/list", headers=auth_header(normal_user))
        ) == [
            owned_token,
            token_id,
        ]

    def test_submitting_grants_access_to_nobody_else(
        self,
        client: TestClient,
        normal_user: User,
        other_user: User,
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        client.post(
            "/tokens/submit",
            headers=auth_header(normal_user),
            json={"wrapper": "scopus", "token": "third-key", "comment": "mine only"},
        )
        assert stored_permissions(user_id=other_user.user_id) == []

    def test_existing_permissions_are_kept_when_submitting(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        user = make_user(email="repeat-submitter@example.org")
        earlier = make_token(users=[(user, False)])
        token_id = client.post(
            "/tokens/submit",
            headers=auth_header(user),
            json={"wrapper": "scopus", "token": "fourth-key", "comment": "another one"},
        ).json()
        assert sorted(stored_permissions(user_id=user.user_id)) == sorted(
            [permission(user, earlier, False), permission(user, token_id, True)]
        )

    def test_the_client_cannot_choose_the_id_or_the_status(
        self,
        client: TestClient,
        normal_user: User,
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        token_id = client.post(
            "/tokens/submit",
            headers=auth_header(normal_user),
            json={
                "token_id": "chosen-by-client",
                "is_active": False,
                "wrapper": "scopus",
                "token": "fifth-key",
                "comment": "server decides",
            },
        ).json()
        assert token_id != "chosen-by-client"
        assert stored_token("chosen-by-client") == {}
        assert stored_token(token_id)["is_active"] is True

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {"wrapper": "scopus", "token": "", "comment": "x"},
                id="empty-token",
            ),
            pytest.param(
                {"wrapper": "scopus", "token": "   ", "comment": "x"},
                id="blank-token",
            ),
            pytest.param({"wrapper": "scopus", "token": "key"}, id="missing-comment"),
            pytest.param(
                {"wrapper": "scopus", "token": "key", "comment": "  "},
                id="blank-comment",
            ),
        ],
    )
    def test_incomplete_submissions_are_rejected(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
        payload: dict[str, Any],
    ) -> None:
        """
        An unusable ``token`` or ``comment`` is refused with 422.

        The endpoint checks the required fields together and reports them together, so
        the answer names none of them.
        """
        other = make_token(token_id="other-token-id")
        response = client.post(
            "/tokens/submit", headers=auth_header(normal_user), json=payload
        )
        assert response.status_code == 422
        assert response.json()["detail"] == MISSING_FIELD_DETAIL
        # Only the two existing tokens are left, nothing was added.
        assert stored_token(owned_token) != {}
        assert stored_token(other) != {}
        assert listed_ids(
            client.get("/tokens/list", headers=auth_header(normal_user))
        ) == [owned_token]

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"token": "key", "comment": "x"}, id="missing-wrapper"),
            pytest.param(
                {"wrapper": "not-a-wrapper", "token": "key", "comment": "x"},
                id="unknown-wrapper",
            ),
        ],
    )
    def test_an_invalid_wrapper_is_rejected(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        stored_permissions: Callable[..., list[PermissionRow]],
        payload: dict[str, Any],
    ) -> None:
        """
        A missing or unknown ``wrapper`` is answered 422, not 500.

        ``AccessToken`` is a table model, so the request body arrives unvalidated and the
        endpoint checks ``wrapper`` itself; nothing reaches the database.

        KNOWN BUG: the check is shared with the other required fields, so an unknown
        wrapper is reported as a missing one.
        """
        response = client.post(
            "/tokens/submit", headers=auth_header(normal_user), json=payload
        )
        assert response.status_code == 422
        assert response.json()["detail"] == MISSING_FIELD_DETAIL
        # Nothing was stored, so the user gained no permission beyond the fixture's.
        assert stored_permissions(user_id=normal_user.user_id) == [
            permission(normal_user, owned_token, True)
        ]

    def test_a_missing_api_key_is_rejected(
        self, client: TestClient, normal_user: User
    ) -> None:
        """A missing ``token`` is rejected like a blank one instead of crashing."""
        response = client.post(
            "/tokens/submit", headers=auth_header(normal_user), json={"comment": "x"}
        )
        assert response.status_code == 422
        assert response.json()["detail"] == MISSING_FIELD_DETAIL


# --------------------------------------------------------------------------------------
# POST /tokens/update
# --------------------------------------------------------------------------------------


class TestUpdateToken:
    """``POST /tokens/update`` - the owner / member / admin matrix."""

    def test_owner_may_deactivate_their_token(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        response = client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={"token_id": owned_token, "is_active": False},
        )
        assert response.status_code == 200
        assert response.json() == owned_token
        assert stored_token(owned_token)["is_active"] is False

    def test_owner_may_activate_their_inactive_token(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        user = make_user(email="reactivator@example.org")
        token_id = make_token(owner=user, is_active=False)
        response = client.post(
            "/tokens/update",
            headers=auth_header(user),
            json={"token_id": token_id, "is_active": True},
        )
        assert response.status_code == 200
        assert stored_token(token_id)["is_active"] is True

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("token", "hacked-key", id="token"),
            pytest.param("comment", "hacked comment", id="comment"),
            pytest.param("wrapper", "openalex", id="wrapper"),
            pytest.param("proxy", "http://evil.example.org", id="proxy"),
        ],
    )
    def test_owner_may_not_change_other_fields(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        stored_token: Callable[[str], dict[str, Any]],
        field: str,
        value: object,
    ) -> None:
        response = client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={"token_id": owned_token, field: value},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            f"Not enough permissions: cannot modify '{field}'"
        )
        stored = stored_token(owned_token)
        assert stored["wrapper"] == "scopus"
        assert stored["token"] == "api-key-value"
        assert stored["comment"] == "token owned by normal_user"
        assert stored["proxy"] is None

    def test_owner_may_resend_the_unchanged_record(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        """
        Sending the whole record, as the web UI does, is accepted as long as the
        restricted fields keep their values.
        """
        stored = stored_token(owned_token)
        response = client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={
                "token_id": owned_token,
                "wrapper": "scopus",
                "token": stored["token"],
                "comment": stored["comment"],
                "proxy": stored["proxy"],
                "is_active": False,
                "last_used": None,
                "locked_until": None,
                "last_feedback": {},
            },
        )
        assert response.status_code == 200
        assert stored_token(owned_token)["is_active"] is False

    def test_one_restricted_field_blocks_the_whole_update(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        response = client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={
                "token_id": owned_token,
                "is_active": False,  # allowed ...
                "comment": "sneaked in",  # ... but this one is not
            },
        )
        assert response.status_code == 403
        # The rejected update is not applied at all, not even the allowed field.
        assert stored_token(owned_token)["is_active"] is True

    def test_permission_without_ownership_may_not_change_anything(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        member = make_user(email="member-update@example.org")
        owner = make_user(email="owner-update@example.org")
        token_id = make_token(owner=owner, users=[(member, False)])
        for body in ({"is_active": False}, {"comment": "edited by member"}):
            response = client.post(
                "/tokens/update",
                headers=auth_header(member),
                json={"token_id": token_id, **body},
            )
            assert response.status_code == 403, body
            assert response.json()["detail"] == (
                "Not enough permissions: you may only update your own tokens"
            )
        assert stored_token(token_id)["is_active"] is True

    def test_user_without_permission_may_not_update(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        outsider = make_user(email="outsider-update@example.org")
        token_id = make_token()
        response = client.post(
            "/tokens/update",
            headers=auth_header(outsider),
            json={"token_id": token_id, "is_active": False},
        )
        assert response.status_code == 403

    def test_owning_one_token_does_not_open_another_one(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        user = make_user(email="two-tokens@example.org")
        make_token(owner=user)
        foreign = make_token()
        response = client.post(
            "/tokens/update",
            headers=auth_header(user),
            json={"token_id": foreign, "is_active": False},
        )
        assert response.status_code == 403
        assert stored_token(foreign)["is_active"] is True

    def test_admin_may_change_all_fields_of_a_foreign_token(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        token_id = make_token(
            owner=make_user(email="owner-of-foreign@example.org"),
            comment="old comment",
        )
        response = client.post(
            "/tokens/update",
            headers=auth_header(admin_user),
            json={
                "token_id": token_id,
                "token": "rotated-key",
                "comment": "rotated by admin",
                "is_active": False,
                "proxy": "http://proxy.example.org",
            },
        )
        assert response.status_code == 200
        assert response.json() == token_id
        stored = stored_token(token_id)
        assert stored["token"] == "rotated-key"
        assert stored["comment"] == "rotated by admin"
        assert stored["is_active"] is False
        assert stored["proxy"] == "http://proxy.example.org"

    def test_admin_may_update_a_token_nobody_has_permission_for(
        self,
        client: TestClient,
        admin_user: User,
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        orphan = make_token()
        response = client.post(
            "/tokens/update",
            headers=auth_header(admin_user),
            json={"token_id": orphan, "is_active": False},
        )
        assert response.status_code == 200
        assert stored_permissions(token_id=orphan) == []  # no permission invented

    def test_admin_may_reactivate_what_the_owner_paused(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        owned_token: str,
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={"token_id": owned_token, "is_active": False},
        )
        response = client.post(
            "/tokens/update",
            headers=auth_header(admin_user),
            json={"token_id": owned_token, "is_active": True},
        )
        assert response.status_code == 200
        assert stored_token(owned_token)["is_active"] is True

    def test_unknown_token_id_is_not_found(
        self, client: TestClient, admin_user: User, normal_user: User
    ) -> None:
        for user in (normal_user, admin_user):
            response = client.post(
                "/tokens/update",
                headers=auth_header(user),
                json={"token_id": "no-such-token", "is_active": False},
            )
            assert response.status_code == 404, user.email
            assert response.json()["detail"] == (
                "No API token found with token_id=no-such-token"
            )

    def test_missing_token_id_is_unprocessable(
        self, client: TestClient, normal_user: User
    ) -> None:
        response = client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={"token_id": None, "is_active": False},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == (
            "Field 'token_id' must be set to update an API token"
        )

    def test_an_omitted_token_id_generates_a_new_one(
        self, client: TestClient, normal_user: User
    ) -> None:
        """
        KNOWN BUG: ``token_id`` has a default value, so the check above can only
        be triggered explicitly - leaving the field out reports a made-up ID as
        missing.
        """
        response = client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={"is_active": False},
        )
        assert response.status_code == 404
        assert response.json()["detail"].startswith("No API token found with token_id=")

    def test_an_invalid_wrapper_is_rejected(
        self,
        client: TestClient,
        admin_user: User,
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        """
        An invalid ``wrapper`` is rejected before the update, not written out.

        Rejecting it keeps the token readable: had the value been accepted, the
        stored row could no longer be loaded (its enum value is unknown).
        """
        token_id = make_token()
        response = client.post(
            "/tokens/update",
            headers=auth_header(admin_user),
            json={"token_id": token_id, "wrapper": "not-a-wrapper"},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == (
            "Field 'wrapper' must be one of: scopus, wos, pubmed, dimensions, openalex"
        )
        assert stored_token(token_id)["wrapper"] == "scopus"
        assert (
            client.get("/tokens/list", headers=auth_header(admin_user)).status_code
            == 200
        )

    def test_permissions_are_untouched_by_updates(
        self,
        client: TestClient,
        normal_user: User,
        owned_token: str,
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        """``is_owner`` is not part of the token record, so it cannot be escalated."""
        response = client.post(
            "/tokens/update",
            headers=auth_header(normal_user),
            json={"token_id": owned_token, "is_owner": False, "is_active": True},
        )
        assert response.status_code == 200
        assert stored_permissions(token_id=owned_token) == [
            permission(normal_user, owned_token, True)
        ]


# --------------------------------------------------------------------------------------
# DELETE /tokens/delete
# --------------------------------------------------------------------------------------


class TestDeleteToken:
    """``DELETE /tokens/delete`` - admins only."""

    @pytest.mark.parametrize("role", ["owner", "member", "outsider"])
    def test_only_admins_may_delete(
        self,
        client: TestClient,
        role: str,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        user, token_id = user_with_role(role, "delete", make_user, make_token)
        response = client.delete(
            f"/tokens/delete?token_id={token_id}", headers=auth_header(user)
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )
        assert stored_token(token_id) != {}

    def test_admin_may_delete_a_token(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        owner = make_user(email="owner-of-doomed@example.org")
        member = make_user(email="member-of-doomed@example.org")
        doomed = make_token(owner=owner, users=[(member, False)])
        spared = make_token(owner=owner)

        response = client.delete(
            f"/tokens/delete?token_id={doomed}", headers=auth_header(admin_user)
        )
        assert response.status_code == 200
        assert response.json() == doomed
        assert stored_token(doomed) == {}
        # All permissions of the deleted token are gone, the other token is intact.
        assert stored_permissions(token_id=doomed) == []
        assert stored_permissions(token_id=spared) == [permission(owner, spared, True)]

    def test_deleting_a_token_removes_all_its_permissions(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        owner = make_user(email="owner-delete-perms@example.org")
        member = make_user(email="member-delete-perms@example.org")
        token_id = make_token(owner=owner, users=[(member, False)])
        assert len(stored_permissions(token_id=token_id)) == 2
        client.delete(
            f"/tokens/delete?token_id={token_id}", headers=auth_header(admin_user)
        )
        assert stored_permissions(token_id=token_id) == []

    def test_a_deleted_token_disappears_from_everyones_list(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        owner = make_user(email="owner-disappears@example.org")
        member = make_user(email="member-disappears@example.org")
        token_id = make_token(owner=owner, users=[(member, False)])
        assert listed_ids(client.get("/tokens/list", headers=auth_header(member))) == [
            token_id
        ]
        client.delete(
            f"/tokens/delete?token_id={token_id}", headers=auth_header(admin_user)
        )
        assert listed_ids(client.get("/tokens/list", headers=auth_header(member))) == []
        assert listed_ids(client.get("/tokens/list", headers=auth_header(owner))) == []
        assert (
            listed_ids(client.get("/tokens/list", headers=auth_header(admin_user)))
            == []
        )

    @pytest.mark.parametrize(
        ("token_id", "status_code"),
        [
            pytest.param("123456", 404, id="numeric-id"),
            pytest.param("not-a-number", 404, id="not-a-number"),
            pytest.param("12.5", 404, id="float"),
            pytest.param("", 404, id="empty"),
        ],
    )
    def test_unknown_ids_are_not_found(
        self, client: TestClient, admin_user: User, token_id: str, status_code: int
    ) -> None:
        response = client.delete(
            f"/tokens/delete?token_id={token_id}", headers=auth_header(admin_user)
        )
        assert response.status_code == status_code

    def test_the_token_id_has_to_be_specified(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.delete("/tokens/delete", headers=auth_header(admin_user))
        assert response.status_code == 422


# --------------------------------------------------------------------------------------
# POST /tokens/permissions
# --------------------------------------------------------------------------------------


class TestEditPermissions:
    """``POST /tokens/permissions`` - admins create and change permissions."""

    def test_admin_grants_access_to_a_user_without_any_permission(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        user = make_user(email="granted@example.org")
        token_id = make_token()
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id),
        )
        assert response.status_code == 200
        assert stored_permissions(token_id=token_id) == [
            permission(user, token_id, False)
        ]

    def test_granted_user_can_now_see_the_token(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="granted-visible@example.org")
        token_id = make_token()
        client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id),
        )
        assert listed_ids(client.get("/tokens/list", headers=auth_header(user))) == [
            token_id
        ]

    def test_granted_user_may_not_edit_the_token(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="granted-no-edit@example.org")
        token_id = make_token()
        client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id),
        )
        response = client.post(
            "/tokens/update",
            headers=auth_header(user),
            json={"token_id": token_id, "is_active": False},
        )
        assert response.status_code == 403

    def test_admin_may_grant_ownership(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        user = make_user(email="promoted@example.org")
        token_id = make_token()
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id, is_owner=True),
        )
        assert response.status_code == 200
        assert stored_permissions(token_id=token_id) == [
            permission(user, token_id, True)
        ]

    def test_a_newly_granted_owner_may_deactivate_the_token(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="promoted-usable@example.org")
        token_id = make_token()
        client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id, is_owner=True),
        )
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(user),
                json={"token_id": token_id, "is_active": False},
            ).status_code
            == 200
        )

    def test_admin_may_revoke_ownership_of_an_existing_permission(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        owned_token: str,
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(normal_user, owned_token),
        )
        assert response.status_code == 200
        assert stored_permissions(token_id=owned_token) == [
            permission(normal_user, owned_token, False)
        ]
        # Losing ownership also takes away the right to change the status.
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(normal_user),
                json={"token_id": owned_token, "is_active": False},
            ).status_code
            == 403
        )

    def test_granting_the_same_permission_twice_is_harmless(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        user = make_user(email="twice@example.org")
        token_id = make_token()
        for _ in range(2):
            assert (
                client.post(
                    "/tokens/permissions",
                    headers=auth_header(admin_user),
                    json=permission_body(user, token_id),
                ).status_code
                == 200
            )
        assert stored_permissions(token_id=token_id) == [
            permission(user, token_id, False)
        ]

    def test_granting_several_permissions_at_once(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        other_user: User,
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        token_id = make_token()
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=[
                *permission_body(normal_user, token_id, is_owner=True),
                *permission_body(other_user, token_id),
            ],
        )
        assert response.status_code == 200
        assert sorted(stored_permissions(token_id=token_id)) == sorted(
            [
                permission(normal_user, token_id, True),
                permission(other_user, token_id, False),
            ]
        )

    def test_permissions_of_other_users_are_kept(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        owner = make_user(email="owner-kept@example.org")
        member = make_user(email="member-kept@example.org")
        token_id = make_token(owner=owner, users=[(member, False)])
        client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(make_user(email="third-kept@example.org"), token_id),
        )
        assert len(stored_permissions(token_id=token_id)) == 3

    def test_an_empty_list_does_nothing(
        self,
        client: TestClient,
        admin_user: User,
        normal_user: User,
        owned_token: str,
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        assert (
            client.post(
                "/tokens/permissions", headers=auth_header(admin_user), json=[]
            ).status_code
            == 200
        )
        assert stored_permissions(token_id=owned_token) == [
            permission(normal_user, owned_token, True)
        ]

    @pytest.mark.parametrize("role", ["owner", "member", "outsider"])
    def test_only_admins_may_grant_permissions(
        self,
        client: TestClient,
        role: str,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        user, token_id = user_with_role(role, "grant", make_user, make_token)
        stranger = make_user(email=f"stranger-grant-{role}@example.org")
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(user),
            json=permission_body(stranger, token_id, is_owner=True),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )
        expected = {
            "owner": [permission(user, token_id, True)],
            "member": [permission(user, token_id, False)],
            "outsider": [],
        }[role]
        assert stored_permissions(user_id=user.user_id) == expected

    def test_users_may_not_promote_themselves(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        member = make_user(email="self-promoter@example.org")
        owner = make_user(email="real-owner@example.org")
        token_id = make_token(owner=owner, users=[(member, False)])
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(member),
            json=permission_body(member, token_id, is_owner=True),
        )
        assert response.status_code == 403
        assert stored_permissions(user_id=member.user_id) == [
            permission(member, token_id, False)
        ]

    def test_the_body_has_to_be_a_list(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json={"user_id": "whoever", "token_id": "whichever", "is_owner": False},
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "list_type"

    def test_permissions_for_unknown_ids_are_created_anyway(
        self,
        client: TestClient,
        admin_user: User,
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        """KNOWN BUG: foreign keys are not enforced, so dangling permissions stick."""
        response = client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body("ghost-user", "ghost-token"),
        )
        assert response.status_code == 200
        assert stored_permissions(user_id="ghost-user") == [
            ("ghost-user", "ghost-token", False)
        ]

    def test_incomplete_permission_entries_are_rejected(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        """
        An entry without ``user_id`` or ``token_id`` is answered as unprocessable.

        Both fields are primary keys, and SQLModel makes primary keys of table
        models optional, so FastAPI lets such an entry through. The endpoint has
        to reject it itself: merging it would make SQLAlchemy insert a row whose
        primary key value is missing (and warn about it), ending in a 500.
        """
        other = make_user(email="incomplete@example.org")
        token_id = make_token()
        bodies = [
            {"user_id": admin_user.user_id},  # token_id omitted
            {"token_id": token_id},  # user_id omitted
            {"user_id": other.user_id, "token_id": "   "},  # blank token_id
            {"user_id": "", "token_id": token_id},  # blank user_id
        ]
        for body in bodies:
            response = client.post(
                "/tokens/permissions",
                headers=auth_header(admin_user),
                json=[body],
            )
            assert response.status_code == 422, body
            assert response.json()["detail"] == (
                "Fields 'user_id' and 'token_id' are required (entry 0)"
            )
        # the endpoint stores the whole list or none of it
        assert stored_permissions(user_id=admin_user.user_id) == []
        assert stored_permissions(user_id=other.user_id) == []
        assert stored_permissions(token_id=token_id) == []


# --------------------------------------------------------------------------------------
# DELETE /tokens/permissions
# --------------------------------------------------------------------------------------


class TestDeletePermissions:
    """``DELETE /tokens/permissions`` - admins revoke access."""

    def test_admin_revokes_a_permission(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        user = make_user(email="revoked@example.org")
        token_id = make_token(users=[(user, False)])
        response = client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id),
        )
        assert response.status_code == 200
        assert response.json() == 1
        assert stored_permissions(token_id=token_id) == []

    def test_a_revoked_user_cannot_see_the_token_anymore(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="revoked-visible@example.org")
        token_id = make_token(users=[(user, False)])
        assert listed_ids(client.get("/tokens/list", headers=auth_header(user))) == [
            token_id
        ]
        client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id),
        )
        assert listed_ids(client.get("/tokens/list", headers=auth_header(user))) == []

    def test_revoking_ownership_removes_the_right_to_edit(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        owner = make_user(email="revoked-owner@example.org")
        token_id = make_token(owner=owner)
        client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(owner, token_id, is_owner=True),
        )
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(owner),
                json={"token_id": token_id, "is_active": False},
            ).status_code
            == 403
        )

    def test_the_token_survives_revoking_all_permissions(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        owner = make_user(email="owner-survives@example.org")
        token_id = make_token(owner=owner)
        response = client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(owner, token_id, is_owner=True),
        )
        assert response.json() == 1
        assert stored_token(token_id) != {}
        assert listed_ids(client.get("/tokens/list", headers=auth_header(owner))) == []

    def test_only_the_listed_permissions_are_removed(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        owner = make_user(email="owner-survives-2@example.org")
        member = make_user(email="member-removed@example.org")
        token_id = make_token(owner=owner, users=[(member, False)])
        response = client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(member, token_id),
        )
        assert response.json() == 1
        assert stored_permissions(token_id=token_id) == [
            permission(owner, token_id, True)
        ]

    def test_the_rowcount_counts_only_existing_permissions(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="mixed-removal@example.org")
        token_id = make_token(users=[(user, False)])
        response = client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=[
                *permission_body(user, token_id),
                *permission_body("ghost-user", "ghost-token"),
            ],
        )
        assert response.json() == 1

    def test_removing_a_nonexistent_permission_is_not_an_error(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body("ghost-user", "ghost-token"),
        )
        assert response.status_code == 200
        assert response.json() == 0

    def test_the_is_owner_value_of_the_payload_does_not_matter(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """Permissions are matched by ``(user_id, token_id)`` only."""
        user = make_user(email="wrong-owner-flag@example.org")
        token_id = make_token(users=[(user, False)])
        response = client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(user, token_id, is_owner=True),
        )
        assert response.json() == 1

    @pytest.mark.parametrize("role", ["owner", "member", "outsider"])
    def test_only_admins_may_revoke_permissions(
        self,
        client: TestClient,
        role: str,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        stored_permissions: Callable[..., list[PermissionRow]],
    ) -> None:
        owner = make_user(email=f"revoke-owner-{role}@example.org")
        member = make_user(email=f"revoke-member-{role}@example.org")
        token_id = make_token(owner=owner, users=[(member, False)])
        actor = {
            "owner": owner,
            "member": member,
            "outsider": make_user(email=f"revoke-outsider-{role}@example.org"),
        }[role]
        response = client.request(
            "DELETE",
            "/tokens/permissions",
            headers=auth_header(actor),
            json=permission_body(owner, token_id, is_owner=True),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Insufficient privileges: admin access required"
        )
        assert sorted(stored_permissions(token_id=token_id)) == sorted(
            [permission(owner, token_id, True), permission(member, token_id, False)]
        )


# --------------------------------------------------------------------------------------
# GET /tokens/permission
# --------------------------------------------------------------------------------------


class TestListPermissions:
    """``GET /tokens/permission``"""

    def test_user_sees_own_permissions(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="own-permissions@example.org")
        owned = make_token(owner=user)
        shared = make_token(users=[(user, False)])
        make_token()  # no relation to `user`
        response = client.get("/tokens/permission", headers=auth_header(user))
        assert response.status_code == 200
        assert sorted(permission_rows(response)) == sorted(
            [(owned, True), (shared, False)]
        )

    def test_a_user_without_permission_gets_an_empty_list(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        outsider = make_user(email="clean-permissions@example.org")
        make_token()
        assert (
            client.get("/tokens/permission", headers=auth_header(outsider)).json() == []
        )

    def test_permissions_of_inactive_tokens_are_included(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        user = make_user(email="inactive-permissions@example.org")
        token_id = make_token(owner=user, is_active=False)
        assert permission_rows(
            client.get("/tokens/permission", headers=auth_header(user))
        ) == [(token_id, True)]

    def test_admin_may_look_up_another_user(
        self,
        client: TestClient,
        admin_user: User,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        member = make_user(email="looked-up@example.org")
        token_id = make_token(users=[(member, False)])
        make_token()  # unrelated
        response = client.get(
            f"/tokens/permission?user_id={member.user_id}",
            headers=auth_header(admin_user),
        )
        assert permission_rows(response) == [(token_id, False)]

    def test_a_non_admin_may_not_look_up_another_user(
        self, client: TestClient, normal_user: User, other_user: User
    ) -> None:
        response = client.get(
            f"/tokens/permission?user_id={other_user.user_id}",
            headers=auth_header(normal_user),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Only admins are allowed to see other's permissions."
        )

    def test_ownership_does_not_allow_looking_up_other_users(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        owner = make_user(email="owner-lookup@example.org")
        make_token(owner=owner)
        target = make_user(email="target-lookup@example.org")
        response = client.get(
            f"/tokens/permission?user_id={target.user_id}", headers=auth_header(owner)
        )
        assert response.status_code == 403

    def test_looking_up_an_unknown_user_is_empty_for_admins(
        self, client: TestClient, admin_user: User
    ) -> None:
        assert (
            client.get(
                "/tokens/permission?user_id=nobody", headers=auth_header(admin_user)
            ).json()
            == []
        )


# --------------------------------------------------------------------------------------
# all together
# --------------------------------------------------------------------------------------


class TestTokenWorkflow:
    """End-to-end flow across all permission levels."""

    def test_submit_grant_pause_edit_revoke(
        self,
        client: TestClient,
        make_user: Callable[..., User],
        admin_user: User,
        stored_token: Callable[[str], dict[str, Any]],
    ) -> None:
        owner = make_user(email="workflow-owner@example.org")
        member = make_user(email="workflow-member@example.org")

        # A regular user submits a token and immediately owns it.
        token_id = client.post(
            "/tokens/submit",
            headers=auth_header(owner),
            json={"wrapper": "pubmed", "token": "workflow-key", "comment": "study"},
        ).json()
        assert listed_ids(client.get("/tokens/list", headers=auth_header(owner))) == [
            token_id
        ]

        # A colleague without permission can neither see nor use it.
        assert listed_ids(client.get("/tokens/list", headers=auth_header(member))) == []
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(member),
                json={"token_id": token_id, "is_active": False},
            ).status_code
            == 403
        )

        # The admin grants read access, which does not include editing.
        client.post(
            "/tokens/permissions",
            headers=auth_header(admin_user),
            json=permission_body(member, token_id),
        )
        assert listed_ids(client.get("/tokens/list", headers=auth_header(member))) == [
            token_id
        ]
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(member),
                json={"token_id": token_id, "is_active": False},
            ).status_code
            == 403
        )

        # The owner may pause the token, but not change its content.
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(owner),
                json={"token_id": token_id, "is_active": False},
            ).status_code
            == 200
        )
        assert stored_token(token_id)["is_active"] is False
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(owner),
                json={"token_id": token_id, "token": "other-key"},
            ).status_code
            == 403
        )

        # The admin repairs the record and brings the token back.
        assert (
            client.post(
                "/tokens/update",
                headers=auth_header(admin_user),
                json={
                    "token_id": token_id,
                    "token": "rotated-key",
                    "comment": "study (rotated)",
                    "is_active": True,
                },
            ).status_code
            == 200
        )
        assert stored_token(token_id)["token"] == "rotated-key"
        assert listed_ids(client.get("/tokens/list", headers=auth_header(member))) == [
            token_id
        ]

        # Revoking the permissions takes the token out of everyone's reach, except
        # the admin's (who may still see it, but not delete it - see `TestDeleteToken`).
        assert (
            client.request(
                "DELETE",
                "/tokens/permissions",
                headers=auth_header(admin_user),
                json=[
                    *permission_body(owner, token_id, is_owner=True),
                    *permission_body(member, token_id),
                ],
            ).json()
            == 2
        )
        assert listed_ids(client.get("/tokens/list", headers=auth_header(owner))) == []
        assert listed_ids(client.get("/tokens/list", headers=auth_header(member))) == []
        assert listed_ids(
            client.get("/tokens/list", headers=auth_header(admin_user))
        ) == [token_id]
