"""Smoke tests for the Use Cases page (Streamlit AppTest, mock data access)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, UseCaseInput
from tests.helpers import MAJA, failing, markdown_text, page_app

MANAGE_LABELS = {"Edit", "Deprecate", "Restore"}


@pytest.fixture
def data_access(tmp_path: Path) -> MockDataAccess:
    """Return mock data with the seeded Use Cases (UC-005 deprecated)."""
    return MockDataAccess(OnePagerDocumentStore(tmp_path))


def _use_cases_page(
    data_access: MockDataAccess, user: CurrentUser | None = MAJA
) -> AppTest:
    """Run the Use Cases page for ``user`` (an Owner/SME by default)."""
    return page_app("use_cases.py", data_access, user).run()


def _open_details(at: AppTest, use_case_id: str) -> None:
    """Select a Use Case as a row click does (AppTest cannot click rows)."""
    at.session_state["uc_selected_id"] = use_case_id
    at.run()


@pytest.mark.unit
def test__active_use_cases__open_page__listed_with_reference_counts(
    data_access: MockDataAccess,
) -> None:
    """Active Use Cases are listed; deprecated ones are hidden by default."""
    # When
    at = _use_cases_page(data_access)

    # Then
    assert not at.exception
    assert "Showing 1-4 of 4 Use Cases" in markdown_text(at)
    table = at.dataframe[0].value
    assert "UC-001" in list(table["ID"])
    assert "UC-005" not in list(table["ID"])
    assert "2 One Pagers" in list(table["Used by"])
    assert any(b.key == "uc_new" for b in at.button)


@pytest.mark.unit
def test__deprecated_use_case__check_show_deprecated__shown_labelled(
    data_access: MockDataAccess,
) -> None:
    """Deprecated rows appear, labelled in the Status column."""
    # Given
    at = _use_cases_page(data_access)

    # When
    at.checkbox(key="uc_filter_show_deprecated").check().run()

    # Then
    assert "Showing 1-5 of 5 Use Cases" in markdown_text(at)
    table = at.dataframe[0].value.set_index("ID")
    assert table.loc["UC-005", "Status"] == "Deprecated"


@pytest.mark.unit
def test__search_without_match__enter_search__empty_warning(
    data_access: MockDataAccess,
) -> None:
    """A search with no match explains why the list is empty."""
    # Given
    at = _use_cases_page(data_access)

    # When
    at.text_input(key="uc_filter_search").input("no such persona").run()

    # Then
    assert any("No Use Cases match your filters" in w.value for w in at.warning)


@pytest.mark.unit
def test__empty_search_result__click_clear__full_list(
    data_access: MockDataAccess,
) -> None:
    """Clearing the filters shows every active Use Case again."""
    # Given
    at = _use_cases_page(data_access)
    at.text_input(key="uc_filter_search").input("no such persona").run()

    # When
    at.button(key="uc_clear_empty").click().run()

    # Then
    assert at.text_input(key="uc_filter_search").value == ""
    assert "Showing 1-4 of 4 Use Cases" in markdown_text(at)


@pytest.mark.unit
def test__list_fails_to_load__open_page__friendly_banner_with_retry(
    data_access: MockDataAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A load error hides the internals and offers a retry."""
    # Given
    monkeypatch.setattr(data_access, "get_use_cases", failing("warehouse unavailable"))

    # When
    at = _use_cases_page(data_access)

    # Then
    assert not at.exception
    assert any("Unable to load Use Cases" in e.value for e in at.error)
    assert all("warehouse unavailable" not in e.value for e in at.error)
    assert any(b.label == "Retry" for b in at.button)


@pytest.mark.unit
def test__owner_sme__open_details__references_and_actions(
    data_access: MockDataAccess,
) -> None:
    """Details list the referencing One Pagers and the management actions."""
    # Given
    at = _use_cases_page(data_access)

    # When
    _open_details(at, "UC-002")

    # Then
    assert "**Referenced by:** OP-0001, OP-0002" in markdown_text(at)
    assert {"Edit", "Deprecate", "Close"} <= {b.label for b in at.button}


@pytest.mark.unit
@pytest.mark.parametrize(
    "user",
    [None, CurrentUser("alice.brown@company.com", "", "Alice Brown")],
    ids=["no-user", "unrecognised"],
)
def test__user_who_may_not_manage__open_details__read_only(
    data_access: MockDataAccess, user: CurrentUser | None
) -> None:
    """Without a recognised Owner/SME there is no New, Edit or Deprecate."""
    # Given
    at = _use_cases_page(data_access, user=user)

    # When
    _open_details(at, "UC-002")

    # Then
    assert all(b.key != "uc_new" for b in at.button)
    assert not MANAGE_LABELS & {b.label for b in at.button}


@pytest.mark.unit
def test__deprecated_use_case__click_restore__active_again(
    data_access: MockDataAccess,
) -> None:
    """Restoring makes the Use Case active and records the user."""
    # Given
    at = _use_cases_page(data_access)
    at.checkbox(key="uc_filter_show_deprecated").check().run()
    _open_details(at, "UC-005")

    # When
    at.button(key="uc_restore").click().run()

    # Then
    assert not at.exception
    restored = data_access.get_use_case("UC-005")
    assert not restored.deprecated
    assert restored.last_updated_by == "MJO"
    assert any("Restored UC-005" in s.value for s in at.toast)


@pytest.mark.unit
def test__markdown_in_user_text__open_page__rendered_literally(
    data_access: MockDataAccess,
) -> None:
    """User text is shown as typed: in the table and escaped in the details."""
    # Given
    data_access.create_use_case(
        UseCaseInput(
            persona="**bold** :red[x] $x$",
            goal="goal",
            scenario="s",
            decision_enabled="d",
            priority="Low",
        ),
        "ABR",
    )

    # When
    at = _use_cases_page(data_access)
    new_id = list(at.dataframe[0].value["ID"])[-1]
    _open_details(at, new_id)

    # Then
    assert "**bold** :red[x] $x$" in list(at.dataframe[0].value["Persona"])
    assert "\\*\\*bold\\*\\* \\:red\\[x\\] \\$x\\$" in markdown_text(at)
