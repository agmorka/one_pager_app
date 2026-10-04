"""Identity source per app mode and the fail-closed entry point.

Identity plan Phase 2 and Architecture.md §4.
"""

from pathlib import Path
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
from tests.conftest import FIXTURES_DIR

APP_DIR = Path(__file__).resolve().parents[2] / "app"
CORPORATE = "x0wadm@becoc001.onmicrosoft.com"


class _RecordingDataAccess(MockDataAccess):
    """Counts ``SELECT current_user()`` calls (``get_current_user``)."""

    def __init__(self, username: str = "dbuadm@becoc001.onmicrosoft.com") -> None:
        super().__init__(OnePagerDocumentStore(FIXTURES_DIR))
        self.calls = 0
        self._username = username

    def get_current_user(self) -> str:
        self.calls += 1
        return self._username


@pytest.fixture
def app_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(APP_DIR))
    import app  # noqa: PLC0415 - needs app/ on sys.path

    return app


def _config(mode: str, **overrides: str) -> AppConfig:
    return AppConfig(APP_MODE=mode, ONE_PAGER_APP_VOLUME_PATH="/Volumes/x", **overrides)


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        {"x-forwarded-preferred-username": CORPORATE},
        {"x-forwarded-email": CORPORATE},
        {"x-forwarded-preferred-username": " ", "x-forwarded-email": CORPORATE},
    ],
)
def test__get_logged_user__databricks_uses_proxy_headers(
    app_module: ModuleType, headers: dict[str, str]
) -> None:
    data_access = _RecordingDataAccess()

    username = app_module.get_logged_user(_config("databricks"), headers, data_access)

    assert username == CORPORATE
    assert data_access.calls == 0


@pytest.mark.unit
def test__get_logged_user__databricks_without_headers_has_no_user(
    app_module: ModuleType,
) -> None:
    data_access = _RecordingDataAccess()

    assert app_module.get_logged_user(_config("databricks"), {}, data_access) is None
    assert data_access.calls == 0  # no SELECT current_user() fallback


@pytest.mark.unit
def test__get_logged_user__local_integration_queries_current_user(
    app_module: ModuleType,
) -> None:
    data_access = _RecordingDataAccess()
    headers = {"x-forwarded-email": CORPORATE}

    username = app_module.get_logged_user(
        _config("local-integration"), headers, data_access
    )

    assert username == "dbuadm@becoc001.onmicrosoft.com"
    assert data_access.calls == 1


@pytest.mark.unit
def test__get_logged_user__local_mock_uses_configured_user(
    app_module: ModuleType,
) -> None:
    config = _config("local-mock", ONE_PAGER_APP_MOCK_USER=CORPORATE)
    headers = {"x-forwarded-email": "other@becoc001.onmicrosoft.com"}

    assert app_module.get_logged_user(config, headers, None) == CORPORATE


# ============================================================================
# Entry point (app.py run with AppTest)
# ============================================================================


def _run_app(monkeypatch: pytest.MonkeyPatch, **env: str) -> AppTest:
    """Run app.py with the given environment (AppConfig.from_env)."""
    monkeypatch.syspath_prepend(str(APP_DIR))
    for name in AppConfig.model_fields:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ONE_PAGER_APP_VOLUME_PATH", str(FIXTURES_DIR))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return AppTest.from_file(str(APP_DIR / "app.py"), default_timeout=30).run()


@pytest.mark.unit
def test__app__recognised_user_gets_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_MOCK_USER=CORPORATE)

    assert not at.exception
    assert at.session_state["current_user_info"].initials == "X0W"
    assert "data_access" in at.session_state
    assert at.session_state["current_user_info"].display_name == "Local Dev User"
    assert not [t for t in at.title if t.value == "Access denied"]


