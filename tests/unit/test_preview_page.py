"""AppTest smoke tests for the Preview page states (UI_Design.md §4.4)."""

from pathlib import Path
from typing import NoReturn

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from tests.conftest import FIXTURES_DIR

APP_DIR = Path(__file__).resolve().parents[2] / "app"


class _FailingDataAccess(MockDataAccess):
    def get_one_pager(self, one_pager_id: str) -> NoReturn:  # noqa: ARG002
        msg = "warehouse 4efe1f3d3f86e320 unreachable: secret internals"
        raise RuntimeError(msg)


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


def _services(tmp_path: Path, data_access_cls: type = MockDataAccess) -> dict:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    user = resolve_current_user("alice.brown@company.com")
    return {
        "services_initialized": True,
        "data_access": data_access_cls(store),
        "document_store": store,
        "current_user": user.username,
        "current_user_info": user,
    }


def _app(state: dict) -> AppTest:
    at = AppTest.from_file(str(APP_DIR / "views" / "preview.py"), default_timeout=30)
    for key, value in state.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test__preview__without_id_does_not_default_to_a_one_pager(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(_services(tmp_path)).run()

    assert not at.exception
    assert "No One Pager selected" in at.info[0].value
    assert "preview_one_pager_id" not in at.session_state
    assert all(t.value != "OP-0001" for t in at.title)

    at.button(key="preview_go_to_registry").click().run()
    assert switched == ["views/registry.py"]


@pytest.mark.unit
def test__preview__with_id_renders_one_pager(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app({**_services(tmp_path), "preview_one_pager_id": "OP-0001"}).run()

    assert not at.exception
    assert not at.error
    assert at.title


@pytest.mark.unit
def test__preview__load_error_is_friendly_with_retry(
    tmp_path: Path, switched: list[str]
) -> None:
    state = {
        **_services(tmp_path, _FailingDataAccess),
        "preview_one_pager_id": "OP-0001",
    }
    at = _app(state).run()

    assert not at.exception
    assert at.error[0].value == "Couldn't load this One Pager. Please retry."
    page_text = " ".join(e.value for e in at.error)
    assert "secret internals" not in page_text
    assert "RuntimeError" not in page_text

    retry = at.button(key="preview_retry_load")
    assert retry.label == "Retry"
    retry.click().run()
    assert not at.exception
    assert at.error[0].value == "Couldn't load this One Pager. Please retry."
