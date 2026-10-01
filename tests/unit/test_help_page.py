"""Help page (UI_Design.md §4.6): content service and AppTest smoke tests."""

from pathlib import Path
from typing import NoReturn

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.help_content import (
    DEFAULT_NODE_COLOR,
    QUICK_REFERENCE,
    ROLE_REQUEST,
    ROLES,
    combinations_table,
    dp_legend,
    op_legend,
    state_diagram_dot,
    text_color,
    transition_table,
)
from onepagerapp.state_machine import DP_STATUSES, OP_STATUSES, TRANSITIONS, Actor
from onepagerapp.workflow import get_workflow_reference
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def data_access(tmp_path: Path) -> MockDataAccess:
    return MockDataAccess(OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path))


# ============================================================================
# Content
# ============================================================================


@pytest.mark.unit
def test__legend__uses_the_reference_table(data_access: MockDataAccess) -> None:
    legend = op_legend(data_access.get_ref_op_status())

    assert [b.status for b in legend] == list(OP_STATUSES)
    approved = next(b for b in legend if b.status == "Approved")
    assert approved.color == "#65B676"
    assert [b.status for b in legend if b.is_terminal] == ["Cancelled"]
    dp_badges = dp_legend(data_access.get_ref_dp_status())
    assert {b.status for b in dp_badges if b.is_terminal} == {"Deprecated", "Cancelled"}


@pytest.mark.unit
def test__legend__complete_without_reference_data() -> None:
    legend = dp_legend(pd.DataFrame())

    assert [b.status for b in legend] == list(DP_STATUSES)
    assert {b.color for b in legend} == {DEFAULT_NODE_COLOR}


@pytest.mark.unit
def test__legend__orders_by_sort_order_and_uses_display_labels() -> None:
    ref = pd.DataFrame(
        [
            {"status": "Draft", "display_label": "Draft", "sort_order": 2},
            {"status": "Approved", "display_label": "Approved ✓", "sort_order": 1},
        ]
    )
    legend = op_legend(ref)

    assert [b.status for b in legend][:2] == ["Approved", "Draft"]
    assert legend[0].label == "Approved ✓"


@pytest.mark.unit
def test__state_diagram__has_every_status_and_transition(
    data_access: MockDataAccess,
) -> None:
    reference = get_workflow_reference()
    dot = state_diagram_dot(
        reference["one_pager_transitions"], op_legend(data_access.get_ref_op_status())
    )

    assert dot.startswith("digraph {")
    for status in OP_STATUSES:
        assert f'"{status}" [label=' in dot
    assert '"In Review" -> "Approved" [label="Approve"]' in dot
    assert '"In Review" -> "Draft Update" [label="Reject"]' in dot
    assert '"Draft" -> "Cancelled" [label="Cancel One Pager"]' in dot
    assert '"Cancelled" [label="Cancelled", fillcolor="#F34421"' in dot
    assert "peripheries=2" in dot  # Cancelled is terminal


@pytest.mark.unit
def test__state_diagram__system_transitions_are_dashed() -> None:
    dot = state_diagram_dot(
        get_workflow_reference()["data_product_transitions"], dp_legend(pd.DataFrame())
    )

    assert (
        '"In Definition" -> "Ready for Development" [label="First approval", '
        'style="dashed"]' in dot
    )
    assert '"Active" -> "Deprecated" [label="Deprecate"];' in dot


@pytest.mark.unit
def test__transition_table__covers_every_rule() -> None:
    reference = get_workflow_reference()
    table = pd.concat(
        [
            transition_table(reference["one_pager_transitions"]),
            transition_table(reference["data_product_transitions"]),
        ]
    )

    assert len(table) == len(TRANSITIONS)
    approve = table[(table["From"] == "In Review") & (table["To"] == "Approved")]
    assert approve.iloc[0]["Who"] == "Approver"
    assert "Reviewer is not Owner/SME" in approve.iloc[0]["Conditions"]
    first_approval = table[table["Action"] == "First approval"]
    assert first_approval.iloc[0]["Who"] == "Automatic"


@pytest.mark.unit
def test__combinations_table__one_row_per_op_status() -> None:
    table = combinations_table(get_workflow_reference()["valid_combinations"])

    assert list(table["One Pager status"]) == list(OP_STATUSES)
    draft = table[table["One Pager status"] == "Draft"].iloc[0]
    assert draft["Valid Data Product statuses"] == "In Definition"


@pytest.mark.unit
def test__static_content__roles_and_quick_reference() -> None:
    assert [r.role for r in ROLES] == ["Owner / SME", "Approver", "Admin", "Viewer"]
    titles = [q.title for q in QUICK_REFERENCE]
    assert "Create a One Pager" in titles
    assert "Review (Approvers)" in titles
    assert all(q.steps for q in QUICK_REFERENCE)
    assert "Entra ID groups" in ROLE_REQUEST.steps[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("background", "expected"),
    [
        ("#F9BD00", "#1B1B1B"),
        ("#808080", "#FFFFFF"),
        ("#0c1c49", "#FFFFFF"),
        ("bad", "#FFFFFF"),
    ],
)
def test__text_color__readable_on_badge(background: str, expected: str) -> None:
    assert text_color(background) == expected


# ============================================================================
# Page
# ============================================================================


class _NoRefData(MockDataAccess):
    def get_ref_op_status(self) -> NoReturn:
        msg = "table missing: secret internals"
        raise RuntimeError(msg)


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


def _app(data_access: MockDataAccess) -> AppTest:
    user = make_user("ZVI", "Zoe Viewer")
    at = AppTest.from_file(str(APP_DIR / "views" / "help.py"), default_timeout=30)
    state = {
        "services_initialized": True,
        "data_access": data_access,
        "current_user": user.username,
        "current_user_info": user,
        "current_user_roles": frozenset(),
    }
    for key, value in state.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test__help_page__renders_every_section(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    at = _app(data_access).run()

    assert not at.exception
    assert not at.warning
    assert at.title[0].value == "Help"
    headers = [h.value for h in at.header]
    assert headers == [
        "The two-status lifecycle",
        "Roles and responsibilities",
        "Workflow quick reference",
        "Status badges",
    ]
    subheaders = [s.value for s in at.subheader]
    expected = {"One Pager status", "Data Product status", "Valid status combinations"}
    assert expected <= set(subheaders)
    assert len(at.get("graphviz_chart")) == 2
    assert len(at.expander) == 3 + len(QUICK_REFERENCE)
    assert ROLE_REQUEST.title in [e.label for e in at.expander]


@pytest.mark.unit
def test__help_page__without_reference_data_warns_and_still_renders(
    tmp_path: Path, switched: list[str]
) -> None:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    at = _app(_NoRefData(store)).run()

    assert not at.exception
    assert "Couldn't load the status colors" in at.warning[0].value
    assert "secret" not in at.warning[0].value
    assert at.button(key="help_retry")
    assert len(at.get("graphviz_chart")) == 2


@pytest.mark.unit
def test__navigation__help_page_for_everyone(switched: list[str]) -> None:
    from app import navigation_entries  # noqa: PLC0415 - needs app/ on sys.path

    for roles in (frozenset(), frozenset({Actor.APPROVER}), frozenset({Actor.ADMIN})):
        assert ("views/help.py", "Help") in navigation_entries(roles)
