"""Identity source per app mode and the fail-closed entry point.

Identity plan Phase 2 and Architecture.md §4.
"""

from pathlib import Path
from types import ModuleType

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.config import AppConfig
from onepagerapp.data_access.mock import MockDataAccess
from tests.conftest import FIXTURES_DIR

APP_DIR = Path(__file__).resolve().parents[2] / "app"
CORPORATE = "x0wadm@becoc001.onmicrosoft.com"


class _RecordingDataAccess(MockDataAccess):
    """Counts ``SELECT current_user()`` calls (``get_current_user``)."""

    def __init__(self, username: str = "dbuadm@becoc001.onmicrosoft.com") -> None:
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
    assert not [t for t in at.title if t.value == "Access denied"]


@pytest.mark.unit
def test__app__unknown_domain_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_app(
        monkeypatch, APP_MODE="local-mock", ONE_PAGER_APP_MOCK_USER="x0wadm@guest.com"
    )

    assert not at.exception
    assert [t.value for t in at.title] == ["Access denied"]
    assert "`x0wadm@guest.com` is not recognised" in at.error[0].value
    assert "current_user_info" not in at.session_state
    assert "data_access" not in at.session_state  # no data access for the session


@pytest.mark.unit
def test__app__databricks_without_proxy_headers_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _run_app(monkeypatch, APP_MODE="databricks")

    assert not at.exception
    assert [t.value for t in at.title] == ["Access denied"]
    assert "could not identify your account" in at.error[0].value
    assert "data_access" not in at.session_state
