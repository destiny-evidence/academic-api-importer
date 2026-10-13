"""Parser for a single ``entry`` of a Scopus Search API response."""
# TODO: Warning, this is completely untested claude-generated code! Needs validation and testing!

import re
from datetime import date, datetime
from typing import Any

from destiny_sdk.core import UUID
from destiny_sdk.enhancements import (
    AbstractContentEnhancement,
    AbstractProcessType,
    Annotation,
    AnnotationEnhancement,
    AuthorPosition,
    Authorship,
    BibliographicMetadataEnhancement,
    BooleanAnnotation,
    DriverVersion,
    Enhancement,
    EnhancementContent,
    EnhancementFileInput,
    Location,
    LocationEnhancement,
    Pagination,
    PublicationVenue,
    PublicationVenueType,
    RawEnhancement,
)
from destiny_sdk.identifiers import (
    DOIIdentifier,
    ExternalIdentifier,
    OtherIdentifier,
    PubMedIdentifier,
)
from destiny_sdk.parsers.exceptions import ExternalIdentifierNotFoundError
from destiny_sdk.references import ReferenceFileInput
from destiny_sdk.visibility import Visibility
from pydantic import ValidationError

# Inline markup used by Scopus in titles/abstracts, e.g. N<inf>2</inf>O.
# A whitelist is used so that text such as "p < 0.05 and q > 1" is preserved.
_MARKUP = re.compile(r"</?(?:inf|sup|sub|i|b|u|it|sc|bold|italic)\s*/?>", re.IGNORECASE)
# Trailing publisher copyright line, e.g. "© 2013 Elsevier Ltd." ("\ufffd" covers
# mis-decoded "©" as seen in the official Elsevier example payload).
_COPYRIGHT = re.compile(
    r"\s*(?:©|\ufffd|Copyright\b)\s*(?:©\s*)?\d{4}\b.{0,300}$", re.DOTALL
)
# Author names like "Abalos D." or "Wisler J.W." (no comma).
_SURNAME_INITIALS = re.compile(r"^(?P<surname>.*\S)\s+(?P<initials>(?:[A-Z]\.-?)+)$")

_VENUE_TYPES = {
    "journal": PublicationVenueType.JOURNAL,
    "trade journal": PublicationVenueType.JOURNAL,
    "conference proceeding": PublicationVenueType.CONFERENCE,
    "book series": PublicationVenueType.BOOK_SERIES,
}

_UNDEFINED = {"", "undefined", "null", "none"}

_RAW_DATA_ERROR = (
    "Cannot include raw data enhancements without "
    "source_export_date and data_description"
)


def _text(value: Any) -> str | None:  # noqa: ANN401
    """Unwrap Scopus values ({"$": "..."} or plain), normalising empties to None."""
    if isinstance(value, dict):
        value = value.get("$")
    if value is None:
        return None
    value = str(value).replace("\u00a0", " ").strip()
    return None if value.lower() in _UNDEFINED else value


