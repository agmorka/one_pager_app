"""The YAML document store on a local folder (fixtures read-only)."""

from pathlib import Path

import pytest

from onepagerapp.documents import OnePagerDocumentStore
from tests.helpers import FIXTURES_DIR, IN_REVIEW_ID


@pytest.mark.unit
def test__fixture_documents__read__parsed() -> None:
    """The sample documents load."""
    # Given
    store = OnePagerDocumentStore(FIXTURES_DIR)

    # When
    approved, in_review = (
        store.read("OP-0001", "1.0.0"),
        store.read(IN_REVIEW_ID, "0.3.0"),
    )

    # Then
    assert approved.product_name == "Person Master Data"
    assert in_review.data_product == "order"


@pytest.mark.unit
def test__write_path__write__file_written_outside_the_fixtures(
    document_store: OnePagerDocumentStore, tmp_path: Path
) -> None:
    """New versions go to the write path; the fixtures stay untouched."""
    # Given
    document = document_store.read(IN_REVIEW_ID, "0.3.0")
    document.version = "0.4.0"

    # When
    path = document_store.write(IN_REVIEW_ID, document, "0.4.0")

    # Then
    file = Path("one_pagers") / IN_REVIEW_ID / "OP-0002_v0.4.0.yml"
    assert path == tmp_path / "written" / file
    assert not (FIXTURES_DIR / file).exists()


@pytest.mark.unit
def test__version_in_write_path__read__found_across_both_roots(
    document_store: OnePagerDocumentStore,
) -> None:
    """Reads see the fixtures and the written files; latest spans both."""
    # Given
    document = document_store.read(IN_REVIEW_ID, "0.3.0")
    document.version = "0.4.0"
    document_store.write(IN_REVIEW_ID, document, "0.4.0")

    # When
    written, latest = (
        document_store.read(IN_REVIEW_ID, "0.4.0"),
        document_store.read(IN_REVIEW_ID),
    )

    # Then
    assert written.version == latest.version == "0.4.0"
    assert document_store.exists(IN_REVIEW_ID, "0.3.0")
    assert "OP-0001" in document_store.list_ids()


@pytest.mark.unit
def test__version_file_exists__write_again__raises(
    document_store: OnePagerDocumentStore,
) -> None:
    """Version files are immutable."""
    # Given
    document = document_store.read(IN_REVIEW_ID, "0.3.0")
    document_store.write("OP-9999", document, "0.1.0")

    # When / Then
    with pytest.raises(RuntimeError, match="Failed to write"):
        document_store.write("OP-9999", document, "0.1.0")
