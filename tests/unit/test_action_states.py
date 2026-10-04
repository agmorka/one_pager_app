"""Preview action states derived from the state machine (UI_Design.md §4.4)."""

import pytest

from onepagerapp.permissions import IMPLEMENTED_ACTIONS, ActionState, get_action_states
from onepagerapp.state_machine import Actor

OWNER = "MJO"
APPROVER_ROLE = (Actor.APPROVER,)
REVIEW_ACTIONS = {"approve", "reject", "add_comment"}


def _states(
    op: str,
    dp: str,
    *,
    user: str = OWNER,
    holder: str | None = None,
    roles: tuple[Actor, ...] = (),
) -> dict[str, ActionState]:
    """Return the action states of a One Pager owned by MJO (SME DPR)."""
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


def _visible(states: dict[str, ActionState]) -> set[str]:
    """Return the visible workflow actions (always-present ones left out)."""
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
def test__owner_and_statuses__action_states__actions_of_the_state_machine(
    op: str, dp: str, expected: set[str]
) -> None:
    """The Owner sees the actions the state machine allows."""
    # When
    visible = _visible(_states(op, dp))

    # Then
    assert visible == expected


@pytest.mark.unit
def test__viewer__action_states__only_export() -> None:
    """A Viewer sees no workflow action but can export."""
    # When
    states = _states("Draft", "In Definition", user="XYZ")

    # Then
    assert _visible(states) == set()
    assert states["export_pdf"].visible


@pytest.mark.unit
def test__approver_of_one_pager_in_review__action_states__review_enabled() -> None:
    """An Approver can approve, reject and comment."""
    # When
    states = _states("In Review", "In Definition", user="APP", roles=APPROVER_ROLE)

    # Then
    assert _visible(states) >= REVIEW_ACTIONS
    assert all(states[name].enabled for name in REVIEW_ACTIONS)


@pytest.mark.unit
def test__approver_who_is_owner__action_states__no_approve() -> None:
    """Segregation of duties: the Owner cannot approve their own One Pager."""
    # When
    states = _states("In Review", "In Definition", roles=APPROVER_ROLE)

    # Then
    assert not states["approve"].visible


@pytest.mark.unit
def test__admin__action_states__cancel_any() -> None:
    """An Admin can cancel any One Pager before approval."""
    # When
    states = _states("In Review", "In Definition", user="ADM", roles=(Actor.ADMIN,))

    # Then
    assert _visible(states) == {"cancel"}


@pytest.mark.unit
@pytest.mark.parametrize("name", ["update", "change_dp_status"])
def test__approved_one_pager__action_states_for_owner__implemented_actions_enabled(
    name: str,
) -> None:
    """Implemented actions are enabled (others would say "coming soon")."""
    # When
    state = _states("Approved", "Ready for Development")[name]

    # Then
    assert name in IMPLEMENTED_ACTIONS
    assert state.enabled


@pytest.mark.unit
@pytest.mark.parametrize(
    ("holder", "enabled"), [(None, True), ("DPR", False)], ids=["free", "locked"]
)
def test__draft_lock__action_states__edit_disabled_when_locked_by_other(
    holder: str | None, enabled: bool
) -> None:
    """Edit stays visible but is disabled while someone else holds the lock."""
    # When
    edit = _states("Draft", "In Definition", holder=holder)["edit"]

    # Then
    assert edit.visible
    assert edit.enabled is enabled


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
def test__owner__action_states__resolve_comment_while_reworking(
    op: str, *, enabled: bool
) -> None:
    """The Owner resolves comments only while the One Pager is in Draft."""
    # When
    resolve = _states(op, "In Definition")["resolve_comment"]

    # Then
    assert resolve.visible
    assert resolve.enabled is enabled


@pytest.mark.unit
@pytest.mark.parametrize("op", ["Draft", "Draft Update", "In Review", "Approved"])
def test__viewer__action_states__resolve_comment_hidden(op: str) -> None:
    """Viewers never resolve comments."""
    # When
    resolve = _states(op, "In Definition", user="XYZ")["resolve_comment"]

    # Then
    assert not resolve.visible
    assert not resolve.enabled


@pytest.mark.unit
def test__update_in_review__action_states_for_approver__review_but_no_update() -> None:
    """Rejecting an update is offered; Update itself is never offered in review."""
    # When
    states = _states("In Review", "Active", user="APP", roles=APPROVER_ROLE)

    # Then
    assert _visible(states) >= REVIEW_ACTIONS
    assert states["reject"].enabled
    assert not states["update"].visible
