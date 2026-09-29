"""Approve: In Review → Approved, next MAJOR version, system DP transition."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import NoReturn

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import ChangeLogEntry
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import Actor, InvalidTransitionError
from onepagerapp.workflow import (
    TransitionError,
    approve_one_pager,
    next_major,
    plan_approval,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
APPROVER = resolve_current_user("cjo@bec.dk")
ROLES = frozenset({Actor.APPROVER})
IN_REVIEW_ID = "OP-0002"  # seeded In Review / In Definition, v0.3.0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.1.0", "1.0.0"), ("0.3.0", "1.0.0"), ("1.2.0", "2.0.0"), ("2.0.0", "3.0.0")],
)
def test__next_major(version: str, expected: str) -> None:
    assert next_major(version) == expected


@pytest.mark.unit
def test__next_major__rejects_invalid_versions() -> None:
    with pytest.raises(ValueError, match="Invalid version"):
        next_major("1.0")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dp_status", "expected_dp"),
    [
        ("In Definition", "Ready for Development"),
        ("Ready for Development", "In Enhancement"),
        ("In Development", "In Enhancement"),
        ("Active", "In Enhancement"),
        ("Deprecated", "In Enhancement"),
        ("In Enhancement", None),
    ],
)
def test__plan_approval__system_dp_transition(
    mock_data_access: MockDataAccess, dp_status: str, expected_dp: str | None
) -> None:
    row = replace(
        mock_data_access.get_one_pager_status_row(IN_REVIEW_ID),
        data_product_status=dp_status,
    )
    plan = plan_approval(row)

    assert plan.data_product_status == expected_dp
    assert len(plan.rules) == (2 if expected_dp else 1)


@pytest.mark.unit
def test__approve__first_approval(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    row = approve_one_pager(
        mock_data_access, document_store, IN_REVIEW_ID, APPROVER, roles=ROLES, now=NOW
    )

    assert (row.one_pager_status, row.data_product_status, row.version) == (
        "Approved",
        "Ready for Development",
        "1.0.0",
    )
    assert (row.reviewed_by, row.reviewed_at) == ("CJO", NOW)
    assert mock_data_access.get_one_pager_status_row(IN_REVIEW_ID).version == "1.0.0"

    entries = mock_data_access.get_change_log(IN_REVIEW_ID)[:2]
    changes = {(e.status_field, e.from_status, e.to_status, e.version) for e in entries}
    assert changes == {
        ("one_pager_status", "In Review", "Approved", "1.0.0"),
        ("data_product_status", "In Definition", "Ready for Development", "1.0.0"),
    }

    approved = document_store.read(IN_REVIEW_ID, "1.0.0")
    assert approved is not None
    assert (approved.one_pager_status, approved.data_product_status) == (
        "Approved",
        "Ready for Development",
    )
    assert approved.version == "1.0.0"
    assert approved.change_log[-1]["summary"].startswith("Data Product ready")
    previous = document_store.read(IN_REVIEW_ID, "0.3.0")
    assert approved.description == previous.description
    assert document_store.exists(IN_REVIEW_ID, "0.3.0")


@pytest.mark.unit
def test__approve__re_approval_sets_in_enhancement(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    rows = mock_data_access._status_rows
    rows["OP-0001"] = replace(
        rows["OP-0001"], one_pager_status="In Review", data_product_status="Active"
    )

    row = approve_one_pager(
        mock_data_access, document_store, "OP-0001", APPROVER, roles=ROLES, now=NOW
    )

    assert (row.version, row.data_product_status) == ("2.0.0", "In Enhancement")
    assert document_store.exists("OP-0001", "2.0.0")


@pytest.mark.unit
def test__approve__approvers_only_and_not_own(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    with pytest.raises(PermissionDeniedError):
        approve_one_pager(
            mock_data_access, document_store, IN_REVIEW_ID, APPROVER, roles=set()
        )
    owner = resolve_current_user("bob.smith@company.com")
    with pytest.raises(PermissionDeniedError, match="Owner or SME"):
        approve_one_pager(
            mock_data_access, document_store, IN_REVIEW_ID, owner, roles=ROLES
        )
    assert not document_store.exists(IN_REVIEW_ID, "1.0.0")


@pytest.mark.unit
def test__approve__only_in_review(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    with pytest.raises(InvalidTransitionError):
        approve_one_pager(
            mock_data_access, document_store, "OP-0001", APPROVER, roles=ROLES
        )


@pytest.mark.unit
def test__approve__failure_changes_nothing(
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(entries: list[ChangeLogEntry]) -> NoReturn:
        msg = "warehouse down"
        raise RuntimeError(msg)

    monkeypatch.setattr(mock_data_access, "append_change_log_entries", fail)

    with pytest.raises(TransitionError):
        approve_one_pager(
            mock_data_access, document_store, IN_REVIEW_ID, APPROVER, roles=ROLES
        )

    row = mock_data_access.get_one_pager_status_row(IN_REVIEW_ID)
    assert (row.one_pager_status, row.version) == ("In Review", "0.3.0")
    assert not document_store.exists(IN_REVIEW_ID, "1.0.0")


@pytest.mark.unit
def test__approve__missing_document(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    rows = mock_data_access._status_rows
    rows[IN_REVIEW_ID] = replace(rows[IN_REVIEW_ID], version="0.9.0")

    with pytest.raises(TransitionError):
        approve_one_pager(
            mock_data_access, document_store, IN_REVIEW_ID, APPROVER, roles=ROLES
        )
    assert mock_data_access.get_one_pager_status_row(IN_REVIEW_ID).one_pager_status == (
        "In Review"
    )
