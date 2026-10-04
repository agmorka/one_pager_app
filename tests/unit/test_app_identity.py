"""Identity source per app mode and the fail-closed entry point.

Identity plan Phase 2 and Architecture.md §4.
"""

from collections.abc import Callable
from types import ModuleType, SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp import data_access as data_access_package
from onepagerapp import directory
from onepagerapp.config import AppConfig
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.directory import DirectoryUser
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.state_machine import Actor
from tests.helpers import (
    ALL_GROUP_ROLES,
    APP_DIR,
    APPROVER_ROLES,
    FIXTURES_DIR,
    config,
    failing,
)

CORPORATE = "x0wadm@becoc001.onmicrosoft.com"
SQL_USER = "dbuadm@becoc001.onmicrosoft.com"
VIEWER_PAGES = ["Registry", "Preview", "Editor", "Use Cases", "Help"]


@pytest.fixture
def app_module(import_app_module: Callable[[str], ModuleType]) -> ModuleType:
    """Return the ``app`` entry module (app/ on sys.path)."""
    return import_app_module("app")


def _counting_data_access(username: str = SQL_USER) -> tuple[MockDataAccess, list]:
    """Return mock data whose ``SELECT current_user()`` answers ``username``.

    The list records one entry per call.
    """
    data_access = MockDataAccess(OnePagerDocumentStore(FIXTURES_DIR))
    calls: list[None] = []

    def get_current_user() -> str:
        calls.append(None)
        return username

    data_access.get_current_user = get_current_user
    return data_access, calls


def _run_app(monkeypatch: pytest.MonkeyPatch, **env: str) -> AppTest:
    """Run app.py with exactly the given environment (AppConfig.from_env)."""
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in AppConfig.model_fields:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ONE_PAGER_APP_VOLUME_PATH", str(FIXTURES_DIR))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return AppTest.from_file(str(APP_DIR / "app.py"), default_timeout=30).run()


def _use_services(
    monkeypatch: pytest.MonkeyPatch,
    data_access: MockDataAccess,
    headers: dict[str, str] | None = None,
) -> None:
    """Use ``data_access`` instead of a real connection; fake the proxy headers."""
    monkeypatch.setattr(
        data_access_package, "create_data_access", lambda *_: data_access
    )
    monkeypatch.setattr(st, "context", SimpleNamespace(headers=headers or {}))


def _page_titles(app_module: ModuleType, roles: frozenset[Actor]) -> list[str]:
    """Return the navigation titles for ``roles``."""
    return [title for _, title in app_module.navigation_entries(roles)]


def _is_denied(at: AppTest) -> bool:
    """Tell whether the access-denied page is shown."""
    return [t.value for t in at.title] == ["Access denied"]


# ============================================================================
# get_logged_user
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        {"x-forwarded-preferred-username": CORPORATE},
        {"x-forwarded-email": CORPORATE},
        {"x-forwarded-preferred-username": " ", "x-forwarded-email": CORPORATE},
    ],
)
def test__databricks_proxy_headers__get_logged_user__username_from_headers(
    app_module: ModuleType, headers: dict[str, str]
) -> None:
    """Deployed, the username comes from the proxy headers, not SQL."""
    # Given
    data_access, calls = _counting_data_access()

    # When
    username = app_module.get_logged_user(
        config(APP_MODE="databricks"), headers, data_access
    )

    # Then
    assert username == CORPORATE
    assert calls == []


@pytest.mark.unit
def test__databricks_without_headers__get_logged_user__no_user_no_fallback(
    app_module: ModuleType,
) -> None:
    """Without headers there is no user; SQL is not used as a fallback."""
    # Given
    data_access, calls = _counting_data_access()

    # When
    username = app_module.get_logged_user(
        config(APP_MODE="databricks"), {}, data_access
    )

    # Then
    assert username is None
    assert calls == []


@pytest.mark.unit
def test__local_integration__get_logged_user__current_user_from_sql(
    app_module: ModuleType,
) -> None:
    """Locally the CLI profile's user comes from ``SELECT current_user()``."""
    # Given
    data_access, calls = _counting_data_access()
    headers = {"x-forwarded-email": CORPORATE}

    # When
    username = app_module.get_logged_user(
        config(APP_MODE="local-integration"), headers, data_access
    )

    # Then
    assert username == SQL_USER
    assert len(calls) == 1


@pytest.mark.unit
def test__local_mock__get_logged_user__configured_mock_user(
    app_module: ModuleType,
) -> None:
    """Mock mode uses the configured user and ignores headers."""
    # Given
    mock = config(APP_MODE="local-mock", ONE_PAGER_APP_MOCK_USER=CORPORATE)
    headers = {"x-forwarded-email": "other@becoc001.onmicrosoft.com"}

    # When
    username = app_module.get_logged_user(mock, headers, None)

    # Then
    assert username == CORPORATE


