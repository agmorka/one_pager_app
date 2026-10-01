"""Smoke tests for the Use Cases page (Streamlit AppTest, mock data access)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, UseCaseFilter, UseCaseInput, UseCasePage
from tests.users import CREATOR_ROLES, make_user

MJO = make_user("MJO")
PAGE = str(Path(__file__).parents[2] / "app" / "views" / "use_cases.py")


class FailingDataAccess(MockDataAccess):
    def get_use_cases(
        self,
        filter: UseCaseFilter,  # noqa: A002, ARG002
        page: int,  # noqa: ARG002
        page_size: int,  # noqa: ARG002
    ) -> UseCasePage:
        msg = "warehouse unavailable"
        raise RuntimeError(msg)


def _app(data_access: MockDataAccess, user: CurrentUser | None = MJO) -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.session_state["data_access"] = data_access
    if user:
        at.session_state["current_user"] = user.username
        at.session_state["current_user_info"] = user
        at.session_state["current_user_roles"] = CREATOR_ROLES
    return at.run()


def _markdown(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


@pytest.fixture
def data_access(tmp_path: Path) -> MockDataAccess:
    return MockDataAccess(OnePagerDocumentStore(tmp_path))


@pytest.mark.unit
def test_populated_state_lists_active_use_cases(data_access: MockDataAccess) -> None:
    at = _app(data_access)
    assert not at.exception
    text = _markdown(at)
    assert "Showing 4 of 4 Use Cases" in text
    assert "UC-001" in text
    assert "UC-005" not in text  # deprecated rows hidden by default
    assert "2 OPs" in text
    assert any(b.key == "uc_new" for b in at.button)


@pytest.mark.unit
def test_show_deprecated_reveals_labelled_deprecated_rows(
    data_access: MockDataAccess,
) -> None:
    at = _app(data_access)
    at.checkbox(key="uc_filter_show_deprecated").check().run()
    text = _markdown(at)
    assert "Showing 5 of 5 Use Cases" in text
    assert ":gray[UC-005]" in text
    assert ":gray[Deprecated]" in text


@pytest.mark.unit
def test_empty_after_filter_and_clear(data_access: MockDataAccess) -> None:
    at = _app(data_access)
    at.text_input(key="uc_filter_search").input("no such persona").run()
    assert any("No Use Cases match your filters" in w.value for w in at.warning)

    at.button(key="uc_clear_empty").click().run()
    assert at.text_input(key="uc_filter_search").value == ""
    assert "Showing 4 of 4 Use Cases" in _markdown(at)


@pytest.mark.unit
def test_error_state_shows_friendly_banner(tmp_path: Path) -> None:
    at = _app(FailingDataAccess(OnePagerDocumentStore(tmp_path)))
    assert not at.exception
    assert any("Unable to load Use Cases" in e.value for e in at.error)
    assert all("warehouse unavailable" not in e.value for e in at.error)
    assert any(b.label == "Retry" for b in at.button)


@pytest.mark.unit
def test_details_show_referencing_one_pagers_and_actions(
    data_access: MockDataAccess,
) -> None:
    at = _app(data_access)
    at.button(key="uc_details_UC-002").click().run()
    assert "**Referenced by:** OP-0001, OP-0002" in _markdown(at)
    labels = {b.label for b in at.button}
    assert {"Edit", "Deprecate", "Close"} <= labels


@pytest.mark.unit
def test_read_only_without_manage_permission(data_access: MockDataAccess) -> None:
    at = _app(data_access, user=None)
    at.button(key="uc_details_UC-002").click().run()
    labels = {b.label for b in at.button}
    assert all(b.key != "uc_new" for b in at.button)
    assert not {"Edit", "Deprecate", "Restore"} & labels


@pytest.mark.unit
def test_restore_deprecated_use_case(data_access: MockDataAccess) -> None:
    at = _app(data_access)
    at.checkbox(key="uc_filter_show_deprecated").check().run()
    at.button(key="uc_details_UC-005").click().run()
    at.button(key="uc_restore").click().run()
    assert not at.exception
    assert not data_access.get_use_case("UC-005").deprecated
    assert data_access.get_use_case("UC-005").last_updated_by == "MJO"
    assert any("Restored UC-005" in s.value for s in at.success)


@pytest.mark.unit
def test_user_text_is_rendered_literally(data_access: MockDataAccess) -> None:
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
    at = _app(data_access)
    assert "\\*\\*bold\\*\\* \\:red\\[x\\] \\$x\\$" in _markdown(at)


@pytest.mark.unit
def test_read_only_for_unrecognised_user(data_access: MockDataAccess) -> None:
    unrecognised = CurrentUser("alice.brown@company.com", "", "Alice Brown")
    at = _app(data_access, user=unrecognised)
    at.button(key="uc_details_UC-002").click().run()
    labels = {b.label for b in at.button}
    assert all(b.key != "uc_new" for b in at.button)
    assert not {"Edit", "Deprecate", "Restore"} & labels
