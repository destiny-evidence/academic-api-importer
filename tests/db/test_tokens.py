"""
Tests for ``academic_apis.db.tokens``: which stored API key the helpers hand out.

Both helpers return the *least recently used* of the qualifying tokens (a token that
was never used counts as used longest ago) and return ``None`` when none qualifies:

=============================  =========================  =============================
condition                      ``get_available_token``    ``get_available_token_for_user``
=============================  =========================  =============================
token wrapper must match       yes                        yes
token must be active           yes                        yes
lock must be absent or past    yes                        yes
user-token permission          any                        ``user_id`` itself
user-query permission          ``query_id`` as owner      not looked at
=============================  =========================  =============================

For ``get_available_token`` the two permissions must belong to the *same* user; the
user's own activity is not looked at anywhere.

- get_available_token(query_id, wrapper) (12 tests)
    - returns the token when the same user owns the query and holds any token permission (owned or shared); fields stay readable after the session closes (detached via expunge)
    - filters out: query permission without is_owner, token access without query ownership, permissions split across different users, requests for a query the token holder can't run
    - parametrized negatives: inactive, still-locked (locked_until in the future), and wrong-wrapper tokens → ``None``
    - expired lock → usable again
    - selection: least recently used first; last_used IS NULL wins; lock filtering happens before ordering
    - same token reachable through two qualifying users returns once (join duplicates don't break ``one_or_none()``)
- get_available_token_for_user(user_id, wrapper) (10 tests)
    - owned and shared tokens returned; no permission, unknown user, inactive/locked/wrong-wrapper → ``None``
    - same ordering and lock semantics, plus: an inactive user still gets their token (the query never checks users.is_active)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from academic_apis.db.tokens import get_available_token, get_available_token_for_user
from academic_apis.utils.types import Wrapper

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from academic_apis.db import User

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run one of the async database helpers in a synchronous test."""
    return asyncio.run(coroutine)


def utc(offset_hours: float = 0.0) -> datetime:
    """Aware UTC timestamp, shifted by ``offset_hours``, as the application creates them."""
    return datetime.now(tz=UTC) + timedelta(hours=offset_hours)


#: Token states that disqualify a token, however well it otherwise fits.
UNUSABLE: dict[str, dict[str, Any]] = {
    "inactive": {"is_active": False},
    "locked": {"locked_until": utc(1)},
    "other-wrapper": {"wrapper": Wrapper.WOS},
}

UNUSABLE_CASES = pytest.mark.parametrize(
    "token_kwargs", UNUSABLE.values(), ids=UNUSABLE
)

# --------------------------------------------------------------------------------------
# get_available_token
# --------------------------------------------------------------------------------------


