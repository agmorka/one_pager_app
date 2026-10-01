from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
import yaml

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import (
    CurrentUser,
    NewOnePagerInput,
    OnePagerDocument,
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
from tests.conftest import FIXTURES_DIR
from tests.users import CREATOR_ROLES

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _fixture_files() -> set[str]:
    return {str(p.relative_to(FIXTURES_DIR)) for p in FIXTURES_DIR.rglob("*")}


@pytest.mark.unit
def test__create__happy_path(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    fixtures_before = _fixture_files()

    result = create_one_pager(
        valid_input,
        creator,
        mock_data_access,
        document_store,
        now=NOW,
        roles=CREATOR_ROLES,
    )

    assert result.ok
    assert result.one_pager_id == "OP-0003"  # mock seeds OP-0001..OP-0002
    assert result.version == "0.1.0"

    header = mock_data_access.get_one_pager_status("OP-0003")
    assert header.one_pager_status == "Draft"
    assert header.data_product_status == "In Definition"
    assert header.version == "0.1.0"
    assert header.owner_initials == "MJO"
    assert header.last_updated_by == "MJO"

    [entry] = mock_data_access.get_change_log("OP-0003")
    assert entry.event_type == "creation"
    assert entry.summary == "Initial draft created"
    assert entry.to_status == "Draft"
    assert entry.from_status is None
    assert entry.status_field == "one_pager_status"
    assert entry.version == "0.1.0"

    users = mock_data_access.get_authorized_users("OP-0003")
    assert {(u.user_initials, u.role) for u in users} == {
        ("MJO", "owner"),
        ("DPR", "sme"),
    }

    # Registry and Preview see the new One Pager
    registry = mock_data_access.get_registry(RegistryFilter(), 1, 20)
    assert "OP-0003" in [r.one_pager_id for r in registry.rows]
    assert mock_data_access.get_registry_status_counts(RegistryFilter())["Draft"] == 1
    preview = mock_data_access.get_one_pager("OP-0003")
    assert preview.document.product_name == "Customer Master Data"

    # YAML written to OP-####/OP-####_v0.1.0.yml as a lenient-valid v2 Draft
    # (not yet submittable), fixtures untouched
    assert document_store.exists("OP-0003", "0.1.0")
    raw = yaml.safe_load(preview.document.raw_content)
    assert validate_lenient(raw) == []
    assert validate_strict(raw) != []
    assert raw["structureDefinition"] == CURRENT_STRUCTURE_DEFINITION
    assert raw["createdBy"] == creator.display_name
    assert raw["changeLog"][0]["summary"] == "Initial draft created"
    assert raw["smes"] == [
        {"name": "Diana Prince", "initials": "DPR", "email": "diana@bec.dk"}
    ]
    assert _fixture_files() == fixtures_before


@pytest.mark.unit
def test__create__status_row_audit_fields(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    create_one_pager(
        valid_input,
        creator,
        mock_data_access,
        document_store,
        now=NOW,
        roles=CREATOR_ROLES,
    )
    row = mock_data_access._status_rows["OP-0003"]
    assert row.created_by == "MJO"
    assert row.created_at == NOW
    assert row.structure_definition == CURRENT_STRUCTURE_DEFINITION
    assert row.pending_pr is False


@pytest.mark.unit
def test__create__validation_errors_write_nothing(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    result = create_one_pager(
        replace(valid_input, product_name=""),
        creator,
        mock_data_access,
        document_store,
        roles=CREATOR_ROLES,
    )
    assert not result.ok
    assert [e.field_path for e in result.errors] == ["productName"]
    assert mock_data_access.get_sequence_value("OP") == 2  # no ID consumed
    assert mock_data_access.get_one_pager_status("OP-0003") is None


@pytest.mark.unit
def test__create__duplicate_data_product(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    result = create_one_pager(
        replace(valid_input, data_product="person"),  # seeded as OP-0001
        creator,
        mock_data_access,
        document_store,
        roles=CREATOR_ROLES,
    )
    assert not result.ok
    assert result.errors[0].field_path == "dataProduct"
    assert "OP-0001" in result.errors[0].message


@pytest.mark.unit
def test__create__second_create_gets_next_id(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    first = create_one_pager(
        valid_input, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )
    second = create_one_pager(
        replace(valid_input, data_product="customer_master_v2"),
        creator,
        mock_data_access,
        document_store,
        roles=CREATOR_ROLES,
    )
    assert (first.one_pager_id, second.one_pager_id) == ("OP-0003", "OP-0004")


@pytest.mark.unit
def test__create__permission_denied(
    valid_input: NewOnePagerInput,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    anonymous = CurrentUser(username="", initials="", display_name="")
    with pytest.raises(PermissionDeniedError):
        create_one_pager(
            valid_input,
            anonymous,
            mock_data_access,
            document_store,
            roles=CREATOR_ROLES,
        )


class _FailingStore(OnePagerDocumentStore):
    def write(
        self,
        one_pager_id: str,  # noqa: ARG002
        document: OnePagerDocument,  # noqa: ARG002
        version: str,  # noqa: ARG002
    ) -> Path:
        msg = "volume unavailable"
        raise RuntimeError(msg)


@pytest.mark.unit
def test__create__document_write_fails(
    valid_input: NewOnePagerInput, creator: CurrentUser, tmp_path: Path
) -> None:
    store = _FailingStore(FIXTURES_DIR, write_path=tmp_path)
    data_access = MockDataAccess(store)
    with pytest.raises(CreateError, match="changes are preserved"):
        create_one_pager(valid_input, creator, data_access, store, roles=CREATOR_ROLES)
    assert data_access.get_one_pager_status("OP-0003") is None
    assert data_access.get_authorized_users("OP-0003") == []


class _StatusInsertFails(MockDataAccess):
    def insert_one_pager_status(self, row: OnePagerStatusRow) -> None:  # noqa: ARG002
        msg = "warehouse unavailable"
        raise RuntimeError(msg)


@pytest.mark.unit
def test__create__status_insert_fails_is_compensated(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    data_access = _StatusInsertFails(document_store)
    with pytest.raises(CreateError):
        create_one_pager(
            valid_input, creator, data_access, document_store, roles=CREATOR_ROLES
        )
    # Rows from the earlier steps were removed; nothing is visible.
    assert data_access.get_authorized_users("OP-0003") == []
    assert data_access.get_change_log("OP-0003") == []
    assert data_access.get_one_pager_status("OP-0003") is None
    # The orphan YAML file is left in place (never referenced) — D5.
    assert document_store.exists("OP-0003", "0.1.0")


class _RaceLoser(MockDataAccess):
    """Simulates another session creating the same data product concurrently."""

    def insert_one_pager_status(self, row: OnePagerStatusRow) -> None:
        super().insert_one_pager_status(row)
        winner = replace(row, one_pager_id="OP-0000")
        self._status_rows[winner.one_pager_id] = winner


@pytest.mark.unit
def test__create__race_on_data_product_lower_id_wins(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    data_access = _RaceLoser(document_store)
    result = create_one_pager(
        valid_input, creator, data_access, document_store, roles=CREATOR_ROLES
    )
    assert not result.ok
    assert "OP-0000" in result.errors[0].message
    assert data_access.get_one_pager_status("OP-0003") is None


@pytest.mark.unit
def test__create__input_is_sanitized(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> None:
    data = replace(valid_input, product_name="  <b>Customer</b> Master  ")
    result = create_one_pager(
        data, creator, mock_data_access, document_store, roles=CREATOR_ROLES
    )
    assert mock_data_access.get_one_pager_status(result.one_pager_id).product_name == (
        "Customer Master"
    )


@pytest.mark.unit
def test__active_reference_values__filters_inactive_and_sorts() -> None:
    df = pd.DataFrame(
        [
            {"domain": "B", "sort_order": "2", "active": "true"},
            {"domain": "A", "sort_order": "1", "active": True},
            {"domain": "C", "sort_order": "3", "active": "false"},
        ]
    )
    assert active_reference_values(df, "domain") == ["A", "B"]
    assert active_reference_values(pd.DataFrame(), "domain") == []
