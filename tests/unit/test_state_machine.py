"""State machine (Backend §2-3), valid combinations (Req §6) and the executor."""

from dataclasses import replace

import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import CurrentUser, OnePagerStatusRow
from onepagerapp.state_machine import (
    DP_STATUSES,
    OP_STATUSES,
    TRANSITIONS,
    VALID_COMBINATIONS,
    Actor,
    InvalidStatusCombinationError,
    InvalidTransitionError,
    TransitionRule,
    check_combination,
    get_rule,
    guard_failure,
    is_valid_combination,
    reject_target,
    transitions_from,
)
from onepagerapp.workflow import TransitionError, apply_transitions, plan_transitions
from tests.helpers import NOW, failing

OP, DP = "one_pager_status", "data_product_status"
SUBMIT = [
    TRANSITIONS[(OP, "Draft", "Ready for Review")],
    TRANSITIONS[(OP, "Ready for Review", "In Review")],
]
CANCEL = [
    TRANSITIONS[(OP, "Draft", "Cancelled")],
    TRANSITIONS[(DP, "In Definition", "Cancelled")],
]


def _actions(status_field: str) -> dict[tuple[str, str], str]:
    """Return (from, to) → action of every transition of ``status_field``."""
    return {
        (r.from_status, r.to_status): r.action
        for r in TRANSITIONS.values()
        if r.status_field == status_field
    }


def _guard(rule_key: tuple[str, str, str], **kwargs: object) -> str | None:
    """Return the guard failure of a Draft / In Definition Owner by default."""
    defaults = {
        "actors": set(),
        "one_pager_status": "Draft",
        "data_product_status": "In Definition",
        "is_owner_or_sme": True,
    }
    return guard_failure(TRANSITIONS[rule_key], **{**defaults, **kwargs})


@pytest.fixture
def draft_row(draft_id: str, mock_data_access: MockDataAccess) -> OnePagerStatusRow:
    """Return the status row of the new Draft OP-0003."""
    return mock_data_access.get_one_pager_status_row(draft_id)


@pytest.mark.unit
def test__transition_table__one_pager_rules__match_backend_design() -> None:
    """The One Pager transitions are exactly those of the design."""
    # When
    op = _actions(OP)

    # Then
    assert op == {
        ("Draft", "Ready for Review"): "owner_submit",
        ("Draft Update", "Ready for Review"): "owner_submit",
        ("Ready for Review", "In Review"): "owner_submit_for_review",
        ("In Review", "Approved"): "approver_approve",
        ("In Review", "Draft"): "approver_reject",
        ("In Review", "Draft Update"): "approver_reject",  # Decision_Log §16
        ("Approved", "Draft Update"): "owner_update",
        ("Draft", "Cancelled"): "cancel",
        ("Ready for Review", "Cancelled"): "cancel",
        ("In Review", "Cancelled"): "cancel",
    }


@pytest.mark.unit
def test__transition_table__data_product_rules__match_backend_design() -> None:
    """The Data Product transitions are exactly those of the design."""
    # When
    dp = _actions(DP)

    # Then
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
@pytest.mark.parametrize("rule", TRANSITIONS.values(), ids=str)
def test__transition_rule__statuses__are_known(rule: TransitionRule) -> None:
    """Every rule moves between statuses of its own field."""
    # Given
    statuses = OP_STATUSES if rule.status_field == OP else DP_STATUSES

    # When / Then
    assert rule.from_status in statuses
    assert rule.to_status in statuses


@pytest.mark.unit
def test__valid_combinations__keys__every_one_pager_status() -> None:
    """Each One Pager status has its valid Data Product statuses."""
    # When
    keys = set(VALID_COMBINATIONS)

    # Then
    assert keys == set(OP_STATUSES)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "status", "actor"),
    [
        (OP, "Cancelled", None),
        (DP, "Cancelled", None),
        (DP, "Deprecated", Actor.OWNER_SME),
    ],
)
def test__terminal_status__transitions_from__none(
    field: str, status: str, actor: Actor | None
) -> None:
    """Cancelled, and Deprecated for the Owner, have no way out."""
    # When
    moves = transitions_from(field, status, actor)

    # Then
    assert moves == []