# ============================================================================
# Entry point (app.py run with AppTest)
# ============================================================================


@pytest.mark.unit
def test__recognised_mock_user__run_app__signed_in_with_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recognised user gets the app and a data access for the session."""
    # When
    at = _run_app(monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_MOCK_USER=CORPORATE)

    # Then
    assert not at.exception
    user = at.session_state["current_user_info"]
    assert (user.initials, user.display_name) == ("X0W", "Local Dev User")
    assert "data_access" in at.session_state
    assert not _is_denied(at)


@pytest.mark.unit
def test__unknown_domain__run_app_twice__denied_and_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unknown user is refused before any data access; a rerun logs no more."""
    # Given
    at = _run_app(
        monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_MOCK_USER="x0wadm@guest.com"
    )

    # When
    at.run()

    # Then
    assert not at.exception
    assert _is_denied(at)
    assert "`x0wadm@guest.com` is not recognised" in at.error[0].value
    assert "current_user_info" not in at.session_state
    assert "data_access" not in at.session_state
    assert [m for m in caplog.messages if "action=access_app" in m] == [
        "action=access_app outcome=permission_denied user=- username=x0wadm@guest.com"
    ]


@pytest.mark.unit
def test__databricks_without_proxy_headers__run_app__denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployed without headers the account cannot be identified."""
    # When
    at = _run_app(monkeypatch, APP_MODE="databricks")

    # Then
    assert not at.exception
    assert _is_denied(at)
    assert "could not identify your account" in at.error[0].value
    assert "data_access" not in at.session_state


@pytest.mark.unit
def test__databricks_proxy_user__run_app__signed_in_without_sql_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deployed, the proxy user is signed in; ``current_user()`` is never used."""
    # Given
    data_access, calls = _counting_data_access()
    _use_services(
        monkeypatch, data_access, {"x-forwarded-preferred-username": CORPORATE}
    )

    # When
    at = _run_app(monkeypatch, APP_MODE="databricks")

    # Then
    assert not at.exception
    assert at.session_state["current_user_info"].initials == "X0W"
    assert at.session_state["data_access"] is data_access
    assert calls == []
    assert not at.error


@pytest.mark.unit
def test__databricks_unknown_domain__run_app__denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proxy user outside the corporate domain is refused."""
    # Given
    data_access, calls = _counting_data_access()
    _use_services(monkeypatch, data_access, {"x-forwarded-email": "x0w@guest.com"})

    # When
    at = _run_app(monkeypatch, APP_MODE="databricks")

    # Then
    assert not at.exception
    assert _is_denied(at)
    assert "data_access" not in at.session_state
    assert calls == []


@pytest.mark.unit
def test__local_integration__run_app_twice__current_user_resolved_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locally the SQL user is looked up once per session; headers are ignored."""
    # Given
    data_access, calls = _counting_data_access(CORPORATE)
    _use_services(monkeypatch, data_access, {"x-forwarded-email": "ignored@x.dk"})
    at = _run_app(monkeypatch, APP_MODE="local-integration")

    # When
    at.run()

    # Then
    assert not at.exception
    assert at.session_state["current_user_info"].initials == "X0W"
    assert len(calls) == 1


@pytest.mark.unit
def test__identity_lookup_fails__run_app__denied_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mock_data_access: MockDataAccess,
) -> None:
    """A failed lookup fails closed."""
    # Given
    mock_data_access.get_current_user = failing("warehouse unavailable")
    _use_services(monkeypatch, mock_data_access)

    # When
    at = _run_app(monkeypatch, APP_MODE="local-integration")

    # Then
    assert not at.exception
    assert _is_denied(at)
    assert "could not identify your account" in at.error[0].value
    assert "current_user_info" not in at.session_state
    assert "action=access_app outcome=permission_denied user=- username=-" in (
        caplog.messages
    )


