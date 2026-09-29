"""One Pager workflow service (Backend_Design.md §2-3).

``create`` makes a new One Pager: (new) -> ``Draft``. Guard: the user may
create One Pagers. Side effects: assign the OP ID, write the YAML document,
insert the authorized users, the change log entry and the
``one_pager_status`` row.

Every other status change goes through ``apply_transitions``, which executes
rules from ``state_machine.TRANSITIONS`` (the single source of truth for
allowed transitions) and enforces the valid OP/DP status combinations.

Write order and failure handling follow New_One_Pager_Plan D5: the YAML file is
written first and the ``one_pager_status`` row last, so a failure at any step
leaves nothing visible in the Registry or Preview. Pure Python — no Streamlit.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pandas as pd

from onepagerapp.audit import (
    Outcome,
    log_event,
    log_permission_denied,
    log_status_transition,
)
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.editing import require_lock, submission_issues
from onepagerapp.id_generator import IdGenerationError, next_id
from onepagerapp.locking import release_lock
from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    CreateResult,
    CurrentUser,
    NewOnePagerInput,
    OnePagerDocument,
    OnePagerStatusRow,
    ReviewComment,
    ValidationError,
)
from onepagerapp.permissions import (
    PermissionDeniedError,
    can_create_one_pager,
    check_can_edit,
    is_owner_or_sme,
)
from onepagerapp.state_machine import (
    DP_CANCELLED,
    DP_STATUS_FIELD,
    DRAFT,
    IN_REVIEW,
    OP_CANCELLED,
    OP_STATUS_FIELD,
    READY_FOR_REVIEW,
    TRANSITIONS,
    Actor,
    InvalidTransitionError,
    TransitionRule,
    check_combination,
    get_rule,
    guard_failure,
    serialize_state_machine,
    transitions_from,
)
from onepagerapp.validation import (
    CURRENT_STRUCTURE_DEFINITION,
    normalize_new_one_pager,
    sanitize_text,
    validate_create,
    validate_lenient,
)

logger = logging.getLogger(__name__)

INITIAL_VERSION = "0.1.0"
INITIAL_OP_STATUS = "Draft"
INITIAL_DP_STATUS = "In Definition"
CREATION_SUMMARY = "Initial draft created"

CREATE_FAILED_MESSAGE = "Create failed — your changes are preserved, please retry."


class CreateError(RuntimeError):
    """A One Pager could not be stored. The message is safe to show to users."""


def active_reference_values(df: pd.DataFrame, column: str) -> list[str]:
    """Return the values of a reference table column, active rows only.

    Rows without an ``active`` column are treated as active. The Statement
    Execution API returns booleans as strings, so "true" is accepted too.
    """
    if df is None or df.empty or column not in df.columns:
        return []
    if "active" in df.columns:
        mask = df["active"].map(lambda v: v is True or str(v).lower() == "true")
        df = df[mask]
    if "sort_order" in df.columns:
        df = df.sort_values("sort_order", key=lambda s: pd.to_numeric(s))
    return [str(v) for v in df[column].tolist()]


def duplicate_error(existing_id: str) -> ValidationError:
    """Field error shown when the data product already has a One Pager."""
    return ValidationError(
        "dataProduct",
        f"A One Pager for this Data Product already exists ({existing_id}).",
    )


def build_initial_document(
    data: NewOnePagerInput, user: CurrentUser, now: datetime
) -> OnePagerDocument:
    """Build the v0.1.0 Draft document (New_One_Pager_Plan D9)."""
    timestamp = now.isoformat(timespec="seconds")
    return OnePagerDocument(
        structure_definition=CURRENT_STRUCTURE_DEFINITION,
        data_product=data.data_product,
        product_name=data.product_name,
        business_domain=data.business_domain,
        data_product_type=data.data_product_type,
        one_pager_status=INITIAL_OP_STATUS,
        data_product_status=INITIAL_DP_STATUS,
        version=INITIAL_VERSION,
        description=data.description,
        owner_name=data.owner.name,
        owner_initials=data.owner.initials,
        owner_email=data.owner.email,
        owner_team=data.owner.team,
        business_problem_statement=data.business_problem_statement,
        smes=[
            {
                k: v
                for k, v in {
                    "name": s.name,
                    "initials": s.initials,
                    "email": s.email,
                    "team": s.team,
                }.items()
                if v
            }
            for s in data.smes
        ],
        created_by=user.display_name,
        created_at=timestamp,
        last_updated=timestamp,
        change_log=[
            {
                "version": INITIAL_VERSION,
                "date": timestamp,
                "author": user.display_name,
                "summary": CREATION_SUMMARY,
            }
        ],
    )


def _authorized_users(
    one_pager_id: str, data: NewOnePagerInput
) -> list[AuthorizedUser]:
    """Build the Owner + SME rows from the document (D3) — not the creator."""
    users = [
        AuthorizedUser(
            one_pager_id=one_pager_id,
            user_initials=data.owner.initials,
            user_name=data.owner.name,
            user_email=data.owner.email,
            user_team=data.owner.team,
            role="owner",
        )
    ]
    users.extend(
        AuthorizedUser(
            one_pager_id=one_pager_id,
            user_initials=s.initials,
            user_name=s.name,
            user_email=s.email,
            user_team=s.team,
            role="sme",
        )
        for s in data.smes
    )
    return users


def _status_row(
    one_pager_id: str, data: NewOnePagerInput, user: CurrentUser, now: datetime
) -> OnePagerStatusRow:
    return OnePagerStatusRow(
        one_pager_id=one_pager_id,
        data_product=data.data_product,
        product_name=data.product_name,
        business_domain=data.business_domain,
        data_product_type=data.data_product_type,
        one_pager_status=INITIAL_OP_STATUS,
        data_product_status=INITIAL_DP_STATUS,
        version=INITIAL_VERSION,
        owner_name=data.owner.name,
        owner_initials=data.owner.initials,
        owner_email=data.owner.email,
        owner_team=data.owner.team,
        created_by=user.initials,
        created_at=now,
        last_updated_at=now,
        last_updated_by=user.initials,
        structure_definition=CURRENT_STRUCTURE_DEFINITION,
    )


def _creation_entry(
    one_pager_id: str, user: CurrentUser, now: datetime
) -> ChangeLogEntry:
    """Build the single system-generated creation entry (D10)."""
    return ChangeLogEntry(
        id=0,
        one_pager_id=one_pager_id,
        version=INITIAL_VERSION,
        event_type="creation",
        author_initials=user.initials,
        author_name=user.display_name,
        summary=CREATION_SUMMARY,
        created_at=now,
        from_status=None,
        to_status=INITIAL_OP_STATUS,
        status_field="one_pager_status",
    )


def _compensate(data_access: DataAccess, one_pager_id: str, user: CurrentUser) -> None:
    """Best-effort removal of the rows of a create that did not complete."""
    try:
        data_access.delete_one_pager_records(one_pager_id)
    except Exception:
        logger.exception(f"Compensation failed for {one_pager_id}")
        log_event(
            "create_one_pager",
            Outcome.COMPENSATION_FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
        )


def create_one_pager(
    data: NewOnePagerInput,
    user: CurrentUser,
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    now: datetime | None = None,
) -> CreateResult:
    """Create a new Draft One Pager.

    Args:
        data: Raw form input (sanitized here).
        user: The authenticated creator.
        data_access: Tabular data access (Delta or mock).
        document_store: YAML document store (volume or local folder).
        now: Creation instant (defaults to the current UTC time).

    Returns:
        CreateResult with the new ID, or with validation errors (nothing written).

    Raises:
        PermissionDeniedError: The user may not create One Pagers.
        CreateError: Storing failed; partial writes were compensated.

    """
    if not can_create_one_pager(user):
        log_permission_denied("create_one_pager", user=user.initials if user else None)
        msg = "You are not allowed to create One Pagers."
        raise PermissionDeniedError(msg)

    data = normalize_new_one_pager(data)
    errors = validate_create(
        data,
        user,
        allowed_domains=active_reference_values(
            data_access.get_ref_business_domains(), "domain"
        ),
        allowed_types=active_reference_values(
            data_access.get_ref_data_product_types(), "type"
        ),
    )
    if errors:
        return CreateResult(errors=errors)

    existing = data_access.get_one_pager_ids_for_data_product(data.data_product)
    if existing:
        return CreateResult(errors=[duplicate_error(existing[0])])

    try:
        one_pager_id = next_id(data_access, "OP")
    except (IdGenerationError, RuntimeError) as e:
        logger.exception("Failed to reserve a One Pager ID")
        raise CreateError(CREATE_FAILED_MESSAGE) from e

    now = now or datetime.now(UTC)
    document = build_initial_document(data, user, now)
    # A new Draft only has to pass the lenient tier; the strict tier is the
    # guard on Submit for Review.
    schema_errors = validate_lenient(document_to_dict(document))
    if schema_errors:
        # The create tier should make this impossible; treat as a bug.
        details = "; ".join(f"{e.field_path}: {e.message}" for e in schema_errors)
        logger.error(
            f"Generated document for {one_pager_id} is not schema-valid: {details}"
        )
        raise CreateError(CREATE_FAILED_MESSAGE)

    # D5 step 2: the file lives under an OP ID nothing references yet.
    try:
        document_store.write(one_pager_id, document, INITIAL_VERSION)
    except RuntimeError as e:
        logger.exception(f"Writing the document for {one_pager_id} failed")
        log_event(
            "create_one_pager",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="write_document",
        )
        raise CreateError(CREATE_FAILED_MESSAGE) from e

    # D5 steps 3-5: status row last, it makes the One Pager visible.
    step = "insert_authorized_users"
    try:
        data_access.insert_authorized_users(_authorized_users(one_pager_id, data))
        step = "append_change_log"
        data_access.append_change_log(_creation_entry(one_pager_id, user, now))
        step = "insert_one_pager_status"
        data_access.insert_one_pager_status(_status_row(one_pager_id, data, user, now))
    except Exception as e:
        logger.exception(f"Create of {one_pager_id} failed at {step}")
        log_event(
            "create_one_pager",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step=step,
        )
        _compensate(data_access, one_pager_id, user)
        raise CreateError(CREATE_FAILED_MESSAGE) from e

    # D4: Delta does not enforce UNIQUE — the lower OP ID wins a race.
    try:
        ids = data_access.get_one_pager_ids_for_data_product(data.data_product)
    except Exception:
        logger.exception(f"Uniqueness re-check failed for {one_pager_id}")
        ids = [one_pager_id]
    if ids and ids[0] != one_pager_id:
        log_event(
            "create_one_pager",
            Outcome.DUPLICATE_RACE,
            user=user.initials,
            one_pager_id=one_pager_id,
            winner=ids[0],
        )
        _compensate(data_access, one_pager_id, user)
        return CreateResult(errors=[duplicate_error(ids[0])])

    log_event(
        "create_one_pager",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
    )
    return CreateResult(one_pager_id=one_pager_id, version=INITIAL_VERSION)


# ============================================================================
# Status transitions (Backend_Design.md §2-3)
# ============================================================================

TRANSITION_FAILED_MESSAGE = "The status could not be changed. Please retry."
TRANSITION_CONFLICT_MESSAGE = (
    "This One Pager was changed by someone else in the meantime. Reload the "
    "page and try again."
)


class TransitionError(RuntimeError):
    """A transition could not be stored. The message is safe to show to users."""


def _transition_entry(
    rule: TransitionRule,
    row: OnePagerStatusRow,
    user: CurrentUser,
    now: datetime,
    note: str | None,
) -> ChangeLogEntry:
    summary = f"{rule.summary}: {note}" if note else rule.summary
    return ChangeLogEntry(
        id=0,
        one_pager_id=row.one_pager_id,
        version=row.version,
        event_type=rule.event_type,
        author_initials=user.initials,
        author_name=user.display_name,
        summary=summary,
        created_at=now,
        from_status=rule.from_status,
        to_status=rule.to_status,
        status_field=rule.status_field,
    )


def plan_transitions(
    row: OnePagerStatusRow, rules: list[TransitionRule]
) -> OnePagerStatusRow:
    """Return the status row after applying ``rules`` in order (nothing stored).

    Raises:
        InvalidTransitionError: A rule does not start from the status the row
            has at that point.
        InvalidStatusCombinationError: The resulting OP/DP pair is not allowed.

    """
    new_row = replace(row)
    for rule in rules:
        if TRANSITIONS.get(rule.key) is not rule:
            msg = f"{rule.action} is not a registered transition."
            raise InvalidTransitionError(msg)
        current = getattr(new_row, rule.status_field)
        if current != rule.from_status:
            msg = (
                f"Cannot {rule.label.lower()}: the status is {current}, "
                f"not {rule.from_status}."
            )
            raise InvalidTransitionError(msg)
        setattr(new_row, rule.status_field, rule.to_status)
    check_combination(new_row.one_pager_status, new_row.data_product_status)
    return new_row


def apply_transitions(  # noqa: PLR0913 - every argument is part of the change
    data_access: DataAccess,
    row: OnePagerStatusRow,
    rules: list[TransitionRule],
    user: CurrentUser,
    *,
    now: datetime | None = None,
    note: str | None = None,
    reviewed: bool = False,
) -> OnePagerStatusRow:
    """Execute one user action made of one or more status transitions.

    All status changes are written with one conditional update of the
    ``one_pager_status`` row (it must still have the status and version the
    caller read), then one change-log entry per transition in a single insert.
    If the change log cannot be written, the row is put back, so an action is
    never half-done (e.g. never stranded in ``Ready for Review``).

    Permission, validation and confirmation guards are the caller's job; this
    function enforces the transition rules and the valid status combinations.

    Args:
        data_access: Tabular data access.
        row: The status row as read by the caller.
        rules: Transitions to apply, in order.
        user: The acting user (change-log author, ``last_updated_by``).
        now: Transition instant (defaults to the current UTC time).
        note: Text appended to every change-log summary (e.g. a comment).
        reviewed: The action is a review decision (Approve / Reject): sets
            ``reviewed_at`` and ``reviewed_by`` (Data_Model.md §3).

    Returns:
        The stored status row.

    Raises:
        InvalidTransitionError: A rule does not apply to the current status.
        InvalidStatusCombinationError: The result is not a valid OP/DP pair.
        TransitionError: Storing failed or the row changed meanwhile.

    """
    now = now or datetime.now(UTC)
    new_row = plan_transitions(row, rules)
    new_row.last_updated_at = now
    new_row.last_updated_by = user.initials
    if reviewed:
        new_row.reviewed_at = now
        new_row.reviewed_by = user.initials
    entries = [_transition_entry(rule, new_row, user, now, note) for rule in rules]
    action = rules[0].action if rules else "transition"

    try:
        updated = data_access.update_one_pager_status(
            new_row,
            expected_version=row.version,
            expected_status=row.one_pager_status,
        )
    except Exception as e:
        logger.exception(f"{action} on {row.one_pager_id} failed")
        log_event(
            action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=row.one_pager_id,
            step="update_one_pager_status",
        )
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e
    if not updated:
        log_event(
            action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=row.one_pager_id,
            step="status_row_changed",
        )
        raise TransitionError(TRANSITION_CONFLICT_MESSAGE)

    try:
        data_access.append_change_log_entries(entries)
    except Exception as e:
        logger.exception(f"Change log of {action} on {row.one_pager_id} failed")
        log_event(
            action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=row.one_pager_id,
            step="append_change_log",
        )
        _undo_transition(data_access, row, new_row, user, action)
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e

    for rule in rules:
        log_status_transition(
            one_pager_id=row.one_pager_id,
            user=user.initials,
            status_field=rule.status_field,
            from_status=rule.from_status,
            to_status=rule.to_status,
            version=new_row.version,
        )
    return new_row


def _undo_transition(
    data_access: DataAccess,
    previous: OnePagerStatusRow,
    written: OnePagerStatusRow,
    user: CurrentUser,
    action: str,
) -> None:
    """Best-effort rollback of a status update whose change log failed."""
    try:
        restored = data_access.update_one_pager_status(
            previous,
            expected_version=written.version,
            expected_status=written.one_pager_status,
        )
    except Exception:
        logger.exception(f"Rolling back {action} on {previous.one_pager_id} failed")
        restored = False
    if not restored:
        log_event(
            action,
            Outcome.COMPENSATION_FAILED,
            user=user.initials,
            one_pager_id=previous.one_pager_id,
        )


def get_workflow_reference() -> dict[str, list[dict[str, object]]]:
    """Transitions and valid OP/DP combinations for the Help page (Backend §14).

    Rendered from ``TRANSITIONS`` / ``VALID_COMBINATIONS``, so the Help page
    always describes the rules the application enforces.
    """
    return serialize_state_machine()


# ============================================================================
# Submit for Review (Backend_Design.md §2)
# ============================================================================

SUBMIT_LOCK_MESSAGE = (
    "Open the One Pager in the Editor to submit it: your session must hold "
    "its edit lock."
)


@dataclass
class SubmitResult:
    """Outcome of ``submit_for_review``: the stored row, or what blocks it."""

    status_row: OnePagerStatusRow | None = None
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status_row is not None and not self.errors


def submit_for_review(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    *,
    now: datetime | None = None,
) -> SubmitResult:
    """Submit for Review: Draft / Draft Update → Ready for Review → In Review.

    One user action (Requirements_and_Scope.md §6): the stored document of the
    current version must pass the strict tier; then both transitions are
    written by ``apply_transitions`` (one status update, both change-log
    entries in one insert, rolled back together), so the One Pager is never
    left in ``Ready for Review``. The version does not change. Afterwards the
    edit lock is released (Backend_Design.md §6).

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user may not submit it (not Owner/SME, or
            not ``Draft``/``Draft Update``), or their session does not hold
            the edit lock.
        TransitionError: Storing failed; nothing changed.

    """
    now = now or datetime.now(UTC)
    row = data_access.get_one_pager_status_row(one_pager_id)
    if row is None:
        msg = f"One Pager {one_pager_id} not found."
        raise NotFoundError(msg)
    check_can_edit(
        user,
        one_pager_id,
        row.one_pager_status,
        data_access.get_authorized_users(one_pager_id),
    )
    try:
        require_lock(data_access, one_pager_id, user, session_id, now, "submit")
    except PermissionDeniedError as e:
        raise type(e)(SUBMIT_LOCK_MESSAGE) from e

    document = data_access.read_document(one_pager_id, row.version)
    if document is None:
        msg = f"The document of {one_pager_id} v{row.version} could not be found."
        raise TransitionError(msg)
    errors = submission_issues(document, row)
    if errors:
        log_event(
            "submit",
            Outcome.EDIT_REJECTED,
            user=user.initials,
            one_pager_id=one_pager_id,
            reason="strict_validation",
            issues=len(errors),
        )
        return SubmitResult(errors=errors)

    rules = [
        get_rule(OP_STATUS_FIELD, row.one_pager_status, READY_FOR_REVIEW),
        get_rule(OP_STATUS_FIELD, READY_FOR_REVIEW, IN_REVIEW),
    ]
    new_row = apply_transitions(data_access, row, rules, user, now=now)

    try:
        release_lock(data_access, one_pager_id, user)
    except Exception:
        # The lock expires on its own; the submission itself succeeded.
        logger.exception(f"Releasing the lock after submitting {one_pager_id} failed")
    return SubmitResult(status_row=new_row)


# ============================================================================
# Cancel (Requirements_and_Scope.md §6)
# ============================================================================

MAX_REASON_LENGTH = 500


def cancel_one_pager(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    *,
    reason: str = "",
    roles: Collection[Actor] = (),
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Cancel a One Pager: OP → ``Cancelled`` and, by the system, DP → ``Cancelled``.

    Allowed from ``Draft``, ``Ready for Review`` or ``In Review`` while the
    Data Product is ``In Definition``, for the Owner/SMEs of the One Pager or
    an Admin. Both status changes and their change-log entries are written as
    one action. Any active edit lock is released, whoever holds it
    (Backend_Design.md §2). Cancellation is permanent.

    Args:
        data_access: Tabular data access.
        one_pager_id: The One Pager to cancel.
        user: The acting user.
        reason: Optional reason, appended to the change-log summaries.
        roles: The user's group roles (Admin may cancel any One Pager).
        now: Transition instant (defaults to the current UTC time).

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user is neither Owner/SME nor Admin.
        InvalidTransitionError: The statuses do not allow cancelling.
        TransitionError: Storing failed; nothing changed.

    """
    row = data_access.get_one_pager_status_row(one_pager_id)
    if row is None:
        msg = f"One Pager {one_pager_id} not found."
        raise NotFoundError(msg)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    if not owner_or_sme and Actor.ADMIN not in roles:
        log_permission_denied("cancel", user=user.initials, one_pager_id=one_pager_id)
        msg = "Only the Owner, an SME or an Admin can cancel this One Pager."
        raise PermissionDeniedError(msg)

    op_rule = get_rule(OP_STATUS_FIELD, row.one_pager_status, OP_CANCELLED)
    failure = guard_failure(
        op_rule,
        actors=set(roles),
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        is_owner_or_sme=owner_or_sme,
    )
    if failure:
        raise InvalidTransitionError(failure)
    dp_rule = get_rule(DP_STATUS_FIELD, row.data_product_status, DP_CANCELLED)

    reason = sanitize_text(reason)[:MAX_REASON_LENGTH]
    new_row = apply_transitions(
        data_access, row, [op_rule, dp_rule], user, now=now, note=reason or None
    )
    _release_any_lock(data_access, one_pager_id, user)
    return new_row


