"""Save Draft (Backend_Design.md §7).

Lenient validation, a required change summary, a MINOR version bump, a new
immutable YAML version file, the ``one_pager_status`` update and a
``content_save`` change-log entry. A failed save is rolled back, so it never
changes anything visible.
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from onepagerapp.audit import Outcome, log_event
from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.editing.authorized_users import (
    authorized_users_from_document,
    sync_authorized_users,
)
from onepagerapp.editing.use_case_links import (
    sync_use_case_references,
    validate_new_use_case_links,
)
from onepagerapp.locking import get_active_lock, is_held_by
from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    CurrentUser,
    OnePagerDocument,
    OnePagerStatusRow,
    ValidationError,
)
from onepagerapp.permissions import (
    PermissionDeniedError,
    check_can_edit,
    require_identity,
)
from onepagerapp.validation import (
    normalize_document,
    sanitize_text,
    validate_edit_basics,
    validate_lenient,
    validate_strict,
)
from onepagerapp.versioning import bump_minor

logger = logging.getLogger(__name__)


MAX_SUMMARY_LENGTH = 500
SAVE_FAILED_MESSAGE = "Save failed — your changes are preserved, please retry."
SAVE_CONFLICT_MESSAGE = (
    "This One Pager was changed by someone else since you opened it. Your "
    "changes are preserved; close the editor and open it again to continue."
)
LOCK_NOT_HELD_MESSAGE = (
    "You no longer hold the edit lock for this One Pager, so it cannot be saved."
)


class SaveError(RuntimeError):
    """A save could not be stored. The message is safe to show to users."""


class LockNotHeldError(PermissionDeniedError):
    """The caller's session does not hold the edit lock of the One Pager."""


@dataclass
class SaveResult:
    """Outcome of ``save_draft``.

    Either ``version`` is set (saved) or ``errors`` is non-empty (validation
    failed, nothing was written).
    """

    version: str | None = None
    document: OnePagerDocument | None = None
    status_row: OnePagerStatusRow | None = None
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.version is not None and not self.errors


def validate_change_summary(summary: str) -> list[ValidationError]:
    """Check the change summary, required on Save Draft (UI_Design.md §4.2)."""
    if not summary:
        return [ValidationError("changeSummary", "Describe what you changed.")]
    if len(summary) > MAX_SUMMARY_LENGTH:
        return [
            ValidationError(
                "changeSummary",
                f"Must be at most {MAX_SUMMARY_LENGTH} characters.",
            )
        ]
    return []


def require_lock(  # noqa: PLR0913 - the lock identity plus the audited action
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    now: datetime | None = None,
    action: str = "save_draft",
) -> None:
    """Raise ``LockNotHeldError`` unless this user and session hold the lock."""
    lock = get_active_lock(data_access, one_pager_id, now)
    if lock is None or not is_held_by(lock, user, session_id):
        log_event(
            action,
            Outcome.EDIT_REJECTED,
            user=user.initials,
            one_pager_id=one_pager_id,
            reason="lock_not_held",
        )
        raise LockNotHeldError(LOCK_NOT_HELD_MESSAGE)


def prepare_saved_document(  # noqa: PLR0913 - every argument ends up in the file
    document: OnePagerDocument,
    row: OnePagerStatusRow,
    version: str,
    summary: str,
    user: CurrentUser,
    now: datetime,
) -> OnePagerDocument:
    """Build the document as it is written for a content save.

    Operational fields come from Delta, which is authoritative (Data_Model.md
    §5): statuses, the registered data product and the schema version are
    copied from the status row, never from the editor. The YAML change log is
    a denormalized copy and gets the new entry appended.
    """
    timestamp = now.isoformat(timespec="seconds")
    return replace(
        document,
        structure_definition=row.structure_definition,
        data_product=row.data_product,
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        version=version,
        last_updated=timestamp,
        change_log=[
            *document.change_log,
            {
                "version": version,
                "date": timestamp,
                "author": user.display_name,
                "summary": summary,
            },
        ],
        raw_content="",
    )


def with_operational_fields(
    document: OnePagerDocument, row: OnePagerStatusRow
) -> OnePagerDocument:
    """Copy of a document whose operational fields match the status row."""
    return replace(
        document,
        structure_definition=row.structure_definition,
        data_product=row.data_product,
        one_pager_status=row.one_pager_status,
        data_product_status=row.data_product_status,
        version=row.version,
    )