@pytest.mark.unit
def test__directory_has_name__run_app_twice__name_shown_looked_up_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The directory name is shown in the sidebar; one lookup per session."""
    # Given
    data_access, _ = _counting_data_access()
    _use_services(
        monkeypatch,
        data_access,
        {"x-forwarded-preferred-username": CORPORATE, "x-forwarded-access-token": "t"},
    )
    lookups: list[str | None] = []

    def get_me(token: str | None) -> DirectoryUser:
        lookups.append(token)
        return DirectoryUser(given_name="Agnieszka", family_name="Kępkowska")

    monkeypatch.setattr(directory, "get_me", get_me)
    at = _run_app(monkeypatch, APP_MODE="databricks")

    # When
    at.run()

    # Then
    assert not at.exception
    user = at.session_state["current_user_info"]
    assert (user.initials, user.display_name) == ("X0W", "Agnieszka Kępkowska")
    assert lookups == ["t"]
    assert "**Agnieszka Kępkowska (X0W)**" in [m.value for m in at.sidebar.markdown]


@pytest.mark.unit
def test__directory_fails__run_app__signed_in_with_initials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A directory failure does not deny access; the initials are shown."""
    # Given
    data_access, _ = _counting_data_access()
    _use_services(
        monkeypatch,
        data_access,
        {"x-forwarded-preferred-username": CORPORATE, "x-forwarded-access-token": "t"},
    )
    monkeypatch.setattr(directory, "lookup_directory_user", failing("directory down"))

    # When
    at = _run_app(monkeypatch, APP_MODE="databricks")

    # Then
    assert not at.exception
    assert not _is_denied(at)
    assert at.session_state["current_user_info"].display_name == "X0W"
    assert "**X0W**" in [m.value for m in at.sidebar.markdown]


# ============================================================================
# Roles
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("env", "roles"),
    [({}, ALL_GROUP_ROLES), ({"ONE_PAGER_APP_MOCK_GROUPS": ""}, frozenset())],
    ids=["interim-group", "no-group"],
)
def test__mock_groups__run_app__roles_follow_membership(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], roles: frozenset[Actor]
) -> None:
    """The interim group gives every role; no group means Viewer."""
    # When
    at = _run_app(monkeypatch, APP_MODE="local-mock", **env)

    # Then
    assert not at.exception
    assert at.session_state["current_user_roles"] == roles


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (frozenset(), ["Viewer"]),
        (APPROVER_ROLES, ["Approver"]),
        (ALL_GROUP_ROLES, ["Owner/SME", "Approver", "Admin"]),
    ],
)
def test__roles__role_names__viewer_only_without_other_roles(
    app_module: ModuleType, roles: frozenset[Actor], expected: list[str]
) -> None:
    """Viewer is shown only when the user has no other role."""
    # When
    names = app_module.role_names(roles)

    # Then
    assert names == expected


@pytest.mark.unit
def test__every_role__run_app__sidebar_role_badges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sidebar shows a badge per role."""
    # When
    at = _run_app(monkeypatch, APP_MODE="local-mock")

    # Then
    sidebar_html = " ".join(m.value for m in at.sidebar.markdown)
    for role in ("Owner/SME", "Approver", "Admin"):
        assert f">{role}</span>" in sidebar_html


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environment", "groups", "expected"),
    [
        ("DEV", {}, None),
        (
            "UAT",
            {},
            "Interim roles: DataPlatEng members act as Owner/SME, Approver and Admin.",
        ),
        (
            "PRD",
            {"ONE_PAGER_APP_GROUP_OWNER_SME": "OPA-OwnerSME-{env}"},
            "Interim roles: DataPlatEng members act as Approver and Admin.",
        ),
        (
            "INT",
            {
                "ONE_PAGER_APP_GROUP_OWNER_SME": "A",
                "ONE_PAGER_APP_GROUP_APPROVER": "B",
                "ONE_PAGER_APP_GROUP_ADMIN": "C",
            },
            None,
        ),
    ],
)
def test__environment_and_groups__interim_roles_notice__outside_dev_while_interim(
    app_module: ModuleType,
    environment: str,
    groups: dict[str, str],
    expected: str | None,
) -> None:
    """The notice names the roles still on the interim group, except in DEV."""
    # Given
    settings = config(
        APP_MODE="local-mock", ONE_PAGER_APP_ENVIRONMENT=environment, **groups
    )

    # When
    notice = app_module.interim_roles_notice(settings)

    # Then
    assert notice == expected


@pytest.mark.unit
def test__uat_on_interim_group__run_app__sidebar_notice_and_warning_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Outside DEV the interim groups are shown and logged as a warning."""
    # When
    at = _run_app(monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_ENVIRONMENT="UAT")

    # Then
    assert not at.exception
    assert any("Interim roles: DataPlatEng" in c.value for c in at.sidebar.caption)
    assert any(
        "Interim role groups in use" in m and "BEC_BECOC001_LHX_UAT_DataPlatEng" in m
        for m in caplog.messages
    )


