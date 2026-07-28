from __future__ import annotations

from typing import get_args

from ._abc import AbstractWrapper
from .pubmed import PubmedWrapper
from .scopus import ScopusWrapper

type APIWrapper[T: AbstractWrapper] = ScopusWrapper | PubmedWrapper

WRAPPERS: dict[str, type[APIWrapper]] = {
    wrapper.wrapper: wrapper for wrapper in get_args(APIWrapper.__value__)
}

__all__ = ["WRAPPERS", "APIWrapper", "PubmedWrapper", "ScopusWrapper"]