@pytest.mark.unit
def test__app__unknown_domain_is_denied(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    at = _run_app(
        monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_MOCK_USER="x0wadm@guest.com"
    )
    at.run()  # a rerun shows the page again but logs only once per session

    assert not at.exception
    assert [t.value for t in at.title] == ["Access denied"]
    assert "`x0wadm@guest.com` is not recognised" in at.error[0].value
    assert "current_user_info" not in at.session_state
    assert "data_access" not in at.session_state  # no data access for the session
    assert [m for m in caplog.messages if "action=access_app" in m] == [
        "action=access_app outcome=permission_denied user=- username=x0wadm@guest.com"
    ]


@pytest.mark.unit
def test__app__databricks_without_proxy_headers_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _run_app(monkeypatch, APP_MODE="databricks")

    assert not at.exception
    assert [t.value for t in at.title] == ["Access denied"]
    assert "could not identify your account" in at.error[0].value
    assert "data_access" not in at.session_state


class _FailingDataAccess(_RecordingDataAccess):
    def get_current_user(self) -> str:
        msg = "warehouse unavailable"
        raise RuntimeError(msg)


def _patch_services(
    monkeypatch: pytest.MonkeyPatch,
    data_access: MockDataAccess,
    headers: dict[str, str] | None = None,
) -> None:
    """Use ``data_access`` instead of a real connection; fake the proxy headers."""
    monkeypatch.setattr(
        data_access_package, "create_data_access", lambda *_: data_access
    )
    monkeypatch.setattr(st, "context", SimpleNamespace(headers=headers or {}))


@pytest.mark.unit
def test__app__databricks_proxy_user_gets_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_access = _RecordingDataAccess()
    _patch_services(
        monkeypatch, data_access, {"x-forwarded-preferred-username": CORPORATE}
    )

    at = _run_app(monkeypatch, APP_MODE="databricks")

    assert not at.exception
    assert at.session_state["current_user_info"].initials == "X0W"
    assert at.session_state["data_access"] is data_access
    assert data_access.calls == 0  # SELECT current_user() is never used
    assert not at.error


@pytest.mark.unit
def test__app__databricks_unknown_domain_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_access = _RecordingDataAccess()
    _patch_services(monkeypatch, data_access, {"x-forwarded-email": "x0w@guest.com"})

    at = _run_app(monkeypatch, APP_MODE="databricks")

    assert not at.exception
    assert [t.value for t in at.title] == ["Access denied"]
    assert "data_access" not in at.session_state
    assert data_access.calls == 0


@pytest.mark.unit
def test__app__local_integration_uses_current_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_access = _RecordingDataAccess(CORPORATE)
    _patch_services(monkeypatch, data_access, {"x-forwarded-email": "ignored@x.dk"})

    at = _run_app(monkeypatch, APP_MODE="local-integration")
    at.run()

    assert not at.exception
    assert at.session_state["current_user_info"].initials == "X0W"
    assert data_access.calls == 1  # resolved once per session


@pytest.mark.unit
def test__app__failed_identity_lookup_is_denied(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_services(monkeypatch, _FailingDataAccess())

    at = _run_app(monkeypatch, APP_MODE="local-integration")

    assert not at.exception
    assert [t.value for t in at.title] == ["Access denied"]
    assert "could not identify your account" in at.error[0].value
    assert "current_user_info" not in at.session_state
    assert "action=access_app outcome=permission_denied user=- username=-" in (
        caplog.messages
    )


@pytest.mark.unit
def test__app__name_from_the_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    data_access = _RecordingDataAccess()
    _patch_services(
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
    at.run()

    assert not at.exception
    user = at.session_state["current_user_info"]
    assert (user.initials, user.display_name) == ("X0W", "Agnieszka Kępkowska")
    assert lookups == ["t"]  # once per session
    assert "**Agnieszka Kępkowska (X0W)**" in [m.value for m in at.sidebar.markdown]


@pytest.mark.unit
def test__app__directory_failure_shows_the_initials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_services(
        monkeypatch,
        _RecordingDataAccess(),
        {"x-forwarded-preferred-username": CORPORATE, "x-forwarded-access-token": "t"},
    )

    def broken(*_: object) -> None:
        msg = "directory down"
        raise RuntimeError(msg)

    monkeypatch.setattr(directory, "lookup_directory_user", broken)

    at = _run_app(monkeypatch, APP_MODE="databricks")

    assert not at.exception
    assert not [t for t in at.title if t.value == "Access denied"]
    assert at.session_state["current_user_info"].display_name == "X0W"
    assert "**X0W**" in [m.value for m in at.sidebar.markdown]


@pytest.mark.unit
def test__app__roles_from_mock_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock")  # default: interim group

    assert at.session_state["current_user_roles"] == {
        Actor.OWNER_SME_GROUP,
        Actor.APPROVER,
        Actor.ADMIN,
    }


@pytest.mark.unit
def test__app__no_group_is_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_MOCK_GROUPS="")

    assert not at.exception
    assert at.session_state["current_user_roles"] == frozenset()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (frozenset(), ["Viewer"]),
        (frozenset({Actor.APPROVER}), ["Approver"]),
        (
            frozenset({Actor.ADMIN, Actor.OWNER_SME_GROUP, Actor.APPROVER}),
            ["Owner/SME", "Approver", "Admin"],
        ),
    ],
)
def test__role_names__viewer_only_without_other_roles(
    app_module: ModuleType, roles: frozenset[Actor], expected: list[str]
) -> None:
    assert app_module.role_names(roles) == expected


