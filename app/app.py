"""One Pager Application — Streamlit entry point.

Bootstraps shared services, sets up global error boundary,
and serves as the composition root for the application.
"""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn

import streamlit as st

from adapters.edit_mode import navigation_guard
from adapters.theme import apply_theme, environment_badge
from onepagerapp.audit import log_unrecognised_user
from onepagerapp.auth import resolve_current_user, resolve_roles
from onepagerapp.config import AppConfig, AppMode
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access import create_data_access
from onepagerapp.data_access.factory import create_document_store
from onepagerapp.models import CurrentUser
from onepagerapp.permissions import can_administer, can_review
from onepagerapp.state_machine import Actor
from onepagerapp.validation import set_initials_pattern

logger = logging.getLogger(__name__)


# Headers set by the Databricks Apps proxy for the signed-in user. They are
# the only trusted identity source in deployed mode (Architecture.md §4).
PROXY_USER_HEADERS = ("x-forwarded-preferred-username", "x-forwarded-email")


def get_logged_user(
    config: AppConfig, headers: Mapping[str, str], data_access: DataAccess | None
) -> str | None:
    """Return the signed-in username from the identity source of the app mode.

    - ``databricks``: only the Databricks Apps proxy headers. There is no
      database fallback: writes run as the service principal, so a wrong
      identity would be written into the audit columns.
    - ``local-integration``: ``SELECT current_user()`` with the CLI profile.
    - ``local-mock``: ``ONE_PAGER_APP_MOCK_USER``.

    Returns:
        The username, or None when the source provides none.

    """
    if config.APP_MODE is AppMode.DATABRICKS:
        for header in PROXY_USER_HEADERS:
            value = (headers.get(header) or "").strip()
            if value:
                return value
        return None
    if config.APP_MODE is AppMode.LOCAL_MOCK:
        return config.ONE_PAGER_APP_MOCK_USER
    if data_access is None:
        return None
    return data_access.get_current_user() or None


def init_config() -> AppConfig:
    """Load the configuration once per session and apply the initials pattern."""
    if "config" not in st.session_state:
        config = AppConfig.from_env()
        set_initials_pattern(config.initials_pattern)
        st.session_state.config = config
    return st.session_state.config


def init_services() -> None:
    """Instantiate shared services once and store them in session state."""
    if (
        "services_initialized" in st.session_state
        and st.session_state.services_initialized
    ):
        return

    config = init_config()
    document_store = create_document_store(config)
    data_access = create_data_access(config, document_store)

    st.session_state.document_store = document_store
    st.session_state.data_access = data_access
    st.session_state.services_initialized = True


def resolve_user() -> CurrentUser | None:
    """Resolve the current user once per session, before any page runs.

    Pages read ``st.session_state.current_user`` (raw username),
    ``st.session_state.current_user_info`` (CurrentUser with initials) and
    ``st.session_state.current_user_roles`` (group roles: Approver, Admin).
    These are set only for a recognised user (non-empty initials).

    Returns:
        The recognised user, or None when there is no username or it is not
        recognised (``st.session_state.unrecognised_user`` then holds the
        username, or "" when there was none).

    """
    user: CurrentUser | None = st.session_state.get("current_user_info")
    if user is not None:
        return user
    config: AppConfig = st.session_state.config
    try:
        username = get_logged_user(
            config, st.context.headers, st.session_state.get("data_access")
        )
    except Exception:
        logger.exception("Failed to retrieve current user")
        username = None
    user = resolve_current_user(username, config) if username else None
    if user is None or not user.initials:
        if "unrecognised_user" not in st.session_state:  # log once per session
            log_unrecognised_user(username)
        st.session_state.unrecognised_user = username or ""
        return None
    st.session_state.current_user = username
    st.session_state.current_user_info = user
    st.session_state.current_user_roles = resolve_roles(user, config)
    return user


def render_access_denied(username: str) -> None:
    """Page shown instead of the app to a user who is not recognised."""
    st.title("Access denied")
    if username:
        shown = username.replace("`", "'")
        st.error(
            f"Your account `{shown}` is not recognised by the One Pager App. "
            "Contact the platform team."
        )
    else:
        st.error(
            "The One Pager App could not identify your account. "
            "Contact the platform team."
        )


ROLE_LABELS = {Actor.APPROVER: "Approver", Actor.ADMIN: "Admin"}


def navigation_entries(roles: frozenset[Actor]) -> list[tuple[str, str]]:
    """(script, title) of the sidebar pages (UI §2).

    Review only for Approvers, Admin only for Admins.
    """
    entries = [
        ("views/registry.py", "Registry"),
        ("views/preview.py", "Preview"),
        ("views/editor.py", "Editor"),
    ]
    if can_review(roles):
        entries.append(("views/review.py", "Review"))
    entries.append(("views/use_cases.py", "Use Cases"))
    entries.append(("views/help.py", "Help"))
    if can_administer(roles):
        entries.append(("views/admin.py", "Admin"))
    return entries


def build_pages(roles: frozenset[Actor]) -> list:
    """The ``st.Page`` objects of ``navigation_entries``; Registry is the default."""
    return [
        st.Page(script, title=title, default=script == "views/registry.py")
        for script, title in navigation_entries(roles)
    ]


def _stop_on_service_error() -> NoReturn:
    logger.exception("Failed to initialize services")
    st.error("Failed to connect to backend services. Please try refreshing the page.")
    st.stop()


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

    apply_theme()

    try:
        config = init_config()
        if config.APP_MODE is AppMode.LOCAL_INTEGRATION:
            # The identity comes from SELECT current_user(), which needs the
            # data access; in the other modes it is created after the check.
            init_services()
    except Exception:
        _stop_on_service_error()

    # Fail closed (Architecture.md §4): no page and no data access for a user
    # who is not recognised.
    if resolve_user() is None:
        render_access_denied(st.session_state.get("unrecognised_user", ""))
        st.stop()

    try:
        init_services()
    except Exception:
        _stop_on_service_error()

    roles: frozenset[Actor] = st.session_state.get("current_user_roles", frozenset())
    pg = st.navigation(build_pages(roles))

    # Rendered before pg.run() so it stays visible when a page calls st.stop().
    with st.sidebar:
        badge = environment_badge(config.environment.value)
        mode = " · mock data" if config.is_mock else ""
        st.markdown(f"Environment: {badge}{mode}", unsafe_allow_html=True)
        st.divider()
        user_name = st.session_state.get("current_user") or "unavailable"
        st.caption(f"Logged user: {user_name}")
        role_names = [ROLE_LABELS[r] for r in ROLE_LABELS if r in roles]
        if role_names:
            st.caption(f"Role: {', '.join(role_names)}")

    navigation_guard(
        pg.title, st.session_state.data_access, st.session_state.current_user_info
    )

    pg.run()

if __name__ == "__main__":
    main()
