"""``Config`` reading of the plain-text list setting ``ods_robot_access_token``."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from academic_apis.utils.config import Config

ENV_VAR = "AAI_ODS_ROBOT_ACCESS_TOKENS"


@pytest.fixture
def make_config(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str | None], Config]:
    """Build a ``Config`` as if ``AAI_ODS_ROBOT_ACCESS_TOKEN`` were set to a value."""

    def factory(value: str | None) -> Config:
        if value is None:
            monkeypatch.delenv(ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(ENV_VAR, value)
        return Config()

    return factory


def test_unset_stays_none(make_config: Callable[[str | None], Config]) -> None:
    assert make_config(None).ods_robot_access_tokens is None
    assert make_config("").ods_robot_access_tokens is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("aXk9_qLm2", {"aXk9_qLm2"}),
        ("aXk9_qLm2, bT4f-Zp8w", {"aXk9_qLm2", "bT4f-Zp8w"}),
        ("aXk9_qLm2;bT4f-Zp8w;cJ7n", {"aXk9_qLm2", "bT4f-Zp8w", "cJ7n"}),
        ("aXk9_qLm2  bT4f-Zp8w", {"aXk9_qLm2", "bT4f-Zp8w"}),
        ("\naXk9_qLm2\nbT4f-Zp8w\n", {"aXk9_qLm2", "bT4f-Zp8w"}),
        ('["aXk9_qLm2", "bT4f-Zp8w"]', {"aXk9_qLm2", "bT4f-Zp8w"}),
    ],
)
def test_random_strings_become_a_list(
    make_config: Callable[[str | None], Config], raw: str, expected: set[str]
) -> None:
    assert make_config(raw).ods_robot_access_tokens == expected


def test_json_list_still_works(make_config: Callable[[str | None], Config]) -> None:
    assert make_config("[]").ods_robot_access_tokens == set()
