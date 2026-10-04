"""Approve: In Review → Approved, next MAJOR version, system DP transition."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import (
    TransitionError,
    approve_one_pager,
    next_major,
    plan_approval,
)
from tests.helpers import (
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    BOB,
    IN_REVIEW_ID,
    NOW,
    failing,
    update_status_row,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.1.0", "1.0.0"), ("0.3.0", "1.0.0"), ("1.2.0", "2.0.0"), ("2.0.0", "3.0.0")],
)
def test__version__next_major__bumps_major_and_resets_minor(
    version: str, expected: str
) -> None:
    """The next MAJOR version starts at minor and patch 0."""
    # When
    result = next_major(version)

    # Then
    assert result == expected


@pytest.mark.unit
def test__invalid_version__next_major__raises_value_error() -> None:
    """A version without three parts is rejected."""
    # When / Then
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
def test__dp_status__plan_approval__adds_system_dp_transition(
    mock_data_access: MockDataAccess, dp_status: str, expected_dp: str | None
) -> None:
    """Approval moves the DP status automatically unless it is In Enhancement."""
    # Given
    row = update_status_row(
        mock_data_access, IN_REVIEW_ID, data_product_status=dp_status
    )

    # When
    plan = plan_approval(row)

    # Then
    assert plan.data_product_status == expected_dp
    assert len(plan.rules) == (2 if expected_dp else 1)


@pytest.mark.unit
def test__first_review__approve__version_1_0_0_ready_for_development(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """The first approval gives v1.0.0 and records the reviewer."""
    # When
    row = approve_one_pager(
        mock_data_access,
        document_store,
        IN_REVIEW_ID,
        APPROVER,
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    assert (row.one_pager_status, row.data_product_status, row.version) == (
        "Approved",
        "Ready for Development",
        "1.0.0",
    )
    assert (row.reviewed_by, row.reviewed_at) == ("CJO", NOW)
    assert mock_data_access.get_one_pager_status_row(IN_REVIEW_ID).version == "1.0.0"


@pytest.mark.unit
def test__first_review__approve__logs_op_and_dp_transitions(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """Both status changes are logged with the new version."""
    # When
    approve_one_pager(
        mock_data_access,
        document_store,
        IN_REVIEW_ID,
        APPROVER,
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    entries = mock_data_access.get_change_log(IN_REVIEW_ID)[:2]
    changes = {(e.status_field, e.from_status, e.to_status, e.version) for e in entries}
    assert changes == {
        ("one_pager_status", "In Review", "Approved", "1.0.0"),
        ("data_product_status", "In Definition", "Ready for Development", "1.0.0"),
    }


@pytest.mark.unit
def test__first_review__approve__writes_approved_copy_of_reviewed_version(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """The approved file copies the reviewed content and keeps the old file."""
    # When
    approve_one_pager(
        mock_data_access,
        document_store,
        IN_REVIEW_ID,
        APPROVER,
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    approved = document_store.read(IN_REVIEW_ID, "1.0.0")
    previous = document_store.read(IN_REVIEW_ID, "0.3.0")
    assert (approved.one_pager_status, approved.data_product_status) == (
        "Approved",
        "Ready for Development",
    )
    assert approved.version == "1.0.0"
    assert approved.change_log[-1]["summary"].startswith("Data Product ready")
    assert approved.description == previous.description


@pytest.mark.unit
def test__active_one_pager_in_review__approve__next_major_in_enhancement(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """A re-approval moves the DP to In Enhancement."""
    # Given
    update_status_row(
        mock_data_access,
        APPROVED_ID,
        one_pager_status="In Review",
        data_product_status="Active",
    )

    # When
    row = approve_one_pager(
        mock_data_access,
        document_store,
        APPROVED_ID,
        APPROVER,
        roles=APPROVER_ROLES,
        now=NOW,
    )

    # Then
    assert (row.version, row.data_product_status) == ("2.0.0", "In Enhancement")
    assert document_store.exists(APPROVED_ID, "2.0.0")


@pytest.mark.unit
def test__user_without_approver_role__approve__raises_permission_denied(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """Only Approvers can approve."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        approve_one_pager(
            mock_data_access, document_store, IN_REVIEW_ID, APPROVER, roles=set()
        )
    assert not document_store.exists(IN_REVIEW_ID, "1.0.0")


@pytest.mark.unit
def test__approver_who_is_owner__approve__raises_permission_denied(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """Segregation of duties: nobody approves their own One Pager."""
    # When / Then
    with pytest.raises(PermissionDeniedError, match="Owner or SME"):
        approve_one_pager(
            mock_data_access, document_store, IN_REVIEW_ID, BOB, roles=APPROVER_ROLES
        )
    assert not document_store.exists(IN_REVIEW_ID, "1.0.0")


@pytest.mark.unit
def test__approved_one_pager__approve__raises_invalid_transition(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """Only a One Pager In Review can be approved."""
    # When / Then
    with pytest.raises(InvalidTransitionError):
        approve_one_pager(
            mock_data_access,
            document_store,
            APPROVED_ID,
            APPROVER,
            roles=APPROVER_ROLES,
        )


@pytest.mark.unit
def test__change_log_write_fails__approve__changes_nothing(
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed approval leaves the row and the version files as they were."""
    # Given
    monkeypatch.setattr(
        mock_data_access, "append_change_log_entries", failing("warehouse down")
    )

    # When / Then
    with pytest.raises(TransitionError):
        approve_one_pager(
            mock_data_access,
            document_store,
            IN_REVIEW_ID,
            APPROVER,
            roles=APPROVER_ROLES,
        )
    row = mock_data_access.get_one_pager_status_row(IN_REVIEW_ID)
    assert (row.one_pager_status, row.version) == ("In Review", "0.3.0")
    assert not document_store.exists(IN_REVIEW_ID, "1.0.0")


@pytest.mark.unit
def test__reviewed_document_missing__approve__raises_and_keeps_status(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> None:
    """Approval needs the reviewed version file."""
    # Given
    update_status_row(mock_data_access, IN_REVIEW_ID, version="0.9.0")

    # When / Then
    with pytest.raises(TransitionError):
        approve_one_pager(
            mock_data_access,
            document_store,
            IN_REVIEW_ID,
            APPROVER,
            roles=APPROVER_ROLES,
        )
    row = mock_data_access.get_one_pager_status_row(IN_REVIEW_ID)
    assert row.one_pager_status == "In Review"
