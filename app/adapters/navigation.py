"""Page scripts and the session keys pages use to hand over to each other.

Pages never pass state through query parameters: ``st.switch_page`` clears
them. The target page reads what it needs from ``st.session_state`` instead.
"""

import streamlit as st

# Page scripts, relative to app/ (``st.navigation`` and ``st.switch_page``).
REGISTRY_PAGE = "views/registry.py"
PREVIEW_PAGE = "views/preview.py"
EDITOR_PAGE = "views/editor.py"
REVIEW_PAGE = "views/review.py"
USE_CASES_PAGE = "views/use_cases.py"
HELP_PAGE = "views/help.py"
ADMIN_PAGE = "views/admin.py"

# The One Pager the Preview page shows.
PREVIEW_ID_KEY = "preview_one_pager_id"
# Set when a page opens a One Pager in Preview, so the app shell keeps the
# selection; opening Preview from the sidebar starts without one.
PREVIEW_OPENED_KEY = "preview_opened"
# Title of the page shown on the previous run (app shell).
LAST_PAGE_KEY = "last_page_title"
# One-time confirmation shown at the top of the Preview page.
PREVIEW_FLASH_KEY = "preview_flash"
# The One Pager the Approver opened from the Review queue (UI_Design.md §4.3).
REVIEW_MODE_KEY = "preview_review_mode"
# "create" or "edit": what the Editor page shows.
EDITOR_MODE_KEY = "editor_mode"
# The One Pager the Editor edits (edit mode).
EDITOR_ID_KEY = "editor_one_pager_id"


def open_preview(one_pager_id: str, flash: str | None = None) -> None:
    """Switch to the Preview page of a One Pager, optionally with a confirmation."""
    st.session_state[PREVIEW_ID_KEY] = one_pager_id
    st.session_state[PREVIEW_OPENED_KEY] = True
    if flash is not None:
        st.session_state[PREVIEW_FLASH_KEY] = flash
    st.switch_page(PREVIEW_PAGE)


def reset_preview_on_sidebar_open(page_title: str) -> None:
    """Clear the Preview selection when Preview is opened from the sidebar.

    Preview then shows no One Pager by default, only the way to the Registry
    (UI_Design.md §4.4). The selection is kept when a page opened a One Pager
    (``open_preview``), on reruns of the Preview page itself, and on the first
    run of a session, so a refresh or a deep link (``?one_pager_id=``) still
    shows it.
    """
    previous = st.session_state.get(LAST_PAGE_KEY)
    st.session_state[LAST_PAGE_KEY] = page_title
    opened = st.session_state.pop(PREVIEW_OPENED_KEY, False)
    if page_title != "Preview" or previous in (None, "Preview") or opened:
        return
    st.session_state.pop(PREVIEW_ID_KEY, None)
    st.session_state.pop(REVIEW_MODE_KEY, None)
    st.query_params.pop("one_pager_id", None)


def open_editor(mode: str) -> None:
    """Switch to the Editor page in ``mode`` ("create" or "edit")."""
    st.session_state[EDITOR_MODE_KEY] = mode
    st.switch_page(EDITOR_PAGE)


def open_in_editor(one_pager_id: str) -> None:
    """Switch to the Editor page in edit mode for this One Pager."""
    st.session_state[EDITOR_ID_KEY] = one_pager_id
    open_editor("edit")


def go_to_registry() -> None:
    """Switch to the Registry page."""
    st.switch_page(REGISTRY_PAGE)
