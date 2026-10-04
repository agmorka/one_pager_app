"""Validation badges and summary: field paths → editor tabs."""

from collections.abc import Callable
from types import ModuleType

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.editing import submission_issues
from onepagerapp.models import OnePagerDocument, OnePagerStatusRow, ValidationError
from onepagerapp.validation import CURRENT_STRUCTURE_DEFINITION
from tests.helpers import NEW_ID, fill_all_sections, status_row


@pytest.fixture
def edit_mode(import_app_module: Callable[[str], ModuleType]) -> ModuleType:
    """Return ``adapters.edit_mode``."""
    return import_app_module("adapters.edit_mode")


@pytest.fixture
def row() -> OnePagerStatusRow:
    """Return a v2 Draft status row of OP-0003, version 0.2.0."""
    return status_row(
        one_pager_id=NEW_ID,
        owner_initials="MJO",
        structure_definition=CURRENT_STRUCTURE_DEFINITION,
    )


@pytest.fixture
def document(draft_id: str, mock_data_access: MockDataAccess) -> OnePagerDocument:
    """Return the stored first version of the new Draft."""
    return mock_data_access.read_document(draft_id, "0.1.0")


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
def test__field_path__tab_for_path__editor_tab_or_none(
    edit_mode: ModuleType, path: str, tab: str | None
) -> None:
    """Each field path maps to the tab where it is edited."""
    # When
    result = edit_mode.tab_for_path(path)

    # Then
    assert result == tab


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
def test__field_path__describe_path__readable_location(
    edit_mode: ModuleType, path: str, text: str
) -> None:
    """Paths are described within their tab, items counted from 1."""
    # When
    result = edit_mode.describe_path(path)

    # Then
    assert result == text


@pytest.mark.unit
def test__errors_on_several_tabs__issues_by_tab__tab_order_then_form(
    edit_mode: ModuleType,
) -> None:
    """Issues are grouped in tab order; form-level ones come last."""
    # When
    grouped = edit_mode.issues_by_tab(
        [
            ValidationError("changeSummary", "x"),
            ValidationError("dataSources", "y"),
            ValidationError("productName", "z"),
        ]
    )

    # Then
    assert list(grouped) == ["Basics", "Data Sources", "Form"]


@pytest.mark.unit
@pytest.mark.parametrize(("count", "label"), [(2, "Basics (2)"), (0, "Basics")])
def test__issue_count__tab_label__count_only_when_issues(
    edit_mode: ModuleType, count: int, label: str
) -> None:
    """The tab label shows the number of issues, if any."""
    # When
    result = edit_mode.tab_label("Basics", count)

    # Then
    assert result == label


@pytest.mark.unit
def test__new_draft__submission_issues__missing_sections_listed(
    document: OnePagerDocument, row: OnePagerStatusRow
) -> None:
    """Missing sections are listed; operational fields come from the row."""
    # Given
    document.version = "garbage"

    # When
    paths = {e.field_path for e in submission_issues(document, row)}

    # Then
    assert {"useCases", "businessRequirements", "dataSources"} <= paths
    assert "dataClassification" in paths
    assert "version" not in paths


@pytest.mark.unit
def test__complete_document__submission_issues__none(
    document: OnePagerDocument, row: OnePagerStatusRow
) -> None:
    """A complete document is ready to submit."""
    # Given
    fill_all_sections(document)

    # When
    issues = submission_issues(document, row)

    # Then
    assert issues == []


@pytest.mark.unit
def test__complete_document_with_bad_email__submission_issues__only_that(
    document: OnePagerDocument, row: OnePagerStatusRow
) -> None:
    """A single problem is reported on its own."""
    # Given
    fill_all_sections(document)
    document.owner_email = "bad"

    # When
    issues = submission_issues(document, row)

    # Then
    assert [e.field_path for e in issues] == ["dataProductOwner.email"]
