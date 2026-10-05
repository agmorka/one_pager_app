"""Identity plan Phases 1 and 5, done-when checks in local-mock mode.

A user configured as ``x0wadm@becoc001.onmicrosoft.com`` gets the initials
``X0W`` and can create and edit a One Pager with ``X0W`` as the Owner.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.config import AppConfig
from onepagerapp.data_access import create_data_access
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.directory import DirectoryUser
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import open_for_edit, working_copy
from onepagerapp.models import CreateResult, CurrentUser, NewOnePagerInput, PersonRef
from onepagerapp.workflow import create_one_pager
from tests.helpers import CREATOR_ROLES, FIXTURES_DIR, LATER, NOW, SESSION_ID, save

CORPORATE = "x0wadm@becoc001.onmicrosoft.com"


@pytest.fixture
def store(tmp_path: Path) -> OnePagerDocumentStore:
    """Return a store on the fixtures that writes to a temp dir."""
    return OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)


@pytest.fixture
def config() -> AppConfig:
    """Return local-mock settings with the corporate mock user."""
    return AppConfig(
        APP_MODE="local-mock",
        ONE_PAGER_APP_VOLUME_PATH=str(FIXTURES_DIR),
        ONE_PAGER_APP_MOCK_USER=CORPORATE,
    )


@pytest.fixture
def data_access(config: AppConfig, store: OnePagerDocumentStore) -> MockDataAccess:
    """Return the local-mock data access."""
    return create_data_access(config, store)


def _create(
    valid_input: NewOnePagerInput,
    owner: PersonRef,
    user: CurrentUser,
    data_access: MockDataAccess,
    store: OnePagerDocumentStore,
) -> CreateResult:
    """Create a One Pager owned by ``owner`` as ``user``."""
    return create_one_pager(
        replace(valid_input, owner=owner),
        user,
        data_access,
        store,
        now=NOW,
        roles=CREATOR_ROLES,
    )


@pytest.mark.unit
def test__corporate_mock_user__resolve_current_user__initials_x0w(
    config: AppConfig, data_access: MockDataAccess
) -> None:
    """The mock user's username gives the initials X0W."""
    # When
    user = resolve_current_user(data_access.get_current_user(), config)

    # Then
    assert user.initials == "X0W"


@pytest.mark.unit
def test__corporate_user_as_owner__create__owner_and_authorized_user_x0w(
    config: AppConfig,
    valid_input: NewOnePagerInput,
    data_access: MockDataAccess,
    store: OnePagerDocumentStore,
) -> None:
    """Lower-case initials in the form become X0W everywhere."""
    # Given
    user = resolve_current_user(CORPORATE, config)
    owner = PersonRef(name="Xenia Wolf", initials="x0w", email="x0w@bec.dk")

    # When
    created = _create(valid_input, owner, user, data_access, store)

    # Then
    assert created.errors == []
    assert store.read(created.one_pager_id, "0.1.0").owner_initials == "X0W"
    users = data_access.get_authorized_users(created.one_pager_id)
    assert ("X0W", "owner") in {(u.user_initials, u.role) for u in users}


@pytest.mark.unit
def test__own_one_pager__edit_and_save__saved_by_x0w(
    config: AppConfig,
    valid_input: NewOnePagerInput,
    data_access: MockDataAccess,
    store: OnePagerDocumentStore,
) -> None:
    """The corporate user edits their own One Pager."""
    # Given
    user = resolve_current_user(CORPORATE, config)
    owner = PersonRef(name="Xenia Wolf", initials="x0w", email="x0w@bec.dk")
    one_pager_id = _create(valid_input, owner, user, data_access, store).one_pager_id
    session = open_for_edit(data_access, one_pager_id, user, SESSION_ID, now=NOW)
    document = working_copy(session.document)
    document.description = "Edited by X0W."

    # When
    saved = save(
        data_access, store, document, user, one_pager_id=one_pager_id, now=LATER
    )

    # Then
    assert (saved.errors, saved.version) == ([], "0.2.0")
    row = data_access.get_one_pager_status_row(one_pager_id)
    assert row.last_updated_by == "X0W"


@pytest.mark.unit
def test__directory_name__create__name_in_change_log_not_in_document(
    config: AppConfig,
    valid_input: NewOnePagerInput,
    data_access: MockDataAccess,
    store: OnePagerDocumentStore,
) -> None:
    """Identity plan Phase 5, done when: a new change log entry shows the name."""
    # Given
    user = resolve_current_user(
        CORPORATE,
        config,
        DirectoryUser(given_name="Agnieszka", family_name="Kępkowska"),
    )
    owner = PersonRef(name="Agnieszka Kępkowska", initials="X0W", email="a@bec.dk")

    # When
    created = _create(valid_input, owner, user, data_access, store)

    # Then
    assert created.errors == []
    [entry] = data_access.get_change_log(created.one_pager_id)
    assert (entry.author_initials, entry.author_name) == ("X0W", "Agnieszka Kępkowska")
    document = store.read(created.one_pager_id, "0.1.0")
    assert document.created_by is None  # the YAML holds no audit fields
    assert document.change_log == [
        {"version": "0.1.0", "summary": "Initial draft created"}
    ]
