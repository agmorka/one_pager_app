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
from adapters.theme import apply_theme, environment_badge, role_badges
from onepagerapp.audit import log_unrecognised_user
from onepagerapp.auth import resolve_current_user, resolve_roles
from onepagerapp.config import AppConfig, AppMode, Environment
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access import create_data_access
from onepagerapp.data_access.factory import create_document_store
from onepagerapp.directory import lookup_directory_user
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
        warn_if_interim_roles(config)
        st.session_state.config = config
    return st.session_state.config


def interim_roles_notice(config: AppConfig) -> str | None:
    """Sidebar notice while roles use the interim group; None in DEV or after.

    The dedicated role groups do not exist yet, so DataPlatEng members hold
    those roles (User_Identity_And_Access_Plan.md §4.1).
    """
    roles = [INTERIM_ROLE_LABELS[r] for r in config.interim_roles]
    if not roles or config.environment is Environment.DEV:
        return None
    return f"Interim roles: DataPlatEng members act as {_join(roles)}."


def warn_if_interim_roles(config: AppConfig) -> None:
    """Log once per session that roles still use the interim group."""
    if config.interim_roles:
        logger.warning(
            "Interim role groups in use for %s: %s members hold these roles. "
            "Configure ONE_PAGER_APP_GROUP_* once the dedicated groups exist.",
            ", ".join(config.interim_roles),
            config.role_groups[config.interim_roles[0]],
        )


INTERIM_ROLE_LABELS = {
    "owner_sme": "Owner/SME",
    "approver": "Approver",
    "admin": "Admin",
}


def _join(names: list[str]) -> str:
    """Join names as "A", "A and B" or "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


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
    ``st.session_state.current_user_info`` (CurrentUser with initials and the
    name from the directory), ``st.session_state.current_user_directory``
    (the directory entry, or None) and
    ``st.session_state.current_user_roles`` (group roles, set by
    ``resolve_session_roles`` once the data access exists).
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
    # The name comes from the directory, once per session. A failed lookup is
    # logged and the initials are shown instead; it never blocks the app.
    try:
        directory_user = lookup_directory_user(config, st.context.headers)
    except Exception:
        logger.exception("Directory lookup failed; showing the initials")
        directory_user = None
    user = resolve_current_user(username, config, directory_user)
    st.session_state.current_user_directory = directory_user
    st.session_state.current_user = username
    st.session_state.current_user_info = user
    return user


def resolve_session_roles() -> frozenset[Actor]:
    """Group roles of the user, checked once per session (needs the data access).

    A role change applies from the next session. A failed check gives the
    Viewer role only (``auth.resolve_roles``).
    """
    roles: frozenset[Actor] | None = st.session_state.get("current_user_roles")
    if roles is None:
        roles = resolve_roles(
            st.session_state.current_user_info,
            st.session_state.config,
            st.session_state.data_access,
        )
        st.session_state.current_user_roles = roles
    return roles


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


def sidebar_user_label(user: CurrentUser) -> str:
    """Name and corporate initials, e.g. "Agnieszka Kępkowska (X0W)".

    Only the initials when the directory had no name (display name = initials).
    """
    if user.display_name and user.display_name != user.initials:
        return f"{user.display_name} ({user.initials})"
    return user.initials


LOGO_PATH = Path(__file__).parent / "assets" / "BEC_FINANCIAL_TECHNOLOGIES_LOGO_RGB.png"


def render_sidebar_user(config: AppConfig, roles: frozenset[Actor]) -> None:
    """Top of the sidebar: the logged user, their roles and the environment."""
    user_label = sidebar_user_label(st.session_state.current_user_info)
    st.markdown(f"👤 **{_escape_markdown(user_label)}**")
    st.markdown(role_badges(role_names(roles)), unsafe_allow_html=True)
    badge = environment_badge(config.environment.value)
    mode = " · mock data" if config.is_mock else ""
    st.markdown(f"Environment: {badge}{mode}", unsafe_allow_html=True)
    notice = interim_roles_notice(config)
    if notice:
        st.caption(f"⚠️ {notice}")


def render_sidebar_logo() -> None:
    """Company logo at the bottom of the sidebar."""
    with st.sidebar:
        st.divider()
        col1, _ = st.columns(2)
        col1.image(str(LOGO_PATH), use_column_width=True)


def _escape_markdown(text: str) -> str:
    """Escape the Markdown characters of a name from the directory."""
    return "".join(f"\\{c}" if c in "\\`*_[]<>|~$" else c for c in text)


# Sidebar role badges, in this order (UI_Design.md §2).
ROLE_LABELS = {
    Actor.OWNER_SME_GROUP: "Owner/SME",
    Actor.APPROVER: "Approver",
    Actor.ADMIN: "Admin",
}


def role_names(roles: frozenset[Actor]) -> list[str]:
    """Badge labels: the group roles, or "Viewer" when the user has none.

    Every signed-in user can view; "Viewer" is shown only when it is the
    user's only role, so the badges say what the user can do beyond viewing.
    """
    return [ROLE_LABELS[r] for r in ROLE_LABELS if r in roles] or ["Viewer"]


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
        render_sidebar_logo()
        st.stop()

    try:
        init_services()
    except Exception:
        _stop_on_service_error()

    roles = resolve_session_roles()
    pages = build_pages(roles)
    # The page links are rendered below the user info (UI_Design.md §2), so
    # Streamlit's own navigation menu (always at the top) is hidden.
    pg = st.navigation(pages, position="hidden")

    # Rendered before pg.run() so it stays visible when a page calls st.stop().
    with st.sidebar:
        render_sidebar_user(config, roles)
        st.divider()
        for page in pages:
            st.page_link(page)

    navigation_guard(
        pg.title, st.session_state.data_access, st.session_state.current_user_info
    )
    render_sidebar_logo()

    pg.run()

if __name__ == "__main__":
    main()
