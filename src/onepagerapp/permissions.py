"""Permission and workflow authorization checks.

Per-record edit access (``check_can_edit``) is checked against the
``one_pager_authorized_users`` rows of a One Pager (Backend_Design.md §5).
Coarse roles from Unity Catalog groups are not resolved yet (Phase 2), so
group checks still allow every authenticated user.

Per Backend_Design.md §5, all authenticated users can view any One Pager.
Permission enforcement happens on state changes (approve, edit, etc.) in the service layer.
"""

from collections.abc import Collection
from dataclasses import dataclass

from onepagerapp.audit import log_permission_denied
from onepagerapp.auth import initials_from_username
from onepagerapp.models import AuthorizedUser, CurrentUser, LockInfo
from onepagerapp.state_machine import (
    APPROVED,
    DP_STATUS_FIELD,
    DRAFT,
    DRAFT_UPDATE,
    OP_CANCELLED,
    OP_STATUS_FIELD,
    TRANSITIONS,
    Actor,
    TransitionRule,
    guard_failure,
    transitions_from,
)

# One Pager statuses whose content may be edited (Requirements_and_Scope.md §5).
EDITABLE_STATUSES = ("Draft", "Draft Update")

# one_pager_authorized_users roles that grant edit access.
AUTHORIZED_ROLES = ("owner", "sme")


class PermissionDeniedError(Exception):
    """Raised by the service layer when a user may not perform an action."""


@dataclass
class ActionState:
    """Button state for a single action.
    
    Attributes:
        enabled: Whether the button should be clickable.
        tooltip: Hover text (reason if disabled, blank if enabled).
        visible: Whether the action applies to this user and status at all;
            the Preview page does not show actions that do not apply.
    """

    enabled: bool
    tooltip: str = ""
    visible: bool = True


def can_view_one_pager(current_user: str | None) -> bool:
    """Check if the current user can view a One Pager.
    
    Per Backend_Design.md §5, all authenticated users can view.
    
    Args:
        current_user: Current user identifier or None if not authenticated.
        
    Returns:
        True if user is authenticated, False otherwise.
    """
    return bool(current_user)


def can_create_one_pager(user: CurrentUser | None) -> bool:
    """Check if the user may create a new One Pager.

    Backend_Design.md §5: creating requires membership of the Owner/SME UC
    group. Group names are not decided yet (Architecture.md §4), so v1 allows
    any authenticated user (New_One_Pager_Plan D7). The Registry uses this to
    show [+ New] and the service layer calls it again to enforce, so adding the
    group check later only changes this function.
    """
    return bool(user and user.username and user.initials)


def is_owner_or_sme(
    user: CurrentUser | None, authorized_users: list[AuthorizedUser]
) -> bool:
    """Whether the user is listed as Owner or SME of a One Pager (Backend §5).

    ``authorized_users`` are the ``one_pager_authorized_users`` rows of that
    One Pager, the authoritative list of who may edit it.
    """
    if not (user and user.initials):
        return False
    return any(
        u.user_initials == user.initials and u.role in AUTHORIZED_ROLES
        for u in authorized_users
    )


def edit_denied_reason(
    user: CurrentUser | None,
    one_pager_status: str,
    authorized_users: list[AuthorizedUser],
) -> str | None:
    """Why the user may not edit a One Pager's content, or None if they may.

    Requirements_and_Scope.md §5: only the Owner and the SMEs of this One
    Pager may edit it, and only while it is ``Draft`` or ``Draft Update``.
    """
    if not is_owner_or_sme(user, authorized_users):
        return "Only the Owner or an SME of this One Pager can edit it."
    if one_pager_status not in EDITABLE_STATUSES:
        return f"A One Pager in status {one_pager_status} cannot be edited."
    return None


def check_can_edit(
    user: CurrentUser | None,
    one_pager_id: str,
    one_pager_status: str,
    authorized_users: list[AuthorizedUser],
) -> None:
    """Enforce ``edit_denied_reason`` in the service layer (logged when denied).

    Raises:
        PermissionDeniedError: With a user-facing message.

    """
    reason = edit_denied_reason(user, one_pager_status, authorized_users)
    if reason is None:
        return
    log_permission_denied(
        "edit_one_pager",
        user=user.initials if user else None,
        one_pager_id=one_pager_id,
    )
    raise PermissionDeniedError(reason)


