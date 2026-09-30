from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa
from httpx import Response
from httpx._status_codes import codes as http
from nacsos_data.models.items import AcademicItemModel
from nacsos_data.models.items.academic import AcademicAuthorModel, AffiliationModel
from nacsos_data.util import as_uuid, clear_empty, get
from sqlmodel import col

from academic_apis.db import AccessToken, db_session
from academic_apis.utils import Wrapper, as_int

from ._abc import AbstractWrapper, OnLoopInfo
from ._client import RequestClient

if TYPE_CHECKING:
    from collections.abc import Generator

    from destiny_sdk.references import Reference as ODSReference


def get_title(obj: dict[str, Any]) -> str | None:
    return obj.get("dc:title")


def get_abstract(obj: dict[str, Any]) -> str | None:
    return obj.get("dc:description")


def get_doi(obj: dict[str, Any]) -> str | None:
    return obj.get("prism:doi")


def get_id(obj: dict[str, Any]) -> str | None:
    return obj.get("eid", obj.get("dc:identifier"))


def get_py(obj: dict[str, Any]) -> int | None:
    py: str | None = obj.get("prism:coverDate")
    if py and len(py) >= 4:  # noqa: PLR2004
        return int(py[:4])
    return None


def get_keywords(obj: dict[str, Any]) -> list[str] | None:
    kw = obj.get("authkeywords", "").split(" | ")
    return clear_empty(kw)


def translate_authors(record: dict[str, Any]) -> list[AcademicAuthorModel] | None:
    affiliations = {
        aff["afid"]: AffiliationModel(
            s2_id=aff["afid"],
            name=aff.get("affilname"),
            country=aff.get("affiliation-country"),
        )
        for aff in record.get("affiliation", [])
    }

    authors: list[dict[str, Any]] | None = record.get("author")
    if not authors or authors is None:
        return None
    return [
        AcademicAuthorModel(
            s2_id=author.get("authid"),
            name=cast("str", author.get("authname")),
            affiliations=clear_empty(
                [affiliations.get(aff["$"]) for aff in author.get("afid", [])],
            ),
        )
        for author in authors
        if author.get("authname")
    ]


