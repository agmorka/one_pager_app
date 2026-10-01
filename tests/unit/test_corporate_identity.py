"""Identity plan Phase 1, done-when check: a corporate user in local-mock mode.

A user configured as ``x0wadm@becoc001.onmicrosoft.com`` gets the initials
``X0W`` and can create and edit a One Pager with ``X0W`` as the Owner.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.config import AppConfig
from onepagerapp.data_access import create_data_access
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import open_for_edit, save_draft, working_copy
from onepagerapp.models import NewOnePagerInput, PersonRef
from onepagerapp.workflow import create_one_pager
from tests.conftest import FIXTURES_DIR

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
DOMAINS = ["Customer", "Sales"]
TYPES = ["Foundational", "Integrated", "Augmented"]


@pytest.mark.unit
def test__corporate_user_creates_and_edits_own_one_pager(
    tmp_path: Path, valid_input: NewOnePagerInput
) -> None:
    config = AppConfig(
        APP_MODE="local-mock",
        ONE_PAGER_APP_VOLUME_PATH=str(FIXTURES_DIR),
        ONE_PAGER_APP_MOCK_USER="x0wadm@becoc001.onmicrosoft.com",
    )
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    data_access = create_data_access(config, store)
    user = resolve_current_user(data_access.get_current_user(), config)
    assert user.initials == "X0W"

    owner = PersonRef(name="Xenia Wolf", initials="x0w", email="x0w@bec.dk")
    created = create_one_pager(
        replace(valid_input, owner=owner), user, data_access, store, now=NOW
    )
    assert created.errors == []
    one_pager_id = created.one_pager_id
    assert store.read(one_pager_id, "0.1.0").owner_initials == "X0W"
    assert ("X0W", "owner") in {
        (u.user_initials, u.role)
        for u in data_access.get_authorized_users(one_pager_id)
    }

    session = open_for_edit(data_access, one_pager_id, user, "s1", now=NOW)
    document = working_copy(session.document)
    document.description = "Edited by X0W."
    saved = save_draft(
        data_access,
        store,
        one_pager_id,
        document,
        "Updated the description",
        user,
        "s1",
        allowed_domains=DOMAINS,
        allowed_types=TYPES,
        now=NOW + timedelta(minutes=5),
    )

    assert saved.errors == []
    assert saved.version == "0.2.0"
    assert data_access.get_one_pager_status_row(one_pager_id).last_updated_by == "X0W"
