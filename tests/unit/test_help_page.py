"""Help page (UI_Design.md §4.6): content service and AppTest smoke tests."""

from collections.abc import Callable
from types import ModuleType

import pandas as pd
import pytest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.help_content import (
    DEFAULT_NODE_COLOR,
    QUICK_REFERENCE,
    ROLE_REQUEST,
    ROLES,
    combinations_table,
    dp_legend,
    guides_for,
    op_legend,
    state_diagram_dot,
    text_color,
    transition_table,
)
from onepagerapp.state_machine import DP_STATUSES, OP_STATUSES, TRANSITIONS, Actor
from onepagerapp.workflow import get_workflow_reference
from tests.helpers import ADMIN_ROLES, APPROVER_ROLES, failing, make_user, page_app

VIEWER = make_user("ZVI", "Zoe Viewer")


# ============================================================================
# Content
# ============================================================================


@pytest.mark.unit
def test__reference_table__op_legend__statuses_in_order_with_colors(
    mock_data_access: MockDataAccess,
) -> None:
    """The One Pager legend uses the reference colours; Cancelled is terminal."""
    # When
    legend = op_legend(mock_data_access.get_ref_op_status())

    # Then
    assert [b.status for b in legend] == list(OP_STATUSES)
    approved = next(b for b in legend if b.status == "Approved")
    assert approved.color == "#65B676"
    assert [b.status for b in legend if b.is_terminal] == ["Cancelled"]


@pytest.mark.unit
def test__reference_table__dp_legend__terminal_statuses_flagged(
    mock_data_access: MockDataAccess,
) -> None:
    """Deprecated and Cancelled are the terminal Data Product statuses."""
    # When
    legend = dp_legend(mock_data_access.get_ref_dp_status())

    # Then
    assert {b.status for b in legend if b.is_terminal} == {"Deprecated", "Cancelled"}


@pytest.mark.unit
def test__no_reference_data__dp_legend__complete_with_default_color() -> None:
    """Without reference data every status is still listed."""
    # When
    legend = dp_legend(pd.DataFrame())

    # Then
    assert [b.status for b in legend] == list(DP_STATUSES)
    assert {b.color for b in legend} == {DEFAULT_NODE_COLOR}


@pytest.mark.unit
def test__sort_order_and_labels__op_legend__ordered_and_labelled() -> None:
    """The reference sort order and display labels are used."""
    # Given
    ref = pd.DataFrame(
        [
            {"status": "Draft", "display_label": "Draft", "sort_order": 2},
            {"status": "Approved", "display_label": "Approved ✓", "sort_order": 1},
        ]
    )

    # When
    legend = op_legend(ref)

    # Then
    assert [b.status for b in legend][:2] == ["Approved", "Draft"]
    assert legend[0].label == "Approved ✓"


@pytest.mark.unit
def test__op_transitions__state_diagram_dot__every_status_and_transition(
    mock_data_access: MockDataAccess,
) -> None:
    """The DOT graph has a node per status and an edge per transition."""
    # Given
    reference = get_workflow_reference()
    legend = op_legend(mock_data_access.get_ref_op_status())

    # When
    dot = state_diagram_dot(reference["one_pager_transitions"], legend)

    # Then
    assert dot.startswith("digraph {")
    for status in OP_STATUSES:
        assert f'"{status}" [label=' in dot
    assert '"In Review" -> "Approved" [label="Approve"]' in dot
    assert '"In Review" -> "Draft Update" [label="Reject"]' in dot
    assert '"Draft" -> "Cancelled" [label="Cancel One Pager"]' in dot
    assert '"Cancelled" [label="Cancelled", fillcolor="#F34421"' in dot
    assert "peripheries=2" in dot  # Cancelled is terminal


@pytest.mark.unit
def test__dp_transitions__state_diagram_dot__system_transitions_dashed() -> None:
    """Automatic transitions are dashed; owner transitions are solid."""
    # When
    dot = state_diagram_dot(
        get_workflow_reference()["data_product_transitions"], dp_legend(pd.DataFrame())
    )

    # Then
    assert (
        '"In Definition" -> "Ready for Development" [label="First approval", '
        'style="dashed"]' in dot
    )
    assert '"Active" -> "Deprecated" [label="Deprecate"];' in dot