def can_review(roles: Collection[Actor]) -> bool:
    """Whether the user may use the Review queue and review One Pagers.

    Approvers only (UI_Design.md §2, "Page visibility by role"). Segregation of
    duties is checked per One Pager when approving or rejecting.
    """
    return Actor.APPROVER in roles


def can_release_lock(user: CurrentUser | None, lock: LockInfo | None) -> bool:
    """Check if the user may release a lock: only its holder may (Backend §6)."""
    return bool(user and lock and lock.locked_by_initials == user.initials)


def extract_initials(user: str | None) -> str:
    """Derive a user's corporate initials from their Databricks identity.

    Kept for the Use Cases page; the single implementation lives in
    ``auth.initials_from_username`` so every feature derives the same initials.
    """
    return initials_from_username(user)


def can_manage_use_cases(current_user: str | None) -> bool:
    """Check if the current user can create, edit, deprecate or restore Use Cases.

    Per Backend_Design.md §5/§9 only Owner/SME group members may manage the
    shared Use Case registry; everyone else has read-only access.

    v1 stub: group resolution (auth.py) does not exist yet, so every
    authenticated user is allowed (see Decision_Log.md). All write actions on
    the Use Cases page go through this function, so switching to the real
    group check is a change here only.

    Args:
        current_user: Current user identifier or None if not authenticated.

    Returns:
        True if the user may manage Use Cases.
    """
    return bool(current_user)


# ============================================================================
# Preview Action Button States
# ============================================================================
# Which actions a user sees and can click on the Preview page (UI_Design.md
# §4.4). Workflow actions are derived from state_machine.TRANSITIONS, so the
# buttons follow the same rules the service layer enforces. Hiding or
# disabling a button is never the only check: every action re-checks in the
# service layer (Backend_Design.md §5).
# ============================================================================

# Actions whose service exists; the others are shown disabled ("coming soon")
# when they would apply.
IMPLEMENTED_ACTIONS: frozenset[str] = frozenset(
    {"edit", "release_lock", "cancel", "change_dp_status"}
)

COMING_SOON = {
    "update": "Update arrives with the review workflow",
    "approve": "Approve arrives with the review workflow",
    "reject": "Reject arrives with the review workflow",
    "cancel": "Cancel coming soon",
    "change_dp_status": "Change DP Status coming soon",
    "add_comment": "Review comments arrive with the review workflow",
    "resolve_comment": "Review comments arrive with the review workflow",
    "export_pdf": "Export PDF coming soon",
}


def get_action_states(  # noqa: PLR0913 - the context of one Preview page
    current_user_initials: str,
    owner_initials: str,  # noqa: ARG001 - kept for callers; Owner/SMEs come from authorized_initials
    one_pager_status: str,
    is_locked: bool,  # noqa: FBT001
    lock_holder_initials: str | None,
    *,
    authorized_initials: Collection[str] = (),
    data_product_status: str = "",
    roles: Collection[Actor] = (),
) -> dict[str, ActionState]:
    """Compute the state of every Preview action for this user and One Pager.

    An action is *visible* when the user's role and the statuses make it
    applicable (UI_Design.md §4.4, "Actions (conditional)") and *enabled*
    when it can be performed now. Actions whose service does not exist yet
    stay disabled with a "coming soon" tooltip.

    Args:
        current_user_initials: Initials of the logged-in user.
        owner_initials: Initials of the One Pager owner.
        one_pager_status: Current document status (Draft, In Review, ...).
        is_locked: Whether the One Pager is currently locked.
        lock_holder_initials: Initials of lock holder if locked, None otherwise.
        authorized_initials: Initials of the Owner/SMEs of this One Pager
            (``one_pager_authorized_users``).
        data_product_status: Current Data Product status.
        roles: Group roles of the user (Approver, Admin). Unity Catalog group
            resolution is not implemented yet (Phase 2), so callers pass none.

    Returns:
        Dictionary mapping action name (e.g. "edit", "approve") to ActionState.

    """
    holder = lock_holder_initials if is_locked else None
    context = _ActionContext(
        user=current_user_initials,
        op_status=one_pager_status,
        dp_status=data_product_status,
        owner_or_sme=current_user_initials in authorized_initials,
        roles=set(roles),
        locked_by_other=bool(holder and holder != current_user_initials),
    )
    states = {
        "edit": _edit_state(
            current_user_initials, one_pager_status, authorized_initials, holder
        ),
        "update": context.rule_state(OP_STATUS_FIELD, DRAFT_UPDATE),
        "approve": context.rule_state(OP_STATUS_FIELD, APPROVED),
        "reject": context.rule_state(OP_STATUS_FIELD, DRAFT),
        "cancel": context.rule_state(OP_STATUS_FIELD, OP_CANCELLED),
        "change_dp_status": context.dp_change_state(),
        "add_comment": context.rule_state(OP_STATUS_FIELD, APPROVED),
        "resolve_comment": ActionState(
            enabled=False,
            tooltip=COMING_SOON["resolve_comment"],
            visible=context.owner_or_sme,
        ),
        "release_lock": _release_lock_state(
            current_user_initials, is_locked, lock_holder_initials
        ),
        "export_pdf": ActionState(enabled=False, tooltip=COMING_SOON["export_pdf"]),
    }
    edit = states["edit"]
    edit.visible = context.owner_or_sme and one_pager_status in EDITABLE_STATUSES
    for name, state in states.items():
        if state.enabled and name not in IMPLEMENTED_ACTIONS:
            state.enabled = False
            state.tooltip = COMING_SOON.get(name, "Coming soon")
    return states


