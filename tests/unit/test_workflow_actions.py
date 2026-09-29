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


@pytest.mark.unit
def test__change_dp_status_and_report(
    actions: ModuleType, data_access: MockDataAccess, alice: CurrentUser
) -> None:
    # OP-0001 is seeded Approved / Ready for Development, owned by Alice.
    assert (
        actions.change_dp_status_and_report(
            data_access, "OP-0001", "In Development", alice, confirmed=True
        )
        is None
    )
    assert st.session_state["preview_flash"] == (
        "Data Product status changed to In Development."
    )
    error = actions.change_dp_status_and_report(
        data_access, "OP-0001", "Deprecated", alice, confirmed=True
    )
    assert "cannot change from In Development to Deprecated" in error


@pytest.mark.unit
def test__reject__requires_a_reason_then_reports_success(
    actions: ModuleType, data_access: MockDataAccess
) -> None:
    from onepagerapp.state_machine import Actor  # noqa: PLC0415

    approver = resolve_current_user("cjo@bec.dk")
    roles = frozenset({Actor.APPROVER})
    st.session_state["preview_review_mode"] = "OP-0002"

    error = actions.reject_and_report(data_access, "OP-0002", approver, " ", roles)
    assert error == "Explain why the One Pager is rejected."
    assert data_access.get_one_pager_status_row("OP-0002").one_pager_status == (
        "In Review"
    )

    error = actions.reject_and_report(
        data_access, "OP-0002", approver, "Data sources missing", roles
    )
    assert error is None
    assert data_access.get_one_pager_status_row("OP-0002").one_pager_status == "Draft"
    assert "rejected" in st.session_state["preview_flash"]
    assert "preview_review_mode" not in st.session_state


@pytest.mark.unit
def test__reject__self_review_is_reported(
    actions: ModuleType, data_access: MockDataAccess
) -> None:
    from onepagerapp.state_machine import Actor  # noqa: PLC0415

    owner = resolve_current_user("bob.smith@company.com")  # Owner of OP-0002
    error = actions.reject_and_report(
        data_access, "OP-0002", owner, "No", frozenset({Actor.APPROVER})
    )
    assert error == "You cannot review a One Pager on which you are Owner or SME."


@pytest.mark.unit
def test__approve__reports_the_new_version(
    actions: ModuleType, data_access: MockDataAccess, tmp_path: Path
) -> None:
    from onepagerapp.state_machine import Actor  # noqa: PLC0415

    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path / "approve")
    approver = resolve_current_user("cjo@bec.dk")
    st.session_state["preview_review_mode"] = "OP-0002"

    error = actions.approve_and_report(
        data_access, store, "OP-0002", approver, frozenset({Actor.APPROVER})
    )

    assert error is None
    assert st.session_state["preview_flash"] == (
        "OP-0002 was approved as v1.0.0. The Data Product is now "
        "Ready for Development."
    )
    assert "preview_review_mode" not in st.session_state

    error = actions.approve_and_report(
        data_access, store, "OP-0002", approver, frozenset({Actor.APPROVER})
    )
    assert error == "The One Pager is Approved, not In Review."


@pytest.mark.unit
def test__add_comment__reports_errors_and_success(
    actions: ModuleType, data_access: MockDataAccess
) -> None:
    from onepagerapp.state_machine import Actor  # noqa: PLC0415

    approver = resolve_current_user("cjo@bec.dk")
    roles = frozenset({Actor.APPROVER})

    assert (
        actions.add_comment_and_report(
            data_access, "OP-0002", approver, "useCases", " ", roles
        )
        == "Write a comment."
    )
    assert (
        actions.add_comment_and_report(
            data_access, "OP-0002", approver, "useCases", "Link UC-001", roles
        )
        is None
    )
    assert st.session_state["preview_flash"] == "Your review comment was added."
    [comment] = data_access.get_review_comments("OP-0002")

    owner = resolve_current_user("bob.smith@company.com")
    error = actions.resolve_comment_and_report(
        data_access, "OP-0002", comment.id, owner
    )
    assert error == (
        "Comments are resolved while the One Pager is being reworked (Draft)."
    )
