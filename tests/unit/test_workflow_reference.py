"""Serialized state machine for the Help page (Backend §14)."""

import json

import pytest

from onepagerapp.state_machine import TRANSITIONS, VALID_COMBINATIONS
from onepagerapp.workflow import get_workflow_reference


@pytest.mark.unit
def test__reference__is_json_and_complete() -> None:
    reference = get_workflow_reference()

    assert json.loads(json.dumps(reference)) == reference
    op = reference["one_pager_transitions"]
    dp = reference["data_product_transitions"]
    assert len(op) + len(dp) == len(TRANSITIONS)
    assert len(reference["valid_combinations"]) == len(VALID_COMBINATIONS)


@pytest.mark.unit
def test__reference__describes_guards() -> None:
    reference = get_workflow_reference()
    by_key = {
        (t["from_status"], t["to_status"]): t
        for t in reference["one_pager_transitions"]
    }

    submit = by_key[("Draft", "Ready for Review")]
    assert submit["who"] == ["Owner/SME"]
    assert "Strict validation passes" in submit["conditions"]

    reject = by_key[("In Review", "Draft")]
    assert reject["who"] == ["Approver"]
    assert "A comment is required" in reject["conditions"]
    assert "Reviewer is not Owner/SME of this One Pager" in reject["conditions"]

    cancel = by_key[("Draft", "Cancelled")]
    assert cancel["who"] == ["Owner/SME", "Admin"]
    assert "Data Product is In Definition" in cancel["conditions"]


@pytest.mark.unit
def test__reference__system_dp_transitions_flagged() -> None:
    dp = get_workflow_reference()["data_product_transitions"]
    first_approval = next(t for t in dp if t["action"] == "system_first_approval")
    assert first_approval["system"] is True
    assert first_approval["who"] == ["System"]
    deprecate = next(t for t in dp if t["action"] == "owner_deprecate")
    assert deprecate["system"] is False
    assert "One Pager is Approved" in deprecate["conditions"]


@pytest.mark.unit
def test__reference__valid_combinations() -> None:
    combos = {
        c["one_pager_status"]: c["data_product_statuses"]
        for c in get_workflow_reference()["valid_combinations"]
    }
    assert combos["Draft"] == ["In Definition"]
    assert combos["Cancelled"] == ["Cancelled"]
    assert "In Enhancement" in combos["Approved"]
