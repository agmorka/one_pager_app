"""Page scripts and the session keys pages use to hand over to each other.

Pages never pass state through query parameters: ``st.switch_page`` clears
them. The target page reads what it needs from ``st.session_state`` instead.
"""

import streamlit as st

# Page scripts, relative to app/ (``st.navigation`` and ``st.switch_page``).
MY_WORK_PAGE = "views/my_work.py"
REGISTRY_PAGE = "views/registry.py"
PREVIEW_PAGE = "views/preview.py"
EDITOR_PAGE = "views/editor.py"
REVIEW_PAGE = "views/review.py"
USE_CASES_PAGE = "views/use_cases.py"
HELP_PAGE = "views/help.py"
ADMIN_PAGE = "views/admin.py"

# The One Pager the Preview page shows.
PREVIEW_ID_KEY = "preview_one_pager_id"
# One-time confirmation shown at the top of the Preview page.
PREVIEW_FLASH_KEY = "preview_flash"
# The One Pager the Approver opened from the Review queue (UI_Design.md §4.3).
REVIEW_MODE_KEY = "preview_review_mode"
# One-time confirmation shown at the top of the Review queue.
REVIEW_FLASH_KEY = "review_flash"
# The Use Case the Use Cases page shows in its details dialog.
USE_CASE_SELECTED_KEY = "uc_selected_id"
# "create" or "edit": what the Editor page shows.
EDITOR_MODE_KEY = "editor_mode"
# One-time confirmation shown when the Editor opens (kept outside the
# editor's own "edit_" state, which is cleared when a One Pager is opened).
EDITOR_FLASH_KEY = "editor_flash"
# The One Pager the Editor edits (edit mode).
EDITOR_ID_KEY = "editor_one_pager_id"


def open_preview(one_pager_id: str, flash: str | None = None) -> None:
    """Switch to the Preview page of a One Pager, optionally with a confirmation."""
    st.session_state[PREVIEW_ID_KEY] = one_pager_id
    if flash is not None:
        st.session_state[PREVIEW_FLASH_KEY] = flash
    st.switch_page(PREVIEW_PAGE)


def open_editor(mode: str) -> None:
    """Switch to the Editor page in ``mode`` ("create" or "edit")."""
    st.session_state[EDITOR_MODE_KEY] = mode
    st.switch_page(EDITOR_PAGE)


def open_in_editor(one_pager_id: str) -> None:
    """Switch to the Editor page in edit mode for this One Pager."""
    st.session_state[EDITOR_ID_KEY] = one_pager_id
    open_editor("edit")


def open_in_review_mode(one_pager_id: str) -> None:
    """Open the One Pager in Preview with the review actions (UI_Design §4.3)."""
    st.session_state[PREVIEW_ID_KEY] = one_pager_id
    st.session_state[REVIEW_MODE_KEY] = one_pager_id
    st.switch_page(PREVIEW_PAGE)


def open_use_case(use_case_id: str) -> None:
    """Switch to the Use Cases page with this Use Case's details open."""
    st.session_state[USE_CASE_SELECTED_KEY] = use_case_id
    st.switch_page(USE_CASES_PAGE)


def back_to_review_queue(flash: str | None = None) -> None:
    """Leave review mode for the Review queue, optionally with a confirmation."""
    st.session_state.pop(REVIEW_MODE_KEY, None)
    if flash is not None:
        st.session_state[REVIEW_FLASH_KEY] = flash
    st.switch_page(REVIEW_PAGE)


def go_to_registry() -> None:
    """Switch to the Registry page."""
    st.switch_page(REGISTRY_PAGE)


def go_to_my_work() -> None:
    """Switch to the My work page (the landing page)."""
    st.switch_page(MY_WORK_PAGE)
