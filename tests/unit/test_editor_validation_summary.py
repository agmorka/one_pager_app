"""Validation badges and summary: field paths → editor tabs."""

from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import submission_issues
from onepagerapp.models import (
    CurrentUser,
    NewOnePagerInput,
    OnePagerStatusRow,
    ValidationError,
)
from onepagerapp.validation import CURRENT_STRUCTURE_DEFINITION
from onepagerapp.workflow import create_one_pager
from tests.unit.test_editing_links import fill_all_sections

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def edit_mode(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(APP_DIR))
    from adapters import edit_mode  # noqa: PLC0415

    return edit_mode


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "tab"),
    [
        ("productName", "Basics"),
        ("smes[1].email", "Basics"),
        ("dataProductOwner.name", "Basics"),
        ("businessProblemStatement", "Business Problem"),
        ("useCases", "Use Cases"),
        ("businessRequirements[0].priority", "Business Requirements"),
        ("dataProductPreview[2].useCaseLinks", "Data Product Preview"),
        ("retentionRequirements", "Classification"),
        ("dataClassification.classificationLevel", "Classification"),
        ("dataGovernanceArtifacts.cdeQuality[0].rule", "Governance"),
        ("openQuestions[0].status", "Scope & Questions"),
        ("changeSummary", None),
        ("", None),
    ],
)
def test__tab_for_path(edit_mode: ModuleType, path: str, tab: str | None) -> None:
    assert edit_mode.tab_for_path(path) == tab


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("productName", ""),
        ("dataSources[0].name", "item 1 \u203a name"),
        (
            "dataGovernanceArtifacts.cdeQuality[1].rule",
            "cdeQuality \u203a item 2 \u203a rule",
        ),
        ("dataProductOwner.email", "email"),
    ],
)
def test__describe_path(edit_mode: ModuleType, path: str, text: str) -> None:
    assert edit_mode.describe_path(path) == text


@pytest.mark.unit
def test__issues_by_tab__tab_order_and_form_group(edit_mode: ModuleType) -> None:
    grouped = edit_mode.issues_by_tab(
        [
            ValidationError("changeSummary", "x"),
            ValidationError("dataSources", "y"),
            ValidationError("productName", "z"),
        ]
    )
    assert list(grouped) == ["Basics", "Data Sources", "Form"]
    assert edit_mode.tab_label("Basics", 2) == "Basics 🔴 2"
    assert edit_mode.tab_label("Basics", 0) == "Basics"


def _row() -> OnePagerStatusRow:
    now = datetime(2026, 9, 29, tzinfo=UTC)
    return OnePagerStatusRow(
        one_pager_id="OP-0003",
        data_product="customer_master",
        product_name="C",
        business_domain="Customer",
        data_product_type="Foundational",
        one_pager_status="Draft",
        data_product_status="In Definition",
        version="0.2.0",
        owner_name="M",
        owner_initials="MJO",
        owner_email="m@bec.dk",
        owner_team=None,
        created_by="MJO",
        created_at=now,
        last_updated_at=now,
        last_updated_by="MJO",
        structure_definition=CURRENT_STRUCTURE_DEFINITION,
    )


@pytest.mark.unit
def test__submission_issues__lists_missing_sections(
    mock_data_access: MockDataAccess,
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    create_one_pager(valid_input, creator, mock_data_access, document_store)
    doc = mock_data_access.read_document("OP-0003", "0.1.0")
    doc.version = "garbage"  # operational fields come from the row

    paths = {e.field_path for e in submission_issues(doc, _row())}

    assert {"useCases", "businessRequirements", "dataSources"} <= paths
    assert "dataClassification" in paths
    assert "version" not in paths


@pytest.mark.unit
def test__submission_issues__complete_document_has_none(
    mock_data_access: MockDataAccess,
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    create_one_pager(valid_input, creator, mock_data_access, document_store)
    doc = mock_data_access.read_document("OP-0003", "0.1.0")
    fill_all_sections(doc)
    assert submission_issues(doc, _row()) == []
    doc.owner_email = "bad"
    assert [e.field_path for e in submission_issues(doc, _row())] == [
        "dataProductOwner.email"
    ]
