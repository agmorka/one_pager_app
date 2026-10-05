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
from adapters.navigation import (
    ADMIN_PAGE,
    EDITOR_PAGE,
    HELP_PAGE,
    MY_WORK_PAGE,
    PREVIEW_PAGE,
    REGISTRY_PAGE,
    REVIEW_PAGE,
    USE_CASES_PAGE,
    reset_preview_on_sidebar_open,
)
from adapters.theme import apply_theme, environment_badge, role_badges
from onepagerapp.audit import log_unrecognised_user
from onepagerapp.auth import resolve_current_user, resolve_roles
from onepagerapp.config import AppConfig, AppMode, Environment
from onepagerapp.data_access import create_data_access
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.factory import create_document_store
from onepagerapp.directory import lookup_directory_user
from onepagerapp.models import CurrentUser
from onepagerapp.my_work import pending_review_count
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
    ``st.session_state.current_user_roles`` (roles in effect: the group
    roles from ``resolve_session_roles``, or an Admin's "View as" choice,
    ``apply_view_as``).
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

    A role change applies from the next session. Group names are compared
    ignoring case; a failed check gives the Viewer role only, unless the
    directory lists the group (``auth.resolve_roles``). These are the real
    roles (``current_user_group_roles``); ``apply_view_as`` sets the roles the
    pages use.
    """
    roles: frozenset[Actor] | None = st.session_state.get("current_user_group_roles")
    if roles is None:
        directory_user = st.session_state.get("current_user_directory")
        roles = resolve_roles(
            st.session_state.current_user_info,
            st.session_state.config,
            st.session_state.data_access,
            directory_user.groups if directory_user else (),
        )
        st.session_state.current_user_group_roles = roles
    return roles


# "View as" choices for Admins (UI_Design.md §2): label -> roles. "All my
# roles" is the user's real roles; every other choice is one role or none.
VIEW_AS_ALL = "All my roles"
VIEW_AS_ROLES: dict[str, frozenset[Actor]] = {
    "Owner/SME": frozenset({Actor.OWNER_SME_GROUP}),
    "Approver": frozenset({Actor.APPROVER}),
    "Admin": frozenset({Actor.ADMIN}),
    "Viewer": frozenset(),
}
VIEW_AS_KEY = "view_as"


def view_as_options(roles: frozenset[Actor]) -> list[str]:
    """List the "View as" choices: only for Admins, only roles the user has.

    Switching can only take roles away, never add one the user does not
    have, so it is safe for the services, which trust the session's roles.
    """
    if Actor.ADMIN not in roles:
        return []
    return [VIEW_AS_ALL] + [
        label for label, view in VIEW_AS_ROLES.items() if view <= roles
    ]


def apply_view_as(roles: frozenset[Actor]) -> frozenset[Actor]:
    """Roles the pages use: the real roles, or an Admin's "View as" choice.

    Sets ``st.session_state.current_user_roles``. A choice that is not
    (or no longer) available falls back to all the user's roles.
    """
    choice = st.session_state.get(VIEW_AS_KEY, VIEW_AS_ALL)
    if choice not in view_as_options(roles):
        st.session_state.pop(VIEW_AS_KEY, None)
        choice = VIEW_AS_ALL
    effective = roles if choice == VIEW_AS_ALL else VIEW_AS_ROLES[choice]
    st.session_state.current_user_roles = effective
    return effective


def _log_view_as() -> None:
    user: CurrentUser = st.session_state.current_user_info
    logger.info(
        "%s switched the view to: %s",
        user.initials,
        st.session_state.get(VIEW_AS_KEY, VIEW_AS_ALL),
    )


def render_view_as(roles: frozenset[Actor]) -> None:
    """Admin-only selector of the user type the app is shown as."""
    options = view_as_options(roles)
    if not options:
        return
    st.selectbox(
        "View as",
        options,
        key=VIEW_AS_KEY,
        on_change=_log_view_as,
        help=(
            "Show the app as a user with only this role, e.g. to check what "
            "an Approver or a Viewer sees. It can only remove roles you have. "
            "Owner/SME rights on your own One Pagers still apply."
        ),
    )


def render_access_denied(username: str) -> None:
    """Page shown instead of the app to a user who is not recognised."""
    st.title("Access denied")
    st.caption("The One Pager App is available to recognised accounts only.")
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
LOGO_WIDTH = 120
USER_ICON = "\N{BUST IN SILHOUETTE}"


def render_sidebar_user(
    roles: frozenset[Actor],
    group_roles: frozenset[Actor] | None = None,
) -> None:
    """Top of the sidebar, under the logo: the logged user and their roles.

    ``roles`` are the roles in effect (badges); ``group_roles`` the real ones,
    which decide whether the Admin "View as" selector is shown.
    """
    group_roles = roles if group_roles is None else group_roles
    user_label = sidebar_user_label(st.session_state.current_user_info)
    st.markdown(f"{USER_ICON} **{_escape_markdown(user_label)}**")
    st.markdown(role_badges(role_names(roles)), unsafe_allow_html=True)
    render_view_as(group_roles)
    if roles != group_roles:
        st.caption(
            f"Viewing as {', '.join(role_names(roles))} "
            f"(your roles: {', '.join(role_names(group_roles))})"
        )


def render_sidebar_environment(config: AppConfig) -> None:
    """Bottom of the sidebar: the environment badge and the interim roles notice."""
    with st.sidebar:
        st.divider()
        badge = environment_badge(config.environment.value)
        mode = " · mock data" if config.is_mock else ""
        st.markdown(f"Environment: {badge}{mode}", unsafe_allow_html=True)
        notice = interim_roles_notice(config)
        if notice:
            st.caption(notice)


def render_sidebar_logo() -> None:
    """Company logo, small, at the top of the sidebar."""
    with st.sidebar:
        st.image(str(LOGO_PATH), width=LOGO_WIDTH)


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
    """(script, title) of the registered pages (UI §2), My work first.

    Review only for Approvers, Admin only for Admins. Preview and Editor are
    registered (``st.switch_page`` needs them) but have no sidebar link: they
    always show one One Pager, opened from another page (``SIDEBAR_HIDDEN``).
    """
    entries = [
        (MY_WORK_PAGE, "My work"),
        (REGISTRY_PAGE, "Registry"),
        (PREVIEW_PAGE, "Preview"),
        (EDITOR_PAGE, "Editor"),
    ]
    if can_review(roles):
        entries.append((REVIEW_PAGE, "Review"))
    entries.append((USE_CASES_PAGE, "Use Cases"))
    entries.append((HELP_PAGE, "Help"))
    if can_administer(roles):
        entries.append((ADMIN_PAGE, "Admin"))
    return entries


# Pages opened only from other pages, for one One Pager: no sidebar link.
SIDEBAR_HIDDEN = frozenset({PREVIEW_PAGE, EDITOR_PAGE})

PAGE_ICONS = {
    MY_WORK_PAGE: ":material/home:",
    REGISTRY_PAGE: ":material/list_alt:",
    PREVIEW_PAGE: ":material/description:",
    EDITOR_PAGE: ":material/edit:",
    REVIEW_PAGE: ":material/rate_review:",
    USE_CASES_PAGE: ":material/groups:",
    HELP_PAGE: ":material/help:",
    ADMIN_PAGE: ":material/settings:",
}


def build_pages(roles: frozenset[Actor]) -> list:
    """Build the ``st.Page`` objects of ``navigation_entries`` (My work default)."""
    return [
        st.Page(
            script,
            title=title,
            icon=PAGE_ICONS.get(script),
            default=script == MY_WORK_PAGE,
        )
        for script, title in navigation_entries(roles)
    ]


def review_link_label(pending: int | None) -> str:
    """Sidebar label of the Review page, with the pending count when known."""
    return f"Review ({pending})" if pending else "Review"


def render_sidebar_links(pages: list, roles: frozenset[Actor]) -> None:
    """Page links (no Preview/Editor); Review shows how many are waiting.

    ``pages`` are ``build_pages(roles)``, in ``navigation_entries`` order.
    """
    pending = None
    if can_review(roles):
        try:
            pending = pending_review_count(st.session_state.data_access, roles)
        except Exception:
            logger.exception("Failed to count the review queue for the sidebar")
    for page, (script, _) in zip(pages, navigation_entries(roles), strict=True):
        if script in SIDEBAR_HIDDEN:
            continue
        if script == REVIEW_PAGE:
            st.page_link(page, label=review_link_label(pending))
        else:
            st.page_link(page)


def _stop_on_service_error() -> NoReturn:
    logger.exception("Failed to initialize services")
    st.error("Failed to connect to backend services. Please try refreshing the page.")
    st.stop()


def main() -> None:
    """Application entry point with global error boundary."""
    st.set_page_config(
        page_title="One Pager App",
        page_icon=":material/description:",
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
    except Exception:  # noqa: BLE001 - the global error boundary
        _stop_on_service_error()

    # Fail closed (Architecture.md §4): no page and no data access for a user
    # who is not recognised.
    render_sidebar_logo()
    if resolve_user() is None:
        render_access_denied(st.session_state.get("unrecognised_user", ""))
        render_sidebar_environment(config)
        st.stop()

    try:
        init_services()
    except Exception:  # noqa: BLE001 - the global error boundary
        _stop_on_service_error()

    group_roles = resolve_session_roles()
    roles = apply_view_as(group_roles)
    pages = build_pages(roles)
    # The page links are rendered below the user info (UI_Design.md §2), so
    # Streamlit's own navigation menu (always at the top) is hidden.
    pg = st.navigation(pages, position="hidden")

    # Rendered before pg.run() so it stays visible when a page calls st.stop().
    with st.sidebar:
        render_sidebar_user(roles, group_roles)
        st.divider()
        with st.container():  # styled as a list without gaps (theme.py)
            render_sidebar_links(pages, roles)

    navigation_guard(
        pg.title, st.session_state.data_access, st.session_state.current_user_info
    )
    reset_preview_on_sidebar_open(pg.title)
    render_sidebar_environment(config)

    pg.run()


if __name__ == "__main__":
    main()
