"""Owner-initiated Data Product transitions (Backend §3, Req §6)."""

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import (
    ConfirmationRequiredError,
    change_data_product_status,
    data_product_options,
)
from tests.helpers import ALICE, APPROVED_ID, BOB, NOW, update_status_row


def _targets(data_access: MockDataAccess, *, owner_or_sme: bool) -> list[str]:
    """Return the DP statuses offered for OP-0001."""
    row = data_access.get_one_pager_status_row(APPROVED_ID)
    return [r.to_status for r in data_product_options(row, owner_or_sme=owner_or_sme)]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dp", "targets"),
    [
        ("Ready for Development", ["In Development"]),
        ("In Enhancement", ["In Development"]),
        ("In Development", ["Active"]),
        ("Active", ["Deprecated"]),
        ("Deprecated", []),
    ],
)
def test__approved_one_pager__options_for_owner__follow_state_machine(
    mock_data_access: MockDataAccess, dp: str, targets: list[str]
) -> None:
    """The Owner is offered the next owner-initiated DP status."""
    # Given
    update_status_row(
        mock_data_access,
        APPROVED_ID,
        one_pager_status="Approved",
        data_product_status=dp,
    )

    # When
    offered = _targets(mock_data_access, owner_or_sme=True)

    # Then
    assert offered == targets


@pytest.mark.unit
@pytest.mark.parametrize(
    "dp",
    [
        "Ready for Development",
        "In Enhancement",
        "In Development",
        "Active",
        "Deprecated",
    ],
)
def test__approved_one_pager__options_for_viewer__none(
    mock_data_access: MockDataAccess, dp: str
) -> None:
    """Somebody who is not Owner or SME is offered nothing."""
    # Given
    update_status_row(mock_data_access, APPROVED_ID, data_product_status=dp)

    # When
    offered = _targets(mock_data_access, owner_or_sme=False)

    # Then
    assert offered == []


@pytest.mark.unit
def test__one_pager_in_draft_update__options_for_owner__none(
    mock_data_access: MockDataAccess,
) -> None:
    """No DP transition is offered while an update is being edited."""
    # Given
    update_status_row(
        mock_data_access,
        APPROVED_ID,
        one_pager_status="Draft Update",
        data_product_status="In Development",
    )

    # When
    offered = _targets(mock_data_access, owner_or_sme=True)

    # Then
    assert offered == []


@pytest.mark.unit
def test__ready_for_development__start_and_activate__active(
    mock_data_access: MockDataAccess,
) -> None:
    """The Owner moves the DP through development to Active."""
    # When
    change_data_product_status(
        mock_data_access, APPROVED_ID, "In Development", ALICE, now=NOW
    )
    row = change_data_product_status(
        mock_data_access, APPROVED_ID, "Active", ALICE, now=NOW
    )

    # Then
    assert (row.one_pager_status, row.data_product_status) == ("Approved", "Active")


@pytest.mark.unit
def test__active_data_product__unconfirmed_deprecate__raises_confirmation(
    mock_data_access: MockDataAccess,
) -> None:
    """Deprecating needs an explicit confirmation."""
    # Given
    update_status_row(mock_data_access, APPROVED_ID, data_product_status="Active")

    # When / Then
    with pytest.raises(ConfirmationRequiredError):
        change_data_product_status(
            mock_data_access, APPROVED_ID, "Deprecated", ALICE, now=NOW
        )


@pytest.mark.unit
def test__active_data_product__confirmed_deprecate__deprecated_and_logged(
    mock_data_access: MockDataAccess,
) -> None:
    """A confirmed deprecation keeps the version and logs the DP change."""
    # Given
    update_status_row(mock_data_access, APPROVED_ID, data_product_status="Active")

    # When
    row = change_data_product_status(
        mock_data_access, APPROVED_ID, "Deprecated", ALICE, confirmed=True, now=NOW
    )

    # Then
    assert (row.one_pager_status, row.data_product_status) == ("Approved", "Deprecated")
    assert row.version == "1.0.0"
    latest = mock_data_access.get_change_log(APPROVED_ID)[0]
    assert (latest.status_field, latest.from_status, latest.to_status) == (
        "data_product_status",
        "Active",
        "Deprecated",
    )
    assert latest.summary == "Data Product deprecated"


@pytest.mark.unit
def test__user_not_owner_or_sme__change_dp_status__raises_permission_denied(
    mock_data_access: MockDataAccess,
) -> None:
    """Only the Owner or an SME changes the DP status."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        change_data_product_status(mock_data_access, APPROVED_ID, "In Development", BOB)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "dp", "target"),
    [
        ("Approved", "Ready for Development", "Active"),  # skips a step
        ("Approved", "Active", "In Enhancement"),  # system transition
        ("Draft Update", "Ready for Development", "In Development"),  # OP not Approved
        ("Approved", "Deprecated", "Active"),  # terminal
    ],
)
def test__transition_not_in_state_machine__change_dp_status__raises_invalid(
    mock_data_access: MockDataAccess, op: str, dp: str, target: str
) -> None:
    """Steps the state machine does not allow the Owner are refused."""
    # Given
    update_status_row(
        mock_data_access, APPROVED_ID, one_pager_status=op, data_product_status=dp
    )

    # When / Then
    with pytest.raises(InvalidTransitionError):
        change_data_product_status(
            mock_data_access, APPROVED_ID, target, ALICE, confirmed=True
        )
    row = mock_data_access.get_one_pager_status_row(APPROVED_ID)
    assert row.data_product_status == dp
