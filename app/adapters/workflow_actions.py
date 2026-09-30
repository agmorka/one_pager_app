"""Workflow actions triggered from the Preview page.

Each handler calls the workflow service and turns its outcome into what the
page shows: a flash message for the next run on success, or a user-facing
error (never internals, UI_Design.md §5).
"""

import logging
from collections.abc import Collection

import streamlit as st

from adapters.cache import writes_data
from onepagerapp.data_access.base import DataAccess, NotFoundError
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.export import EXPORT_FAILED_MESSAGE, PdfExport, export_one_pager_pdf
from onepagerapp.models import CurrentUser
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.review import (
    COMMENT_FAILED_MESSAGE,
    CommentError,
    add_review_comment,
    resolve_review_comment,
)
from onepagerapp.state_machine import Actor, InvalidTransitionError
from onepagerapp.workflow import (
    TRANSITION_FAILED_MESSAGE,
    CommentRequiredError,
    ConfirmationRequiredError,
    TransitionError,
    approve_one_pager,
    cancel_one_pager,
    change_data_product_status,
    reject_one_pager,
    start_update,
)

logger = logging.getLogger(__name__)

FLASH_KEY = "preview_flash"
REVIEW_MODE_KEY = "preview_review_mode"


def cancel_and_report(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser, reason: str
) -> str | None:
    """Cancel the One Pager; return a user-facing error, or None on success."""
    try:
        with writes_data():
            cancel_one_pager(data_access, one_pager_id, user, reason=reason)
    except (PermissionDeniedError, InvalidTransitionError, TransitionError) as e:
        return str(e)
    except Exception:
        logger.exception(f"Failed to cancel {one_pager_id}")
        return TRANSITION_FAILED_MESSAGE
    st.session_state[FLASH_KEY] = f"{one_pager_id} was cancelled."
    return None


def change_dp_status_and_report(
    data_access: DataAccess,
    one_pager_id: str,
    to_status: str,
    user: CurrentUser,
    *,
    confirmed: bool,
) -> str | None:
    """Change the Data Product status; return a user-facing error or None."""
    try:
        with writes_data():
            row = change_data_product_status(
                data_access, one_pager_id, to_status, user, confirmed=confirmed
            )
    except (
        PermissionDeniedError,
        InvalidTransitionError,
        ConfirmationRequiredError,
        TransitionError,
    ) as e:
        return str(e)
    except Exception:
        logger.exception(f"Failed to change the DP status of {one_pager_id}")
        return TRANSITION_FAILED_MESSAGE
    st.session_state[FLASH_KEY] = (
        f"Data Product status changed to {row.data_product_status}."
    )
    return None


def reject_and_report(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    comment: str,
    roles: Collection[Actor],
) -> str | None:
    """Reject the One Pager; return a user-facing error, or None on success."""
    try:
        with writes_data():
            reject_one_pager(data_access, one_pager_id, user, comment, roles=roles)
    except (
        PermissionDeniedError,
        InvalidTransitionError,
        CommentRequiredError,
        TransitionError,
    ) as e:
        return str(e)
    except Exception:
        logger.exception(f"Failed to reject {one_pager_id}")
        return TRANSITION_FAILED_MESSAGE
    st.session_state.pop(REVIEW_MODE_KEY, None)
    st.session_state[FLASH_KEY] = (
        f"{one_pager_id} was rejected and is back in Draft for its Owner."
    )
    return None


def approve_and_report(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    one_pager_id: str,
    user: CurrentUser,
    roles: Collection[Actor],
) -> str | None:
    """Approve the One Pager; return a user-facing error, or None on success."""
    try:
        with writes_data():
            row = approve_one_pager(
                data_access, document_store, one_pager_id, user, roles=roles
            )
    except (PermissionDeniedError, InvalidTransitionError, TransitionError) as e:
        return str(e)
    except Exception:
        logger.exception(f"Failed to approve {one_pager_id}")
        return TRANSITION_FAILED_MESSAGE
    st.session_state.pop(REVIEW_MODE_KEY, None)
    st.session_state[FLASH_KEY] = (
        f"{one_pager_id} was approved as v{row.version}. The Data Product is "
        f"now {row.data_product_status}."
    )
    return None


def add_comment_and_report(  # noqa: PLR0913 - the parts of one comment
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    section: str | None,
    comment: str,
    roles: Collection[Actor],
) -> str | None:
    """Add an Approver's review comment; return a user-facing error or None."""
    try:
        add_review_comment(
            data_access, one_pager_id, user, section, comment, roles=roles
        )
    except (
        PermissionDeniedError,
        InvalidTransitionError,
        ValueError,
        CommentError,
    ) as e:
        return str(e)
    except Exception:
        logger.exception(f"Failed to add a review comment to {one_pager_id}")
        return COMMENT_FAILED_MESSAGE
    st.session_state[FLASH_KEY] = "Your review comment was added."
    return None


def resolve_comment_and_report(
    data_access: DataAccess, one_pager_id: str, comment_id: int, user: CurrentUser
) -> str | None:
    """Mark a review comment as resolved; return a user-facing error or None."""
    try:
        resolve_review_comment(data_access, one_pager_id, comment_id, user)
    except (PermissionDeniedError, CommentError) as e:
        return str(e)
    except Exception:
        logger.exception(f"Failed to resolve comment {comment_id} of {one_pager_id}")
        return COMMENT_FAILED_MESSAGE
    return None


def update_and_report(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser
) -> str | None:
    """Start an update of an approved One Pager; return an error or None."""
    try:
        with writes_data():
            start_update(data_access, one_pager_id, user, confirmed=True)
    except (
        PermissionDeniedError,
        InvalidTransitionError,
        ConfirmationRequiredError,
        TransitionError,
    ) as e:
        return str(e)
    except Exception:
        logger.exception(f"Failed to start an update of {one_pager_id}")
        return TRANSITION_FAILED_MESSAGE
    st.session_state[FLASH_KEY] = (
        f"{one_pager_id} is now in Draft Update. Choose Edit to change it."
    )
    return None


def export_pdf_and_report(
    data_access: DataAccess,
    one_pager_id: str,
    user: CurrentUser,
    status_colors: dict[str, str],
) -> tuple[PdfExport | None, str | None]:
    """Render the One Pager as a PDF; return it, or a user-facing error."""
    try:
        export = export_one_pager_pdf(
            data_access, one_pager_id, user, status_colors=status_colors
        )
    except (PermissionDeniedError, NotFoundError) as e:
        return None, str(e)
    except Exception:
        logger.exception(f"Failed to export {one_pager_id} as PDF")
        return None, EXPORT_FAILED_MESSAGE
    return export, None
