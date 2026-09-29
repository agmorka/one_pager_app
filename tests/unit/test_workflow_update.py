"""Update: Approved → Draft Update (Req §5, Backend §2, §6)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import open_for_edit, save_draft, working_copy
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import Actor, InvalidTransitionError
from onepagerapp.workflow import (
    ConfirmationRequiredError,
    TransitionError,
    approve_one_pager,
    start_update,
    submit_for_review,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
ALICE = resolve_current_user("alice.brown@company.com")  # Owner of OP-0001
APPROVED_ID = "OP-0001"  # seeded Approved / Ready for Development, v1.0.0


@pytest.mark.unit
def test__update__to_draft_update_keeping_version_and_dp(
    mock_data_access: MockDataAccess,
) -> None:
    row = start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True, now=NOW)

    assert (row.one_pager_status, row.data_product_status, row.version) == (
        "Draft Update",
        "Ready for Development",
        "1.0.0",
    )
    entry = mock_data_access.get_change_log(APPROVED_ID)[0]
    assert (entry.from_status, entry.to_status, entry.version) == (
        "Approved",
        "Draft Update",
        "1.0.0",
    )
    assert mock_data_access.get_lock(APPROVED_ID) is None  # taken in the Editor


@pytest.mark.unit
def test__update__needs_confirmation(mock_data_access: MockDataAccess) -> None:
    with pytest.raises(ConfirmationRequiredError):
        start_update(mock_data_access, APPROVED_ID, ALICE)
    assert mock_data_access.get_one_pager_status_row(APPROVED_ID).one_pager_status == (
        "Approved"
    )


@pytest.mark.unit
def test__update__owner_or_sme_only(mock_data_access: MockDataAccess) -> None:
    other = resolve_current_user("cjo@bec.dk")
    with pytest.raises(PermissionDeniedError):
        start_update(mock_data_access, APPROVED_ID, other, confirmed=True)


@pytest.mark.unit
def test__update__only_when_approved(mock_data_access: MockDataAccess) -> None:
    start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True)
    with pytest.raises(InvalidTransitionError):
        start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True)


@pytest.mark.unit
def test__update__approved_document_must_exist(
    mock_data_access: MockDataAccess,
) -> None:
    rows = mock_data_access._status_rows
    rows[APPROVED_ID] = replace(rows[APPROVED_ID], version="3.0.0")

    with pytest.raises(TransitionError, match="could not be found"):
        start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True)
    assert mock_data_access.get_one_pager_status_row(APPROVED_ID).one_pager_status == (
        "Approved"
    )


@pytest.mark.unit
def test__full_update_cycle_ends_in_the_next_major(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    from tests.unit.test_editing_links import fill_all_sections  # noqa: PLC0415

    start_update(mock_data_access, APPROVED_ID, ALICE, confirmed=True, now=NOW)
    doc = working_copy(
        open_for_edit(mock_data_access, APPROVED_ID, ALICE, "s1", now=NOW).document
    )
    fill_all_sections(doc)
    saved = save_draft(
        mock_data_access,
        document_store,
        APPROVED_ID,
        doc,
        "Refreshed",
        ALICE,
        "s1",
        allowed_domains=["Customer"],
        allowed_types=["Foundational"],
        now=NOW,
    )
    assert saved.ok, saved.errors
    assert saved.version == "1.1.0"
    submitted = submit_for_review(mock_data_access, APPROVED_ID, ALICE, "s1", now=NOW)
    assert submitted.ok, submitted.errors

    row = approve_one_pager(
        mock_data_access,
        document_store,
        APPROVED_ID,
        resolve_current_user("cjo@bec.dk"),
        roles={Actor.APPROVER},
        now=NOW,
    )

    assert (row.one_pager_status, row.data_product_status, row.version) == (
        "Approved",
        "In Enhancement",
        "2.0.0",
    )
    assert document_store.exists(APPROVED_ID, "1.0.0")
    assert document_store.exists(APPROVED_ID, "2.0.0")


@pytest.mark.unit
def test__rejected_update_returns_to_draft_update(
    mock_data_access: MockDataAccess,
) -> None:
    from onepagerapp.workflow import reject_one_pager  # noqa: PLC0415

    rows = mock_data_access._status_rows
    rows[APPROVED_ID] = replace(
        rows[APPROVED_ID], one_pager_status="In Review", data_product_status="Active"
    )

    row = reject_one_pager(
        mock_data_access,
        APPROVED_ID,
        resolve_current_user("cjo@bec.dk"),
        "Keep the old lineage",
        roles={Actor.APPROVER},
        now=NOW,
    )

    assert (row.one_pager_status, row.data_product_status) == ("Draft Update", "Active")
    entry = mock_data_access.get_change_log(APPROVED_ID)[0]
    assert entry.summary == "Update rejected: Keep the old lineage"
