"""Preview workflow action handlers (the dialogs themselves need a browser)."""

from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
import streamlit as st

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput, PersonRef
from onepagerapp.workflow import create_one_pager
from tests.conftest import FIXTURES_DIR

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def actions(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(APP_DIR))
    monkeypatch.setattr(st, "session_state", {})
    from adapters import workflow_actions  # noqa: PLC0415

    return workflow_actions


@pytest.fixture
def alice() -> CurrentUser:
    return resolve_current_user("alice.brown@company.com")


@pytest.fixture
def data_access(tmp_path: Path, alice: CurrentUser) -> MockDataAccess:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    data_access = MockDataAccess(store)
    create_one_pager(
        NewOnePagerInput(
            data_product="customer_master",
            product_name="Customer Master",
            business_domain="Customer",
            data_product_type="Foundational",
            description="Unified customer view",
            owner=PersonRef("Alice Brown", "AB", "alice.brown@company.com"),
        ),
        alice,
        data_access,
        store,
        now=datetime(2026, 9, 29, tzinfo=UTC),
    )
    return data_access


@pytest.mark.unit
def test__cancel_and_report(
    actions: ModuleType, data_access: MockDataAccess, alice: CurrentUser
) -> None:
    assert actions.cancel_and_report(data_access, "OP-0003", alice, "Dup") is None
    row = data_access.get_one_pager_status_row("OP-0003")
    assert row.one_pager_status == "Cancelled"
    assert st.session_state["preview_flash"] == "OP-0003 was cancelled."

    error = actions.cancel_and_report(data_access, "OP-0003", alice, "")
    assert "cannot change from Cancelled" in error


@pytest.mark.unit
def test__cancel_and_report__permission_error_is_shown(
    actions: ModuleType, data_access: MockDataAccess
) -> None:
    stranger = resolve_current_user("xyz@bec.dk")
    error = actions.cancel_and_report(data_access, "OP-0003", stranger, "")
    assert error == "Only the Owner, an SME or an Admin can cancel this One Pager."
