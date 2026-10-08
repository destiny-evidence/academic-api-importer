"""
Tests for ``academic_apis.db.permissions``: the ``has_query_permission`` helper the
API guards query endpoints with, and the ``has_query_ownership`` wrapper around it.

The rules covered here (``query_id=None`` means "all queries at once"):

======================  ==============  ==================  ======================  ======================
user                    ``query_id``    no permission row   row with ``is_owner``   row with ``is_owner``
                                          for the query              = False                  = True
======================  ==============  ==================  ======================  ======================
inactive                   anything     False               False                   False
active admin               anything     True                True                    True      (``admin_override=True``)
active admin               anything     False               True                    True      (``admin_override=False``)
active, permissions      ``None``       False               False                   False
active, permissions      an ID          False               True                    True
======================  ==============  ==================  ======================  ======================

``require_ownership=True`` (what :func:`~academic_apis.db.permissions.has_query_ownership`
passes) downgrades the "row with ``is_owner=False``" column to ``False``: only owner rows
and the admin override still grant access. Permission rows are matched on both the user
and the query, so a row never leaks to a different user or a different query.

has_query_permission
- Inactive users — denied in all configurations (owner row, admin, query_id=None, every flag combination)
- Admin with admin_override=True — passes for any ID, including None and unknown query IDs, with no rows stored
- Admin with admin_override=False — treated like a normal user: needs a permission row; query_id=None never matches (the column is non-null, so "all queries" stays admin-override-only)
- query_id=None for non-admins — denied even when the user owns a query
- Row with is_owner=False — grants access, but not with require_ownership=True
- Row with is_owner=True — grants both
- Row matching — (user_id, query_id) is the key: a grant for user A doesn't leak to user B, nor to another query
- Unknown query ID — returns False without raising

has_query_ownership
- Delegation verified: owner row → True, non-owner grant / no row → False
- Admin override owns anything; without the override, an owner row is required
- Inherits the activity check (inactive admin owner → False)

The tests reuse the shared conftest.py fixtures (make_user, make_query, admin_user, owned_query, …) and add a local synchronous session fixture, since the helpers take a plain Session (unlike the async db_session the endpoints use).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlmodel import Session

from academic_apis.db import User, make_engine
from academic_apis.db.permissions import has_query_ownership, has_query_permission

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# --------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    """
    Synchronous session for the functions under test.

    The permission helpers take a plain :class:`~sqlmodel.Session`, unlike the API
    endpoints that use them; the data itself is stored through the same database the
    async fixtures write to.
    """
    with Session(bind=make_engine()) as session:
        yield session


# --------------------------------------------------------------------------------------
# inactive users
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("admin_override", [True, False])
@pytest.mark.parametrize("require_ownership", [True, False])
def test_inactive_users_never_have_permission(
    session: Session,
    inactive_user: User,
    make_query: Callable[..., int],
    admin_override: bool,
    require_ownership: bool,
) -> None:
    """Even an inactive admin with an owner row is denied in every configuration."""
    query_id = make_query(owner=inactive_user)

    assert not has_query_permission(
        session=session,
        user=inactive_user,
        query_id=query_id,
        require_ownership=require_ownership,
        admin_override=admin_override,
    )
    assert not has_query_permission(
        session=session,
        user=inactive_user,
        query_id=None,
        admin_override=admin_override,
    )


def test_inactive_owner_fails_ownership_check(
    session: Session, inactive_user: User, make_query: Callable[..., int]
) -> None:
    """``has_query_ownership`` inherits the activity check, admin or not."""
    query_id = make_query(owner=inactive_user)

    assert not has_query_ownership(session, inactive_user, query_id)


# --------------------------------------------------------------------------------------
# admins
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("query_id", [None, 999])
def test_admin_override_grants_everything(
    session: Session, admin_user: User, query_id: int | None
) -> None:
    """With the override, an admin passes for any query ID, without any row stored."""
    assert has_query_permission(session=session, user=admin_user, query_id=query_id)


def test_admin_without_override_still_needs_a_permission_row(
    session: Session,
    admin_user: User,
    make_query: Callable[..., int],
) -> None:
    """``admin_override=False`` treats an admin like any other user."""
    ungranted = make_query()
    granted = make_query(users=[(admin_user, False)])
    owned = make_query(owner=admin_user)

    assert not has_query_permission(
        session=session, user=admin_user, query_id=ungranted, admin_override=False
    )
    assert has_query_permission(
        session=session, user=admin_user, query_id=granted, admin_override=False
    )
    assert has_query_permission(
        session=session, user=admin_user, query_id=owned, admin_override=False
    )


def test_admin_without_override_cannot_query_all(
    session: Session, admin_user: User
) -> None:
    """``query_id=None`` never matches a row (the column is non-null), admins included."""
    assert not has_query_permission(
        session=session, user=admin_user, query_id=None, admin_override=False
    )


def test_ownership_admin_override(
    session: Session, admin_user: User, make_query: Callable[..., int]
) -> None:
    """Admins own every query by override; without it they need an owner row."""
    foreign_query = make_query()
    owned = make_query(owner=admin_user)

    assert has_query_ownership(session, admin_user, foreign_query)
    assert has_query_ownership(session, admin_user, owned, admin_override=False)
    assert not has_query_ownership(
        session, admin_user, foreign_query, admin_override=False
    )


# --------------------------------------------------------------------------------------
# users with query permissions
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("is_owner", [True, False])
def test_query_id_none_requires_admin(
    session: Session,
    make_user: Callable[..., User],
    make_query: Callable[..., int],
    is_owner: bool,
) -> None:
    """No active non-admin may act on "all queries", not even a query's owner."""
    user = make_user(email="owner@example.org")
    make_query(users=[(user, is_owner)])  # a grant exists, and still: "all" is denied

    assert not has_query_permission(session=session, user=user, query_id=None)