@dataclass
class _ActionContext:
    user: str
    op_status: str
    dp_status: str
    owner_or_sme: bool
    roles: set[Actor]
    locked_by_other: bool

    def _failure(self, rule: TransitionRule) -> str | None:
        return guard_failure(
            rule,
            actors=self.roles,
            one_pager_status=self.op_status,
            data_product_status=self.dp_status,
            is_owner_or_sme=self.owner_or_sme,
        )

    def rule_state(self, status_field: str, to_status: str) -> ActionState:
        """State of the user action that moves ``status_field`` to ``to_status``."""
        current = self.op_status if status_field == OP_STATUS_FIELD else self.dp_status
        rule = TRANSITIONS.get((status_field, current, to_status))
        if rule is None or rule.is_system:
            return ActionState(enabled=False, visible=False)
        reason = self._failure(rule)
        if reason:
            return ActionState(enabled=False, tooltip=reason, visible=False)
        if self.locked_by_other:
            return ActionState(
                enabled=False, tooltip="Another user is editing this One Pager"
            )
        return ActionState(enabled=True)

    def dp_change_state(self) -> ActionState:
        """Change DP Status: enabled when any owner DP transition applies."""
        rules = [
            r
            for r in transitions_from(DP_STATUS_FIELD, self.dp_status, Actor.OWNER_SME)
            if self._failure(r) is None
        ]
        if not rules:
            return ActionState(enabled=False, visible=False)
        return ActionState(enabled=True)


def _edit_state(
    current_user_initials: str,
    one_pager_status: str,
    authorized_initials: Collection[str],
    lock_holder_initials: str | None,
) -> ActionState:
    if current_user_initials not in authorized_initials:
        return ActionState(
            enabled=False,
            tooltip="Only the Owner or an SME of this One Pager can edit it",
        )
    if one_pager_status not in EDITABLE_STATUSES:
        return ActionState(
            enabled=False,
            tooltip=f"A One Pager in status {one_pager_status} cannot be edited",
        )
    if lock_holder_initials and lock_holder_initials != current_user_initials:
        return ActionState(
            enabled=False, tooltip="Another user is editing this One Pager"
        )
    return ActionState(enabled=True)


def _release_lock_state(
    current_user_initials: str, is_locked: bool, lock_holder_initials: str | None  # noqa: FBT001
) -> ActionState:
    if not is_locked:
        return ActionState(enabled=False, tooltip="This One Pager is not locked")
    if lock_holder_initials != current_user_initials:
        return ActionState(
            enabled=False, tooltip="Only the lock holder can release this lock"
        )
    return ActionState(enabled=True)


def get_status_timeline_stages() -> list[dict]:
    """Return the One Pager status timeline stages for display.
    
    Represents the linear progression: Draft → Ready for Review → In Review → Approved
    (with alternative path for Draft Update after approval).
    
    Returns:
        List of stage dicts with: status, label, sort_order
    """
    return [
        {"status": "Draft", "label": "Draft", "sort_order": 1},
        {"status": "Ready for Review", "label": "Ready for Review", "sort_order": 2},
        {"status": "In Review", "label": "In Review", "sort_order": 3},
        {"status": "Approved", "label": "Approved", "sort_order": 4},
        {"status": "Draft Update", "label": "Draft Update", "sort_order": 5},
    ]
