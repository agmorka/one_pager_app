from pathlib import Path

import pytest

from onepagerapp.documents.store import OnePagerDocumentStore
from tests.conftest import FIXTURES_DIR


@pytest.mark.unit
def test__write_path__reads_fixtures_and_writes_elsewhere(tmp_path: Path) -> None:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    document = store.read("OP-0002", "0.3.0")
    document.version = "0.4.0"

    path = store.write("OP-0002", document, "0.4.0")

    assert path == tmp_path / "OP-0002" / "OP-0002_v0.4.0.yml"
    assert not (FIXTURES_DIR / "OP-0002" / "OP-0002_v0.4.0.yml").exists()
    assert store.read("OP-0002", "0.4.0").version == "0.4.0"
    assert store.read("OP-0002").version == "0.4.0"  # latest across both roots
    assert store.exists("OP-0002", "0.3.0")
    assert "OP-0001" in store.list_ids()


@pytest.mark.unit
def test__write__version_files_are_immutable(tmp_path: Path) -> None:
    store = OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path)
    document = store.read("OP-0002", "0.3.0")
    store.write("OP-9999", document, "0.1.0")
    with pytest.raises(RuntimeError, match="Failed to write"):
        store.write("OP-9999", document, "0.1.0")
