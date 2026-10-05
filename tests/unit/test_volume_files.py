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
from onepagerapp.models import OnePagerDocument
from tests.helpers import FIXTURES_DIR

BASE = "/Volumes/dev_bia_meta/onepager_app/one_pager_registry"


def _files_api() -> SimpleNamespace:
    """Return an in-memory Files API: path → bytes; folders implied by paths."""
    files: dict[str, bytes] = {}

    def upload(
        file_path: str, contents: io.BytesIO, *, overwrite: bool = False
    ) -> None:
        if file_path in files and not overwrite:
            raise AlreadyExists(file_path)
        files[file_path] = contents.read()

    def download(file_path: str) -> SimpleNamespace:
        if file_path not in files:
            raise NotFound(file_path)
        return SimpleNamespace(contents=io.BytesIO(files[file_path]))

    def get_metadata(file_path: str) -> None:
        if file_path not in files:
            raise NotFound(file_path)

    def delete(file_path: str) -> None:
        if files.pop(file_path, None) is None:
            raise NotFound(file_path)

    def list_directory_contents(directory_path: str) -> list[DirectoryEntry]:
        prefix = directory_path.rstrip("/") + "/"
        children = {
            p.removeprefix(prefix).split("/")[0]: "/" in p.removeprefix(prefix)
            for p in files
            if p.startswith(prefix)
        }
        if not children:
            raise NotFound(directory_path)
        return [
            DirectoryEntry(name=name, path=prefix + name, is_directory=is_dir)
            for name, is_dir in children.items()
        ]

    return SimpleNamespace(
        files=files,
        upload=upload,
        download=download,
        get_metadata=get_metadata,
        delete=delete,
        list_directory_contents=list_directory_contents,
    )


@pytest.fixture
def files_api() -> SimpleNamespace:
    """Return an empty in-memory Files API."""
    return _files_api()


@pytest.fixture
def volume_store(files_api: SimpleNamespace) -> OnePagerDocumentStore:
    """Return a document store on the volume, through ``files_api``."""
    return OnePagerDocumentStore(
        BASE, files=VolumeFiles(SimpleNamespace(files=files_api))
    )


@pytest.fixture
def document() -> OnePagerDocument:
    """Return the latest version of OP-0001 from the fixtures."""
    return OnePagerDocumentStore(FIXTURES_DIR).read("OP-0001")


@pytest.mark.unit
def test__document__write_to_volume__stored_through_the_files_api(
    volume_store: OnePagerDocumentStore,
    files_api: SimpleNamespace,
    document: OnePagerDocument,
) -> None:
    """The version file is uploaded under the volume path."""
    # When
    path = volume_store.write("OP-0003", document, "0.1.0")

    # Then
    assert str(path) == f"{BASE}/one_pagers/OP-0003/OP-0003_v0.1.0.yml"
    assert str(path) in files_api.files


@pytest.mark.unit
def test__written_version__read_back__same_content_and_listed(
    volume_store: OnePagerDocumentStore, document: OnePagerDocument
) -> None:
    """A written version can be found, listed and read (also as the latest)."""
    # Given
    volume_store.write("OP-0003", document, "0.1.0")

    # When
    exact, latest = volume_store.read("OP-0003", "0.1.0"), volume_store.read("OP-0003")

    # Then
    assert volume_store.exists("OP-0003", "0.1.0")
    assert volume_store.list_ids() == ["OP-0003"]
    assert exact.raw_content == latest.raw_content == document.raw_content


@pytest.mark.unit
def test__version_file_exists__write_again__raises(
    volume_store: OnePagerDocumentStore, document: OnePagerDocument
) -> None:
    """A version file is never overwritten."""
    # Given
    volume_store.write("OP-0003", document, "0.1.0")

    # When / Then
    with pytest.raises(RuntimeError, match="Failed to write"):
        volume_store.write("OP-0003", document, "0.1.0")


@pytest.mark.unit
def test__empty_volume__read_and_list__nothing_found(
    volume_store: OnePagerDocumentStore,
) -> None:
    """Missing files read as None and nothing is listed or discarded."""
    # When / Then
    assert volume_store.read("OP-0009", "0.1.0") is None
    assert volume_store.read("OP-0009") is None
    assert not volume_store.exists("OP-0009", "0.1.0")
    assert volume_store.list_ids() == []
    assert not volume_store.discard_unreferenced("OP-0009", "0.1.0")


@pytest.mark.unit
def test__unreferenced_file__discard_unreferenced__deleted(
    volume_store: OnePagerDocumentStore,
    files_api: SimpleNamespace,
    document: OnePagerDocument,
) -> None:
    """A file no row refers to can be removed."""
    # Given
    volume_store.write("OP-0003", document, "0.1.0")

    # When
    discarded = volume_store.discard_unreferenced("OP-0003", "0.1.0")

    # Then
    assert discarded
    assert not files_api.files


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
def test__volume_path__create_document_store__files_api_for_a_volume_only(
    monkeypatch: pytest.MonkeyPatch, mode: str, volume_path: str, expected: type
) -> None:
    """A ``/Volumes`` path outside mock mode uses the Files API."""
    # Given
    monkeypatch.setattr(factory, "service_client", lambda _: SimpleNamespace())
    config = AppConfig(APP_MODE=mode, ONE_PAGER_APP_VOLUME_PATH=volume_path)

    # When
    store = factory.create_document_store(config)

    # Then
    assert isinstance(store._files, expected)


@pytest.mark.unit
@pytest.mark.parametrize(
    "settings",
    [{}, {"ONE_PAGER_APP_SP_CLIENT_ID": "id"}],
    ids=["no-credentials", "client-id-only"],
)
def test__no_complete_credentials__service_client__default_auth(
    monkeypatch: pytest.MonkeyPatch, settings: dict[str, str]
) -> None:
    """Without a client ID and secret the default auth is used."""
    # Given
    calls: list[dict] = []
    monkeypatch.setattr(connection, "WorkspaceClient", lambda **kw: calls.append(kw))

    # When
    connection.service_client(AppConfig(ONE_PAGER_APP_VOLUME_PATH=BASE, **settings))

    # Then
    assert calls == [{}]


@pytest.mark.unit
def test__service_principal_credentials__service_client__oauth_m2m(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured service principal is used; the secret is not in the repr."""
    # Given
    calls: list[dict] = []
    monkeypatch.setattr(connection, "WorkspaceClient", lambda **kw: calls.append(kw))
    config = AppConfig(
        ONE_PAGER_APP_VOLUME_PATH=BASE,
        ONE_PAGER_APP_SP_CLIENT_ID="spn-id",
        ONE_PAGER_APP_SP_CLIENT_SECRET="spn-secret",  # noqa: S106 - test value
    )

    # When
    connection.service_client(config)

    # Then
    assert calls == [
        {
            "client_id": "spn-id",
            "client_secret": "spn-secret",
            "auth_type": "oauth-m2m",
        }
    ]
    assert "spn-secret" not in repr(config)


@pytest.mark.unit
def test__missing_folder__local_files_list__empty(tmp_path: Path) -> None:
    """Listing a folder that does not exist gives nothing."""
    # Given
    missing = tmp_path / "missing"

    # When
    dirs, files = LocalFiles().list_dirs(missing), LocalFiles().list_files(missing)

    # Then
    assert (dirs, files) == ([], [])
