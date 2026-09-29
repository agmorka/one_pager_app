import pytest
import yaml

from onepagerapp.documents.serialization import (
    document_from_dict,
    document_to_dict,
    document_to_yaml,
)
from onepagerapp.documents.store import OnePagerDocumentStore
from tests.conftest import FIXTURES_DIR


@pytest.mark.unit
def test__new_fields_round_trip() -> None:
    data = {
        "structureDefinition": "structure_one_pager_v_1.json",
        "dataProduct": "customer_master",
        "productName": "Customer Master",
        "businessDomain": "Customer",
        "dataProductType": "Foundational",
        "onePagerStatus": "Draft",
        "dataProductStatus": "In Definition",
        "version": "0.1.0",
        "dataProductOwner": {"name": "A B", "initials": "AB", "email": "a@b.dk"},
        "description": "d",
        "createdBy": "A B",
        "createdAt": "2026-09-29T10:00:00+00:00",
        "lastUpdated": "2026-09-29T10:00:00+00:00",
        "changeLog": [
            {
                "version": "0.1.0",
                "date": "2026-09-29T10:00:00+00:00",
                "author": "A B",
                "summary": "Initial draft created",
            }
        ],
    }
    document = document_from_dict(data)
    assert document.created_by == "A B"
    assert document.change_log[0]["summary"] == "Initial draft created"
    assert document_to_dict(document) == data
    assert yaml.safe_load(document_to_yaml(document)) == data


@pytest.mark.unit
def test__empty_business_problem_statement_is_omitted() -> None:
    document = document_from_dict(
        {"dataProduct": "x", "dataProductOwner": {}, "businessProblemStatement": ""}
    )
    assert "businessProblemStatement" not in document_to_dict(document)


@pytest.mark.unit
def test__fixtures_still_load() -> None:
    store = OnePagerDocumentStore(FIXTURES_DIR)
    assert store.read("OP-0001", "1.0.0").product_name == "Person Master Data"
    assert store.read("OP-0002", "0.3.0").data_product == "order"
