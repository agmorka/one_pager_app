"""Opening an existing One Pager in the Editor (Backend_Design.md §6-7).

``open_for_edit`` checks that the user may edit the One Pager (Owner/SME,
status ``Draft`` or ``Draft Update``), acquires the edit lock and reads the
current document fresh from the document store (editor content is never
cached, UI_Design.md §6).
"""

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from onepagerapp.data_access.base import DataAccess, require_status_row
from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.locking import DEFAULT_LOCK_TTL, LockResult, acquire_lock
from onepagerapp.models import CurrentUser, OnePagerDocument, OnePagerStatusRow
from onepagerapp.permissions import check_can_edit, require_identity
from onepagerapp.validation import normalize_document

logger = logging.getLogger(__name__)


class DocumentMissingError(RuntimeError):
    """The status row points to a document version that cannot be found."""


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
    require_identity(user, "load_for_edit", one_pager_id)
    row = require_status_row(data_access, one_pager_id)
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


def has_unsaved_changes(saved: OnePagerDocument, working: OnePagerDocument) -> bool:
    """Whether the working copy differs from the stored document in content.

    Both are normalized first, so whitespace, HTML tags or a blank row the
    user left behind do not count as a change.
    """
    return document_to_dict(normalize_document(saved)) != document_to_dict(
        normalize_document(working)
    )