class TestGetAvailableToken:
    def test_the_token_of_a_query_owner_is_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """The owner of a query may run it with a token they also own."""
        user = make_user(email="owner@example.org")
        token_id = make_token(owner=user, token="scopus-key")
        query_id = make_query(owner=user)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token.token_id == token_id
        # Detached: the fields are still readable after the session has been closed.
        assert token.token == "scopus-key"

    def test_a_shared_token_qualifies_when_the_user_owns_the_query(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """Owning the token is not required; any permission on it is enough."""
        user = make_user(email="borrower@example.org")
        token_id = make_token(
            owner=make_user(email="someone-else@example.org"), users=[(user, False)]
        )
        query_id = make_query(owner=user)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token.token_id == token_id

    def test_query_access_without_ownership_is_not_enough(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """The query permission must carry ``is_owner``."""
        user = make_user(email="reader@example.org")
        make_token(owner=user)
        query_id = make_query(users=[(user, False)])
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token is None

    def test_token_access_without_query_ownership_is_not_enough(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """A token is not available for queries the token holder cannot run."""
        token_holder = make_user(email="token-holder@example.org")
        outsider = make_user(email="outsider@example.org")
        make_token(owner=token_holder)
        query_id = make_query(owner=outsider)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token is None

    def test_grants_to_different_users_do_not_combine(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """One user must hold *both* permissions; they are not joined across users."""
        query_owner = make_user(email="query-owner@example.org")
        token_owner = make_user(email="token-owner@example.org")
        make_token(owner=token_owner)
        query_id = make_query(owner=query_owner)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token is None

    @UNUSABLE_CASES
    def test_unusable_tokens_leave_no_result(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
        token_kwargs: dict[str, Any],
    ) -> None:
        """Inactive, still locked, and foreign-wrapper tokens are filtered out."""
        user = make_user(email="blocked@example.org")
        make_token(owner=user, **token_kwargs)
        query_id = make_query(owner=user)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token is None

    def test_a_token_with_an_expired_lock_is_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """``locked_until`` in the past means the token is usable again."""
        user = make_user(email="unlocked@example.org")
        token_id = make_token(owner=user, locked_until=utc(-1))
        query_id = make_query(owner=user)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token.token_id == token_id

    def test_the_least_recently_used_token_is_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """Candidates are spread over time; the oldest use wins."""
        user = make_user(email="chooser@example.org")
        make_token(owner=user, token="middle", last_used=utc(-2))
        make_token(owner=user, token="newest", last_used=utc(-1))
        make_token(owner=user, token="oldest", last_used=utc(-3))
        query_id = make_query(owner=user)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token.token == "oldest"

    def test_an_unused_token_is_preferred_over_a_used_one(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """``last_used is NULL`` sorts first, so never-used tokens are used first."""
        user = make_user(email="fresh@example.org")
        make_token(owner=user, token="long-abandoned", last_used=utc(-1000))
        token_id = make_token(owner=user, token="never-used", last_used=None)
        query_id = make_query(owner=user)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token.token_id == token_id
        assert token.token == "never-used"

    def test_the_lock_filter_applies_before_the_ordering(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """The locked token is the least recently used, but must not be picked."""
        user = make_user(email="skip-locked@example.org")
        make_token(owner=user, token="locked", last_used=utc(-10), locked_until=utc(1))
        token_id = make_token(owner=user, token="usable", last_used=utc(-5))
        query_id = make_query(owner=user)
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token.token_id == token_id
        assert token.token == "usable"

    def test_a_token_reachable_through_two_users_is_returned_once(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """Two users qualify with the same token; the join must not break ``one()``."""
        owner = make_user(email="owner-of-both@example.org")
        borrower = make_user(email="borrower-of-both@example.org")
        token_id = make_token(owner=owner, users=[(borrower, False)])
        query_id = make_query(owner=owner, users=[(borrower, True)])
        token = run(get_available_token(query_id=query_id, wrapper=Wrapper.SCOPUS))
        assert token.token_id == token_id

    def test_a_token_is_only_available_for_queries_its_owner_may_run(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        make_query: Callable[..., int],
    ) -> None:
        """The token holder owns *a* query, but not the one that is asked about."""
        user = make_user(email="one-query@example.org")
        make_token(owner=user)
        make_query(owner=user)  # the query this token may be used for
        stranger = make_user(email="stranger@example.org")
        stranger_query_id = make_query(query="TITLE-ABS-KEY(stranger)", owner=stranger)
        token = run(
            get_available_token(query_id=stranger_query_id, wrapper=Wrapper.SCOPUS)
        )
        assert token is None


# --------------------------------------------------------------------------------------
# get_available_token_for_user
# --------------------------------------------------------------------------------------


class TestGetAvailableTokenForUser:
    def test_a_token_owned_by_the_user_is_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """An owned token qualifies; queries play no role here."""
        user = make_user(email="owner@example.org")
        token_id = make_token(owner=user, token="scopus-key")
        token = run(
            get_available_token_for_user(user_id=user.user_id, wrapper=Wrapper.SCOPUS)
        )
        assert token.token_id == token_id
        # Detached: the fields are still readable after the session has been closed.
        assert token.token == "scopus-key"

    def test_a_shared_token_is_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """Any permission qualifies, ownership of the token is not required."""
        owner = make_user(email="someone-else@example.org")
        borrower = make_user(email="borrower@example.org")
        token_id = make_token(owner=owner, users=[(borrower, False)])
        token = run(
            get_available_token_for_user(
                user_id=borrower.user_id, wrapper=Wrapper.SCOPUS
            )
        )
        assert token.token_id == token_id

    def test_a_token_of_another_user_is_not_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """Without a permission row, the token is invisible to the user."""
        make_token(owner=make_user(email="someone-else@example.org"))
        outsider = make_user(email="outsider@example.org")
        token = run(
            get_available_token_for_user(
                user_id=outsider.user_id, wrapper=Wrapper.SCOPUS
            )
        )
        assert token is None

    def test_an_unknown_user_leaves_no_result(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """A user ID missing from the database simply has no available token."""
        make_token(owner=make_user(email="someone-else@example.org"))
        token = run(
            get_available_token_for_user(user_id="ghost-user", wrapper=Wrapper.SCOPUS)
        )
        assert token is None

    @UNUSABLE_CASES
    def test_unusable_tokens_leave_no_result(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
        token_kwargs: dict[str, Any],
    ) -> None:
        """Inactive, still locked, and foreign-wrapper tokens are filtered out."""
        user = make_user(email="blocked@example.org")
        make_token(owner=user, **token_kwargs)
        token = run(
            get_available_token_for_user(user_id=user.user_id, wrapper=Wrapper.SCOPUS)
        )
        assert token is None

    def test_a_token_with_an_expired_lock_is_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """``locked_until`` in the past means the token is usable again."""
        user = make_user(email="unlocked@example.org")
        token_id = make_token(owner=user, locked_until=utc(-1))
        token = run(
            get_available_token_for_user(user_id=user.user_id, wrapper=Wrapper.SCOPUS)
        )
        assert token.token_id == token_id

    def test_the_least_recently_used_token_is_returned(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """Candidates are spread over time; the oldest use wins."""
        user = make_user(email="chooser@example.org")
        make_token(owner=user, token="middle", last_used=utc(-2))
        make_token(owner=user, token="newest", last_used=utc(-1))
        make_token(owner=user, token="oldest", last_used=utc(-3))
        token = run(
            get_available_token_for_user(user_id=user.user_id, wrapper=Wrapper.SCOPUS)
        )
        assert token.token == "oldest"

    def test_an_unused_token_is_preferred_over_a_used_one(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """``last_used is NULL`` sorts first, so never-used tokens are used first."""
        user = make_user(email="fresh@example.org")
        make_token(owner=user, token="long-abandoned", last_used=utc(-1000))
        token_id = make_token(owner=user, token="never-used", last_used=None)
        token = run(
            get_available_token_for_user(user_id=user.user_id, wrapper=Wrapper.SCOPUS)
        )
        assert token.token_id == token_id
        assert token.token == "never-used"

    def test_the_lock_filter_applies_before_the_ordering(
        self,
        make_user: Callable[..., User],
        make_token: Callable[..., str],
    ) -> None:
        """The locked token is the least recently used, but must not be picked."""
        user = make_user(email="skip-locked@example.org")
        make_token(owner=user, token="locked", last_used=utc(-10), locked_until=utc(1))
        token_id = make_token(owner=user, token="usable", last_used=utc(-5))
        token = run(
            get_available_token_for_user(user_id=user.user_id, wrapper=Wrapper.SCOPUS)
        )
        assert token.token_id == token_id
        assert token.token == "usable"

    def test_an_inactive_user_still_gets_their_token(
        self, make_token: Callable[..., str], inactive_user: User
    ) -> None:
        """The helper does not look at ``users.is_active``, only at the token."""
        token_id = make_token(owner=inactive_user)
        token = run(
            get_available_token_for_user(
                user_id=inactive_user.user_id, wrapper=Wrapper.SCOPUS
            ),
        )
        assert token.token_id == token_id