@pytest.mark.unit
def test__all_transitions__transition_table__one_row_per_rule() -> None:
    """The table lists who may do each transition and when."""
    # Given
    reference = get_workflow_reference()

    # When
    table = pd.concat(
        [
            transition_table(reference["one_pager_transitions"]),
            transition_table(reference["data_product_transitions"]),
        ]
    )

    # Then
    assert len(table) == len(TRANSITIONS)
    approve = table[(table["From"] == "In Review") & (table["To"] == "Approved")]
    assert approve.iloc[0]["Who"] == "Approver"
    assert "Reviewer is not Owner/SME" in approve.iloc[0]["Conditions"]
    first_approval = table[table["Action"] == "First approval"]
    assert first_approval.iloc[0]["Who"] == "Automatic"


@pytest.mark.unit
def test__valid_combinations__combinations_table__one_row_per_op_status() -> None:
    """Each One Pager status lists its valid Data Product statuses."""
    # When
    table = combinations_table(get_workflow_reference()["valid_combinations"])

    # Then
    assert list(table["One Pager status"]) == list(OP_STATUSES)
    draft = table[table["One Pager status"] == "Draft"].iloc[0]
    assert draft["Valid Data Product statuses"] == "In Definition"


@pytest.mark.unit
def test__static_content__roles_and_quick_reference__complete() -> None:
    """Roles, quick reference steps and the role request are present."""
    # When
    titles = [q.title for q in QUICK_REFERENCE]

    # Then
    assert [r.role for r in ROLES] == ["Owner / SME", "Approver", "Admin", "Viewer"]
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
def test__badge_background__text_color__readable(
    background: str, expected: str
) -> None:
    """Light backgrounds get dark text, others (and bad values) white."""
    # When
    color = text_color(background)

    # Then
    assert color == expected


# ============================================================================
# Page
# ============================================================================


@pytest.mark.unit
def test__viewer__open_help_page__every_section_rendered(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The page has the lifecycle, roles and quick reference."""
    # When
    at = page_app("help.py", mock_data_access, VIEWER, frozenset()).run()

    # Then
    assert not at.exception
    assert not at.warning
    assert at.title[0].value == "Help"
    assert [h.value for h in at.header] == [
        "How do I…",
        "The two-status lifecycle",
        "Roles and responsibilities",
        "Glossary",
    ]
    expected = {"One Pager status", "Data Product status", "Valid status combinations"}
    assert expected <= {s.value for s in at.subheader}
    assert len(at.get("graphviz_chart")) == 2
    labels = [e.label for e in at.expander]
    viewer_guides = guides_for([])
    # One per guide for everyone, "other roles", 2 transition tables, role request
    assert len(at.expander) == len(viewer_guides) + 4
    assert (
        f"Guides for other roles ({len(QUICK_REFERENCE) - len(viewer_guides)})"
        in labels
    )
    assert ROLE_REQUEST.title in labels


@pytest.mark.unit
def test__approver__open_help_page__review_guide_listed(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Approvers find the review guide among their guides."""
    # When
    at = page_app("help.py", mock_data_access, VIEWER, APPROVER_ROLES).run()

    # Then
    assert "Review (Approvers)" in [e.label for e in at.expander]


@pytest.mark.unit
def test__help_link_on_registry__open_help__registry_guide_first(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The Help button of a page opens Help on that page's guide."""
    # Given
    at = page_app("registry.py", mock_data_access, roles=frozenset()).run()

    # When
    at.button(key="help_link_registry").click().run()

    # Then
    assert switched == ["views/help.py"]
    help_page = page_app(
        "help.py", mock_data_access, VIEWER, frozenset(), help_topic="registry"
    ).run()
    assert help_page.expander[0].label == "Find a One Pager"
    assert "help_topic" not in help_page.session_state


@pytest.mark.unit
def test__reference_data_fails__open_help_page__warning_and_still_rendered(
    mock_data_access: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without status colours the page warns, offers a retry and still draws."""
    # Given
    monkeypatch.setattr(
        mock_data_access,
        "get_ref_op_status",
        failing("table missing: secret internals"),
    )

    # When
    at = page_app("help.py", mock_data_access, VIEWER, frozenset()).run()

    # Then
    assert not at.exception
    assert "Couldn't load the status colors" in at.warning[0].value
    assert "secret" not in at.warning[0].value
    assert at.button(key="help_retry")
    assert len(at.get("graphviz_chart")) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "roles",
    [frozenset(), APPROVER_ROLES, ADMIN_ROLES],
    ids=["viewer", "approver", "admin"],
)
def test__any_roles__navigation_entries__help_page_listed(
    import_app_module: Callable[[str], ModuleType], roles: frozenset[Actor]
) -> None:
    """Everyone sees the Help page."""
    # Given
    app = import_app_module("app")

    # When
    entries = app.navigation_entries(roles)

    # Then
    assert ("views/help.py", "Help") in entries