@pytest.mark.unit
def test__app__sidebar_shows_role_badges(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock")

    sidebar_html = " ".join(m.value for m in at.sidebar.markdown)
    for role in ("Owner/SME", "Approver", "Admin"):
        assert f">{role}</span>" in sidebar_html


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environment", "overrides", "expected"),
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
def test__interim_roles_notice__outside_dev_while_interim(
    app_module: ModuleType,
    environment: str,
    overrides: dict[str, str],
    expected: str | None,
) -> None:
    config = _config("local-mock", ONE_PAGER_APP_ENVIRONMENT=environment, **overrides)

    assert app_module.interim_roles_notice(config) == expected


@pytest.mark.unit
def test__app__interim_roles_warning_and_notice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_ENVIRONMENT="UAT")

    assert not at.exception
    assert any("Interim roles: DataPlatEng" in c.value for c in at.sidebar.caption)
    assert any(
        "Interim role groups in use" in m and "BEC_BECOC001_LHX_UAT_DataPlatEng" in m
        for m in caplog.messages
    )


@pytest.mark.unit
def test__app__no_interim_notice_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_ENVIRONMENT="DEV")

    assert not any("Interim roles" in c.value for c in at.sidebar.caption)


def _page_titles(app_module: ModuleType, roles: frozenset[Actor]) -> list[str]:
    return [title for _, title in app_module.navigation_entries(roles)]


@pytest.mark.unit
def test__done_when__dataplateng_member_sees_review_and_admin(
    monkeypatch: pytest.MonkeyPatch, app_module: ModuleType
) -> None:
    """Identity plan Phase 6, done when: roles follow the groups per session."""
    member = _run_app(monkeypatch, APP_MODE="local-mock")
    member_pages = _page_titles(app_module, member.session_state["current_user_roles"])
    assert {"Review", "Admin"} <= set(member_pages)

    non_member = _run_app(
        monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_MOCK_GROUPS="Other"
    )
    viewer_pages = _page_titles(
        app_module, non_member.session_state["current_user_roles"]
    )
    assert viewer_pages == ["Registry", "Preview", "Editor", "Use Cases", "Help"]

    # Another Approver group: the member loses Review from the next session.
    moved = _run_app(
        monkeypatch,
        APP_MODE="local-mock",
        ONE_PAGER_APP_GROUP_APPROVER="OPA-Approver-{env}",
    )
    moved_pages = _page_titles(app_module, moved.session_state["current_user_roles"])
    assert "Review" not in moved_pages
    assert "Admin" in moved_pages


@pytest.mark.unit
def test__app__sidebar_user_on_top_and_logo_at_the_bottom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock")

    children = list(at.sidebar.children.values())
    assert not at.exception
    assert children[0].value == "**Local Dev User (LDU)**"
    kinds = [child.type for child in children]
    first_link = kinds.index("page_link")
    assert "markdown" not in kinds[first_link:]  # user info above the links
    assert kinds[-1] == "horizontal"  # the logo columns come last


ALL_GROUP_ROLES = frozenset({Actor.OWNER_SME_GROUP, Actor.APPROVER, Actor.ADMIN})


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
def test__view_as_options__admins_only_and_never_more_roles(
    app_module: ModuleType, roles: frozenset[Actor], expected: list[str]
) -> None:
    assert app_module.view_as_options(roles) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("choice", "roles", "pages"),
    [
        (
            "Viewer",
            frozenset(),
            ["Registry", "Preview", "Editor", "Use Cases", "Help"],
        ),
        (
            "Approver",
            frozenset({Actor.APPROVER}),
            ["Registry", "Preview", "Editor", "Review", "Use Cases", "Help"],
        ),
        (
            "Owner/SME",
            frozenset({Actor.OWNER_SME_GROUP}),
            ["Registry", "Preview", "Editor", "Use Cases", "Help"],
        ),
    ],
)
def test__app__admin_views_the_app_as_another_user_type(
    monkeypatch: pytest.MonkeyPatch,
    app_module: ModuleType,
    choice: str,
    roles: frozenset[Actor],
    pages: list[str],
) -> None:
    at = _run_app(monkeypatch, APP_MODE="local-mock")  # interim group: every role
    assert at.session_state["current_user_roles"] == ALL_GROUP_ROLES

    at.sidebar.selectbox(key="view_as").set_value(choice).run()

    assert not at.exception
    assert at.session_state["current_user_roles"] == roles
    assert at.session_state["current_user_group_roles"] == ALL_GROUP_ROLES
    assert _page_titles(app_module, roles) == pages
    shown = ", ".join(app_module.role_names(roles))
    assert any(f"Viewing as {shown}" in c.value for c in at.sidebar.caption)

    at.sidebar.selectbox(key="view_as").set_value("All my roles").run()

    assert at.session_state["current_user_roles"] == ALL_GROUP_ROLES
    assert not any("Viewing as" in c.value for c in at.sidebar.caption)


@pytest.mark.unit
def test__app__no_view_as_for_non_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_app(
        monkeypatch,
        APP_MODE="local-mock",
        ONE_PAGER_APP_GROUP_ADMIN="OPA-Admin-{env}",  # not in the mock groups
    )

    assert not at.exception
    assert Actor.ADMIN not in at.session_state["current_user_roles"]
    assert not [s for s in at.sidebar.selectbox if s.key == "view_as"]
