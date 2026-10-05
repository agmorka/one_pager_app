"""Serialization between One Pager YAML dicts and OnePagerDocument.

Field names follow the current schema, structure_one_pager_v_2.json (camelCase
in YAML, snake_case on the model). Structured list items are preserved as dicts
so content survives a read/write round-trip.

Older documents load into the same model: list items keep the shape they were
written with (e.g. v1 inline Use Case objects), and the pre-v2
``dataElementPreview`` key is read as ``dataProductPreview``. Documents are
always written with the v2 keys.
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
        smes=_dict_items(data.get("smes")),
        use_cases=_dict_items(data.get("useCases")),
        business_requirements=_dict_items(data.get("businessRequirements")),
        data_sources=_dict_items(data.get("dataSources")),
        data_product_preview=_dict_items(
            data.get("dataProductPreview", data.get("dataElementPreview"))
        ),
        data_classification=(
            _classification_from_dict(dc) if isinstance(dc, dict) and dc else {}
        ),
        retention_requirements=_dict_items(data.get("retentionRequirements")),
        data_governance_artifacts=_governance_from_dict(
            data.get("dataGovernanceArtifacts")
        ),
        out_of_scope=_str_items(data.get("outOfScope")),
        open_questions=_dict_items(data.get("openQuestions")),
        assumptions=_str_items(data.get("assumptions")),
        created_by=_optional_str(data.get("createdBy")),
        created_at=_optional_str(data.get("createdAt")),
        last_updated=_optional_str(data.get("lastUpdated")),
        change_log=_dict_items(data.get("changeLog")),
        raw_content=raw_content,
    )


def document_to_dict(document: OnePagerDocument) -> dict[str, Any]:
    """Build a schema-shaped YAML dict from an OnePagerDocument.

    Optional/empty sections are omitted so the output stays close to the schema.
    No timestamps or user names are written (``createdBy``, ``createdAt``,
    ``lastUpdated``, the change log's ``date`` and ``author``); older files
    that have them lose them when a new version is written.
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

    optional: dict[str, Any] = {
        "businessProblemStatement": document.business_problem_statement,
        "smes": document.smes,
        "useCases": document.use_cases,
        "businessRequirements": document.business_requirements,
        "dataSources": document.data_sources,
        "dataClassification": document.data_classification,
        "dataProductPreview": document.data_product_preview,
        "retentionRequirements": document.retention_requirements,
        "dataGovernanceArtifacts": {
            k: v for k, v in document.data_governance_artifacts.items() if v
        },
        "outOfScope": document.out_of_scope,
        "openQuestions": document.open_questions,
        "assumptions": document.assumptions,
        "changeLog": [
            change_log_item(str(e.get("version", "")), str(e.get("summary", "")))
            for e in document.change_log
        ],
    }
    data.update({key: value for key, value in optional.items() if value})

    return data


def change_log_item(version: str, summary: str) -> dict[str, str]:
    """Entry of the YAML ``changeLog``: the version and what changed.

    The file holds no timestamps and no authors; when and by whom is in the
    ``change_log`` table.
    """
    return {"version": version, "summary": summary}


def document_to_yaml(document: OnePagerDocument) -> str:
    """Serialize an OnePagerDocument to YAML text."""
    text: str = yaml.safe_dump(
        document_to_dict(document),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return text


def _optional_str(value: Any) -> str | None:  # noqa: ANN401 - any YAML scalar
    """Return ``value`` as a string, or None when absent.

    YAML may parse unquoted timestamps into datetime objects; they are kept as
    ISO-8601 strings on the model.
    """
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _dict_items(value: object) -> list[dict[str, Any]]:
    """Copy the dict items of a YAML list; anything else becomes []."""
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _str_items(value: object) -> list[str]:
    """Non-empty string items of a YAML list; anything else becomes []."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


GOVERNANCE_SECTIONS = ("businessConcepts", "cdeQuality", "cdeLineage")


def _governance_from_dict(value: object) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    return {
        key: _dict_items(value.get(key)) for key in GOVERNANCE_SECTIONS if key in value
    }


def _classification_from_dict(dc: dict[str, Any]) -> dict[str, Any]:
    """Copy the classification, keeping only the keys the document sets.

    Missing flags are not defaulted to False, so strict validation can report
    them as missing. ``retentionRequirements`` inside the classification is the
    v1 location (a string); v2 keeps retention as a top-level list.
    """
    result: dict[str, Any] = {}
    if dc.get("classificationLevel"):
        result["classificationLevel"] = dc["classificationLevel"]
    for flag in ("containsPII", "containsSensitiveData"):
        if dc.get(flag) is not None:
            result[flag] = bool(dc[flag])
    if dc.get("retentionRequirements"):
        result["retentionRequirements"] = dc["retentionRequirements"]
    return result
