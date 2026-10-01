"""AppTest smoke tests for the Review page (UI_Design.md §4.3)."""

from pathlib import Path
from typing import NoReturn

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.state_machine import Actor
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"


class _FailingDataAccess(MockDataAccess):
    def get_one_pager_status_rows(self, one_pager_status: str) -> NoReturn:  # noqa: ARG002
        msg = "warehouse unreachable: secret internals"
        raise RuntimeError(msg)


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


def _app(
    tmp_path: Path,
    roles: frozenset[Actor] = frozenset({Actor.APPROVER}),
    data_access_cls: type = MockDataAccess,
) -> AppTest:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    user = make_user("CJO")
    at = AppTest.from_file(str(APP_DIR / "views" / "review.py"), default_timeout=30)
    state = {
        "services_initialized": True,
        "data_access": data_access_cls(store),
        "document_store": store,
        "current_user": user.username,
        "current_user_info": user,
        "current_user_roles": roles,
    }
    for key, value in state.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test__review_page__lists_in_review_items(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(tmp_path).run()

    assert not at.exception
    assert at.title[0].value == "Review Queue"
    assert at.metric[0].value == "1"
    assert any("OP-0002" in m.value for m in at.markdown)

    at.button(key="review_open_OP-0002").click().run()
    assert switched == ["views/preview.py"]
    assert at.session_state["preview_one_pager_id"] == "OP-0002"
    assert at.session_state["preview_review_mode"] == "OP-0002"


@pytest.mark.unit
def test__review_page__empty_queue(tmp_path: Path, switched: list[str]) -> None:
    at = _app(tmp_path)
    at.session_state["data_access"]._status_rows.pop("OP-0002")
    at.run()

    assert not at.exception
    assert at.info[0].value == "Nothing waiting for your review."


@pytest.mark.unit
def test__review_page__not_for_non_approvers(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(tmp_path, roles=frozenset()).run()

    assert not at.exception
    assert "Only Approvers" in at.info[0].value
    assert not at.metric


@pytest.mark.unit
def test__review_page__error_state_hides_internals(
    tmp_path: Path, switched: list[str]
) -> None:
    at = _app(tmp_path, data_access_cls=_FailingDataAccess).run()

    assert not at.exception
    assert at.error[0].value == "Couldn't load the review queue. Please retry."
    assert "secret" not in at.error[0].value
    assert at.button(key="review_retry")


@pytest.mark.unit
def test__navigation__review_page_only_for_approvers(
    switched: list[str],  # puts app/ on sys.path
) -> None:
    from app import navigation_entries  # noqa: PLC0415 - needs app/ on sys.path

    def titles(roles: frozenset[Actor]) -> list[str]:
        return [title for _, title in navigation_entries(roles)]

    assert "Review" in titles(frozenset({Actor.APPROVER}))
    assert "Review" not in titles(frozenset())
    assert "Review" not in titles(frozenset({Actor.ADMIN}))
