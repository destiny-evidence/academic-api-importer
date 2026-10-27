from __future__ import annotations

from typing import Any, get_args

from destiny_sdk.references import ReferenceFileInput
from nacsos_data.models.items import AcademicItemModel

from academic_apis.utils import Format, Wrapper

from ._abc import OnLoopInfo
from .pubmed import PubmedWrapper
from .scopus import ScopusWrapper
from .scopus.parser_nacsos import translate_record_nacsos
from .scopus.parser_ods import ScopusParser

type APIWrapper = ScopusWrapper | PubmedWrapper

WRAPPERS: dict[str, type[APIWrapper]] = {
    wrapper.wrapper: wrapper for wrapper in get_args(APIWrapper.__value__)
}


def parse_object(
    obj: dict[str, Any] | None,
    source: Wrapper,
    target: Format,
) -> AcademicItemModel | ReferenceFileInput | None:
    if obj is None:
        return None
    if source == Wrapper.SCOPUS:
        if target == Format.ODS:
            return ScopusParser().parse_reference(obj)
        if target == Format.NACSOS:
            return translate_record_nacsos(obj)

    raise NotImplementedError(
        f"No parser available to convert from {source} format into {target} format."
    )


__all__ = ["WRAPPERS", "APIWrapper", "OnLoopInfo", "PubmedWrapper", "ScopusWrapper"]
