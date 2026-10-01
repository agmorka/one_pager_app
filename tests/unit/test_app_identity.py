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
    assert "Logged user: Agnieszka Kępkowska (X0W)" in [c.value for c in at.caption]


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
    assert "Logged user: X0W" in [c.value for c in at.caption]
