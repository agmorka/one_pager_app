"""Editing existing One Pagers (Requirements_and_Scope.md §5, Backend_Design.md §7).

``open_for_edit`` is what the Editor calls when it opens an existing One
Pager: it checks that the user may edit it (Owner/SME, status ``Draft`` or
``Draft Update``), acquires the edit lock and reads the current document fresh
from the document store (editor content is never cached, UI_Design.md §6).

``save_draft`` is **Save Draft**: lenient validation, a required change
summary, a MINOR version bump, a new immutable YAML version file, the
``one_pager_status`` update and a ``content_save`` change-log entry.

Pure Python — no Streamlit.
"""

import copy
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from onepagerapp.audit import Outcome, log_event
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.locking import (
    DEFAULT_LOCK_TTL,
    LockResult,
    acquire_lock,
    get_active_lock,
    is_held_by,
)
from onepagerapp.models import (
    ChangeLogEntry,
    CurrentUser,
    OnePagerDocument,
    OnePagerStatusRow,
    ValidationError,
)
from onepagerapp.permissions import PermissionDeniedError, check_can_edit
from onepagerapp.validation import (
    normalize_document,
    sanitize_text,
    validate_edit_basics,
    validate_lenient,
)

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


class DocumentMissingError(RuntimeError):
    """The status row points to a document version that cannot be found."""


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


@dataclass
class EditSession:
    """What the Editor needs to edit an existing One Pager.

    Attributes:
        status_row: The ``one_pager_status`` row when the editor opened.
        document: The document of ``status_row.version`` as stored; the editor
            works on a copy of it.
        lock: Outcome of the lock acquisition. When ``lock.acquired`` is False
            the editor shows ``lock.message`` and does not allow editing.

    """

    status_row: OnePagerStatusRow
    document: OnePagerDocument
    lock: LockResult

    @property
    def one_pager_id(self) -> str:
        return self.status_row.one_pager_id


def load_for_edit(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> tuple[OnePagerStatusRow, OnePagerDocument]:
    """Read the status row and current document after the edit permission check.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user may not edit it (logged).
        DocumentMissingError: The current version's document is missing.

    """
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
    document = data_access.read_document(one_pager_id, row.version)
    if document is None:
        logger.error(f"Document of {one_pager_id} v{row.version} is missing")
        msg = f"The document of {one_pager_id} v{row.version} could not be found."
        raise DocumentMissingError(msg)
    return row, document


def open_for_edit(  # noqa: PLR0913 - every argument is part of the edit identity
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    *,
    ttl: timedelta = DEFAULT_LOCK_TTL,
    now: datetime | None = None,
) -> EditSession:
    """Open an existing One Pager in the Editor (edit mode).

    The permission check runs before the lock is taken, so a user who may not
    edit never blocks others with a lock.

    Raises:
        NotFoundError: No One Pager with this ID.
        PermissionDeniedError: The user may not edit it.
        DocumentMissingError: The current version's document is missing.
        RuntimeError: The lock or document could not be read or written.

    """
    row, document = load_for_edit(data_access, one_pager_id, user)
    lock = acquire_lock(data_access, one_pager_id, user, session_id, ttl=ttl, now=now)
    return EditSession(status_row=row, document=document, lock=lock)


def working_copy(document: OnePagerDocument) -> OnePagerDocument:
    """Return a deep copy of a document for the editor to change freely."""
    return copy.deepcopy(document)


def bump_minor(version: str) -> str:
    """Next MINOR version (Requirements_and_Scope.md §7): 0.3.0 -> 0.4.0.

    PATCH is not used, so it is reset to 0.

    Raises:
        ValueError: If ``version`` is not MAJOR.MINOR.PATCH.

    """
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):  # noqa: PLR2004
        msg = f"Invalid version {version!r}"
        raise ValueError(msg)
    major, minor, _ = (int(p) for p in parts)
    return f"{major}.{minor + 1}.0"


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


def require_lock(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    session_id: str,
    now: datetime | None = None,
) -> None:
    """Raise ``LockNotHeldError`` unless this user and session hold the lock."""
    lock = get_active_lock(data_access, one_pager_id, now)
    if lock is None or not is_held_by(lock, user, session_id):
        log_event(
            "save_draft",
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
    started from — this makes the new version current), then the change-log
    entry. If the change log cannot be written the status row is put back, so
    a failed save changes nothing visible.

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
    require_lock(data_access, one_pager_id, user, session_id, now)

    summary = sanitize_text(summary)
    document = normalize_document(document)
    # Keep a stored value selectable even if it was deactivated since.
    errors = validate_change_summary(summary) + validate_edit_basics(
        document,
        allowed_domains=[*allowed_domains, row.business_domain],
        allowed_types=[*allowed_types, row.data_product_type],
    )
    version = bump_minor(row.version)
    saved = prepare_saved_document(document, row, version, summary, user, now)
    errors += validate_lenient(document_to_dict(saved))
    if errors:
        return SaveResult(errors=errors)

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

    try:
        data_access.append_change_log(_content_save_entry(new_row, summary, user, now))
    except Exception as e:
        logger.exception(f"Change log of {one_pager_id} v{version} failed")
        _save_failed(user, one_pager_id, "append_change_log")
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
