from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from xml.etree.ElementTree import Element
from xml.etree.ElementTree import fromstring as parse_xml

from httpx import Response, codes
from nacsos_data.models.items.academic import (
    AcademicAuthorModel,
    AcademicItemModel,
    AffiliationModel,
)

from academic_apis.utils import Wrapper, as_uuid, clear_empty

from ._abc import AbstractWrapper
from ._client import RequestClient

if TYPE_CHECKING:
    from collections.abc import Generator

    from destiny_sdk.references import Reference as ODSReference


def xml2dict(element: Element) -> dict[str, Any]:
    base: dict[str, Any] = {}
    for child in element:
        if child.tag not in base:
            base[child.tag] = []
        base[child.tag].append(xml2dict(child))
    base |= {f"@{attr}": val for attr, val in element.attrib.items()}
    if element.text and len(element.text.strip()) > 0:
        base["_text"] = element.text.strip()
    return base


def element_text(parent: Element, path: str) -> str:
    """Text of a required child element; raises if it is missing or empty."""
    element = parent.find(path)
    if element is None or not element.text:
        raise ValueError(f"Missing or empty XML element: {path}")
    return element.text.strip()


def select(obj: dict[str, Any], *keys: str, default: Any = None) -> Any:  # noqa: ANN401
    for key in keys:
        obj = obj.get(key)  # type: ignore[assignment]
        if obj is None or len(obj) == 0:
            return default
        obj = obj[0]  # type: ignore[index]
    return obj


def get_ids(pm_info: dict[str, Any]) -> dict[str, str]:
    ids = {}
    for article_id in select(pm_info, "ArticleIdList", default={}).get("ArticleId", []):
        ids[article_id["@IdType"]] = article_id["_text"]
    return ids


def get_authors(citation: dict[str, Any]) -> Generator[AcademicAuthorModel]:
    authors = select(citation, "Article", "AuthorList", default={}).get("Author", [])
    for author in authors:
        affiliations: list[AffiliationModel] = [
            AffiliationModel(name=select(aff, "Affiliation", default={}).get("_text"))
            for aff in author.get("AffiliationInfo", [])
        ]
        affiliations = [aff for aff in affiliations if aff.name is not None]

        yield AcademicAuthorModel(
            name=f"{select(author, 'ForeName', default={}).get('_text', '')} "
            f"{select(author, 'LastName', default={}).get('_text', '')}",
            affiliations=None if len(affiliations) == 0 else affiliations,
        )


class PubmedWrapper(AbstractWrapper):
    wrapper = Wrapper.PUBMED
    page_max = 50

    async def _fetch_raw(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Element]:
        """
        Pubmed API wrapper for downloading all records for a given query.

        Direct article lookup (IDs can be comma separated):
        https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?api_key=KEY&db=pubmed&id=17975326

        API Documentation:
        https://www.ncbi.nlm.nih.gov/books/NBK25497/
        https://www.ncbi.nlm.nih.gov/books/NBK25499/#chapter4.EFetch

        Works in two stages:
          1) Initiate query and get QueryKey
          2) Fetch result pages for QueryKey

        :param query:
        :return:
        """
        if self.api_key is None:
            raise AssertionError("Missing API key!")

        n_records = 0
        n_pages = 0
        async with RequestClient(
            backoff_rate=self.backoff_rate,
            max_req_per_sec=self.max_req_per_sec,
            max_retries=self.max_retries,
            proxy=self.proxy,
            timeout=120,
        ) as request_client:
            self.logger.info(f"Running query: {query}")
            search_page = await request_client.post(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                data={
                    "api_key": self.api_key,
                    "db": "pubmed",
                    "term": query,
                    "usehistory": "y",
                },
                params=params,
            )
            tree = parse_xml(search_page.text)  # noqa: S314

            errors = tree.find("ErrorList")
            if errors is not None:
                for error in errors.iter():
                    self.logger.error(f"Error {error.tag}: {''.join(error.itertext())}")

            web_env = element_text(tree, "WebEnv")
            query_key = element_text(tree, "QueryKey")
            translation = tree.find("QueryTranslation")
            if translation is not None and translation.text:
                self.logger.warning(f"Query translated to: {translation.text}")

            self.n_results = int(element_text(tree, "Count"))
            page_size = int(element_text(tree, "RetMax"))

            done = False

            def on_done(response: Response) -> dict[str, Any]:  # noqa: ARG001
                self.logger.info("Seemed to have reached the end (BAD_REQUEST).")
                done = True  # noqa: F841
                return {}

            request_client.on(codes.BAD_REQUEST, on_done)
            while not done:
                result_page = await request_client.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    params={
                        "api_key": self.api_key,
                        "db": "pubmed",
                        "WebEnv": web_env,
                        "query_key": query_key,
                        "retmax": page_size,
                        "retstart": n_records,
                    },
                )

                self.api_feedback = {
                    "rate_limit": result_page.headers.get("x-ratelimit-limit"),
                    "rate_left": result_page.headers.get("x-ratelimit-remaining"),
                }

                tree = parse_xml(result_page.text)  # noqa: S314
                articles = list(tree.findall("PubmedArticle"))

                n_records += len(articles)
                for article in articles:
                    yield article

                self.logger.info(
                    f"Found {n_records:,}/{self.n_results:,} records after processing page {n_pages} ({page_size} per page) | {self.api_feedback}",
                )

                if n_records >= self.n_results or len(articles) == 0:
                    self.logger.info(
                        "Seemed to have reached the end (count zero or total reached)."
                    )
                    break

    async def fetch_raw(
        self,
        query: str,
        params: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any]]:
        async for entry in self._fetch_raw(query, params=params):
            yield xml2dict(entry)

    @classmethod
    def translate_record_nacsos(
        cls, record: dict[str, Any], project_id: str | uuid.UUID | None = None
    ) -> AcademicItemModel:
        citation: dict[str, Any] = record.get("MedlineCitation", [{}])[0]
        pm_info: dict[str, Any] = record.get("PubmedData", [{}])[0]
        pm_ids = get_ids(pm_info)
        py: str | None = select(
            citation, "Article", "ArticleDate", "Year", default={}
        ).get("_text")
        pyi: int | None = None if py is None else int(py)

        pm_info.pop("ReferenceList", None)

        return AcademicItemModel(
            item_id=uuid.uuid4(),
            project_id=as_uuid(project_id),
            doi=pm_ids.get("doi"),
            title=select(citation, "Article", "ArticleTitle", default={}).get("_text"),
            pubmed_id=pm_ids.get("pubmed"),
            text=select(
                citation, "Article", "Abstract", "AbstractText", default={}
            ).get("_text"),
            publication_year=pyi,
            authors=clear_empty(list(get_authors(citation))),
            source=select(citation, "Article", "Journal", "Title", default={}).get(
                "_text"
            ),
            keywords=clear_empty(
                [
                    kw.get("_text", "").strip()
                    for kw in select(citation, "KeywordList", default={}).get(
                        "Keyword", []
                    )
                ],
            ),
            meta={"pubmed-api": clear_empty(record)},
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
        raise NotImplementedError
