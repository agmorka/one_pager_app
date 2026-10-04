"""The document store on a Unity Catalog volume, through the Files API.

Databricks Apps do not mount volumes, so writing to ``/Volumes/...`` as a
local folder fails with ``Permission denied: '/Volumes'``.
"""

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import AlreadyExists, NotFound
from databricks.sdk.service.files import DirectoryEntry

from onepagerapp.config import AppConfig
from onepagerapp.data_access import connection, factory
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.documents.files import LocalFiles, VolumeFiles
from tests.conftest import FIXTURES_DIR

BASE = "/Volumes/dev_bia_meta/onepager_app/one_pager_registry"


class _FakeFilesApi:
    """In-memory Files API: path -> bytes; folders are implied by the paths."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload(
        self, file_path: str, contents: io.BytesIO, *, overwrite: bool = False
    ) -> None:
        if file_path in self.files and not overwrite:
            raise AlreadyExists(file_path)
        self.files[file_path] = contents.read()

    def download(self, file_path: str) -> SimpleNamespace:
        if file_path not in self.files:
            raise NotFound(file_path)
        return SimpleNamespace(contents=io.BytesIO(self.files[file_path]))

    def get_metadata(self, file_path: str) -> None:
        if file_path not in self.files:
            raise NotFound(file_path)

    def delete(self, file_path: str) -> None:
        if self.files.pop(file_path, None) is None:
            raise NotFound(file_path)

    def list_directory_contents(self, directory_path: str) -> list[DirectoryEntry]:
        prefix = directory_path.rstrip("/") + "/"
        children = {
            p.removeprefix(prefix).split("/")[0]: "/" in p.removeprefix(prefix)
            for p in self.files
            if p.startswith(prefix)
        }
        if not children:
            raise NotFound(directory_path)
        return [
            DirectoryEntry(name=name, path=prefix + name, is_directory=is_dir)
            for name, is_dir in children.items()
        ]


def _volume_store() -> tuple[OnePagerDocumentStore, _FakeFilesApi]:
    api = _FakeFilesApi()
    client = SimpleNamespace(files=api)
    return OnePagerDocumentStore(BASE, files=VolumeFiles(client)), api


@pytest.mark.unit
def test__volume_store__writes_and_reads_through_the_files_api() -> None:
    document = OnePagerDocumentStore(FIXTURES_DIR).read("OP-0001")
    assert document is not None
    store, api = _volume_store()

    path = store.write("OP-0003", document, "0.1.0")

    assert str(path) == f"{BASE}/OP-0003/OP-0003_v0.1.0.yml"
    assert str(path) in api.files
    assert store.exists("OP-0003", "0.1.0")
    assert store.list_ids() == ["OP-0003"]
    assert store.read("OP-0003", "0.1.0").raw_content == document.raw_content
    assert store.read("OP-0003").raw_content == document.raw_content  # latest


@pytest.mark.unit
def test__volume_store__never_overwrites_a_version_file() -> None:
    document = OnePagerDocumentStore(FIXTURES_DIR).read("OP-0001")
    store, _ = _volume_store()
    store.write("OP-0003", document, "0.1.0")

    with pytest.raises(RuntimeError, match="Failed to write"):
        store.write("OP-0003", document, "0.1.0")


@pytest.mark.unit
def test__volume_store__missing_files() -> None:
    store, _ = _volume_store()

    assert store.read("OP-0009", "0.1.0") is None
    assert store.read("OP-0009") is None
    assert not store.exists("OP-0009", "0.1.0")
    assert store.list_ids() == []
    assert not store.discard_unreferenced("OP-0009", "0.1.0")


@pytest.mark.unit
def test__volume_store__discards_an_unreferenced_file() -> None:
    document = OnePagerDocumentStore(FIXTURES_DIR).read("OP-0001")
    store, api = _volume_store()
    store.write("OP-0003", document, "0.1.0")

    assert store.discard_unreferenced("OP-0003", "0.1.0")
    assert not api.files


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "volume_path", "expected"),
    [
        ("databricks", BASE, VolumeFiles),
        ("local-integration", BASE, VolumeFiles),
        ("databricks", "/tmp/registry", LocalFiles),  # noqa: S108
        ("local-mock", str(FIXTURES_DIR), LocalFiles),
    ],
)
def test__create_document_store__files_api_for_a_volume(
    monkeypatch: pytest.MonkeyPatch, mode: str, volume_path: str, expected: type
) -> None:
    monkeypatch.setattr(factory, "service_client", lambda _: SimpleNamespace())
    config = AppConfig(APP_MODE=mode, ONE_PAGER_APP_VOLUME_PATH=volume_path)

    store = factory.create_document_store(config)

    assert isinstance(store._files, expected)


@pytest.mark.unit
def test__service_client__default_auth_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(connection, "WorkspaceClient", lambda **kw: calls.append(kw))

    connection.service_client(AppConfig(ONE_PAGER_APP_VOLUME_PATH=BASE))
    connection.service_client(  # a client ID alone is not enough
        AppConfig(ONE_PAGER_APP_VOLUME_PATH=BASE, ONE_PAGER_APP_SP_CLIENT_ID="id")
    )

    assert calls == [{}, {}]


@pytest.mark.unit
def test__service_client__configured_service_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(connection, "WorkspaceClient", lambda **kw: calls.append(kw))
    config = AppConfig(
        ONE_PAGER_APP_VOLUME_PATH=BASE,
        ONE_PAGER_APP_SP_CLIENT_ID="spn-id",
        ONE_PAGER_APP_SP_CLIENT_SECRET="spn-secret",  # noqa: S106 - test value
    )

    connection.service_client(config)

    assert calls == [
        {
            "client_id": "spn-id",
            "client_secret": "spn-secret",
            "auth_type": "oauth-m2m",
        }
    ]
    assert "spn-secret" not in repr(config)


@pytest.mark.unit
def test__local_files__lists_only_existing_folders(tmp_path: Path) -> None:
    assert LocalFiles().list_dirs(tmp_path / "missing") == []
    assert LocalFiles().list_files(tmp_path / "missing") == []
