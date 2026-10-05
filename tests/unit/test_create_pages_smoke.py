"""AppTest smoke tests for the create flow in local-mock mode.

Each page script runs on its own with the services injected into session
state, and st.switch_page is recorded (see ``tests.helpers.page_app``).
"""

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import CurrentUser
from tests.helpers import ALICE, NEW_ID, button_labelled, page_app


def _create_form(data_access: MockDataAccess, **state: object) -> AppTest:
    """Return the Editor in create mode for Alice."""
    return page_app("editor.py", data_access, editor_mode="create", **state)


def _fill_and_create(at: AppTest) -> None:
    """Fill the required create fields and click Create Draft."""
    at.text_input(key="create_data_product").input("customer_master")
    at.text_input(key="create_product_name").input("Customer Master")
    at.selectbox(key="create_business_domain").select("Customer")
    at.selectbox(key="create_data_product_type").select("Foundational")
    at.text_area(key="create_description").input("Unified customer view")
    at.run()
    button_labelled(at, "Create Draft").click().run()


@pytest.mark.unit
def test__owner_sme__click_new_on_registry__editor_in_create_mode(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """+ New opens the Editor in create mode."""
    # Given
    at = page_app("registry.py", mock_data_access).run()
    assert not at.exception

    # When
    at.button(key="registry_new").click().run()

    # Then
    assert switched == ["views/editor.py"]
    assert at.session_state["editor_mode"] == "create"


@pytest.mark.unit
def test__viewer__open_registry__no_new_button(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Viewers cannot start a One Pager."""
    # When
    at = page_app("registry.py", mock_data_access, roles=frozenset()).run()

    # Then
    assert not at.exception
    assert not [b for b in at.button if b.key == "registry_new"]


@pytest.mark.unit
def test__viewer__open_editor_in_create_mode__refused(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The Editor checks the permission too."""
    # When
    at = page_app(
        "editor.py", mock_data_access, roles=frozenset(), editor_mode="create"
    ).run()

    # Then
    assert not at.exception
    assert "You don't have permission to create One Pagers." in [
        e.value for e in at.error
    ]


@pytest.mark.unit
def test__no_intent__open_editor__start_from_registry_message(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Opened directly, the Editor points to the Registry."""
    # When
    at = page_app("editor.py", mock_data_access).run()

    # Then
    assert not at.exception
    assert "Start from the Registry" in at.info[0].value


@pytest.mark.unit
def test__signed_in_user__open_create_form__owner_prefilled(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The Owner defaults to the signed-in user."""
    # When
    at = _create_form(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.title[0].value == "New One Pager"
    assert at.text_input(key="create_owner_name").value == "Alice Brown"
    assert at.text_input(key="create_owner_initials").value == "ABR"
    assert at.text_input(key="create_owner_email").value == ALICE.username


@pytest.mark.unit
def test__user_with_directory_email__open_create_form__directory_values(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The directory name and email are preferred when known."""
    # Given
    user = CurrentUser(
        username="x0wadm@becoc001.onmicrosoft.com",
        initials="X0W",
        display_name="Agnieszka Kępkowska",
        email="agnieszka.kepkowska@bec.dk",
    )

    # When
    at = page_app("editor.py", mock_data_access, user, editor_mode="create").run()

    # Then
    assert not at.exception
    assert at.text_input(key="create_owner_name").value == "Agnieszka Kępkowska"
    assert at.text_input(key="create_owner_initials").value == "X0W"
    assert at.text_input(key="create_owner_email").value == (
        "agnieszka.kepkowska@bec.dk"
    )


@pytest.mark.unit
def test__empty_form__click_create_draft__errors_and_nothing_written(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Missing fields are reported and no One Pager is created."""
    # Given
    at = _create_form(mock_data_access).run()

    # When
    button_labelled(at, "Create Draft").click().run()

    # Then
    errors = [e.value for e in at.error]
    assert "Data Product is required." in errors
    assert "Description is required." in errors
    assert "5 issue(s)" in at.warning[0].value
    assert switched == []
    assert mock_data_access.get_one_pager_status(NEW_ID) is None


@pytest.mark.unit
def test__valid_form__click_create_draft__draft_created_preview_opened(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A valid form creates the Draft and opens its Preview."""
    # Given
    at = _create_form(mock_data_access).run()

    # When
    _fill_and_create(at)

    # Then
    assert not at.exception
    assert switched == ["views/preview.py"]
    assert at.session_state["preview_one_pager_id"] == NEW_ID
    assert "OP-0003 created" in at.session_state["preview_flash"]
    header = mock_data_access.get_one_pager_status(NEW_ID)
    assert (header.product_name, header.one_pager_status) == (
        "Customer Master",
        "Draft",
    )


@pytest.mark.unit
def test__draft_just_created__open_preview__shown_with_flash(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The Preview shows the new One Pager and the success message."""
    # Given
    at = _create_form(mock_data_access).run()
    _fill_and_create(at)

    # When
    preview = page_app(
        "preview.py",
        mock_data_access,
        preview_one_pager_id=NEW_ID,
        preview_flash=at.session_state["preview_flash"],
    ).run()

    # Then
    assert not preview.exception
    assert preview.title[0].value == "Customer Master"
    assert "OP-0003 created" in preview.toast[0].value