@pytest.mark.unit
def test__submit_transition__get_rule__requires_strict_validation() -> None:
    """Submitting requires strict validation."""
    # When
    rule = get_rule(OP, "Draft", "Ready for Review")

    # Then
    assert rule.requires_strict_validation


@pytest.mark.unit
def test__unknown_transition__get_rule__raises_invalid_transition() -> None:
    """A transition that is not in the table is refused, naming both statuses."""
    # When / Then
    with pytest.raises(InvalidTransitionError, match="Draft to Approved"):
        get_rule(OP, "Draft", "Approved")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "dp", "valid"),
    [
        ("Draft", "In Definition", True),
        ("In Review", "In Definition", True),
        ("In Review", "Active", True),  # review of an update (Decision_Log §16)
        ("Ready for Review", "In Development", True),
        ("In Review", "Cancelled", False),
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
def test__status_pair__is_valid_combination__matches_requirements(
    op: str, dp: str, valid: bool
) -> None:
    """Only the documented One Pager / Data Product pairs are valid."""
    # When
    result = is_valid_combination(op, dp)

    # Then
    assert result is valid


@pytest.mark.unit
@pytest.mark.parametrize(("op", "dp"), [("Draft", "Active"), ("Approved", "Cancelled")])
def test__invalid_status_pair__check_combination__raises(op: str, dp: str) -> None:
    """An invalid pair raises."""
    # When / Then
    with pytest.raises(InvalidStatusCombinationError):
        check_combination(op, dp)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, None),
        (
            {"is_owner_or_sme": False},
            "Only the Owner or an SME or an Admin can do this.",
        ),
        ({"is_owner_or_sme": False, "actors": {Actor.ADMIN}}, None),
    ],
    ids=["owner", "stranger", "admin"],
)
def test__cancel_rule__guard_failure__owner_sme_or_admin(
    kwargs: dict, expected: str | None
) -> None:
    """Cancel is for the Owner/SME or an Admin."""
    # When
    failure = _guard((OP, "Draft", "Cancelled"), **kwargs)

    # Then
    assert failure == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "text"),
    [
        ({"data_product_status": "Active"}, "In Definition"),
        ({"one_pager_status": "In Review"}, "status is In Review"),
    ],
)
def test__cancel_rule_wrong_status__guard_failure__names_the_status(
    kwargs: dict, text: str
) -> None:
    """A status that does not match the rule is named in the failure."""
    # When
    failure = _guard((OP, "Draft", "Cancelled"), **kwargs)

    # Then
    assert text in failure


@pytest.mark.unit
@pytest.mark.parametrize(("owner_or_sme", "allowed"), [(False, True), (True, False)])
def test__approve_rule__guard_failure__segregation_of_duties(
    owner_or_sme: bool, allowed: bool
) -> None:
    """An Approver cannot approve a One Pager they are Owner or SME of."""
    # When
    failure = _guard(
        (OP, "In Review", "Approved"),
        one_pager_status="In Review",
        actors={Actor.APPROVER},
        is_owner_or_sme=owner_or_sme,
    )

    # Then
    assert (failure is None) is allowed
    if not allowed:
        assert "Owner or SME" in failure


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op_status", "allowed"), [("Approved", True), ("Draft Update", False)]
)
def test__owner_dp_rule__guard_failure__needs_approved_one_pager(
    op_status: str, allowed: bool
) -> None:
    """Owner DP transitions need the One Pager to be Approved."""
    # When
    failure = _guard(
        (DP, "Ready for Development", "In Development"),
        one_pager_status=op_status,
        data_product_status="Ready for Development",
    )

    # Then
    assert (failure is None) is allowed
    if not allowed:
        assert "Approved" in failure


@pytest.mark.unit
def test__system_rule__guard_failure_for_admin__automatic_only() -> None:
    """System rules are never user actions, not even for Admins."""
    # When
    failure = _guard((DP, "In Definition", "Cancelled"), actors={Actor.ADMIN})

    # Then
    assert "automatically" in failure