def test_plain_row_grants_access_but_not_ownership(
    session: Session,
    normal_user: User,
    other_user: User,
    make_query: Callable[..., int],
) -> None:
    """A row with ``is_owner=False`` passes the default check but not ownership."""
    granted = make_query(users=[(normal_user, False)])

    assert has_query_permission(session=session, user=normal_user, query_id=granted)
    assert not has_query_permission(
        session=session,
        user=normal_user,
        query_id=granted,
        require_ownership=True,
    )
    assert not has_query_ownership(session, normal_user, granted)

    # the other user has no row at all and is denied everywhere
    assert not has_query_permission(session=session, user=other_user, query_id=granted)
    assert not has_query_ownership(session, other_user, granted)


def test_owner_row_grants_access_and_ownership(
    session: Session, normal_user: User, owned_query: int
) -> None:
    """A row with ``is_owner=True`` passes every variant of both checks."""
    assert has_query_permission(session=session, user=normal_user, query_id=owned_query)
    assert has_query_permission(
        session=session,
        user=normal_user,
        query_id=owned_query,
        require_ownership=True,
    )
    assert has_query_ownership(session, normal_user, owned_query)


def test_rows_are_matched_per_user_and_query(
    session: Session,
    normal_user: User,
    other_user: User,
    make_query: Callable[..., int],
) -> None:
    """``(user_id, query_id)`` is the key: neither half may differ from the row's."""
    granted = make_query(users=[(normal_user, False)])
    other_query = make_query()

    assert has_query_permission(session=session, user=normal_user, query_id=granted)
    # same user, different query -> no row
    assert not has_query_permission(
        session=session, user=normal_user, query_id=other_query
    )
    # same query, different user -> no row
    assert not has_query_permission(session=session, user=other_user, query_id=granted)


def test_unknown_query_id_has_no_rows(session: Session, normal_user: User) -> None:
    """A query ID nobody has a row for is denied without raising."""
    assert not has_query_permission(session=session, user=normal_user, query_id=999)