class ScopusWrapper(AbstractWrapper):
    # https://dev.elsevier.com/api_key_settings.html
    # COMPLETE view / Max 25 results per request with a weekly quota of 20k requests at 9 queries per second
    page_max = 25
    wrapper = Wrapper.SCOPUS

    async def on_response(self, response: Response | None) -> None:
        if response:
            self.api_feedback = {
                "scopus_requests_limit": response.headers.get("x-ratelimit-limit"),
                "scopus_requests_remaining": response.headers.get(
                    "x-ratelimit-remaining"
                ),
                "scopus_requests_reset": response.headers.get("x-ratelimit-reset"),
                # only set to QUOTA_EXCEEDED when quota exceeded
                "scopus_requests_exceeded": response.headers.get("x-els-status"),
                "status": response.status_code,
            }
            if response.status_code != http.OK:
                self.api_feedback |= {"message": response.text}

        if self.skip_token_logging:
            return

        async with db_session() as session:
            locked_until: dt.datetime | None = None
            if (
                as_int(self.api_feedback.get("scopus_requests_remaining"), default=0)
                < self.page_max
                or self.api_feedback.get("scopus_requests_exceeded") == "QUOTA_EXCEEDED"
            ):
                timestamp: str | int | None = self.api_feedback.get(
                    "scopus_requests_reset",
                )
                if (
                    timestamp is not None
                    and isinstance(timestamp, int)
                    and timestamp > 1780000000  # noqa: PLR2004
                ):
                    locked_until = dt.datetime.fromtimestamp(timestamp, tz=dt.UTC)
                else:
                    locked_until = dt.datetime.now(tz=dt.UTC) + dt.timedelta(
                        days=2,
                    )  # fallback to locking for two days

            session.exec(
                sa.update(AccessToken)
                .values(
                    last_feedback=self.api_feedback,
                    last_used=dt.datetime.now(tz=dt.UTC),
                    locked_until=locked_until,
                )
                .where(
                    col(AccessToken.wrapper) == self.wrapper,
                    col(AccessToken.token) == self.api_key,
                ),
            )
            session.commit()
        return

    async def fetch_raw(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any]]:
        """
        Scopus API wrapper for downloading all records for a given query.

        API overview
        https://dev.elsevier.com/

        API Documentation:
        https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl

        :param query:
        :param params:
        :return:
        """
        if self.api_key is None:
            raise AssertionError("Missing API key!")

        async with RequestClient(
            backoff_rate=self.backoff_rate,
            max_req_per_sec=self.max_req_per_sec,
            max_retries=self.max_retries,
            proxy=self.proxy,
            timeout=120,
            logger=self.logger.getChild("client"),
            on_response=self.on_response,
        ) as request_client:
            next_cursor: str | None = "*"
            n_pages = 0
            n_records = 0
            while True:
                self.logger.info(f"Fetching page {n_pages}...")

                page = await request_client.post(
                    "https://api.elsevier.com/content/search/scopus",
                    params={
                        "cursor": next_cursor,
                        # https://dev.elsevier.com/sc_search_views.html
                        "view": "COMPLETE",
                        "count": self.page_max,
                    }
                    | (params or {}),
                    headers={
                        "Accept": "application/json",
                        "X-ELS-APIKey": self.api_key,
                    },
                    data={
                        "query": query,
                    },
                )

                n_pages += 1
                data = page.json()

                next_cursor = get(
                    data,
                    "search-results",
                    "cursor",
                    "@next",
                    default=None,
                )
                entries: list[dict[str, Any]] | None = get(
                    data,
                    "search-results",
                    "entry",
                    default=[],
                )
                self.n_results: int | None = get(
                    data,
                    "search-results",
                    "opensearch:totalResults",
                    default=0,
                )

                if (
                    entries is None
                    or len(entries) == 0
                    or self.n_results is None
                    or self.n_results == 0
                ):
                    break

                if len(entries) == 1 and entries[0].get("error") is not None:
                    break

                for entry in entries:
                    yield entry

                n_records += len(entries)

                await self.on_loop(
                    OnLoopInfo(
                        n_results=self.n_results,
                        n_retrieved=n_records,
                        api_feedback=self.api_feedback,
                    ),
                )

                self.logger.debug(
                    f"Found {n_records}/{self.n_results} records after processing page {n_pages} {self.api_feedback}",
                )

    @classmethod
    def translate_record_nacsos(
        cls,
        record: dict[str, Any],
        project_id: str | uuid.UUID | None = None,
    ) -> AcademicItemModel:
        return AcademicItemModel(
            item_id=uuid.uuid4(),
            project_id=as_uuid(project_id),
            doi=get_doi(record),
            title=get_title(record),
            scopus_id=get_id(record),
            text=get_abstract(record),
            # title_slug  # not required
            publication_year=get_py(record),
            source=record.get("prism:publicationName"),
            keywords=get_keywords(record),
            authors=translate_authors(record),
            meta={"scopus-api": clear_empty(record)},
        )

    @classmethod
    def translate_record_ods(cls, record: dict[str, Any]) -> ODSReference:
        raise NotImplementedError

    @classmethod
    def generate_subqueries(
        cls,
        query: str,
        py_start: int = 2010,
        py_end: int | None = None,
        py_steps: int = 2,
        use_py_filter: bool = False,
    ) -> Generator[str]:
        alphabet = "abcdefghijklmnopqrstuvwxyz"

        if py_end is None:
            py_end = dt.datetime.now(tz=dt.UTC).year

        if py_start >= py_end:
            raise AssertionError(
                f"Start year ({py_start}) should be strictly smaller than end year ({py_end})",
            )

        for char1 in alphabet:
            for char2 in alphabet:
                firstauth = f"FIRSTAUTH ( {char1}{char2}* )"

                if use_py_filter:
                    yield f"( {query} ) AND {firstauth} AND ( PUBYEAR BEF {py_start} )"
                    last_end_py = py_start
                    for py in range(py_start - 1, py_end - 1, py_steps):
                        last_end_py = py + py_steps + 1
                        yield f"( {query} ) AND {firstauth} AND ( PUBYEAR AFT {py} ) AND ( PUBYEAR BEF {last_end_py} )"
                    yield f"( {query} ) AND {firstauth} AND ( PUBYEAR AFT {last_end_py - 1} )"
                else:
                    yield f"( {query} ) AND {firstauth}"