def _as_list(value: Any) -> list[Any]:  # noqa: ANN401
    """Scopus returns a single object instead of a one-element list in places."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _to_int(value: Any) -> int | None:  # noqa: ANN401
    text = _text(value)
    return int(text) if text and text.isdigit() else None


def _to_bool(value: Any) -> bool | None:  # noqa: ANN401
    if isinstance(value, bool):
        return value
    text = _text(value)
    if text is None:
        return None
    return text.lower() in {"true", "1"}


ISSN_LENGTH = 8


def _format_issn(raw: Any) -> str | None:  # noqa: ANN401
    text = _text(raw)
    if not text:
        return None
    text = text.replace("-", "").upper()
    return f"{text[:4]}-{text[4:]}" if len(text) == ISSN_LENGTH else text


class ScopusParser:
    """
    Parser for one ``entry`` object of the Scopus Search API (COMPLETE view).

    See: https://dev.elsevier.com/payloads/search/scopusSearchResp.json
    """

    version = "1.0"

    def __init__(
        self,
        *,
        tags: list[str] | None = None,
        include_raw_data: bool = False,
        source_export_date: datetime | None = None,
        data_description: str | None = None,
        raw_enhancement_excludes: list[str] | None = None,
        metadata_visibility: Visibility = Visibility.PUBLIC,
        abstract_visibility: Visibility = Visibility.RESTRICTED,
        strip_markup: bool = True,
        strip_copyright: bool = False,
        include_affiliations: bool = True,
    ) -> None:
        """
        Initialise the parser.

        Args:
            tags: Optional ``<scheme>/<label>[@<score>]`` tags applied to every entry.
            include_raw_data: Add the full entry as a RawEnhancement.
            source_export_date: Retrieval date (required for raw data).
            data_description: Description of the raw data (required for raw data).
            raw_enhancement_excludes: Top-level entry keys to drop from raw data.
            metadata_visibility: Visibility of non-abstract enhancements.
            abstract_visibility: Visibility of the abstract and of raw data (which
                contains the abstract). Scopus abstracts are licensed content.
            strip_markup: Remove Scopus inline markup (<inf>, <sup>, ...).
            strip_copyright: Remove a trailing copyright line from the abstract.
            include_affiliations: Emit affiliations as annotations.

        """
        self.tags = tags or []
        self.parser_source = f"destiny_sdk.scopus_parser@{self.version}"
        self.include_raw_data = include_raw_data
        self.source_export_date = source_export_date
        self.data_description = data_description
        self.raw_enhancement_excludes = raw_enhancement_excludes or []
        self.metadata_visibility = metadata_visibility
        self.abstract_visibility = abstract_visibility
        self.strip_markup = strip_markup
        self.strip_copyright = strip_copyright
        self.include_affiliations = include_affiliations

        if include_raw_data and not (source_export_date and data_description):
            raise RuntimeError(_RAW_DATA_ERROR)

    # ------------------------------------------------------------------ helpers
    def _clean(self, value: Any) -> str | None:  # noqa: ANN401
        text = _text(value)
        if text and self.strip_markup:
            text = _MARKUP.sub("", text).strip()
        return text or None

    @staticmethod
    def _links(entry: dict[str, Any]) -> dict[str, str]:
        return {
            link["@ref"]: link["@href"]
            for link in _as_list(entry.get("link"))
            if isinstance(link, dict) and link.get("@ref") and link.get("@href")
        }

    @staticmethod
    def _scopus_id(entry: dict[str, Any]) -> str | None:
        ident = _text(entry.get("dc:identifier"))
        return ident.removeprefix("SCOPUS_ID:") if ident else None

    # -------------------------------------------------------------- identifiers
    def parse_identifiers(self, entry: dict[str, Any]) -> list[ExternalIdentifier]:
        """Extract all external identifiers available in the entry."""
        identifiers: list[ExternalIdentifier] = []

        candidates: list[tuple[type, dict[str, Any]]] = []
        if doi := _text(entry.get("prism:doi")):
            candidates.append((DOIIdentifier, {"identifier": doi}))
        if pmid := _to_int(entry.get("pubmed-id")):
            candidates.append((PubMedIdentifier, {"identifier": pmid}))
        for key, name in (
            ("dc:identifier", "Scopus ID"),
            ("eid", "Scopus EID"),
            ("pii", "Elsevier PII"),
        ):
            value = self._scopus_id(entry) if key == "dc:identifier" else entry.get(key)
            if value := _text(value):
                candidates.append(
                    (
                        OtherIdentifier,
                        {"identifier": value, "other_identifier_name": name},
                    )
                )

        for cls, kwargs in candidates:
            try:
                identifiers.append(cls(**kwargs))
            except ValidationError:
                continue

        if not identifiers:
            msg = f"No external identifiers found for Scopus entry: {entry.get('dc:title')!r}"
            raise ExternalIdentifierNotFoundError(detail=msg)
        return identifiers

    # ------------------------------------------------------------ bibliographic
    @staticmethod
    def _display_name(author: dict[str, Any]) -> str | None:
        """Build 'FIRSTNAME MIDDLENAME LASTNAME' as required by Authorship."""
        surname = _text(author.get("surname"))
        given = _text(author.get("given-name")) or _text(author.get("initials"))
        if surname:
            return f"{given} {surname}" if given else surname
        return ScopusParser._display_name_from_authname(_text(author.get("authname")))

    @staticmethod
    def _display_name_from_authname(name: str | None) -> str | None:
        """Convert 'Surname, I.' or 'Surname I.' into 'I. Surname'."""
        if not name:
            return None
        if "," in name:
            surname, _, initials = name.partition(",")
            return f"{initials.strip()} {surname.strip()}".strip()
        if match := _SURNAME_INITIALS.match(name):
            return f"{match['initials']} {match['surname']}"
        return name

    def _parse_authorships(self, entry: dict[str, Any]) -> list[Authorship]:
        authors = sorted(
            (a for a in _as_list(entry.get("author")) if isinstance(a, dict)),
            key=lambda a: _to_int(a.get("@seq")) or 0,
        )
        names = [(self._display_name(a), _text(a.get("orcid"))) for a in authors]
        names = [(n, o) for n, o in names if n]

        if not names:  # STANDARD view: only the first author is provided
            if creator := self._display_name_from_authname(
                _text(entry.get("dc:creator"))
            ):
                return [Authorship(display_name=creator, position=AuthorPosition.FIRST)]
            return []

        # Scopus caps the author list (author-count/@limit); if truncated, the
        # final listed author is not the true last author.
        total = _to_int(entry.get("author-count"))
        truncated = total is not None and total > len(names)

        authorships = []
        for i, (name, orcid) in enumerate(names):
            position = AuthorPosition.MIDDLE
            if i == 0:
                position = AuthorPosition.FIRST
            elif i == len(names) - 1 and not truncated:
                position = AuthorPosition.LAST
            authorships.append(
                Authorship(display_name=name or "", orcid=orcid, position=position)
            )
        return authorships

    @staticmethod
    def _parse_pagination(entry: dict[str, Any]) -> Pagination | None:
        first = _text(entry.get("prism:startingPage"))
        last = _text(entry.get("prism:endingPage"))
        if page_range := _text(entry.get("prism:pageRange")):
            start, sep, end = page_range.partition("-")
            first = first or start
            last = last or (end if sep else None)
        first = first or _text(entry.get("article-number"))

        pagination = Pagination(
            volume=_text(entry.get("prism:volume")),
            issue=_text(entry.get("prism:issueIdentifier")),
            first_page=first,
            last_page=last,
        )
        return pagination if pagination.model_dump(exclude_none=True) else None

    @staticmethod
    def _parse_venue(entry: dict[str, Any]) -> PublicationVenue | None:
        name = _text(entry.get("prism:publicationName"))
        aggregation = _text(entry.get("prism:aggregationType"))
        issns = [
            i
            for i in (
                _format_issn(entry.get("prism:issn")),
                _format_issn(entry.get("prism:eIssn")),
            )
            if i
        ]
        if not (name or aggregation or issns):
            return None
        venue_type = None
        if aggregation:
            venue_type = _VENUE_TYPES.get(
                aggregation.lower(), PublicationVenueType.OTHER
            )
        return PublicationVenue(
            display_name=name,
            venue_type=venue_type,
            issn=list(dict.fromkeys(issns)) or None,
            host_organization_name=_text(entry.get("dc:publisher")),
        )

    @staticmethod
    def _parse_dates(entry: dict[str, Any]) -> tuple[date | None, int | None]:
        cover = _text(entry.get("prism:coverDate"))
        if not cover:
            return None, None
        try:
            parsed = date.fromisoformat(cover)
        except ValueError:  # e.g. "2016-00-00"
            year = cover[:4]
            return None, int(year) if year.isdigit() else None
        return parsed, parsed.year

    def parse_bibliographic(self, entry: dict[str, Any]) -> EnhancementContent | None:
        """Build the bibliographic metadata enhancement."""
        publication_date, publication_year = self._parse_dates(entry)
        content = BibliographicMetadataEnhancement(
            title=self._clean(entry.get("dc:title")),
            authorship=self._parse_authorships(entry) or None,
            cited_by_count=_to_int(entry.get("citedby-count")),
            publication_date=publication_date,
            publication_year=publication_year,
            publisher=_text(entry.get("dc:publisher")),
            pagination=self._parse_pagination(entry),
            publication_venue=self._parse_venue(entry),
        )
        populated = content.model_dump(exclude_none=True, exclude={"enhancement_type"})
        return content if populated else None

    # ----------------------------------------------------------------- abstract
    def parse_abstract(self, entry: dict[str, Any]) -> EnhancementContent | None:
        """Build the abstract enhancement."""
        abstract = self._clean(entry.get("dc:description"))
        if abstract and self.strip_copyright:
            abstract = _COPYRIGHT.sub("", abstract).strip()
        if not abstract:
            return None
        return AbstractContentEnhancement(
            process=AbstractProcessType.CLOSED_API, abstract=abstract
        )

    # ---------------------------------------------------------------- locations
    def parse_locations(self, entry: dict[str, Any]) -> EnhancementContent | None:
        """Build the location enhancement (DOI landing page and Scopus record)."""
        links = self._links(entry)
        is_oa = _to_bool(entry.get("openaccessFlag"))
        if is_oa is None:
            is_oa = _to_bool(entry.get("openaccess"))
        free_to_read = [
            v
            for v in (
                _text(x) for x in _as_list((entry.get("freetoread") or {}).get("value"))
            )
            if v
        ]

        candidates: list[dict[str, Any]] = []
        if doi := _text(entry.get("prism:doi")):
            extra = {
                "full_text_api_url": links.get("full-text"),
                "free_to_read": free_to_read or None,
            }
            candidates.append(
                {
                    "landing_page_url": f"https://doi.org/{doi}",
                    "version": DriverVersion.PUBLISHED_VERSION,
                    "is_oa": is_oa,
                    "extra": {k: v for k, v in extra.items() if v} or None,
                }
            )
        if record := links.get("scopus"):
            extra = {
                "source": "scopus",
                "scopus_id": self._scopus_id(entry),
                "eid": _text(entry.get("eid")),
                "abstract_api_url": links.get("self") or _text(entry.get("prism:url")),
                "cited_by_url": links.get("scopus-citedby"),
            }
            candidates.append(
                {
                    "landing_page_url": record,
                    "extra": {k: v for k, v in extra.items() if v},
                }
            )

        locations = []
        for kwargs in candidates:
            try:
                locations.append(Location(**kwargs))
            except ValidationError:
                continue
        return LocationEnhancement(locations=locations) if locations else None

    # -------------------------------------------------------------- annotations
    def parse_annotations(self, entry: dict[str, Any]) -> EnhancementContent | None:
        """Build annotations: keywords, document/source type, funding, affiliations, OA, tags."""
        annotations: list[Annotation] = []

        def add(
            scheme: str,
            label: str | None,
            data: dict[str, Any] | None = None,
            score: float | None = None,
        ) -> None:
            if label:
                annotations.append(
                    BooleanAnnotation(
                        scheme=scheme,
                        label=label,
                        value=True,
                        score=score,
                        data={k: v for k, v in (data or {}).items() if v is not None},
                    )
                )

        if keywords := _text(entry.get("authkeywords")):
            for keyword in dict.fromkeys(k.strip() for k in keywords.split("|")):
                add("scopus:author_keyword", keyword)

        add(
            "scopus:document_type",
            _text(entry.get("subtypeDescription")),
            {"code": _text(entry.get("subtype"))},
        )
        add(
            "scopus:source_type",
            _text(entry.get("prism:aggregationType")),
            {"source_id": _text(entry.get("source-id"))},
        )
        add(
            "scopus:funding_sponsor",
            _text(entry.get("fund-sponsor")),
            {
                "acronym": _text(entry.get("fund-acr")),
                "number": _text(entry.get("fund-no")),
            },
        )

        labels = [
            _text(x)
            for x in _as_list((entry.get("freetoreadLabel") or {}).get("value"))
        ]
        for label in filter(None, labels):
            add("scopus:open_access", label)

        if self.include_affiliations:
            for aff in _as_list(entry.get("affiliation")):
                if not isinstance(aff, dict):
                    continue
                add(
                    "scopus:affiliation",
                    _text(aff.get("affilname")),
                    {
                        "afid": _text(aff.get("afid")),
                        "city": _text(aff.get("affiliation-city")),
                        "country": _text(aff.get("affiliation-country")),
                        "name_variants": [
                            v
                            for v in (
                                _text(n) for n in _as_list(aff.get("name-variant"))
                            )
                            if v
                        ]
                        or None,
                    },
                )

        for tag in self.tags:
            scheme_label, _, score = tag.partition("@")
            scheme, _, label = scheme_label.partition("/")
            add(scheme, label, score=float(score) if score else None)

        return AnnotationEnhancement(annotations=annotations) if annotations else None

    # ---------------------------------------------------------------------- raw
    def parse_raw(self, entry: dict[str, Any]) -> EnhancementContent:
        """Store the full entry, minus excluded keys, as a raw enhancement."""
        if self.source_export_date is None or self.data_description is None:
            raise RuntimeError(_RAW_DATA_ERROR)
        data = {
            k: v for k, v in entry.items() if k not in self.raw_enhancement_excludes
        }
        return RawEnhancement(
            source_export_date=self.source_export_date,
            description=self.data_description,
            metadata={"api": "scopus_search", "scopus_id": self._scopus_id(entry)},
            data=data,
        )

    # ------------------------------------------------------------------- public
    def parse_contents(
        self, entry: dict[str, Any]
    ) -> list[tuple[EnhancementContent, Visibility]]:
        """Return all enhancement contents with their intended visibility."""
        if "error" in entry:  # e.g. {"error": "Result set was empty"}
            return []
        pub, restricted = self.metadata_visibility, self.abstract_visibility
        contents = [
            (self.parse_bibliographic(entry), pub),
            (self.parse_abstract(entry), restricted),
            (self.parse_locations(entry), pub),
            (self.parse_annotations(entry), pub),
        ]
        if self.include_raw_data:
            contents.append((self.parse_raw(entry), restricted))
        return [(c, v) for c, v in contents if c is not None]

    def parse_reference(
        self,
        entry: dict[str, Any],
        source: str | None = None,
        robot_version: str | None = None,
    ) -> ReferenceFileInput:
        """Convert an entry into a new reference with identifiers and enhancements."""
        source = source or self.parser_source
        return ReferenceFileInput(
            visibility=self.metadata_visibility,
            identifiers=self.parse_identifiers(entry),
            enhancements=[
                EnhancementFileInput(
                    source=source,
                    visibility=vis,
                    content=content,
                    # `robot_version` is only accepted under its validation alias
                    processor_version=robot_version,
                )
                for content, vis in self.parse_contents(entry)
            ],
        )

    def parse_enhancements(
        self,
        entry: dict[str, Any],
        reference_id: UUID,
        source: str | None = None,
        robot_version: str | None = None,
    ) -> list[Enhancement]:
        """Convert an entry into enhancements for an existing reference."""
        source = source or self.parser_source
        return [
            Enhancement(
                reference_id=reference_id,
                source=source,
                visibility=vis,
                robot_version=robot_version,
                content=content,
            )
            for content, vis in self.parse_contents(entry)
        ]
