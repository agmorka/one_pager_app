"""Workflow actions triggered from the Preview page.

Each handler calls the workflow service and turns its outcome into what the
page shows: a flash message for the next run on success, or a user-facing
error (never internals, UI_Design.md §5).
"""

import logging

import streamlit as st

from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import CurrentUser
from onepagerapp.permissions import PermissionDeniedError
from onepagerapp.state_machine import InvalidTransitionError
from onepagerapp.workflow import (
    TRANSITION_FAILED_MESSAGE,
    ConfirmationRequiredError,
    TransitionError,
    cancel_one_pager,
    change_data_product_status,
)

logger = logging.getLogger(__name__)

FLASH_KEY = "preview_flash"


def cancel_and_report(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser, reason: str
) -> str | None:
    """Cancel the One Pager; return a user-facing error, or None on success."""
    try:
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
