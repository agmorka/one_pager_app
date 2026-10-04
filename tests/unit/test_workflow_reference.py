"""Serialized state machine for the Help page (Backend §14)."""

import json

import pytest

from onepagerapp.state_machine import TRANSITIONS, VALID_COMBINATIONS
from onepagerapp.workflow import get_workflow_reference


def _op_transition(from_status: str, to_status: str) -> dict:
    """Return the serialized One Pager transition between two statuses."""
    return next(
        t
        for t in get_workflow_reference()["one_pager_transitions"]
        if (t["from_status"], t["to_status"]) == (from_status, to_status)
    )


def _dp_transition(action: str) -> dict:
    """Return the serialized Data Product transition with ``action``."""
    return next(
        t
        for t in get_workflow_reference()["data_product_transitions"]
        if t["action"] == action
    )


@pytest.mark.unit
def test__state_machine__get_workflow_reference__json_with_every_rule() -> None:
    """The reference survives a JSON round trip and lists every rule."""
    # When
    reference = get_workflow_reference()

    # Then
    assert json.loads(json.dumps(reference)) == reference
    op = reference["one_pager_transitions"]
    dp = reference["data_product_transitions"]
    assert len(op) + len(dp) == len(TRANSITIONS)
    assert len(reference["valid_combinations"]) == len(VALID_COMBINATIONS)


@pytest.mark.unit
def test__submit_rule__get_workflow_reference__owner_sme_with_strict_validation() -> (
    None
):
    """Submit is the Owner/SME's and needs strict validation."""
    # When
    submit = _op_transition("Draft", "Ready for Review")

    # Then
    assert submit["who"] == ["Owner/SME"]
    assert "Strict validation passes" in submit["conditions"]


@pytest.mark.unit
def test__reject_rule__get_workflow_reference__approver_with_comment_guards() -> None:
    """Reject is the Approver's, needs a comment and segregation of duties."""
    # When
    reject = _op_transition("In Review", "Draft")

    # Then
    assert reject["who"] == ["Approver"]
    assert "A comment is required" in reject["conditions"]
    assert "Reviewer is not Owner/SME of this One Pager" in reject["conditions"]


@pytest.mark.unit
def test__cancel_rule__get_workflow_reference__owner_sme_or_admin_in_definition() -> (
    None
):
    """Cancel is for the Owner/SME or an Admin while the DP is In Definition."""
    # When
    cancel = _op_transition("Draft", "Cancelled")

    # Then
    assert cancel["who"] == ["Owner/SME", "Admin"]
    assert "Data Product is In Definition" in cancel["conditions"]


@pytest.mark.unit
def test__first_approval_rule__get_workflow_reference__flagged_as_system() -> None:
    """System DP transitions are flagged and done by the System."""
    # When
    first_approval = _dp_transition("system_first_approval")

    # Then
    assert first_approval["system"] is True
    assert first_approval["who"] == ["System"]


@pytest.mark.unit
def test__deprecate_rule__get_workflow_reference__owner_action_on_approved() -> None:
    """Deprecate is an owner action that needs an Approved One Pager."""
    # When
    deprecate = _dp_transition("owner_deprecate")

    # Then
    assert deprecate["system"] is False
    assert "One Pager is Approved" in deprecate["conditions"]


@pytest.mark.unit
def test__valid_combinations__get_workflow_reference__dp_statuses_per_op_status() -> (
    None
):
    """Each One Pager status lists its valid Data Product statuses."""
    # When
    combos = {
        c["one_pager_status"]: c["data_product_statuses"]
        for c in get_workflow_reference()["valid_combinations"]
    }

    # Then
    assert combos["Draft"] == ["In Definition"]
    assert combos["Cancelled"] == ["Cancelled"]
    assert "In Enhancement" in combos["Approved"]
