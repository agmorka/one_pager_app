"""Editing existing One Pagers (Requirements_and_Scope.md §5, Backend_Design.md §7).

Modules:

- ``session``: ``open_for_edit``, what the Editor calls when it opens an
  existing One Pager (permission check, edit lock, fresh document).
- ``save``: ``save_draft``, the **Save Draft** action.
- ``authorized_users``: keeps ``one_pager_authorized_users`` in line with the
  document's Owner/SMEs.
- ``use_case_links``: links and unlinks Use Cases.
- ``requirements``: assigns Business Requirement IDs.

Everything is re-exported here, so callers import from ``onepagerapp.editing``.
Pure Python — no Streamlit.
"""

from onepagerapp.editing.authorized_users import (
    AuthorizedUsersDiff,
    authorized_users_from_document,
    diff_authorized_users,
    sync_authorized_users,
)
from onepagerapp.editing.requirements import assign_requirement_id
from onepagerapp.editing.save import (
    LOCK_NOT_HELD_MESSAGE,
    MAX_SUMMARY_LENGTH,
    SAVE_CONFLICT_MESSAGE,
    SAVE_FAILED_MESSAGE,
    LockNotHeldError,
    SaveError,
    SaveResult,
    prepare_saved_document,
    require_lock,
    save_draft,
    submission_issues,
    validate_change_summary,
    with_operational_fields,
)
from onepagerapp.editing.session import (
    DocumentMissingError,
    EditSession,
    has_unsaved_changes,
    load_for_edit,
    open_for_edit,
    working_copy,
)
from onepagerapp.editing.use_case_links import (
    DEPRECATED_LINK_MESSAGE,
    UNKNOWN_LINK_MESSAGE,
    link_denied_reason,
    link_use_case,
    sync_use_case_references,
    unlink_use_case,
    validate_new_use_case_links,
)
from onepagerapp.versioning import bump_minor

__all__ = [
    "DEPRECATED_LINK_MESSAGE",
    "LOCK_NOT_HELD_MESSAGE",
    "MAX_SUMMARY_LENGTH",
    "SAVE_CONFLICT_MESSAGE",
    "SAVE_FAILED_MESSAGE",
    "UNKNOWN_LINK_MESSAGE",
    "AuthorizedUsersDiff",
    "DocumentMissingError",
    "EditSession",
    "LockNotHeldError",
    "SaveError",
    "SaveResult",
    "assign_requirement_id",
    "authorized_users_from_document",
    "bump_minor",
    "diff_authorized_users",
    "has_unsaved_changes",
    "link_denied_reason",
    "link_use_case",
    "load_for_edit",
    "open_for_edit",
    "prepare_saved_document",
    "require_lock",
    "save_draft",
    "submission_issues",
    "sync_authorized_users",
    "sync_use_case_references",
    "unlink_use_case",
    "validate_change_summary",
    "validate_new_use_case_links",
    "with_operational_fields",
    "working_copy",
]
