from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from httpx import Response
from httpx._status_codes import codes as http
from nacsos_data.util import get
from sqlmodel import col

from academic_apis.db import AccessToken, db_session
from academic_apis.utils import Wrapper, as_int, drop_whitespaces, settings
from academic_apis.wrappers._abc import AbstractWrapper, OnLoopInfo
from academic_apis.wrappers._client import RequestClient

if TYPE_CHECKING:
    from collections.abc import Generator

    from destiny_sdk.references import ReferenceFileInput as ODSReference
    from nacsos_data.models.items import AcademicItemModel


class ScopusWrapper(AbstractWrapper):
    # https://dev.elsevier.com/api_key_settings.html
    # COMPLETE view / Max 25 results per request with a weekly quota of 20k requests at 9 queries per second
    page_max = 25
    wrapper = Wrapper.SCOPUS

    async def on_response(self, response: Response | None) -> None:
        if response:
            self.last_response = response
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
                < (settings.scopus_page_size or self.page_max)
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
            n_pages = 1
            n_records = 0
            while True:
                self.logger.info(f"Fetching page {n_pages}...")

                page = await request_client.post(
                    "https://api.elsevier.com/content/search/scopus",
                    params={
                        "cursor": next_cursor,
                        # https://dev.elsevier.com/sc_search_views.html
                        "view": "COMPLETE",
                        "count": (settings.scopus_page_size or self.page_max),
                    }
                    | (params or {}),
                    headers={
                        "Accept": "application/json",
                        "X-ELS-APIKey": self.api_key,
                    },
                    data={
                        "query": drop_whitespaces(query),
                    },
                )

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
                    self.logger.info(
                        "Response had no additional results, assuming to be done!"
                    )
                    break

                if len(entries) == 1 and entries[0].get("error") is not None:
                    self.logger.error(
                        "Had only one result entry which had an error code!"
                    )
                    self.logger.error(f"Error from response: {entries[0].get('error')}")
                    break

                for entry in entries:
                    yield entry

                # TODO: database entries are always 25 or less, was the "+=" already fixed before then?
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
                n_pages += 1

    @classmethod
    def translate_record_nacsos(
        cls,
        record: dict[str, Any],
        project_id: str | uuid.UUID | None = None,
    ) -> AcademicItemModel:
        from .parser_nacsos import translate_record_nacsos

        return translate_record_nacsos(record=record, project_id=project_id)

    @classmethod
    def translate_record_ods(cls, record: dict[str, Any]) -> ODSReference:
        from .parser_ods import ScopusParser

        return ScopusParser().parse_reference(
            entry=record, source="scopus", robot_version="1.0"
        )

    @classmethod
    def generate_subqueries(
        cls,
        query: str,
        py_start: int = 2010,
        py_end: int | None = None,
        py_steps: int = 2,
        use_py_filter: bool = False,
        use_firstauth_filter: bool = True,
    ) -> Generator[str]:
        alphabet = "abcdefghijklmnopqrstuvwxyz"

        if py_end is None:
            py_end = dt.datetime.now(tz=dt.UTC).year

        if py_start >= py_end:
            raise AssertionError(
                f"Start year ({py_start}) should be strictly smaller than end year ({py_end})",
            )
        query = drop_whitespaces(query)

        author_subqueries: list[str | None] = (
            [
                f"FIRSTAUTH ( {char1}{char2}* )"
                for char1 in alphabet
                for char2 in alphabet
            ]
            if use_firstauth_filter
            else [None]
        )
        for firstauth in author_subqueries:
            base = f"( {query} )"
            if firstauth:
                base += f" AND {firstauth}"

            if use_py_filter:
                yield f"{base} AND ( PUBYEAR BEF {py_start} )"
                last_end_py = py_start
                for py in range(py_start - 1, py_end - 1, py_steps):
                    last_end_py = py + py_steps + 1
                    yield f"{base} AND ( PUBYEAR AFT {py} ) AND ( PUBYEAR BEF {last_end_py} )"
                yield f"{base} AND ( PUBYEAR AFT {last_end_py - 1} )"
            else:
                yield base
