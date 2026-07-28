from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from academic_apis.utils.types import Format, Record, Wrapper

if TYPE_CHECKING:
    import uuid
    from collections.abc import Generator, Iterable
    from pathlib import Path

    from destiny_sdk.references import Reference as ODSReference
    from nacsos_data.models.items import AcademicItemModel

base_logger = logging.getLogger(__name__)


class AbstractWrapper(ABC):
    logger: logging.Logger

    def __init__(
        self,
        api_key: str | None = None,
        proxy: str | None = None,
        max_req_per_sec: int = 5,
        max_retries: int = 5,
        backoff_rate: float = 5.0,
        ignored_exceptions: list[type[Exception]] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.api_key = api_key
        self.proxy = proxy
        self.max_req_per_sec = max_req_per_sec
        self.max_retries = max_retries
        self.backoff_rate = backoff_rate
        self.ignored_exceptions = ignored_exceptions or []
        self.api_feedback: dict[str, int] = {}
        self.n_results: int | None = None

        self.logger = (
            logging.getLogger(type(self).__name__) if logger is None else logger
        )

    @classmethod  # type: ignore[misc]
    @property
    @abstractmethod
    def page_max(cls) -> int:
        raise NotImplementedError

    @classmethod  # type: ignore[misc]
    @property
    @abstractmethod
    def wrapper(cls) -> Wrapper:
        raise NotImplementedError

    @abstractmethod
    def fetch_raw(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> Generator[dict[str, Any]]:
        raise NotImplementedError

    @classmethod
    def translate_record(
        cls,
        fmt: Format,
        record: dict[str, Any],
    ) -> Record:
        if fmt == Format.ODS:
            return cls.translate_record_ods(record)
        if fmt == Format.NACSOS:
            return cls.translate_record_nacsos(record)
        raise NotImplementedError(f"Unknown format: {fmt}")

    @classmethod
    @abstractmethod
    def translate_record_ods(
        cls,
        record: dict[str, Any],
    ) -> ODSReference:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def translate_record_nacsos(
        cls,
        record: dict[str, Any],
        project_id: str | uuid.UUID | None = None,
    ) -> AcademicItemModel:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def generate_subqueries(
        cls,
        query: str,
        py_start: int = 2010,
        py_end: int | None = None,
        py_steps: int = 2,
        use_py_filter: bool = False,
    ) -> Generator[str]:
        raise NotImplementedError

    def fetch_n_results(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> int | None:
        for _record in self.fetch_raw(query=query, params=params):
            break
        return self.n_results

    def download_raw(
        self,
        query: str,
        target: Path,
        params: dict[str, Any] | None = None,
    ) -> None:
        collect_raw_jsonl(target=target, items=self.fetch_raw(query, params=params))

    @classmethod
    def read_translated(
        cls,
        fmt: Format,
        source: Path,
    ) -> Generator[Record]:
        with source.open() as f_in:
            for li, line in enumerate(f_in):
                try:
                    record = json.loads(line)
                    yield cls.translate_record(fmt=fmt, record=record)
                except json.decoder.JSONDecodeError as e:
                    base_logger.exception(f"Failed to decode record in line {li}")
                    base_logger.exception(f"Error message: {e}")

    @classmethod
    def convert_file(
        cls,
        fmt: Format,
        source: Path,
        target: Path,
    ) -> None:
        with target.open("w") as f_out:
            f_out.writelines(
                (
                    item.model_dump_json(exclude_defaults=True) + "\n"
                    for item in cls.read_translated(fmt=fmt, source=source)
                ),
            )


def _assert_target(target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"File {target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)


def collect_raw_jsonl(target: Path, items: Iterable[dict[str, Any]]) -> None:
    _assert_target(target)
    with target.open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(item) + "\n" for item in items)


def collect_jsonl(target: Path, items: Iterable[AcademicItemModel]) -> None:
    _assert_target(target)
    with target.open("w", encoding="utf-8") as f:
        f.writelines(
            (item.model_dump_json(exclude_defaults=True) + "\n" for item in items),
        )
