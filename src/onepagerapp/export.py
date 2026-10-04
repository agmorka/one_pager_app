"""PDF export of a One Pager (Requirements_and_Scope.md §8, Backend_Design.md §10).

``export_one_pager_pdf`` renders every section of a One Pager, with its Use
Case references resolved from the shared ``use_cases`` table, into a
business-readable A4 PDF (``onepagerapp.pdf``). Any authenticated user may
export any One Pager, like viewing it.

The document is read like the Preview page reads it (the current version from
the One Pager store). Reading an approved version from Git follows with the Git
integration (Phase 8, item 7.3).

The section column definitions and ``resolve_use_cases`` are shared with the
Preview page, so both show the same content.

Pure Python — no Streamlit.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.models import (
    ChangeLogEntry,
    CurrentUser,
    OnePagerDocument,
    PreviewData,
)
from onepagerapp.pdf import BOLD, MUTED_COLOR, SMALL_SIZE, PdfDocument
from onepagerapp.permissions import PermissionDeniedError, can_view_one_pager
from onepagerapp.timeutils import utc_label

logger = logging.getLogger(__name__)

EXPORT_FAILED_MESSAGE = "The PDF could not be created. Please retry."
DEFAULT_STATUS_COLOR = "#808080"

# Section tables: document key → column label, in display order. A key may
# list fallbacks separated by "|" (first non-empty wins), for v1 documents.
USE_CASE_COLUMNS = {
    "useCaseId": "ID",
    "persona": "Persona",
    "goal": "Goal",
    "decisionEnabled": "Decision Enabled",
    "priority": "Priority",
    "deprecated": "Deprecated",
}
REQUIREMENT_COLUMNS = {
    "id": "ID",
    "requirement|description": "Requirement",
    "priority": "Priority",
    "notes": "Notes",
}
DATA_SOURCE_COLUMNS = {
    "name|sourceName": "Name",
    "sourceSystem|sourceType": "Source System",
    "epoId": "EPO ID",
    "dataProvided|description": "Data Provided",
    "refreshFrequency": "Refresh Frequency",
}
DATA_ELEMENT_COLUMNS = {
    "elementName": "Element",
    "dataType": "Type",
    "isPrimaryKey": "PK",
    "containsPII": "PII",
    "isCriticalDataElement": "CDE",
    "cdeCriticalityTiering": "CDE Tier",
    "description": "Description",
    "example": "Example",
    "source": "Source",
    "useCaseLinks": "Use Cases",
}
RETENTION_COLUMNS = {
    "dataCategory": "Data Category",
    "retentionPeriod": "Retention Period",
    "legalBasis": "Legal Basis",
}
BUSINESS_CONCEPT_COLUMNS = {"name": "Concept", "definition": "Definition"}
CDE_QUALITY_COLUMNS = {
    "elementName": "Element",
    "dimension": "Dimension",
    "rule": "Rule",
    "threshold": "Threshold",
}
CDE_LINEAGE_COLUMNS = {
    "elementName": "Element",
    "sourceSystem": "Source System",
    "sourceField": "Source Field",
    "transformation": "Transformation",
}
OPEN_QUESTION_COLUMNS = {
    "question": "Question",
    "owner": "Owner",
    "dueDate": "Due",
    "status": "Status",
    "answer": "Answer",
}


def cell_text(row: dict[str, Any], keys: str) -> str:
    """Display text of one table cell ("" when empty).

    ``keys`` is a column key of the tables above: the first non-empty value
    wins. Lists are joined with commas and booleans shown as Yes/No.
    """
    for key in keys.split("|"):
        value = row.get(key)
        if value not in (None, "", []):
            if isinstance(value, list):
                return ", ".join(map(str, value))
            if isinstance(value, bool):
                return "Yes" if value else "No"
            return str(value)
    return ""


def resolve_use_cases(
    data_access: DataAccess, doc: OnePagerDocument
) -> list[dict[str, Any]]:
    """Rows for the Use Cases table, resolved from the shared use_cases table.

    v2 documents hold only ``useCaseId`` references (Data_Model.md §5); their
    content is looked up here. v1 documents hold inline objects, shown as-is.
    A reference that cannot be resolved still shows its ID.
    """
    rows: list[dict[str, Any]] = []
    for item in doc.use_cases:
        use_case_id = item.get("useCaseId")
        if not use_case_id:
            rows.append(item)
            continue
        try:
            use_case = data_access.get_use_case(use_case_id)
        except Exception:
            logger.exception(f"Failed to resolve Use Case {use_case_id}")
            use_case = None
        if use_case is None:
            rows.append({"useCaseId": use_case_id, "persona": "(not available)"})
            continue
        rows.append(
            {
                "useCaseId": use_case.use_case_id,
                "persona": use_case.persona,
                "goal": use_case.goal,
                "decisionEnabled": use_case.decision_enabled,
                "priority": use_case.priority,
                "deprecated": use_case.deprecated,
            }
        )
    return rows


@dataclass
class PdfExport:
    """An exported One Pager: the file name to offer and the PDF bytes."""

    filename: str
    content: bytes


def export_filename(one_pager_id: str, version: str) -> str:
    """Download name of the PDF, e.g. ``OP-0001_v1.0.0.pdf``."""
    return f"{one_pager_id}_v{version}.pdf"


def export_one_pager_pdf(  # noqa: PLR0913 - service call with its context
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser | None,
    *,
    status_colors: dict[str, str] | None = None,
    now: datetime | None = None,
    compress: bool = True,
) -> PdfExport:
    """Render the One Pager as a PDF (Backend_Design.md §10).

    Args:
        data_access: Data access to read the One Pager and its Use Cases.
        one_pager_id: The One Pager to export.
        user: The exporting user; any authenticated user may export.
        status_colors: Badge color per One Pager and Data Product status
            (``ref_op_status`` / ``ref_dp_status``); gray when missing.
        now: Export time printed on the document (default: now, UTC).
        compress: Compress the page contents (tests read them uncompressed).

    Raises:
        PermissionDeniedError: The user is not authenticated.
        NotFoundError: No One Pager with this ID.

    """
    initials = user.initials if user else None
    if not (user and can_view_one_pager(user.username)):
        log_permission_denied("export_pdf", user=initials, one_pager_id=one_pager_id)
        msg = "Sign in to export One Pagers."
        raise PermissionDeniedError(msg)
    preview_data = data_access.get_one_pager(one_pager_id)
    if preview_data is None:
        msg = f"One Pager {one_pager_id} not found."
        raise NotFoundError(msg)
    use_case_rows = resolve_use_cases(data_access, preview_data.document)
    content = render_one_pager_pdf(
        preview_data,
        use_case_rows,
        status_colors or {},
        exported_by=user.display_name,
        exported_at=now or datetime.now(UTC),
        compress=compress,
    )
    header = preview_data.header
    log_event(
        "export_pdf",
        Outcome.SUCCESS,
        user=initials,
        one_pager_id=one_pager_id,
        version=header.version,
    )
    return PdfExport(export_filename(one_pager_id, header.version), content)


def render_one_pager_pdf(  # noqa: PLR0913 - everything printed on the document
    preview_data: PreviewData,
    use_case_rows: list[dict[str, Any]],
    status_colors: dict[str, str],
    *,
    exported_by: str,
    exported_at: datetime,
    compress: bool = True,
) -> bytes:
    """Lay out every One Pager section, in editor-tab order (UI_Design §4.4)."""
    header = preview_data.header
    doc = preview_data.document
    pdf = PdfDocument(
        f"{header.product_name} ({header.one_pager_id})",
        footer=f"{header.one_pager_id} {header.product_name} - v{header.version}",
        compress=compress,
        created_at=exported_at,
    )
    pdf.title_band(
        header.product_name, f"{header.one_pager_id}  |  Version {header.version}"
    )
    pdf.status_badges(
        [
            (
                "One Pager",
                header.one_pager_status,
                status_colors.get(header.one_pager_status, DEFAULT_STATUS_COLOR),
            ),
            (
                "Data Product",
                header.data_product_status,
                status_colors.get(header.data_product_status, DEFAULT_STATUS_COLOR),
            ),
        ]
    )
    pdf.spacer(4)
    _overview(pdf, preview_data, exported_by, exported_at)

    pdf.heading("Description")
    _text(pdf, doc.description, "No description provided.")
    pdf.heading("Business Problem Statement")
    _text(
        pdf, doc.business_problem_statement, "No business problem statement provided."
    )
    pdf.heading("Use Cases")
    _records(pdf, use_case_rows, USE_CASE_COLUMNS, "No use cases provided.")
    pdf.heading("Business Requirements")
    _records(
        pdf,
        doc.business_requirements,
        REQUIREMENT_COLUMNS,
        "No business requirements provided.",
    )
    pdf.heading("Data Sources")
    _records(pdf, doc.data_sources, DATA_SOURCE_COLUMNS, "No data sources provided.")
    pdf.heading("Data Product Preview")
    _records(
        pdf,
        doc.data_product_preview,
        DATA_ELEMENT_COLUMNS,
        "No data product preview provided.",
    )
    _classification(pdf, doc)
    _governance(pdf, doc)
    pdf.heading("Scope & Questions")
    pdf.subheading("Out of Scope")
    _list(pdf, doc.out_of_scope, "Nothing listed as out of scope.")
    pdf.subheading("Open Questions")
    _records(pdf, doc.open_questions, OPEN_QUESTION_COLUMNS, "No open questions.")
    pdf.subheading("Assumptions")
    _list(pdf, doc.assumptions, "No assumptions listed.")
    _change_log(pdf, preview_data.change_log)
    return pdf.build()


def _person(person: dict[str, Any]) -> str:
    name = person.get("name") or ""
    initials = person.get("initials")
    text = f"{name} ({initials})" if initials else name
    details = [str(person[k]) for k in ("email", "team") if person.get(k)]
    return f"{text}, {', '.join(details)}" if details else text


def _overview(
    pdf: PdfDocument,
    preview_data: PreviewData,
    exported_by: str,
    exported_at: datetime,
) -> None:
    header = preview_data.header
    doc = preview_data.document
    owner = _person(
        {
            "name": header.owner_name,
            "initials": header.owner_initials,
            "email": header.owner_email,
            "team": doc.owner_team,
        }
    )
    pdf.heading("Overview")
    pdf.label_value("Data Product", doc.data_product or "-")
    pdf.label_value("Business Domain", doc.business_domain or "-")
    pdf.label_value("Data Product Type", doc.data_product_type or "-")
    pdf.label_value("Owner", owner)
    pdf.label_value("SMEs", "; ".join(_person(s) for s in doc.smes) or "None")
    pdf.label_value(
        "Last Updated",
        f"{utc_label(header.last_updated_at)} by {header.last_updated_by}",
    )
    pdf.paragraph(
        f"Exported {utc_label(exported_at)} by {exported_by}.",
        size=SMALL_SIZE,
        color=MUTED_COLOR,
    )


def _text(pdf: PdfDocument, text: str, empty: str) -> None:
    if text and text.strip():
        pdf.paragraph(text.strip())
    else:
        pdf.paragraph(empty, color=MUTED_COLOR)


def _list(pdf: PdfDocument, items: list[str], empty: str) -> None:
    if items:
        pdf.bullets([str(item) for item in items])
    else:
        pdf.paragraph(empty, color=MUTED_COLOR)


def _records(
    pdf: PdfDocument, rows: list[dict[str, Any]], columns: dict[str, str], empty: str
) -> None:
    """One block per table row: the first column as its title, then the rest.

    Wide tables (the data element grid has ten columns) do not fit an A4
    page, so each row is printed as labelled lines instead of a table row.
    Empty cells are left out.
    """
    if not rows:
        pdf.paragraph(empty, color=MUTED_COLOR)
        return
    keys = list(columns)
    for number, row in enumerate(rows, start=1):
        title = cell_text(row, keys[0]) or f"{columns[keys[0]]} {number}"
        pdf.ensure_space(40)
        pdf.paragraph(title, font=BOLD, after=1)
        for key in keys[1:]:
            value = cell_text(row, key)
            if value:
                pdf.label_value(columns[key], value, indent=12)
        pdf.spacer(5)


def _yes_no(value: object) -> str:
    return "Yes" if value else "No"


def _classification(pdf: PdfDocument, doc: OnePagerDocument) -> None:
    pdf.heading("Classification")
    classification = doc.data_classification
    if classification:
        pdf.label_value(
            "Classification Level",
            str(classification.get("classificationLevel") or "N/A"),
        )
        pdf.label_value("Contains PII", _yes_no(classification.get("containsPII")))
        pdf.label_value(
            "Contains Sensitive Data",
            _yes_no(classification.get("containsSensitiveData")),
        )
    else:
        pdf.paragraph("No classification provided.", color=MUTED_COLOR)
    pdf.subheading("Retention Requirements")
    if classification.get("retentionRequirements"):  # v1: text in the classification
        pdf.paragraph(str(classification["retentionRequirements"]))
    else:
        _records(
            pdf,
            doc.retention_requirements,
            RETENTION_COLUMNS,
            "No retention requirements provided.",
        )


def _governance(pdf: PdfDocument, doc: OnePagerDocument) -> None:
    governance = doc.data_governance_artifacts
    pdf.heading("Governance")
    pdf.subheading("Business Concepts")
    _records(
        pdf,
        governance.get("businessConcepts", []),
        BUSINESS_CONCEPT_COLUMNS,
        "No business concepts provided.",
    )
    pdf.subheading("CDE Quality")
    _records(
        pdf,
        governance.get("cdeQuality", []),
        CDE_QUALITY_COLUMNS,
        "No CDE quality rules provided.",
    )
    pdf.subheading("CDE Lineage")
    _records(
        pdf,
        governance.get("cdeLineage", []),
        CDE_LINEAGE_COLUMNS,
        "No CDE lineage provided.",
    )


def _change_log(pdf: PdfDocument, entries: list[ChangeLogEntry]) -> None:
    pdf.heading("Change Log")
    if not entries:
        pdf.paragraph("No changes recorded yet.", color=MUTED_COLOR)
        return
    for entry in entries:
        line = (
            f"v{entry.version}  |  {utc_label(entry.created_at)}  |  "
            f"{entry.author_name} ({entry.author_initials})"
        )
        pdf.ensure_space(30)
        pdf.paragraph(line, font=BOLD, after=1)
        summary = entry.summary
        if entry.event_type == "status_transition" and entry.to_status:
            summary = f"{summary} ({entry.from_status} -> {entry.to_status})"
        pdf.paragraph(summary, after=5)
