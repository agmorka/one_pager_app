"""One Pager Application — Streamlit entry point.

Bootstraps shared services, sets up global error boundary,
and serves as the composition root for the application.
"""

import logging
from collections.abc import Mapping
from pathlib import Path

import streamlit as st

from adapters.edit_mode import navigation_guard
from adapters.theme import apply_theme, environment_badge
from onepagerapp.auth import resolve_current_user
from onepagerapp.config import AppConfig
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access import create_data_access
from onepagerapp.data_access.factory import create_document_store

logger = logging.getLogger(__name__)


def get_logged_user(data_access: DataAccess, headers: Mapping[str, str]) -> str:
    """Resolve the current user with priority chain: headers > database.

    In Databricks Apps: headers are trusted (set by proxy) and instant (no DB query).
    Fallback to database for local-integration and mock modes where headers unavailable.

    Priority order:
    1. x-forwarded-preferred-username (Databricks Apps - fastest, trusted)
    2. x-forwarded-email (Databricks Apps - fallback header)
    3. data_access.get_current_user() (Database query - used locally)
    """
    # Try headers first: instant, no database round-trip, trusted proxy source
    proxied_user = (
        headers.get("x-forwarded-preferred-username")
        or headers.get("x-forwarded-email")
    )
    if proxied_user:
        return proxied_user

    # Fallback to database for local development and non-Apps environments
    return data_access.get_current_user()


def init_services() -> None:
    """Instantiate shared services once and store them in session state."""
    if (
        "services_initialized" in st.session_state
        and st.session_state.services_initialized
    ):
        return

    config = AppConfig.from_env()
    document_store = create_document_store(config)
    data_access = create_data_access(config, document_store)

    st.session_state.config = config
    st.session_state.document_store = document_store
    st.session_state.data_access = data_access
    st.session_state.services_initialized = True


def resolve_user() -> None:
    """Resolve the current user once per session, before any page runs.

    Pages read ``st.session_state.current_user`` (raw username) and
    ``st.session_state.current_user_info`` (CurrentUser with initials).
    """
    if st.session_state.get("current_user_info") is not None:
        return
    try:
        username = get_logged_user(
            st.session_state.data_access,
            st.context.headers,
        )
    except Exception:
        logger.exception("Failed to retrieve current user")
        return
    st.session_state.current_user = username
    st.session_state.current_user_info = resolve_current_user(username)


def main() -> None:
    """Application entry point with global error boundary."""
    st.set_page_config(
        page_title="One Pager App",
        page_icon="📋",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    logo_path = (
        Path(__file__).parent / "assets" / "BEC_FINANCIAL_TECHNOLOGIES_LOGO_RGB.png"
    )
    with st.sidebar:
        col1, col2 = st.columns(2)
        col1.image(str(logo_path), use_column_width=True)

    pg = st.navigation(
        [
            st.Page("views/registry.py", title="Registry", default=True),
            st.Page("views/preview.py", title="Preview"),
            st.Page("views/editor.py", title="Editor"),
            st.Page("views/use_cases.py", title="Use Cases"),
        ]
    )

    apply_theme()

    try:
        init_services()
    except Exception:
        logger.exception("Failed to initialize services")
        st.error(
            "Failed to connect to backend services. Please try refreshing the page."
        )
        st.stop()

    resolve_user()

    # Rendered before pg.run() so it stays visible when a page calls st.stop().
    with st.sidebar:
        config: AppConfig = st.session_state.config
        badge = environment_badge(config.environment.value)
        mode = " · mock data" if config.is_mock else ""
        st.markdown(f"Environment: {badge}{mode}", unsafe_allow_html=True)
        st.divider()
        user_name = st.session_state.get("current_user") or "unavailable"
        st.caption(f"Logged user: {user_name}")

    if st.session_state.get("current_user_info") is not None:
        navigation_guard(
            pg.title, st.session_state.data_access, st.session_state.current_user_info
        )

    pg.run()

if __name__ == "__main__":
    main()
