"""Compare two versions of a One Pager ("What changed", UI_Design.md §4.3).

``comparison_base`` picks the version an Approver compares against: the last
approved version, else the last version that was rejected, else none (the
first version under review). ``diff_documents`` lists the sections that differ.
``changed_sections`` names the sections changed in the editor, for the change
summary suggested on Save Draft.

Pure Python — no Streamlit.
"""

from dataclasses import dataclass
from typing import Any

import yaml

from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.models import ChangeLogEntry, OnePagerDocument
from onepagerapp.state_machine import APPROVED, DRAFT, DRAFT_UPDATE, IN_REVIEW
from onepagerapp.timeutils import as_utc
from onepagerapp.validation import normalize_document

# Document keys that are metadata, not content: never shown as a change.
METADATA_KEYS = frozenset(
    {
        "structureDefinition",
        "onePagerStatus",
        "dataProductStatus",
        "version",
        "createdBy",
        "createdAt",
        "lastUpdated",
        "changeLog",
    }
)

# Content keys in reading order, with the label shown to users.
SECTION_LABELS: dict[str, str] = {
    "dataProduct": "Data Product",
    "productName": "Product Name",
    "businessDomain": "Business Domain",
    "dataProductType": "Product Type",
    "description": "Description",
    "dataProductOwner": "Data Product Owner",
    "smes": "Subject Matter Experts",
    "businessProblemStatement": "Business Problem Statement",
    "useCases": "Use Cases",
    "businessRequirements": "Business Requirements",
    "dataSources": "Data Sources",
    "dataProductPreview": "Data Product Preview",
    "dataClassification": "Classification",
    "retentionRequirements": "Retention Requirements",
    "dataGovernanceArtifacts": "Governance",
    "outOfScope": "Out of Scope",
    "openQuestions": "Open Questions",
    "assumptions": "Assumptions",
}


@dataclass(frozen=True)
class ComparisonBase:
    """The version to compare with, and why it was chosen.

    Attributes:
        version: The document version to compare against.
        reason: "approved" (last approved version) or "rejected" (the last
            version sent back by an Approver).

    """

    version: str
    reason: str

    @property
    def label(self) -> str:
        """Readable description, e.g. "the last approved version (v1.0.0)"."""
        kind = "approved" if self.reason == "approved" else "reviewed (rejected)"
        return f"the last {kind} version (v{self.version})"


@dataclass(frozen=True)
class SectionChange:
    """One section that differs between two versions.

    Attributes:
        key: Document key, e.g. "dataSources".
        label: Readable section name.
        kind: "added", "removed" or "changed".
        before: The old content as readable text ("" when added).
        after: The new content as readable text ("" when removed).

    """

    key: str
    label: str
    kind: str
    before: str
    after: str


def _is_review_outcome(entry: ChangeLogEntry, to_status: tuple[str, ...]) -> bool:
    return (
        entry.event_type == "status_transition"
        and entry.status_field in (None, "one_pager_status")
        and entry.to_status in to_status
    )


def comparison_base(
    change_log: list[ChangeLogEntry], current_version: str
) -> ComparisonBase | None:
    """Return the version to compare ``current_version`` with; None if first.

    The last approval wins; without one, the last rejection (In Review back to
    Draft / Draft Update) is used, so a resubmission shows what was reworked.
    """
    newest_first = sorted(change_log, key=lambda e: as_utc(e.created_at), reverse=True)
    for entry in newest_first:
        if _is_review_outcome(entry, (APPROVED,)) and entry.version != current_version:
            return ComparisonBase(entry.version, "approved")
    for entry in newest_first:
        if (
            _is_review_outcome(entry, (DRAFT, DRAFT_UPDATE))
            and entry.from_status == IN_REVIEW
            and entry.version != current_version
        ):
            return ComparisonBase(entry.version, "rejected")
    return None


def _content(document: OnePagerDocument) -> dict[str, Any]:
    data = document_to_dict(normalize_document(document))
    return {k: v for k, v in data.items() if k not in METADATA_KEYS}


def _as_text(value: Any) -> str:  # noqa: ANN401 - any YAML value
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value
    return str(yaml.safe_dump(value, sort_keys=False, allow_unicode=True)).strip()


def diff_documents(old: OnePagerDocument, new: OnePagerDocument) -> list[SectionChange]:
    """Sections whose content differs, in reading order."""
    before, after = _content(old), _content(new)
    keys = [k for k in SECTION_LABELS if k in before or k in after]
    keys += sorted((before.keys() | after.keys()) - set(keys))
    changes = []
    for key in keys:
        old_text, new_text = _as_text(before.get(key)), _as_text(after.get(key))
        if old_text == new_text:
            continue
        kind = "added" if not old_text else "removed" if not new_text else "changed"
        changes.append(
            SectionChange(key, SECTION_LABELS.get(key, key), kind, old_text, new_text)
        )
    return changes


def changed_sections(saved: OnePagerDocument, working: OnePagerDocument) -> list[str]:
    """Labels of the sections changed in the working copy, in reading order."""
    return [change.label for change in diff_documents(saved, working)]


def suggested_summary(saved: OnePagerDocument, working: OnePagerDocument) -> str:
    """Suggest a change summary, e.g. "Updated Description, Data Sources"."""
    labels = changed_sections(saved, working)
    return f"Updated {', '.join(labels)}" if labels else ""
