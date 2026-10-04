"""Registry lock indicator (UI_Design.md §4.1, Requirements_and_Scope.md §10)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import LockInfo
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _lock(one_pager_id: str, initials: str, expires_in: timedelta) -> LockInfo:
    now = datetime.now(UTC)
    return LockInfo(
        one_pager_id=one_pager_id,
        locked_by_initials=initials,
        locked_by_name=initials,
        session_id="s1",
        acquired_at=now - timedelta(minutes=5),
        last_heartbeat=now - timedelta(minutes=5),
        expires_at=now + expires_in,
    )


class _BrokenLocksDataAccess(MockDataAccess):
    def get_locks(self, one_pager_ids: list[str]) -> list[LockInfo]:  # noqa: ARG002
        msg = "locks table unreachable"
        raise RuntimeError(msg)


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


def _run(tmp_path: Path, data_access_cls: type = MockDataAccess) -> tuple:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    data_access = data_access_cls(store)
    data_access._locks = {
        "OP-0001": _lock("OP-0001", "MJO", timedelta(minutes=20)),
        "OP-0002": _lock("OP-0002", "BSM", -timedelta(minutes=1)),
    }
    user = make_user("ABR", "Alice Brown")
    at = AppTest.from_file(str(APP_DIR / "views" / "registry.py"), default_timeout=30)
    for key, value in {
        "services_initialized": True,
        "data_access": data_access,
        "document_store": store,
        "current_user": user.username,
        "current_user_info": user,
    }.items():
        at.session_state[key] = value
    return at.run(), data_access


@pytest.mark.unit
def test__registry__shows_lock_icon_and_holder_for_active_locks_only(
    tmp_path: Path, switched: list[str]
) -> None:
    at, _ = _run(tmp_path)

    assert not at.exception
    cells = [m.value for m in at.markdown]
    assert "**Lock**" in cells
    assert "MJO" in cells
    assert "BS" not in cells


@pytest.mark.unit
def test__registry__unreadable_locks_do_not_break_the_table(
    tmp_path: Path, switched: list[str]
) -> None:
    at, _ = _run(tmp_path, _BrokenLocksDataAccess)

    assert not at.exception
    assert not at.error
    assert any("Lock status is unavailable" in c.value for c in at.caption)
    cells = [m.value for m in at.markdown]
    assert "OP-0001" in cells
    assert "?" in cells