def submission_issues(
    document: OnePagerDocument, row: OnePagerStatusRow
) -> list[ValidationError]:
    """Everything that blocks Submit for Review (strict tier, Backend_Design §4).

    Runs on the normalized document with its operational fields taken from
    the status row, plus the Owner/SME checks, so the editor can show what
    is left to do while the user is still editing.
    """
    document = with_operational_fields(normalize_document(document), row)
    owner_errors = [
        e
        for e in validate_edit_basics(
            document,
            allowed_domains=[document.business_domain],
            allowed_types=[document.data_product_type],
        )
        if e.field_path.startswith(("dataProductOwner", "smes"))
    ]
    strict = validate_strict(document_to_dict(document))
    seen = {(e.field_path, e.message) for e in strict}
    return strict + [e for e in owner_errors if (e.field_path, e.message) not in seen]


def _saved_status_row(
    row: OnePagerStatusRow,
    document: OnePagerDocument,
    user: CurrentUser,
    now: datetime,
) -> OnePagerStatusRow:
    return replace(
        row,
        product_name=document.product_name,
        business_domain=document.business_domain,
        data_product_type=document.data_product_type,
        version=document.version,
        owner_name=document.owner_name,
        owner_initials=document.owner_initials,
        owner_email=document.owner_email,
        owner_team=document.owner_team,
        last_updated_at=now,
        last_updated_by=user.initials,
    )


def _content_save_entry(
    row: OnePagerStatusRow, summary: str, user: CurrentUser, now: datetime
) -> ChangeLogEntry:
    return ChangeLogEntry(
        id=0,
        one_pager_id=row.one_pager_id,
        version=row.version,
        event_type="content_save",
        author_initials=user.initials,
        author_name=user.display_name,
        summary=summary,
        created_at=now,
    )


def _save_failed(user: CurrentUser, one_pager_id: str, step: str) -> None:
    log_event(
        "save_draft",
        Outcome.FAILED,
        user=user.initials,
        one_pager_id=one_pager_id,
        step=step,
    )


def _write_version(  # noqa: PLR0913 - the parts of one save
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    row: OnePagerStatusRow,
    saved: OnePagerDocument,
    user: CurrentUser,
    now: datetime,
) -> OnePagerStatusRow:
    """Write the new version file, then point the status row at it.

    Raises:
        SaveError: Nothing was changed (an unreferenced file is discarded).

    """
    one_pager_id, version = row.one_pager_id, saved.version
    # A file for this version can only be left over from a save that failed
    # before the status row pointed to it: it is not part of the history.
    document_store.discard_unreferenced(one_pager_id, version)
    try:
        document_store.write(one_pager_id, saved, version)
    except RuntimeError as e:
        logger.exception(f"Writing {one_pager_id} v{version} failed")
        _save_failed(user, one_pager_id, "write_document")
        raise SaveError(SAVE_FAILED_MESSAGE) from e

    new_row = _saved_status_row(row, saved, user, now)
    try:
        updated = data_access.update_one_pager_status(
            new_row, expected_version=row.version, expected_status=row.one_pager_status
        )
    except Exception as e:
        logger.exception(f"Updating the status row of {one_pager_id} failed")
        _save_failed(user, one_pager_id, "update_one_pager_status")
        document_store.discard_unreferenced(one_pager_id, version)
        raise SaveError(SAVE_FAILED_MESSAGE) from e
    if not updated:
        _save_failed(user, one_pager_id, "status_row_changed")
        document_store.discard_unreferenced(one_pager_id, version)
        raise SaveError(SAVE_CONFLICT_MESSAGE)

    return new_row


