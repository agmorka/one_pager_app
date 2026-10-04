"""Create a One Pager: a new Draft in every table plus its first YAML version."""

from dataclasses import replace

import pandas as pd
import pytest
import yaml

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import (
    CurrentUser,
    NewOnePagerInput,
    OnePagerStatusRow,
    RegistryFilter,
)
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.validation import (
    CURRENT_STRUCTURE_DEFINITION,
    validate_lenient,
    validate_strict,
)
from onepagerapp.workflow import CreateError, active_reference_values, create_one_pager
from tests.helpers import CREATOR_ROLES, FIXTURES_DIR, NEW_ID, NOW, failing


def _fixture_files() -> set[str]:
    """Return every path below the fixtures folder."""
    return {str(p.relative_to(FIXTURES_DIR)) for p in FIXTURES_DIR.rglob("*")}


@pytest.mark.unit
def test__valid_input__create__returns_first_draft_version(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A valid create gets the next ID and version 0.1.0."""
    # When
    result = create_one_pager(
        valid_input,
        creator,
        mock_data_access,
        document_store,
        now=NOW,
        roles=CREATOR_ROLES,
    )

    # Then
    assert result.ok
    assert (result.one_pager_id, result.version) == (NEW_ID, "0.1.0")


@pytest.mark.unit
def test__valid_input__create__writes_status_row_change_log_and_users(
    draft_id: str, mock_data_access: MockDataAccess
) -> None:
    """A create writes the header, one creation entry and the Owner/SMEs."""
    # Given a created One Pager (fixture)

    # When
    header = mock_data_access.get_one_pager_status(draft_id)
    [entry] = mock_data_access.get_change_log(draft_id)
    users = mock_data_access.get_authorized_users(draft_id)

    # Then
    assert (header.one_pager_status, header.data_product_status) == (
        "Draft",
        "In Definition",
    )
    assert (header.version, header.owner_initials, header.last_updated_by) == (
        "0.1.0",
        "MJO",
        "MJO",
    )
    assert (entry.event_type, entry.summary, entry.version) == (
        "creation",
        "Initial draft created",
        "0.1.0",
    )
    assert (entry.status_field, entry.from_status, entry.to_status) == (
        "one_pager_status",
        None,
        "Draft",
    )
    assert {(u.user_initials, u.role) for u in users} == {
        ("MJO", "owner"),
        ("DPR", "sme"),
    }


@pytest.mark.unit
def test__valid_input__create__fills_the_status_row_audit_fields(
    draft_id: str, mock_data_access: MockDataAccess
) -> None:
    """The status row records who created it, when, and the schema version."""
    # Given a created One Pager (fixture)

    # When
    row = mock_data_access._status_rows[draft_id]

    # Then
    assert (row.created_by, row.created_at) == ("MJO", NOW)
    assert row.structure_definition == CURRENT_STRUCTURE_DEFINITION
    assert row.pending_pr is False


@pytest.mark.unit
def test__valid_input__create__registry_and_preview_show_it(
    draft_id: str, mock_data_access: MockDataAccess
) -> None:
    """The new One Pager is listed in the Registry and can be previewed."""
    # Given a created One Pager (fixture)

    # When
    registry = mock_data_access.get_registry(RegistryFilter(), 1, 20)
    counts = mock_data_access.get_registry_status_counts(RegistryFilter())
    preview = mock_data_access.get_one_pager(draft_id)

    # Then
    assert draft_id in [r.one_pager_id for r in registry.rows]
    assert counts["Draft"] == 1
    assert preview.document.product_name == "Customer Master Data"


@pytest.mark.unit
def test__valid_input__create__writes_a_lenient_valid_v2_yaml(
    draft_id: str,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """The YAML file is a lenient-valid v2 Draft that is not yet submittable."""
    # Given a created One Pager (fixture)

    # When
    raw = yaml.safe_load(mock_data_access.get_one_pager(draft_id).document.raw_content)

    # Then
    assert document_store.exists(draft_id, "0.1.0")
    assert validate_lenient(raw) == []
    assert validate_strict(raw) != []
    assert raw["structureDefinition"] == CURRENT_STRUCTURE_DEFINITION
    assert raw["createdBy"] == creator.display_name
    assert raw["changeLog"][0]["summary"] == "Initial draft created"
    assert raw["smes"] == [
        {"name": "Diana Prince", "initials": "DPR", "email": "diana@bec.dk"}
    ]


@pytest.mark.unit
def test__fixture_documents__create__leaves_fixtures_untouched(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """New version files go to the write path, never into the fixtures."""
    # Given
    fixtures_before = _fixture_files()

    # When
    create_one_pager(
        valid_input, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )

    # Then
    assert _fixture_files() == fixtures_before


@pytest.mark.unit
def test__empty_product_name__create__returns_error_and_writes_nothing(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Validation errors are returned before any ID or row is written."""
    # Given
    data = replace(valid_input, product_name="")

    # When
    result = create_one_pager(
        data, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )

    # Then
    assert not result.ok
    assert [e.field_path for e in result.errors] == ["productName"]
    assert mock_data_access.get_sequence_value("OP") == 2  # no ID consumed
    assert mock_data_access.get_one_pager_status(NEW_ID) is None


@pytest.mark.unit
def test__data_product_of_existing_one_pager__create__returns_duplicate_error(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A data product that already has a One Pager names that One Pager."""
    # Given
    data = replace(valid_input, data_product="person")  # seeded as OP-0001

    # When
    result = create_one_pager(
        data, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )

    # Then
    assert not result.ok
    assert result.errors[0].field_path == "dataProduct"
    assert "OP-0001" in result.errors[0].message


@pytest.mark.unit
def test__one_pager_already_created__create_another__gets_the_next_id(
    draft_id: str,
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Each create consumes the next sequence value."""
    # Given
    data = replace(valid_input, data_product="customer_master_v2")

    # When
    second = create_one_pager(
        data, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )

    # Then
    assert second.one_pager_id == "OP-0004"


@pytest.mark.unit
def test__anonymous_user__create__raises_permission_denied(
    valid_input: NewOnePagerInput,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """A user without initials cannot create a One Pager."""
    # Given
    anonymous = CurrentUser(username="", initials="", display_name="")

    # When / Then
    with pytest.raises(PermissionDeniedError):
        create_one_pager(
            valid_input,
            anonymous,
            mock_data_access,
            document_store,
            roles=CREATOR_ROLES,
        )


@pytest.mark.unit
def test__document_write_fails__create__raises_and_leaves_no_rows(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed YAML write stops the create before any row is visible."""
    # Given
    monkeypatch.setattr(document_store, "write", failing("volume unavailable"))

    # When / Then
    with pytest.raises(CreateError, match="changes are preserved"):
        create_one_pager(
            valid_input, creator, mock_data_access, document_store, roles=CREATOR_ROLES
        )
    assert mock_data_access.get_one_pager_status(NEW_ID) is None
    assert mock_data_access.get_authorized_users(NEW_ID) == []


@pytest.mark.unit
def test__status_insert_fails__create__removes_earlier_rows_keeps_yaml(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows of earlier steps are compensated; the orphan YAML stays (D5)."""
    # Given
    monkeypatch.setattr(
        mock_data_access, "insert_one_pager_status", failing("warehouse unavailable")
    )

    # When / Then
    with pytest.raises(CreateError):
        create_one_pager(
            valid_input, creator, mock_data_access, document_store, roles=CREATOR_ROLES
        )
    assert mock_data_access.get_authorized_users(NEW_ID) == []
    assert mock_data_access.get_change_log(NEW_ID) == []
    assert mock_data_access.get_one_pager_status(NEW_ID) is None
    assert document_store.exists(NEW_ID, "0.1.0")


@pytest.mark.unit
def test__concurrent_create_of_same_data_product__create__lower_id_wins(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing a race on the data product removes this create and names the winner."""
    # Given another session inserts OP-0000 for the same data product meanwhile
    insert = mock_data_access.insert_one_pager_status

    def insert_and_lose_the_race(row: OnePagerStatusRow) -> None:
        insert(row)
        winner = replace(row, one_pager_id="OP-0000")
        mock_data_access._status_rows[winner.one_pager_id] = winner

    monkeypatch.setattr(
        mock_data_access, "insert_one_pager_status", insert_and_lose_the_race
    )

    # When
    result = create_one_pager(
        valid_input, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )

    # Then
    assert not result.ok
    assert "OP-0000" in result.errors[0].message
    assert mock_data_access.get_one_pager_status(NEW_ID) is None


@pytest.mark.unit
def test__product_name_with_html_and_spaces__create__stores_sanitized_name(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    """Input is stripped of HTML tags and surrounding whitespace."""
    # Given
    data = replace(valid_input, product_name="  <b>Customer</b> Master  ")

    # When
    result = create_one_pager(
        data, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )

    # Then
    header = mock_data_access.get_one_pager_status(result.one_pager_id)
    assert header.product_name == "Customer Master"


@pytest.mark.unit
def test__inactive_reference_value__active_reference_values__excluded_and_sorted() -> (
    None
):
    """Only active values are offered, in sort order."""
    # Given
    df = pd.DataFrame(
        [
            {"domain": "B", "sort_order": "2", "active": "true"},
            {"domain": "A", "sort_order": "1", "active": True},
            {"domain": "C", "sort_order": "3", "active": "false"},
        ]
    )

    # When
    values = active_reference_values(df, "domain")

    # Then
    assert values == ["A", "B"]


@pytest.mark.unit
def test__empty_reference_table__active_reference_values__returns_nothing() -> None:
    """An empty reference table offers no values."""
    # Given
    df = pd.DataFrame()

    # When
    values = active_reference_values(df, "domain")

    # Then
    assert values == []
