"""What every page needs: the session's services and user, and common states.

``app.py`` sets the services and the signed-in user in ``st.session_state``
before any page runs (it refuses users it does not recognise). Pages read them
through these helpers, and show flash messages and load errors the same way.
"""

from datetime import datetime
from typing import NoReturn

import streamlit as st

from adapters.navigation import open_help
from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.base import DataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser
from onepagerapp.state_machine import Actor
from onepagerapp.timeutils import utc_label

SERVICES_MISSING_MESSAGE = "Services not initialized. Please refresh the page."
ALERT_ICON = ":material/warning:"


def require_data_access() -> DataAccess:
    """Return the session's data access; stop the page when services are missing."""
    if "data_access" not in st.session_state:
        st.error(SERVICES_MISSING_MESSAGE)
        st.stop()
    data_access: DataAccess = st.session_state.data_access
    return data_access


def require_document_store() -> OnePagerDocumentStore:
    """Return the session's document store; stop the page if services are missing."""
    if "document_store" not in st.session_state:
        st.error(SERVICES_MISSING_MESSAGE)
        st.stop()
    document_store: OnePagerDocumentStore = st.session_state.document_store
    return document_store


def current_user() -> CurrentUser | None:
    """Return the signed-in user (resolved once per session by app.py), or None."""
    user: CurrentUser | None = st.session_state.get("current_user_info")
    return user


def signed_in_user() -> CurrentUser:
    """Return the signed-in user, for pages whose services need a ``CurrentUser``.

    Falls back to an unrecognised user (no initials), whom every service
    refuses, should the session have no user.
    """
    return current_user() or resolve_current_user(
        st.session_state.get("current_user", "unknown"), st.session_state.config
    )


def current_initials() -> str | None:
    """Return the initials of the signed-in user; None if nobody is recognised."""
    user = current_user()
    return (user.initials or None) if user else None


def current_roles() -> frozenset[Actor]:
    """Return the roles in effect: the group roles, or an Admin's "View as"."""
    roles: frozenset[Actor] = st.session_state.get("current_user_roles", frozenset())
    return roles


def page_header(title: str, subtitle: str, help_topic: str | None = None) -> None:
    """Page title with a one-line subtitle in gray underneath (as on Help).

    With ``help_topic``, a **? Help** button next to the title opens the Help
    page on the guide for this page.
    """
    if help_topic is None:
        st.title(title)
        st.caption(subtitle)
        return
    title_col, help_col = st.columns([8, 1], vertical_alignment="center")
    title_col.title(title)
    with help_col:
        help_button(help_topic)
    st.caption(subtitle)


def help_button(help_topic: str) -> None:
    """**Help** button that opens the Help page on the guide for ``help_topic``."""
    if st.button(
        ":material/help: Help",
        key=f"help_link_{help_topic}",
        help="How this page works",
        use_container_width=True,
    ):
        open_help(help_topic)


def set_flash(key: str, message: str) -> None:
    """Queue a success message for the next run (``st.rerun`` discards this one)."""
    st.session_state[key] = message


def show_flash(key: str) -> None:
    """Show (once) the success message queued under ``key``, as a toast."""
    flash = st.session_state.pop(key, None)
    if flash:
        st.toast(flash, icon=":material/check_circle:")


def render_retry_banner(message: str, key: str) -> bool:
    """Show a user-friendly error banner with Retry; True when Retry was clicked.

    No exception text or internals are shown; callers log the details.
    """
    st.error(message, icon=ALERT_ICON)
    return st.button("Retry", key=key)


def render_error_state(message: str, key: str) -> NoReturn:
    """Show the error banner with a Retry button, then stop the page.

    Args:
        message: Friendly message for the banner.
        key: Unique widget key for the Retry button.

    """
    if render_retry_banner(message, key):
        st.rerun()
    st.stop()


def timestamp_label(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a timestamp in UTC ("2026-09-19 10:15 UTC"); "-" when unset."""
    return "-" if value is None else utc_label(value, fmt)