def _release_any_lock(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> None:
    """Remove the edit lock of a cancelled One Pager, whoever holds it."""
    try:
        lock = data_access.get_lock(one_pager_id)
        if lock is None:
            return
        data_access.delete_lock(
            one_pager_id, locked_by_initials=lock.locked_by_initials
        )
    except Exception:
        logger.exception(f"Releasing the lock of cancelled {one_pager_id} failed")
        return
    log_event(
        "release_lock",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        previous_holder=lock.locked_by_initials,
        reason="cancelled",
    )


# ============================================================================
# Owner-initiated Data Product transitions (Backend_Design.md §3)
# ============================================================================


class ConfirmationRequiredError(ValueError):
    """The transition is destructive and was not confirmed (e.g. Deprecate)."""


def data_product_options(
    row: OnePagerStatusRow, *, owner_or_sme: bool
) -> list[TransitionRule]:
    """List the DP transitions the Owner/SMEs can make now (Change DP Status)."""
    return [
        rule
        for rule in transitions_from(
            DP_STATUS_FIELD, row.data_product_status, Actor.OWNER_SME
        )
        if guard_failure(
            rule,
            actors=set(),
            one_pager_status=row.one_pager_status,
            data_product_status=row.data_product_status,
            is_owner_or_sme=owner_or_sme,
        )
        is None
    ]


def change_data_product_status(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    one_pager_id: str,
    to_status: str,
    user: CurrentUser,
    *,
    confirmed: bool = False,
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Start development, activate or deprecate a Data Product (Owner/SMEs only).

    Only owner-initiated rules of ``TRANSITIONS`` qualify (system transitions
    happen through approval and cancel), only while the One Pager is
    ``Approved``, and Deprecate must be ``confirmed``. The version does not
    change; one change-log entry is written.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user is not Owner/SME of the One Pager.
        InvalidTransitionError: The transition does not exist, is a system
            transition, or its status guards fail.
        ConfirmationRequiredError: A destructive transition was not confirmed.
        TransitionError: Storing failed; nothing changed.

    """
    row = data_access.get_one_pager_status_row(one_pager_id)
    if row is None:
        msg = f"One Pager {one_pager_id} not found."
        raise NotFoundError(msg)
    rule = get_rule(DP_STATUS_FIELD, row.data_product_status, to_status)
    if Actor.OWNER_SME not in rule.actors:
        msg = f"{rule.label} happens automatically and cannot be chosen."
        raise InvalidTransitionError(msg)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    if not owner_or_sme:
        log_permission_denied(
            "change_data_product_status", user=user.initials, one_pager_id=one_pager_id
        )
        msg = "Only the Owner or an SME can change the Data Product status."
        raise PermissionDeniedError(msg)
    failure = guard_failure(
        rule,
        actors=set(),
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        is_owner_or_sme=owner_or_sme,
    )
    if failure:
        raise InvalidTransitionError(failure)
    if rule.requires_confirmation and not confirmed:
        msg = f"Confirm that you want to {rule.label.lower()} this Data Product."
        raise ConfirmationRequiredError(msg)
    return apply_transitions(data_access, row, [rule], user, now=now)


# ============================================================================
# Review decisions (Backend_Design.md §2, §13)
# ============================================================================

MAX_COMMENT_LENGTH = 2000
REJECT_COMMENT_REQUIRED = "Explain why the One Pager is rejected."


class CommentRequiredError(ValueError):
    """A mandatory comment (the reason for a Reject) is missing."""


def _check_review_decision(  # noqa: PLR0913 - the context of one review decision
    data_access: DataAccess,
    one_pager_id: str,
    to_status: str,
    user: CurrentUser,
    roles: Collection[Actor],
    action: str,
) -> tuple[OnePagerStatusRow, TransitionRule]:
    """Load the row and check an Approve / Reject may be made by this user.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user is not an Approver, or is Owner/SME of
            this One Pager (segregation of duties, Architecture.md §4).
        InvalidTransitionError: The One Pager is not ``In Review``.

    """
    row = data_access.get_one_pager_status_row(one_pager_id)
    if row is None:
        msg = f"One Pager {one_pager_id} not found."
        raise NotFoundError(msg)
    if Actor.APPROVER not in roles:
        log_permission_denied(action, user=user.initials, one_pager_id=one_pager_id)
        msg = "Only an Approver can review this One Pager."
        raise PermissionDeniedError(msg)
    if row.one_pager_status != IN_REVIEW:
        msg = f"The One Pager is {row.one_pager_status}, not {IN_REVIEW}."
        raise InvalidTransitionError(msg)
    rule = get_rule(OP_STATUS_FIELD, row.one_pager_status, to_status)
    owner_or_sme = is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id))
    failure = guard_failure(
        rule,
        actors=set(roles),
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        is_owner_or_sme=owner_or_sme,
    )
    if failure and owner_or_sme and rule.segregation_of_duties:
        log_permission_denied(action, user=user.initials, one_pager_id=one_pager_id)
        raise PermissionDeniedError(failure)
    if failure:
        raise InvalidTransitionError(failure)
    return row, rule


def reject_one_pager(  # noqa: PLR0913 - every argument is part of the action
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    comment: str,
    *,
    roles: Collection[Actor],
    now: datetime | None = None,
) -> OnePagerStatusRow:
    """Reject a One Pager: ``In Review`` → ``Draft``, with a mandatory comment.

    Only an Approver who is not Owner/SME of the One Pager may reject it. The
    comment is stored in ``review_comments`` (document level, unresolved) and
    appended to the change-log summary (Requirements_and_Scope.md §7). The
    version does not change. Write order: the comment first, then the status
    change with its change-log entry; if the status change fails the comment
    is removed again, so a failed Reject changes nothing.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: Not an Approver, or Owner/SME of this One Pager.
        InvalidTransitionError: The One Pager is not ``In Review``.
        CommentRequiredError: The comment is empty.
        TransitionError: Storing failed; nothing changed.

    """
    now = now or datetime.now(UTC)
    row, rule = _check_review_decision(
        data_access, one_pager_id, DRAFT, user, roles, "reject"
    )
    comment = sanitize_text(comment)[:MAX_COMMENT_LENGTH]
    if rule.requires_comment and not comment:
        raise CommentRequiredError(REJECT_COMMENT_REQUIRED)

    review_comment = ReviewComment(
        id=0,
        one_pager_id=one_pager_id,
        version=row.version,
        section=None,
        reviewer_initials=user.initials,
        reviewer_name=user.display_name,
        comment=comment,
        resolved=False,
        created_at=now,
    )
    try:
        data_access.add_review_comment(review_comment)
    except Exception as e:
        logger.exception(f"Storing the reject comment of {one_pager_id} failed")
        log_event(
            rule.action,
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="add_review_comment",
        )
        raise TransitionError(TRANSITION_FAILED_MESSAGE) from e

    try:
        return apply_transitions(
            data_access, row, [rule], user, now=now, note=comment, reviewed=True
        )
    except Exception:
        try:
            data_access.delete_review_comment(review_comment)
        except Exception:
            logger.exception(f"Removing the reject comment of {one_pager_id} failed")
            log_event(
                rule.action,
                Outcome.COMPENSATION_FAILED,
                user=user.initials,
                one_pager_id=one_pager_id,
            )
        raise
