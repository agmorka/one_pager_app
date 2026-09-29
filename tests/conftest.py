"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from onepagerapp.auth import resolve_current_user
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput, PersonRef

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sample_one_pagers"


@pytest.fixture
def document_store(tmp_path: Path) -> OnePagerDocumentStore:
    """Fixture documents are read-only; new documents go to a temp dir."""
    return OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path / "written")


@pytest.fixture
def mock_data_access(document_store: OnePagerDocumentStore) -> MockDataAccess:
    return MockDataAccess(document_store)


@pytest.fixture
def creator() -> CurrentUser:
    return resolve_current_user("MJOADM@BECOC001.onmicrosoft.com")


@pytest.fixture
def valid_input() -> NewOnePagerInput:
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