def save_draft(  # noqa: PLR0913 - every argument is needed to save
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    document: OnePagerDocument,
    summary: str,
    user: CurrentUser,
    session_id: str,
    *,
    allowed_domains: list[str],
    allowed_types: list[str],
    now: datetime | None = None,
) -> SaveResult:
    """Save the editor's working copy as a new MINOR version (Backend_Design §7).

    The permission check uses the Owner/SME list *before* the edit, so an
    Owner can hand the One Pager over to someone else (Backend_Design §11).

    Write order: the new YAML version file first (nothing references it yet),
    then the ``one_pager_status`` row (conditional on the version the editor
    started from — this makes the new version current), then the sync of
    ``use_case_references`` with the linked Use Cases (Backend_Design §9) and
    of ``one_pager_authorized_users`` with the Owner/SMEs (§11), then the
    change-log entry. If a later step fails the earlier ones are put
    back, so a failed save changes nothing visible.

    Args:
        data_access: Tabular data access (Delta or mock).
        document_store: YAML document store.
        one_pager_id: The One Pager being edited.
        document: The editor's working copy.
        summary: The Owner's description of the change (change-log summary).
        user: The saving user.
        session_id: The editor's session; it must hold the edit lock.
        allowed_domains: Active ``ref_business_domains`` values.
        allowed_types: Active ``ref_data_product_types`` values.
        now: Save instant (defaults to the current UTC time).

    Returns:
        SaveResult with the new version, or with validation errors.

    Raises:
        NotFoundError: The One Pager does not exist.
        PermissionDeniedError: The user may not edit it (logged), or
            ``LockNotHeldError`` when their session lost the edit lock.
        SaveError: Storing failed; nothing visible was changed.

    """
    require_identity(user, "save_draft", one_pager_id)
    now = now or datetime.now(UTC)
    row = require_status_row(data_access, one_pager_id)
    # Checked against the list *before* the edit (Backend_Design §11).
    authorized_before = data_access.get_authorized_users(one_pager_id)
    check_can_edit(user, one_pager_id, row.one_pager_status, authorized_before)
    require_lock(data_access, one_pager_id, user, session_id, now)

    summary = sanitize_text(summary)
    document = normalize_document(document)
    # Keep a stored value selectable even if it was deactivated since.
    errors = validate_change_summary(summary) + validate_edit_basics(
        document,
        allowed_domains=[*allowed_domains, row.business_domain],
        allowed_types=[*allowed_types, row.data_product_type],
    )
    linked_before = data_access.get_linked_use_case_ids(one_pager_id)
    errors += validate_new_use_case_links(
        data_access, linked_before, document.use_case_ids
    )
    version = bump_minor(row.version)
    saved = prepare_saved_document(document, row, version, summary, user, now)
    errors += validate_lenient(document_to_dict(saved))
    if errors:
        return SaveResult(errors=errors)

    new_row = _write_version(data_access, document_store, row, saved, user, now)

    # Use Case links first: linking re-checks that the user is Owner/SME,
    # which a hand-over in this same save would otherwise revoke.
    step = "sync_use_case_references"
    try:
        sync_use_case_references(
            data_access, one_pager_id, linked_before, saved.use_case_ids, user
        )
        step = "sync_authorized_users"
        sync_authorized_users(
            data_access,
            authorized_before,
            authorized_users_from_document(one_pager_id, saved),
        )
        step = "append_change_log"
        data_access.append_change_log(_content_save_entry(new_row, summary, user, now))
    except Exception as e:
        logger.exception(f"Save of {one_pager_id} v{version} failed at {step}")
        _save_failed(user, one_pager_id, step)
        _restore_authorized_users(data_access, one_pager_id, authorized_before, user)
        _restore_use_case_references(data_access, one_pager_id, linked_before, user)
        if _restore_status_row(data_access, row, new_row, user):
            document_store.discard_unreferenced(one_pager_id, version)
        raise SaveError(SAVE_FAILED_MESSAGE) from e

    log_event(
        "save_draft",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        version=version,
    )
    return SaveResult(version=version, document=saved, status_row=new_row)


def _restore_authorized_users(
    data_access: DataAccess,
    one_pager_id: str,
    previous: list[AuthorizedUser],
    user: CurrentUser,
) -> None:
    """Best-effort: put the authorized users back as they were before a save."""
    try:
        sync_authorized_users(
            data_access, data_access.get_authorized_users(one_pager_id), previous
        )
    except Exception:
        logger.exception(f"Restoring the authorized users of {one_pager_id} failed")
        log_event(
            "save_draft",
            Outcome.COMPENSATION_FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="restore_authorized_users",
        )


def _restore_use_case_references(
    data_access: DataAccess, one_pager_id: str, previous: list[str], user: CurrentUser
) -> None:
    """Best-effort: put the Use Case links back as they were before a save."""
    try:
        current = set(data_access.get_linked_use_case_ids(one_pager_id))
        for use_case_id in current - set(previous):
            data_access.remove_use_case_reference(one_pager_id, use_case_id)
        for use_case_id in set(previous) - current:
            data_access.add_use_case_reference(one_pager_id, use_case_id)
    except Exception:
        logger.exception(f"Restoring the Use Case links of {one_pager_id} failed")
        log_event(
            "save_draft",
            Outcome.COMPENSATION_FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="restore_use_case_references",
        )


def _restore_status_row(
    data_access: DataAccess,
    previous: OnePagerStatusRow,
    written: OnePagerStatusRow,
    user: CurrentUser,
) -> bool:
    """Best-effort: put back the status row a failed save had already updated."""
    try:
        restored = data_access.update_one_pager_status(
            previous,
            expected_version=written.version,
            expected_status=written.one_pager_status,
        )
    except Exception:
        logger.exception(f"Restoring the status row of {previous.one_pager_id} failed")
        restored = False
    if not restored:
        log_event(
            "save_draft",
            Outcome.COMPENSATION_FAILED,
            user=user.initials,
            one_pager_id=previous.one_pager_id,
        )
    return restored