@pytest.mark.unit
def test__dev_on_interim_group__run_app__no_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEV does not show the interim notice."""
    # When
    at = _run_app(monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_ENVIRONMENT="DEV")

    # Then
    assert not any("Interim roles" in c.value for c in at.sidebar.caption)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("env", "included", "excluded"),
    [
        ({}, {"Review", "Admin"}, set()),
        (
            {"ONE_PAGER_APP_MOCK_GROUPS": "Other"},
            set(VIEWER_PAGES),
            {"Review", "Admin"},
        ),
        ({"ONE_PAGER_APP_GROUP_APPROVER": "OPA-Approver-{env}"}, {"Admin"}, {"Review"}),
    ],
    ids=["dataplateng-member", "non-member", "approver-group-moved"],
)
def test__session_groups__run_app__pages_follow_the_groups(
    monkeypatch: pytest.MonkeyPatch,
    app_module: ModuleType,
    env: dict[str, str],
    included: set[str],
    excluded: set[str],
) -> None:
    """Identity plan Phase 6, done when: roles follow the groups per session."""
    # When
    at = _run_app(monkeypatch, APP_MODE="local-mock", **env)

    # Then
    pages = set(_page_titles(app_module, at.session_state["current_user_roles"]))
    assert included <= pages
    assert not excluded & pages


@pytest.mark.unit
def test__signed_in__run_app__user_on_top_and_logo_at_the_bottom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sidebar starts with the user and ends with the logo columns."""
    # When
    at = _run_app(monkeypatch, APP_MODE="local-mock")

    # Then
    assert not at.exception
    children = list(at.sidebar.children.values())
    assert children[0].value == "**Local Dev User (LDU)**"
    kinds = [child.type for child in children]
    first_link = kinds.index("page_link")
    assert "markdown" not in kinds[first_link:]
    assert kinds[-1] == "horizontal"


# ============================================================================
# View as (admins)
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (
            ALL_GROUP_ROLES,
            ["All my roles", "Owner/SME", "Approver", "Admin", "Viewer"],
        ),
        (frozenset({Actor.ADMIN}), ["All my roles", "Admin", "Viewer"]),
        (frozenset({Actor.APPROVER, Actor.OWNER_SME_GROUP}), []),  # no Admin
        (frozenset(), []),
    ],
)
def test__roles__view_as_options__admins_only_and_never_more_roles(
    app_module: ModuleType, roles: frozenset[Actor], expected: list[str]
) -> None:
    """Only Admins can view as another user type, and only as their own roles."""
    # When
    options = app_module.view_as_options(roles)

    # Then
    assert options == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("choice", "roles", "pages"),
    [
        ("Viewer", frozenset(), VIEWER_PAGES),
        (
            "Approver",
            APPROVER_ROLES,
            ["Registry", "Preview", "Editor", "Review", "Use Cases", "Help"],
        ),
        ("Owner/SME", frozenset({Actor.OWNER_SME_GROUP}), VIEWER_PAGES),
    ],
)
def test__admin__view_as_other_user_type__roles_and_pages_narrowed(
    monkeypatch: pytest.MonkeyPatch,
    app_module: ModuleType,
    choice: str,
    roles: frozenset[Actor],
    pages: list[str],
) -> None:
    """Viewing as another type narrows the roles; the group roles are kept."""
    # Given
    at = _run_app(monkeypatch, APP_MODE="local-mock")

    # When
    at.sidebar.selectbox(key="view_as").set_value(choice).run()

    # Then
    assert not at.exception
    assert at.session_state["current_user_roles"] == roles
    assert at.session_state["current_user_group_roles"] == ALL_GROUP_ROLES
    assert _page_titles(app_module, roles) == pages
    shown = ", ".join(app_module.role_names(roles))
    assert any(f"Viewing as {shown}" in c.value for c in at.sidebar.caption)


@pytest.mark.unit
def test__admin_viewing_as_viewer__choose_all_my_roles__roles_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Choosing "All my roles" ends the view-as."""
    # Given
    at = _run_app(monkeypatch, APP_MODE="local-mock")
    at.sidebar.selectbox(key="view_as").set_value("Viewer").run()

    # When
    at.sidebar.selectbox(key="view_as").set_value("All my roles").run()

    # Then
    assert at.session_state["current_user_roles"] == ALL_GROUP_ROLES
    assert not any("Viewing as" in c.value for c in at.sidebar.caption)


@pytest.mark.unit
def test__user_without_admin_role__run_app__no_view_as(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-admins get no view-as selector."""
    # When
    at = _run_app(
        monkeypatch,
        APP_MODE="local-mock",
        ONE_PAGER_APP_GROUP_ADMIN="OPA-Admin-{env}",  # not in the mock groups
    )

    # Then
    assert not at.exception
    assert Actor.ADMIN not in at.session_state["current_user_roles"]
    assert not [s for s in at.sidebar.selectbox if s.key == "view_as"]
