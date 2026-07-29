from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from .config import CONFIG_ENV_VAR, DEFAULT_CONFIG_FILE, Config, settings
from .logging import init_logging
from .types import (
    DictLikeEncoder,
    Format,
    Mode,
    Wrapper,
    assert_int,
    assert_str,
)

if TYPE_CHECKING:
    from typing import Any


def clear_empty(obj: Any | None) -> Any | None:  # noqa: ANN401
    """
    Recursively checks the object for empty-like things and explicitly sets them to None (or drops keys)

    :param obj:
    :return:
    """
    if obj is None:
        return None

    if isinstance(obj, str):
        if len(obj) == 0:
            return None
        return obj

    if isinstance(obj, list):
        tmp_l = [clear_empty(li) for li in obj]
        tmp_l = [li for li in tmp_l if li is not None]
        if len(tmp_l) > 0:
            return tmp_l
        return None

    if isinstance(obj, dict):
        tmp_d = {key: clear_empty(val) for key, val in obj.items()}
        tmp_d = {key: val for key, val in tmp_d.items() if val is not None}
        if len(tmp_d) > 0:
            return tmp_d
        return None

    return obj


def as_uuid(val: str | uuid.UUID | None = None) -> uuid.UUID | None:
    if val is None:
        return None

    if isinstance(val, uuid.UUID):
        return val
    if isinstance(val, str):
        return uuid.UUID(val)

    raise TypeError(f"Not a valid UUID: {type(val)}")


def as_int(val: object | None, default: int = 0) -> int:
    if val is None:
        return default
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.isdecimal():
        return int(val)
    return default


__all__ = [
    "CONFIG_ENV_VAR",
    "DEFAULT_CONFIG_FILE",
    "Config",
    "DictLikeEncoder",
    "Format",
    "Mode",
    "Wrapper",
    "as_uuid",
    "assert_int",
    "assert_str",
    "clear_empty",
    "init_logging",
    "settings",
]