@pytest.mark.unit
def test__draft__plan_submit__in_review(draft_row: OnePagerStatusRow) -> None:
    """The two submit rules in order lead to In Review."""
    # When
    planned = plan_transitions(draft_row, SUBMIT)

    # Then
    assert planned.one_pager_status == "In Review"


@pytest.mark.unit
def test__draft__plan_rules_out_of_order__raises_invalid_transition(
    draft_row: OnePagerStatusRow,
) -> None:
    """Each rule must start from the status the previous one left."""
    # When / Then
    with pytest.raises(InvalidTransitionError):
        plan_transitions(draft_row, list(reversed(SUBMIT)))


@pytest.mark.unit
def test__draft__plan_op_cancel_without_dp__raises_invalid_combination(
    draft_row: OnePagerStatusRow,
) -> None:
    """OP Cancelled with DP In Definition is not a valid result."""
    # When / Then
    with pytest.raises(InvalidStatusCombinationError):
        plan_transitions(draft_row, CANCEL[:1])


@pytest.mark.unit
def test__draft__apply_submit__one_update_keeping_the_version(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
) -> None:
    """The row is updated once; a status change keeps the version."""
    # Given
    later = NOW.replace(hour=11)

    # When
    row = apply_transitions(mock_data_access, draft_row, SUBMIT, creator, now=later)

    # Then
    assert (row.one_pager_status, row.version, row.last_updated_at) == (
        "In Review",
        "0.1.0",
        later,
    )
    stored = mock_data_access.get_one_pager_status_row("OP-0003")
    assert stored.one_pager_status == "In Review"


@pytest.mark.unit
def test__draft__apply_submit__one_entry_per_rule_newest_first(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
) -> None:
    """Same timestamp, so the later entry (higher id) comes first."""
    # When
    apply_transitions(
        mock_data_access, draft_row, SUBMIT, creator, now=NOW.replace(hour=11)
    )

    # Then
    log = mock_data_access.get_change_log("OP-0003")[:2]
    assert [(e.from_status, e.to_status) for e in log] == [
        ("Ready for Review", "In Review"),
        ("Draft", "Ready for Review"),
    ]
    assert {e.event_type for e in log} == {"status_transition"}


@pytest.mark.unit
def test__note__apply_cancel__note_in_summary(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
) -> None:
    """A note is appended to the summary; cancelling is a cancellation event."""
    # When
    apply_transitions(
        mock_data_access, draft_row, CANCEL, creator, now=NOW, note="Obsolete"
    )

    # Then
    entries = mock_data_access.get_change_log("OP-0003")
    assert any(e.summary == "One Pager cancelled: Obsolete" for e in entries)
    assert any(e.event_type == "cancellation" for e in entries)


@pytest.mark.unit
def test__stale_row__apply_transitions__raises_conflict(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
) -> None:
    """A row that changed since it was read is a conflict; nothing is logged."""
    # Given
    stale = replace(draft_row, version="0.0.9")

    # When / Then
    with pytest.raises(TransitionError, match="changed by someone else"):
        apply_transitions(mock_data_access, stale, SUBMIT, creator, now=NOW)
    assert len(mock_data_access.get_change_log("OP-0003")) == 1


@pytest.mark.unit
def test__change_log_write_fails__apply_transitions__rolls_back(
    draft_row: OnePagerStatusRow,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed change log write restores the status."""
    # Given
    monkeypatch.setattr(mock_data_access, "append_change_log_entries", failing())

    # When / Then
    with pytest.raises(TransitionError, match="Please retry"):
        apply_transitions(mock_data_access, draft_row, SUBMIT, creator, now=NOW)
    row = mock_data_access.get_one_pager_status_row("OP-0003")
    assert row.one_pager_status == "Draft"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("dp_status", "target"),
    [
        ("In Definition", "Draft"),
        ("Ready for Development", "Draft Update"),
        ("In Development", "Draft Update"),
        ("Active", "Draft Update"),
        ("In Enhancement", "Draft Update"),
    ],
)
def test__dp_status__reject_target__update_cycles_return_to_draft_update(
    dp_status: str, target: str
) -> None:
    """A rejected first review returns to Draft, a rejected update to Draft Update."""
    # When
    result = reject_target(dp_status)

    # Then
    assert result == target
