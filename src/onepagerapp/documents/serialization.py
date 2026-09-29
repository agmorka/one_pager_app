"""Serialization between One Pager YAML dicts and OnePagerDocument.

Field names follow structure_one_pager_v_1.json (camelCase in YAML,
snake_case on the model). Structured list items are preserved as dicts so
content survives a read/write round-trip.
"""

from typing import Any

import yaml

from onepagerapp.models import OnePagerDocument


def document_from_dict(data: dict[str, Any], raw_content: str = "") -> OnePagerDocument:
    """Build an OnePagerDocument from a parsed YAML dict."""
    owner = data.get("dataProductOwner") or {}
    dc = data.get("dataClassification") or {}

    return OnePagerDocument(
        structure_definition=data.get("structureDefinition", ""),
        data_product=data.get("dataProduct", ""),
        product_name=data.get("productName", ""),
        business_domain=data.get("businessDomain", ""),
        data_product_type=data.get("dataProductType", ""),
        one_pager_status=data.get("onePagerStatus", ""),
        data_product_status=data.get("dataProductStatus", ""),
        version=data.get("version", ""),
        description=data.get("description", ""),
        owner_name=owner.get("name", "") if isinstance(owner, dict) else "",
        owner_initials=owner.get("initials", "") if isinstance(owner, dict) else "",
        owner_email=owner.get("email", "") if isinstance(owner, dict) else "",
        owner_team=owner.get("team") if isinstance(owner, dict) else None,
        business_problem_statement=data.get("businessProblemStatement", "") or "",
        smes=[dict(s) for s in data.get("smes", []) if isinstance(s, dict)],
        use_cases=[dict(u) for u in data.get("useCases", []) if isinstance(u, dict)],
        business_requirements=[
            dict(b) for b in data.get("businessRequirements", []) if isinstance(b, dict)
        ],
        data_sources=[dict(d) for d in data.get("dataSources", []) if isinstance(d, dict)],
        data_element_preview=[
            dict(e) for e in data.get("dataElementPreview", []) if isinstance(e, dict)
        ],
        data_classification=(
            _classification_from_dict(dc) if isinstance(dc, dict) and dc else {}
        ),
        created_by=_optional_str(data.get("createdBy")),
        created_at=_optional_str(data.get("createdAt")),
        last_updated=_optional_str(data.get("lastUpdated")),
        change_log=[dict(c) for c in data.get("changeLog", []) or [] if isinstance(c, dict)],
        raw_content=raw_content,
    )


def document_to_dict(document: OnePagerDocument) -> dict[str, Any]:
    """Build a schema-shaped YAML dict from an OnePagerDocument.

    Optional/empty sections are omitted so the output stays close to the schema.
    """
    owner: dict[str, Any] = {
        "name": document.owner_name,
        "initials": document.owner_initials,
        "email": document.owner_email,
    }
    if document.owner_team:
        owner["team"] = document.owner_team

    data: dict[str, Any] = {
        "structureDefinition": document.structure_definition,
        "dataProduct": document.data_product,
        "productName": document.product_name,
        "businessDomain": document.business_domain,
        "dataProductType": document.data_product_type,
        "onePagerStatus": document.one_pager_status,
        "dataProductStatus": document.data_product_status,
        "version": document.version,
        "dataProductOwner": owner,
        "description": document.description,
    }

    if document.business_problem_statement:
        data["businessProblemStatement"] = document.business_problem_statement

    if document.smes:
        data["smes"] = document.smes
    if document.use_cases:
        data["useCases"] = document.use_cases
    if document.business_requirements:
        data["businessRequirements"] = document.business_requirements
    if document.data_sources:
        data["dataSources"] = document.data_sources
    if document.data_classification:
        data["dataClassification"] = document.data_classification
    if document.data_element_preview:
        data["dataElementPreview"] = document.data_element_preview
    if document.created_by:
        data["createdBy"] = document.created_by
    if document.created_at:
        data["createdAt"] = document.created_at
    if document.last_updated:
        data["lastUpdated"] = document.last_updated
    if document.change_log:
        data["changeLog"] = document.change_log

    return data


def document_to_yaml(document: OnePagerDocument) -> str:
    """Serialize an OnePagerDocument to YAML text."""
    return yaml.safe_dump(
        document_to_dict(document),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def _optional_str(value: Any) -> str | None:
    """Return ``value`` as a string, or None when absent.

    YAML may parse unquoted timestamps into datetime objects; they are kept as
    ISO-8601 strings on the model.
    """
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _classification_from_dict(dc: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "classificationLevel": dc.get("classificationLevel", ""),
        "containsPII": bool(dc.get("containsPII", False)),
        "containsSensitiveData": bool(dc.get("containsSensitiveData", False)),
    }
    if dc.get("retentionRequirements"):
        result["retentionRequirements"] = dc["retentionRequirements"]
    return result
