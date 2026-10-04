"""Input sanitization (Architecture.md §8): trim text and strip HTML tags."""

import re
from dataclasses import replace
from typing import Any

from onepagerapp.models import NewOnePagerInput, OnePagerDocument, PersonRef

_HTML_TAG = re.compile(r"<[^>]*>")


def sanitize_text(value: str | None) -> str:
    """Trim whitespace and strip HTML tags from a free-text value."""
    if not value:
        return ""
    return _HTML_TAG.sub("", str(value)).strip()


def _sanitize_person(person: PersonRef) -> PersonRef:
    team = sanitize_text(person.team)
    return PersonRef(
        name=sanitize_text(person.name),
        initials=sanitize_text(person.initials).upper(),
        email=sanitize_text(person.email),
        team=team or None,
    )


def _is_blank_person(person: PersonRef) -> bool:
    return not (person.name or person.initials or person.email or person.team)


def normalize_new_one_pager(data: NewOnePagerInput) -> NewOnePagerInput:
    """Return a sanitized copy of the create input.

    Trims and strips HTML from every text field, upper-cases initials and drops
    SME rows that are completely empty (e.g. a blank row left in the grid).
    """
    smes = [_sanitize_person(s) for s in data.smes]
    return replace(
        data,
        data_product=sanitize_text(data.data_product),
        product_name=sanitize_text(data.product_name),
        business_domain=sanitize_text(data.business_domain),
        data_product_type=sanitize_text(data.data_product_type),
        description=sanitize_text(data.description),
        business_problem_statement=sanitize_text(data.business_problem_statement),
        owner=_sanitize_person(data.owner),
        smes=[s for s in smes if not _is_blank_person(s)],
    )


def _sanitize_value(value: object) -> object:
    """Sanitize a document value: text is trimmed and stripped of HTML.

    Lists drop items that end up empty; dicts drop keys whose value ends up
    empty ("" / None / []), so blank optional fields are not stored.
    """
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        items = [_sanitize_value(item) for item in value]
        return [item for item in items if item not in ("", None, [], {})]
    if isinstance(value, dict):
        cleaned = {key: _sanitize_value(item) for key, item in value.items()}
        return {k: v for k, v in cleaned.items() if v not in ("", None, [])}
    return value


def _clean_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = _sanitize_value(items)
    return (
        [item for item in cleaned if isinstance(item, dict)]
        if isinstance(cleaned, list)
        else []
    )


def _clean_strings(items: list[str]) -> list[str]:
    return [sanitize_text(item) for item in items if sanitize_text(item)]


def _clean_dict(value: dict[str, Any]) -> dict[str, Any]:
    cleaned = _sanitize_value(value)
    return cleaned if isinstance(cleaned, dict) else {}


def person_from_dict(value: dict[str, Any]) -> PersonRef:
    """Build a PersonRef from a document person dict (owner / SME item)."""
    return PersonRef(
        name=str(value.get("name") or ""),
        initials=str(value.get("initials") or ""),
        email=str(value.get("email") or ""),
        team=value.get("team") or None,
    )


def normalize_document(document: OnePagerDocument) -> OnePagerDocument:
    """Return a sanitized copy of an edited document (Architecture.md §8).

    Every text value is trimmed and stripped of HTML, initials are upper-cased
    and empty list items (e.g. a blank SME row) are dropped.
    """
    owner = _sanitize_person(
        PersonRef(
            name=document.owner_name,
            initials=document.owner_initials,
            email=document.owner_email,
            team=document.owner_team,
        )
    )
    smes = [
        _sanitize_person(person_from_dict(s))
        for s in document.smes
        if isinstance(s, dict)
    ]
    return replace(
        document,
        product_name=sanitize_text(document.product_name),
        business_domain=sanitize_text(document.business_domain),
        data_product_type=sanitize_text(document.data_product_type),
        description=sanitize_text(document.description),
        owner_name=owner.name,
        owner_initials=owner.initials,
        owner_email=owner.email,
        owner_team=owner.team,
        business_problem_statement=sanitize_text(document.business_problem_statement),
        smes=[
            {k: v for k, v in vars(s).items() if v}
            for s in smes
            if not _is_blank_person(s)
        ],
        use_cases=_clean_items(document.use_cases),
        business_requirements=_clean_items(document.business_requirements),
        data_sources=_clean_items(document.data_sources),
        data_product_preview=_clean_items(document.data_product_preview),
        data_classification=_clean_dict(document.data_classification),
        retention_requirements=_clean_items(document.retention_requirements),
        data_governance_artifacts=_clean_dict(document.data_governance_artifacts),
        out_of_scope=_clean_strings(document.out_of_scope),
        open_questions=_clean_items(document.open_questions),
        assumptions=_clean_strings(document.assumptions),
    )
