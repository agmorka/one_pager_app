"""Shared pytest fixtures.

Constants, builders and fakes that are not fixtures live in ``tests.helpers``.
"""

import importlib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import streamlit as st

from onepagerapp.config import AppConfig
from onepagerapp.data_access import lakehouse
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.editing import open_for_edit, working_copy
from onepagerapp.models import (
    CurrentUser,
    NewOnePagerInput,
    OnePagerDocument,
    PersonRef,
)
from onepagerapp.workflow import create_one_pager
from tests.helpers import (
    ALICE,
    APP_DIR,
    CREATOR_ROLES,
    FIXTURES_DIR,
    MAJA,
    NEW_ID,
    NOW,
    SESSION_ID,
    fake_connection,
)

# ============================================================================
# Data
# ============================================================================


@pytest.fixture
def document_store(tmp_path: Path) -> OnePagerDocumentStore:
    """Return a store on the read-only fixtures that writes to a temp dir."""
    return OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path / "written")


@pytest.fixture
def mock_data_access(document_store: OnePagerDocumentStore) -> MockDataAccess:
    """Return mock data seeded with OP-0001 (Approved) and OP-0002 (In Review)."""
    return MockDataAccess(document_store)


@pytest.fixture
def creator() -> CurrentUser:
    """Return the user who creates new One Pagers (MJO, Owner in valid_input)."""
    return MAJA


@pytest.fixture
def valid_input() -> NewOnePagerInput:
    """Return create-form input that passes validation, owned by MJO."""
    return NewOnePagerInput(
        data_product="customer_master",
        product_name="Customer Master Data",
        business_domain="Customer",
        data_product_type="Foundational",
        description="Unified, authoritative view of all customers.",
        owner=PersonRef(
            name="Maja Johansen",
            initials="MJO",
            email="maja.johansen@bec.dk",
            team="Data Platform",
        ),
        smes=[
            PersonRef(name="Diana Prince", initials="DPR", email="diana@bec.dk"),
        ],
        business_problem_statement="Customer data is scattered across systems.",
    )


@pytest.fixture
def draft_id(
    valid_input: NewOnePagerInput,
    creator: CurrentUser,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
) -> str:
    """Create OP-0003 v0.1.0 as a Draft (Owner MJO, SME DPR) and return its ID."""
    result = create_one_pager(
        valid_input,
        creator,
        mock_data_access,
        document_store,
        now=NOW,
        roles=CREATOR_ROLES,
    )
    assert result.one_pager_id == NEW_ID
    return NEW_ID


@pytest.fixture
def alices_draft(
    mock_data_access: MockDataAccess, document_store: OnePagerDocumentStore
) -> MockDataAccess:
    """Create OP-0003 "Customer Master" owned by Alice (SME DPR); return the data."""
    result = create_one_pager(
        NewOnePagerInput(
            data_product="customer_master",
            product_name="Customer Master",
            business_domain="Customer",
            data_product_type="Foundational",
            description="Unified customer view",
            owner=PersonRef("Alice Brown", "ABR", "alice.brown@company.com"),
            smes=[PersonRef("Diana Prince", "DPR", "diana@bec.dk")],
        ),
        ALICE,
        mock_data_access,
        document_store,
        now=NOW,
        roles=CREATOR_ROLES,
    )
    assert result.one_pager_id == NEW_ID
    return mock_data_access


@pytest.fixture
def opened_draft(
    draft_id: str, creator: CurrentUser, mock_data_access: MockDataAccess
) -> OnePagerDocument:
    """Open the new Draft in the Editor (lock held by SESSION_ID) for a copy."""
    session = open_for_edit(mock_data_access, draft_id, creator, SESSION_ID, now=NOW)
    return working_copy(session.document)


# ============================================================================
# Lakehouse
# ============================================================================


@pytest.fixture
def connection() -> SimpleNamespace:
    """Return a fake Statement API connection that records every statement."""
    return fake_connection()


@pytest.fixture
def patched_lakehouse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, connection: SimpleNamespace
) -> LakehouseAccess:
    """Return a ``LakehouseAccess`` with default settings on the fake connection."""
    monkeypatch.setattr(lakehouse, "DatabricksConnection", lambda _config: connection)
    config = AppConfig(ONE_PAGER_APP_VOLUME_PATH=str(tmp_path))
    return lakehouse.LakehouseAccess(config, OnePagerDocumentStore(tmp_path))


# ============================================================================
# Streamlit app
# ============================================================================


@pytest.fixture
def import_app_module(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], ModuleType]:
    """Return an importer for modules under app/ (puts app/ on sys.path)."""
    monkeypatch.syspath_prepend(str(APP_DIR))
    return importlib.import_module


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record st.switch_page targets instead of switching; put app/ on sys.path."""
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets
