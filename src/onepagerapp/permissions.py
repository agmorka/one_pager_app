"""Permission and workflow authorization checks (v1 stub).

This module defines read-only permission checks and a stub for action button authorization.
v1 renders all state-changing actions as disabled buttons with "coming soon" tooltips.

Full enforcement will be implemented when the service layer (workflow.py, locking.py) exists.

Per Backend_Design.md §5, all authenticated users can view any One Pager.
Permission enforcement happens on state changes (approve, edit, etc.) in the service layer.
"""

from dataclasses import dataclass


@dataclass
class ActionState:
    """Button state for a single action.
    
    Attributes:
        enabled: Whether the button should be clickable.
        tooltip: Hover text (reason if disabled, blank if enabled).
    """

    enabled: bool
    tooltip: str = ""


def can_view_one_pager(current_user: str | None) -> bool:
    """Check if the current user can view a One Pager.
    
    Per Backend_Design.md §5, all authenticated users can view.
    
    Args:
        current_user: Current user identifier or None if not authenticated.
        
    Returns:
        True if user is authenticated, False otherwise.
    """
    return bool(current_user)


# ============================================================================
# v1 Action Button States (Stub)
# ============================================================================
# These functions return the button state (enabled/disabled + tooltip).
# v1 renders all state-changing actions as DISABLED with "coming soon" tooltips.
# Full enforcement lands when the service layer exists.
# ============================================================================


def get_action_states(
    current_user_initials: str,
    owner_initials: str,
    one_pager_status: str,
    is_locked: bool,
    lock_holder_initials: str | None,
) -> dict[str, ActionState]:
    """Compute the button state for all actions in the Preview page.
    
    v1: All state-changing actions return DISABLED.
    
    Args:
        current_user_initials: Initials of the logged-in user.
        owner_initials: Initials of the One Pager owner.
        one_pager_status: Current document status (Draft, In Review, Approved, etc.).
        is_locked: Whether the One Pager is currently locked.
        lock_holder_initials: Initials of lock holder if locked, None otherwise.
        
    Returns:
        Dictionary mapping action name (e.g. "edit", "approve") to ActionState.
    """
    # v1: All actions disabled with "coming soon" message
    return {
        "edit": ActionState(
            enabled=False,
            tooltip="Edit coming soon — requires Editor page and lock acquisition",
        ),
        "update": ActionState(
            enabled=False,
            tooltip="Update coming soon",
        ),
        "approve": ActionState(
            enabled=False,
            tooltip="Approve coming soon — requires approval workflow",
        ),
        "reject": ActionState(
            enabled=False,
            tooltip="Reject coming soon — requires approval workflow",
        ),
        "change_dp_status": ActionState(
            enabled=False,
            tooltip="Change DP Status coming soon — requires workflow state machine",
        ),
        "cancel": ActionState(
            enabled=False,
            tooltip="Cancel coming soon — requires approval workflow",
        ),
        "add_comment": ActionState(
            enabled=False,
            tooltip="Add comment coming soon",
        ),
        "resolve_comment": ActionState(
            enabled=False,
            tooltip="Resolve comment coming soon",
        ),
        "release_lock": ActionState(
            enabled=False,
            tooltip="Release lock coming soon",
        ),
        "export_pdf": ActionState(
            enabled=False,
            tooltip="Export PDF coming soon",
        ),
    }


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
