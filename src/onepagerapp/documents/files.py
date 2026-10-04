"""File access used by the document store: local filesystem or UC volume.

Databricks Apps do not mount Unity Catalog volumes, so ``/Volumes/...`` is not
a folder inside the app container: writing there with ``open()`` fails with
``Permission denied: '/Volumes'``. Deployed, the volume is read and written
through the Files API (``VolumeFiles``) instead, as the service principal of
the ``WorkspaceClient`` it is given (``service_identity.service_client``).

Both classes raise the built-in exceptions (``FileNotFoundError``,
``FileExistsError``, ``OSError``), so the store handles them the same way.
"""

import io
import logging
from pathlib import Path
from typing import Protocol

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceConflict
from databricks.sdk.errors.base import DatabricksError

logger = logging.getLogger(__name__)


class FileAccess(Protocol):
    """The file operations the document store needs."""

    def read_text(self, path: Path) -> str:
        """Content of a file; ``FileNotFoundError`` when it does not exist."""

    def exists(self, path: Path) -> bool:
        """Whether the file exists."""

    def create(self, path: Path, content: str) -> None:
        """Write a new file, creating its folder; ``FileExistsError`` if it exists."""

    def delete(self, path: Path) -> None:
        """Delete a file; ``FileNotFoundError`` when it does not exist."""

    def list_dirs(self, path: Path) -> list[str]:
        """Names of the sub-folders of ``path`` (empty when it does not exist)."""

    def list_files(self, path: Path) -> list[str]:
        """Names of the files in ``path`` (empty when it does not exist)."""


def _os_error(path: Path, error: DatabricksError) -> OSError:
    """Return the ``OSError`` the store handles for a failed Files API call."""
    return OSError(f"{path}: {error}")


class LocalFiles:
    """The local filesystem (``local-mock``, tests, a mounted folder)."""

    def read_text(self, path: Path) -> str:
        return Path(path).read_text(encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return Path(path).exists()

    def create(self, path: Path, content: str) -> None:
        local = Path(path)
        local.parent.mkdir(parents=True, exist_ok=True)
        with local.open("x", encoding="utf-8") as f:
            f.write(content)

    def delete(self, path: Path) -> None:
        Path(path).unlink()

    def list_dirs(self, path: Path) -> list[str]:
        local = Path(path)
        if not local.is_dir():
            return []
        return [p.name for p in local.iterdir() if p.is_dir()]

    def list_files(self, path: Path) -> list[str]:
        local = Path(path)
        if not local.is_dir():
            return []
        return [p.name for p in local.iterdir() if p.is_file()]


class VolumeFiles:
    """A Unity Catalog volume through the Files API (``/Volumes/<c>/<s>/<v>/...``).

    Every call runs as the principal of ``client``.
    """

    def __init__(self, client: WorkspaceClient) -> None:
        """Files of the volume, accessed with ``client``."""
        self._client = client

    def read_text(self, path: Path) -> str:
        try:
            response = self._client.files.download(str(path))
        except NotFound as e:
            raise FileNotFoundError(str(path)) from e
        except DatabricksError as e:
            raise _os_error(path, e) from e
        if response.contents is None:
            msg = f"{path}: the download returned no content"
            raise OSError(msg)
        with response.contents as contents:
            return contents.read().decode("utf-8")

    def exists(self, path: Path) -> bool:
        try:
            self._client.files.get_metadata(str(path))
        except NotFound:
            return False
        except DatabricksError as e:
            raise _os_error(path, e) from e
        return True

    def create(self, path: Path, content: str) -> None:
        # The Files API creates missing parent folders on upload.
        try:
            self._client.files.upload(
                str(path), io.BytesIO(content.encode("utf-8")), overwrite=False
            )
        except ResourceConflict as e:
            raise FileExistsError(str(path)) from e
        except DatabricksError as e:
            raise _os_error(path, e) from e

    def delete(self, path: Path) -> None:
        try:
            self._client.files.delete(str(path))
        except NotFound as e:
            raise FileNotFoundError(str(path)) from e
        except DatabricksError as e:
            raise _os_error(path, e) from e

    def list_dirs(self, path: Path) -> list[str]:
        return [name for name, is_dir in self._entries(path) if is_dir]

    def list_files(self, path: Path) -> list[str]:
        return [name for name, is_dir in self._entries(path) if not is_dir]

    def _entries(self, path: Path) -> list[tuple[str, bool]]:
        try:
            return [
                (
                    entry.name or Path(entry.path or "").name,
                    bool(entry.is_directory),
                )
                for entry in self._client.files.list_directory_contents(str(path))
            ]
        except NotFound:
            return []
        except DatabricksError as e:
            raise _os_error(path, e) from e
