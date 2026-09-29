"""Preview action states derived from the state machine (UI_Design.md §4.4)."""

import pytest

from onepagerapp.permissions import IMPLEMENTED_ACTIONS, get_action_states
from onepagerapp.state_machine import Actor

OWNER = "MJO"


def _states(
    op: str,
    dp: str,
    *,
    user: str = OWNER,
    holder: str | None = None,
    roles: tuple[Actor, ...] = (),
) -> dict:
    return get_action_states(
        user,
        OWNER,
        op,
        holder is not None,
        holder,
        authorized_initials={OWNER, "DPR"},
        data_product_status=dp,
        roles=roles,
    )


def _visible(states: dict) -> set[str]:
    return {name for name, s in states.items() if s.visible} - {
        "release_lock",
        "resolve_comment",
        "export_pdf",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "dp", "expected"),
    [
        ("Draft", "In Definition", {"edit", "cancel"}),
        ("Draft Update", "Active", {"edit"}),  # no Cancel past In Definition
        ("In Review", "In Definition", {"cancel"}),
        ("Approved", "Ready for Development", {"update", "change_dp_status"}),
        ("Approved", "Deprecated", {"update"}),  # Deprecated is terminal
        ("Cancelled", "Cancelled", set()),
    ],
)
def test__owner_actions_by_status(op: str, dp: str, expected: set[str]) -> None:
    assert _visible(_states(op, dp)) == expected


@pytest.mark.unit
def test__viewer_sees_no_workflow_actions() -> None:
    assert _visible(_states("Draft", "In Definition", user="XYZ")) == set()
    assert _states("Draft", "In Definition", user="XYZ")["export_pdf"].visible


@pytest.mark.unit
def test__approver_actions_respect_segregation_of_duties() -> None:
    approver = _states(
        "In Review", "In Definition", user="APP", roles=(Actor.APPROVER,)
    )
    assert {"approve", "reject", "add_comment"} <= _visible(approver)

    self_review = _states("In Review", "In Definition", roles=(Actor.APPROVER,))
    assert not self_review["approve"].visible


@pytest.mark.unit
def test__admin_can_cancel_any() -> None:
    admin = _states("In Review", "In Definition", user="ADM", roles=(Actor.ADMIN,))
    assert _visible(admin) == {"cancel"}


@pytest.mark.unit
def test__not_implemented_actions_stay_disabled() -> None:
    states = _states("Approved", "Ready for Development")
    for name in ("update", "change_dp_status"):
        if name not in IMPLEMENTED_ACTIONS:
            assert not states[name].enabled
            assert "coming" in states[name].tooltip or "arrives" in states[name].tooltip


@pytest.mark.unit
def test__edit_enabled_unless_locked_by_other() -> None:
    assert _states("Draft", "In Definition")["edit"].enabled
    locked = _states("Draft", "In Definition", holder="DPR")
    assert not locked["edit"].enabled
    assert locked["edit"].visible


@pytest.mark.unit
def test__review_actions_are_enabled_for_approvers() -> None:
    approver = _states(
        "In Review", "In Definition", user="APP", roles=(Actor.APPROVER,)
    )
    for name in ("approve", "reject", "add_comment"):
        assert approver[name].visible
        assert approver[name].enabled, name


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "enabled"),
    [
        ("Draft", True),
        ("Draft Update", True),
        ("In Review", False),
        ("Approved", False),
    ],
)
def test__resolve_comment_for_owner_while_reworking(op: str, *, enabled: bool) -> None:
    owner = _states(op, "In Definition")["resolve_comment"]
    assert owner.visible
    assert owner.enabled is enabled

    viewer = _states(op, "In Definition", user="XYZ")["resolve_comment"]
    assert not viewer.visible
    assert not viewer.enabled


@pytest.mark.unit
def test__reject_of_an_update_is_offered_to_approvers() -> None:
    approver = _states("In Review", "Active", user="APP", roles=(Actor.APPROVER,))
    assert {"approve", "reject", "add_comment"} <= _visible(approver)
    assert approver["reject"].enabled
    # Update is the Owner's action on Approved, never offered in review.
    assert not approver["update"].visible
