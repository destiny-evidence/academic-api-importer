from datetime import datetime
from enum import Enum, StrEnum
from json import JSONEncoder
from pathlib import Path
from typing import Any
from uuid import UUID

from destiny_sdk.references import Reference as ODSReference
from nacsos_data.models.items import AcademicItemModel
from pydantic import BaseModel


class Mode(Enum):
    READ = "read"
    WRITE = "write"


class Format(str, Enum):
    ODS = "ods"
    NACSOS = "nacsos"


class Wrapper(str, Enum):
    SCOPUS = "scopus"
    WOS = "wos"
    PUBMED = "pubmed"
    DIMENSIONS = "dimensions"
    OPENALEX = "openalex"


class DictLikeEncoder(JSONEncoder):
    def default(self, o: object) -> Any:  # noqa: ANN401
        if isinstance(o, datetime):
            return o.strftime("%Y-%m-%dT%H:%M:%S")
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, BaseModel):
            return o.model_dump()
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Enum):
            return str(o.value)
        if isinstance(o, StrEnum):
            return o.value
        return JSONEncoder.default(self, o)


def assert_int(obj: object | None) -> int:
    if obj is None:
        raise ValueError("Value is missing!")
    if type(obj) is int:
        return obj
    raise ValueError(f"{type(obj)} not an integer: {obj}!")


def assert_str(obj: object | None) -> str:
    if obj is None:
        raise ValueError("Value is missing!")
    if type(obj) is str:
        return obj
    raise ValueError(f"{type(obj)} not a string: {obj}!")


type Record = AcademicItemModel | ODSReference
