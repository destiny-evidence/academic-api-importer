from __future__ import annotations

import uuid
from typing import Any, cast

from nacsos_data.models.items import AcademicItemModel
from nacsos_data.models.items.academic import AcademicAuthorModel, AffiliationModel
from nacsos_data.util import as_uuid, clear_empty


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


def translate_record_nacsos(
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
