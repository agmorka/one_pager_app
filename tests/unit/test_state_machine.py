"""State machine (Backend §2-3), valid combinations (Req §6) and the executor."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput, OnePagerStatusRow
from onepagerapp.state_machine import (
    DP_STATUSES,
    OP_STATUSES,
    TRANSITIONS,
    VALID_COMBINATIONS,
    Actor,
    InvalidStatusCombinationError,
    InvalidTransitionError,
    check_combination,
    get_rule,
    guard_failure,
    is_valid_combination,
    transitions_from,
)
from onepagerapp.workflow import (
    TransitionError,
    apply_transitions,
    create_one_pager,
    plan_transitions,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
OP, DP = "one_pager_status", "data_product_status"


@pytest.mark.unit
def test__op_transitions_match_backend_design() -> None:
    op = {
        (r.from_status, r.to_status): r.action
        for r in TRANSITIONS.values()
        if r.status_field == OP
    }
    assert op == {
        ("Draft", "Ready for Review"): "owner_submit",
        ("Draft Update", "Ready for Review"): "owner_submit",
        ("Ready for Review", "In Review"): "owner_submit_for_review",
        ("In Review", "Approved"): "approver_approve",
        ("In Review", "Draft"): "approver_reject",
        ("Approved", "Draft Update"): "owner_update",
        ("Draft", "Cancelled"): "cancel",
        ("Ready for Review", "Cancelled"): "cancel",
        ("In Review", "Cancelled"): "cancel",
    }


@pytest.mark.unit
def test__dp_transitions_match_backend_design() -> None:
    dp = {
        (r.from_status, r.to_status): r.action
        for r in TRANSITIONS.values()
        if r.status_field == DP
    }
    assert dp == {
        ("In Definition", "Ready for Development"): "system_first_approval",
        ("In Definition", "Cancelled"): "system_cancel",
        ("Ready for Development", "In Enhancement"): "system_re_approval",
        ("In Development", "In Enhancement"): "system_re_approval",
        ("Active", "In Enhancement"): "system_re_approval",
        ("Deprecated", "In Enhancement"): "system_re_approval",
        ("Ready for Development", "In Development"): "owner_start_dev",
        ("In Enhancement", "In Development"): "owner_start_dev",
        ("In Development", "Active"): "owner_activate",
        ("Active", "Deprecated"): "owner_deprecate",
    }


@pytest.mark.unit
def test__every_status_is_known() -> None:
    for rule in TRANSITIONS.values():
        statuses = OP_STATUSES if rule.status_field == OP else DP_STATUSES
        assert rule.from_status in statuses
        assert rule.to_status in statuses
    assert set(VALID_COMBINATIONS) == set(OP_STATUSES)


@pytest.mark.unit
def test__terminal_states_have_no_way_out() -> None:
    assert transitions_from(OP, "Cancelled") == []
    assert transitions_from(DP, "Cancelled") == []
    owner_moves = transitions_from(DP, "Deprecated", Actor.OWNER_SME)
    assert owner_moves == []


@pytest.mark.unit
def test__get_rule__unknown_transition() -> None:
    assert get_rule(OP, "Draft", "Ready for Review").requires_strict_validation
    with pytest.raises(InvalidTransitionError, match="Draft to Approved"):
        get_rule(OP, "Draft", "Approved")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "dp", "valid"),
    [
        ("Draft", "In Definition", True),
        ("In Review", "In Definition", True),
        ("Draft", "Active", False),
        ("Approved", "Ready for Development", True),
        ("Approved", "In Definition", False),
        ("Draft Update", "Active", True),
        ("Draft Update", "In Definition", False),
        ("Cancelled", "Cancelled", True),
        ("Cancelled", "In Definition", False),
        ("Approved", "Cancelled", False),
    ],
)
def test__valid_combinations(op: str, dp: str, valid: bool) -> None:
    assert is_valid_combination(op, dp) is valid
    if not valid:
        with pytest.raises(InvalidStatusCombinationError):
            check_combination(op, dp)


def _guard(rule_key: tuple[str, str, str], **kwargs) -> str | None:  # noqa: ANN003
    defaults = {
        "actors": set(),
        "one_pager_status": "Draft",
        "data_product_status": "In Definition",
        "is_owner_or_sme": True,
    }
    return guard_failure(TRANSITIONS[rule_key], **{**defaults, **kwargs})


@pytest.mark.unit
def test__guard_failure__actors_and_statuses() -> None:
    cancel = (OP, "Draft", "Cancelled")
    assert _guard(cancel) is None
    assert _guard(cancel, is_owner_or_sme=False) == (
        "Only the Owner or an SME or an Admin can do this."
    )
    assert _guard(cancel, is_owner_or_sme=False, actors={Actor.ADMIN}) is None
    assert "In Definition" in _guard(cancel, data_product_status="Active")
    assert "status is In Review" in _guard(cancel, one_pager_status="In Review")


@pytest.mark.unit
def test__guard_failure__segregation_of_duties() -> None:
    approve = (OP, "In Review", "Approved")
    kwargs = {"one_pager_status": "In Review", "actors": {Actor.APPROVER}}
    assert _guard(approve, **kwargs, is_owner_or_sme=False) is None
    assert "Owner or SME" in _guard(approve, **kwargs)


@pytest.mark.unit
def test__guard_failure__owner_dp_transitions_need_approved_op() -> None:
    start = (DP, "Ready for Development", "In Development")
    kwargs = {"data_product_status": "Ready for Development"}
    assert _guard(start, one_pager_status="Approved", **kwargs) is None
    assert "Approved" in _guard(start, one_pager_status="Draft Update", **kwargs)


@pytest.mark.unit
def test__guard_failure__system_rules_are_never_user_actions() -> None:
    rule = (DP, "In Definition", "Cancelled")
    assert "automatically" in _guard(rule, actors={Actor.ADMIN})


@pytest.fixture
def draft_row(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> OnePagerStatusRow:
    create_one_pager(valid_input, creator, mock_data_access, document_store, now=NOW)
    return mock_data_access.get_one_pager_status_row("OP-0003")


SUBMIT = [
    TRANSITIONS[(OP, "Draft", "Ready for Review")],
    TRANSITIONS[(OP, "Ready for Review", "In Review")],
]
CANCEL = [
    TRANSITIONS[(OP, "Draft", "Cancelled")],
    TRANSITIONS[(DP, "In Definition", "Cancelled")],
]


@pytest.mark.unit
def test__plan_transitions__checks_order_and_combination(
    draft_row: OnePagerStatusRow,
) -> None:
    assert plan_transitions(draft_row, SUBMIT).one_pager_status == "In Review"
    with pytest.raises(InvalidTransitionError):
        plan_transitions(draft_row, list(reversed(SUBMIT)))
    with pytest.raises(InvalidStatusCombinationError):
        plan_transitions(draft_row, CANCEL[:1])  # OP Cancelled with DP In Definition


@pytest.mark.unit
def test__apply_transitions__one_update_and_one_entry_per_rule(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
) -> None:
    later = NOW.replace(hour=11)
    row = apply_transitions(mock_data_access, draft_row, SUBMIT, creator, now=later)

    assert row.one_pager_status == "In Review"
    assert row.version == "0.1.0"  # status changes keep the version
    assert row.last_updated_at == later
    stored = mock_data_access.get_one_pager_status_row("OP-0003")
    assert stored.one_pager_status == "In Review"
    log = mock_data_access.get_change_log("OP-0003")
    # Newest first; same timestamp, so the later entry (higher id) first.
    assert [(e.from_status, e.to_status) for e in log[:2]] == [
        ("Ready for Review", "In Review"),
        ("Draft", "Ready for Review"),
    ]
    assert {e.event_type for e in log[:2]} == {"status_transition"}


@pytest.mark.unit
def test__apply_transitions__note_goes_into_summary(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
) -> None:
    apply_transitions(
        mock_data_access, draft_row, CANCEL, creator, now=NOW, note="Obsolete"
    )
    entries = mock_data_access.get_change_log("OP-0003")
    assert any(e.summary == "One Pager cancelled: Obsolete" for e in entries)
    assert any(e.event_type == "cancellation" for e in entries)


@pytest.mark.unit
def test__apply_transitions__stale_row_is_a_conflict(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
) -> None:
    stale = replace(draft_row, version="0.0.9")
    with pytest.raises(TransitionError, match="changed by someone else"):
        apply_transitions(mock_data_access, stale, SUBMIT, creator, now=NOW)
    assert len(mock_data_access.get_change_log("OP-0003")) == 1


class _ChangeLogFails(MockDataAccess):
    def append_change_log_entries(self, entries) -> None:  # noqa: ANN001, ARG002
        msg = "boom"
        raise RuntimeError(msg)


@pytest.mark.unit
def test__apply_transitions__change_log_failure_rolls_back(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    document_store: OnePagerDocumentStore,
) -> None:
    data_access = _ChangeLogFails(document_store)
    create_one_pager(valid_input, creator, data_access, document_store, now=NOW)
    row = data_access.get_one_pager_status_row("OP-0003")

    with pytest.raises(TransitionError, match="Please retry"):
        apply_transitions(data_access, row, SUBMIT, creator, now=NOW)

    assert data_access.get_one_pager_status_row("OP-0003").one_pager_status == "Draft"
