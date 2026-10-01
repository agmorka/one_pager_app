"""One Pager and Data Product status state machines (Backend_Design.md §2-3).

``TRANSITIONS`` is the single source of truth for which status changes are
allowed, who may make them and what guards apply. Nothing else in the app
decides whether a transition is valid: the workflow service executes these
rules, the Preview page asks them which actions to offer, and the Help page
renders them.

``VALID_COMBINATIONS`` encodes which Data Product statuses may go with each
One Pager status (Requirements_and_Scope.md §6); every transition's result is
checked against it.

Pure Python — no Streamlit, no storage.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

OP_STATUS_FIELD = "one_pager_status"
DP_STATUS_FIELD = "data_product_status"

# One Pager statuses (document lifecycle).
DRAFT = "Draft"
READY_FOR_REVIEW = "Ready for Review"
IN_REVIEW = "In Review"
APPROVED = "Approved"
DRAFT_UPDATE = "Draft Update"
OP_CANCELLED = "Cancelled"

OP_STATUSES = (DRAFT, READY_FOR_REVIEW, IN_REVIEW, APPROVED, DRAFT_UPDATE, OP_CANCELLED)

# Data Product statuses (product lifecycle).
IN_DEFINITION = "In Definition"
READY_FOR_DEVELOPMENT = "Ready for Development"
IN_DEVELOPMENT = "In Development"
ACTIVE = "Active"
IN_ENHANCEMENT = "In Enhancement"
DEPRECATED = "Deprecated"
DP_CANCELLED = "Cancelled"

DP_STATUSES = (
    IN_DEFINITION,
    READY_FOR_DEVELOPMENT,
    IN_DEVELOPMENT,
    ACTIVE,
    IN_ENHANCEMENT,
    DEPRECATED,
    DP_CANCELLED,
)

# DP statuses a Data Product can have once its One Pager was approved.
POST_APPROVAL_DP_STATUSES = (
    READY_FOR_DEVELOPMENT,
    IN_DEVELOPMENT,
    ACTIVE,
    IN_ENHANCEMENT,
    DEPRECATED,
)

# Requirements_and_Scope.md §6, "Valid status combinations". A first approval
# always gives Ready for Development and a re-approval In Enhancement; the
# Owner can move it on from there, and Draft Update keeps whatever it was.
# The review of an update (Draft Update → Ready for Review → In Review) keeps
# the preserved DP status too (Decision_Log.md §16).
VALID_COMBINATIONS: dict[str, tuple[str, ...]] = {
    DRAFT: (IN_DEFINITION,),
    READY_FOR_REVIEW: (IN_DEFINITION, *POST_APPROVAL_DP_STATUSES),
    IN_REVIEW: (IN_DEFINITION, *POST_APPROVAL_DP_STATUSES),
    APPROVED: POST_APPROVAL_DP_STATUSES,
    DRAFT_UPDATE: POST_APPROVAL_DP_STATUSES,
    OP_CANCELLED: (DP_CANCELLED,),
}


class Actor(str, Enum):
    """Who performs a transition."""

    OWNER_SME = "owner_sme"
    """Owner or SME of this Data Product (per-record check)."""
    OWNER_SME_GROUP = "owner_sme_group"
    """Member of the Owner/SME group: may create One Pagers and manage Use
    Cases. Never a transition actor; editing a One Pager needs OWNER_SME."""
    APPROVER = "approver"
    """Member of the Approver group."""
    ADMIN = "admin"
    """Member of the Admin group."""
    SYSTEM = "system"
    """Triggered by another transition; never a user action."""


@dataclass(frozen=True)
class TransitionRule:
    """One allowed status change.

    Attributes:
        action: Action name (Backend_Design.md §2-3), e.g. ``owner_submit``.
        status_field: ``one_pager_status`` or ``data_product_status``.
        from_status: Status before the transition.
        to_status: Status after the transition.
        actors: Who may perform it.
        label: Short user-facing name of the action.
        summary: System-generated change-log summary.
        event_type: ``change_log.event_type`` of its entry.
        requires_strict_validation: The document must pass the strict tier.
        requires_op_status: One Pager statuses the DP transition needs.
        requires_dp_status: Data Product statuses the OP transition needs.
        requires_comment: A non-empty comment is mandatory (Reject).
        requires_confirmation: The UI must ask before performing it.
        segregation_of_duties: The actor must not be Owner/SME of this One
            Pager (Approve / Reject).

    """

    action: str
    status_field: str
    from_status: str
    to_status: str
    actors: tuple[Actor, ...]
    label: str
    summary: str
    event_type: str = "status_transition"
    requires_strict_validation: bool = False
    requires_op_status: tuple[str, ...] = ()
    requires_dp_status: tuple[str, ...] = ()
    requires_comment: bool = False
    requires_confirmation: bool = False
    segregation_of_duties: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.status_field, self.from_status, self.to_status)

    @property
    def is_system(self) -> bool:
        return self.actors == (Actor.SYSTEM,)


class InvalidTransitionError(ValueError):
    """The requested status change is not in ``TRANSITIONS``."""


class InvalidStatusCombinationError(ValueError):
    """A One Pager / Data Product status pair is not allowed (Req §6)."""


_OWNER = (Actor.OWNER_SME,)
_SYSTEM = (Actor.SYSTEM,)


def _op(  # noqa: PLR0913 - mirrors the columns of the Backend_Design table
    action: str,
    from_status: str,
    to_status: str,
    actors: tuple[Actor, ...],
    label: str,
    summary: str,
    **guards: Any,  # noqa: ANN401 - TransitionRule guard fields
) -> TransitionRule:
    return TransitionRule(
        action=action,
        status_field=OP_STATUS_FIELD,
        from_status=from_status,
        to_status=to_status,
        actors=actors,
        label=label,
        summary=summary,
        **guards,
    )


def _dp(  # noqa: PLR0913 - mirrors the columns of the Backend_Design table
    action: str,
    from_status: str,
    to_status: str,
    actors: tuple[Actor, ...],
    label: str,
    summary: str,
    **guards: Any,  # noqa: ANN401 - TransitionRule guard fields
) -> TransitionRule:
    return TransitionRule(
        action=action,
        status_field=DP_STATUS_FIELD,
        from_status=from_status,
        to_status=to_status,
        actors=actors,
        label=label,
        summary=summary,
        **guards,
    )


_CANCELLABLE = (DRAFT, READY_FOR_REVIEW, IN_REVIEW)

_RULES: tuple[TransitionRule, ...] = (
    # --- One Pager status (Backend_Design.md §2) ---------------------------
    _op(
        "owner_submit",
        DRAFT,
        READY_FOR_REVIEW,
        _OWNER,
        "Submit for Review",
        "Submitted for review",
        requires_strict_validation=True,
    ),
    _op(
        "owner_submit",
        DRAFT_UPDATE,
        READY_FOR_REVIEW,
        _OWNER,
        "Submit for Review",
        "Update submitted for review",
        requires_strict_validation=True,
    ),
    # Automatic second half of the atomic Submit for Review.
    _op(
        "owner_submit_for_review",
        READY_FOR_REVIEW,
        IN_REVIEW,
        _OWNER,
        "Submit for Review",
        "Review started",
        requires_strict_validation=True,
    ),
    _op(
        "approver_approve",
        IN_REVIEW,
        APPROVED,
        (Actor.APPROVER,),
        "Approve",
        "One Pager approved",
        segregation_of_duties=True,
    ),
    _op(
        "approver_reject",
        IN_REVIEW,
        DRAFT,
        (Actor.APPROVER,),
        "Reject",
        "One Pager rejected",
        requires_dp_status=(IN_DEFINITION,),
        segregation_of_duties=True,
        requires_comment=True,
        requires_confirmation=True,
    ),
    # A rejected update goes back to Draft Update: the DP status stays as it
    # was before the update (Decision_Log.md §16).
    _op(
        "approver_reject",
        IN_REVIEW,
        DRAFT_UPDATE,
        (Actor.APPROVER,),
        "Reject",
        "Update rejected",
        requires_dp_status=POST_APPROVAL_DP_STATUSES,
        segregation_of_duties=True,
        requires_comment=True,
        requires_confirmation=True,
    ),
    _op(
        "owner_update",
        APPROVED,
        DRAFT_UPDATE,
        _OWNER,
        "Update",
        "Update started from the approved version",
        requires_confirmation=True,
    ),
    *(
        _op(
            "cancel",
            status,
            OP_CANCELLED,
            (Actor.OWNER_SME, Actor.ADMIN),
            "Cancel One Pager",
            "One Pager cancelled",
            event_type="cancellation",
            requires_dp_status=(IN_DEFINITION,),
            requires_confirmation=True,
        )
        for status in _CANCELLABLE
    ),
    # --- Data Product status (Backend_Design.md §3) ------------------------
    _dp(
        "system_first_approval",
        IN_DEFINITION,
        READY_FOR_DEVELOPMENT,
        _SYSTEM,
        "First approval",
        "Data Product ready for development (first approval)",
    ),
    _dp(
        "system_cancel",
        IN_DEFINITION,
        DP_CANCELLED,
        _SYSTEM,
        "Cancel",
        "Data Product cancelled with its One Pager",
    ),
    *(
        _dp(
            "system_re_approval",
            status,
            IN_ENHANCEMENT,
            _SYSTEM,
            "Re-approval",
            "Data Product in enhancement (updated One Pager approved)",
        )
        for status in (READY_FOR_DEVELOPMENT, IN_DEVELOPMENT, ACTIVE, DEPRECATED)
    ),
    # Owner-initiated DP transitions need an Approved One Pager: while it is
    # in Draft Update the DP status is preserved (Req §6 combinations).
    _dp(
        "owner_start_dev",
        READY_FOR_DEVELOPMENT,
        IN_DEVELOPMENT,
        _OWNER,
        "Start development",
        "Development started",
        requires_op_status=(APPROVED,),
    ),
    _dp(
        "owner_start_dev",
        IN_ENHANCEMENT,
        IN_DEVELOPMENT,
        _OWNER,
        "Start development",
        "Development of the enhancement started",
        requires_op_status=(APPROVED,),
    ),
    _dp(
        "owner_activate",
        IN_DEVELOPMENT,
        ACTIVE,
        _OWNER,
        "Activate",
        "Data Product activated",
        requires_op_status=(APPROVED,),
    ),
    _dp(
        "owner_deprecate",
        ACTIVE,
        DEPRECATED,
        _OWNER,
        "Deprecate",
        "Data Product deprecated",
        requires_op_status=(APPROVED,),
        requires_confirmation=True,
    ),
)

# (status_field, from_status, to_status) → rule.
TRANSITIONS: dict[tuple[str, str, str], TransitionRule] = {r.key: r for r in _RULES}


def get_rule(status_field: str, from_status: str, to_status: str) -> TransitionRule:
    """Look up a transition.

    Raises:
        InvalidTransitionError: The transition does not exist.

    """
    rule = TRANSITIONS.get((status_field, from_status, to_status))
    if rule is None:
        field = "One Pager" if status_field == OP_STATUS_FIELD else "Data Product"
        msg = f"{field} status cannot change from {from_status} to {to_status}."
        raise InvalidTransitionError(msg)
    return rule


def transitions_from(
    status_field: str, from_status: str, actor: Actor | None = None
) -> list[TransitionRule]:
    """Transitions leaving a status, optionally only those ``actor`` performs."""
    return [
        rule
        for rule in _RULES
        if rule.status_field == status_field
        and rule.from_status == from_status
        and (actor is None or actor in rule.actors)
    ]


def reject_target(data_product_status: str) -> str:
    """Return the One Pager status a Reject goes back to.

    ``Draft``, or ``Draft Update`` when the rejected review was of an update
    (the Data Product was approved before).
    """
    return DRAFT if data_product_status == IN_DEFINITION else DRAFT_UPDATE


def is_valid_combination(op_status: str, dp_status: str) -> bool:
    """Whether a One Pager / Data Product status pair is allowed (Req §6)."""
    return dp_status in VALID_COMBINATIONS.get(op_status, ())


def check_combination(op_status: str, dp_status: str) -> None:
    """Raise ``InvalidStatusCombinationError`` for a pair that is not allowed."""
    if not is_valid_combination(op_status, dp_status):
        msg = (
            f"One Pager status {op_status} cannot go with Data Product status "
            f"{dp_status}."
        )
        raise InvalidStatusCombinationError(msg)


def guard_failure(
    rule: TransitionRule,
    *,
    actors: set[Actor],
    one_pager_status: str,
    data_product_status: str,
    is_owner_or_sme: bool,
) -> str | None:
    """Why ``rule`` cannot run in this situation, or None if its guards pass.

    Checks who may perform it (``actors`` are the roles the user has; Owner/SME
    is per record), segregation of duties and the status guards. Validation,
    comments and confirmation are checked by the caller when it executes.
    """
    reason: str | None = None
    current = (
        one_pager_status
        if rule.status_field == OP_STATUS_FIELD
        else data_product_status
    )
    user_actors = set(actors)
    if is_owner_or_sme:
        user_actors.add(Actor.OWNER_SME)
    if current != rule.from_status:
        reason = f"The status is {current}, not {rule.from_status}."
    elif not user_actors.intersection(rule.actors) or rule.is_system:
        reason = _actor_message(rule)
    elif rule.segregation_of_duties and is_owner_or_sme:
        reason = "You cannot review a One Pager on which you are Owner or SME."
    elif rule.requires_op_status and one_pager_status not in rule.requires_op_status:
        reason = (
            f"Only possible while the One Pager is "
            f"{' or '.join(rule.requires_op_status)}."
        )
    elif rule.requires_dp_status and data_product_status not in rule.requires_dp_status:
        reason = (
            f"Only possible while the Data Product is "
            f"{' or '.join(rule.requires_dp_status)}."
        )
    return reason


def _actor_message(rule: TransitionRule) -> str:
    if rule.is_system:
        return "This status change happens automatically."
    names = {
        Actor.OWNER_SME: "the Owner or an SME",
        Actor.APPROVER: "an Approver",
        Actor.ADMIN: "an Admin",
    }
    who = " or ".join(names[a] for a in rule.actors if a in names)
    return f"Only {who} can do this."


# ============================================================================
# Serialization for the Help page (Backend_Design.md §14)
# ============================================================================

_ACTOR_NAMES = {
    Actor.OWNER_SME: "Owner/SME",
    Actor.APPROVER: "Approver",
    Actor.ADMIN: "Admin",
    Actor.SYSTEM: "System",
}


def _conditions(rule: TransitionRule) -> list[str]:
    conditions: list[str] = []
    if rule.requires_strict_validation:
        conditions.append("Strict validation passes")
    if rule.requires_op_status:
        conditions.append(f"One Pager is {' or '.join(rule.requires_op_status)}")
    if rule.requires_dp_status:
        conditions.append(f"Data Product is {' or '.join(rule.requires_dp_status)}")
    if rule.segregation_of_duties:
        conditions.append("Reviewer is not Owner/SME of this One Pager")
    if rule.requires_comment:
        conditions.append("A comment is required")
    if rule.requires_confirmation:
        conditions.append("Asks for confirmation")
    return conditions


def serialize_transition(rule: TransitionRule) -> dict[str, object]:
    """Return one transition as plain data (JSON-serializable)."""
    return {
        "status_field": rule.status_field,
        "from_status": rule.from_status,
        "to_status": rule.to_status,
        "action": rule.action,
        "label": rule.label,
        "who": [_ACTOR_NAMES[a] for a in rule.actors],
        "system": rule.is_system,
        "conditions": _conditions(rule),
        "summary": rule.summary,
    }


def serialize_state_machine() -> dict[str, list[dict[str, object]]]:
    """Return the transitions and valid combinations as plain data (Help page).

    Returns:
        ``one_pager_transitions`` and ``data_product_transitions`` (in the
        order of Backend_Design.md §2-3) and ``valid_combinations`` (one entry
        per One Pager status, Requirements_and_Scope.md §6).

    """
    return {
        "one_pager_transitions": [
            serialize_transition(r) for r in _RULES if r.status_field == OP_STATUS_FIELD
        ],
        "data_product_transitions": [
            serialize_transition(r) for r in _RULES if r.status_field == DP_STATUS_FIELD
        ],
        "valid_combinations": [
            {"one_pager_status": op, "data_product_statuses": list(dps)}
            for op, dps in VALID_COMBINATIONS.items()
        ],
    }
