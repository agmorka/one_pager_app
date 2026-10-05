"""Filesystem store for One Pager YAML documents.

Reads and writes ``{base}/one_pagers/{id}/{id}_v{version}.yml`` where ``base``
comes from ONE_PAGER_APP_VOLUME_PATH. The One Pager folders are kept in their
own ``one_pagers`` folder, apart from other content of the volume (e.g. the
app wheels). The current version is supplied by the caller (from
the one_pager_status table); a highest-version fallback is used only when no
version is given.

An optional ``write_path`` redirects writes to a separate folder that is read
before ``base_path``. ``local-mock`` mode uses it with a temporary directory so
the version-controlled fixtures are never modified.

Deployed, the base path is the registry volume. Databricks Apps do not mount
volumes, so the store then uses the Files API (``files.VolumeFiles``) and every
read and write runs as the service principal (Architecture.md §8): users need
no volume grant, and the app checks who may open or change a One Pager.
"""

import logging
from pathlib import Path

import yaml

from onepagerapp.documents.files import FileAccess, LocalFiles
from onepagerapp.documents.serialization import document_from_dict, document_to_yaml
from onepagerapp.models import OnePagerDocument

logger = logging.getLogger(__name__)

# Folder under the base path that holds one folder per One Pager.
ONE_PAGERS_FOLDER = "one_pagers"


class OnePagerDocumentStore:
    """Reads and writes One Pager YAML documents at a configured base path."""

    def __init__(
        self,
        base_path: str | Path,
        write_path: str | Path | None = None,
        files: FileAccess | None = None,
    ) -> None:
        """Store at ``base_path``; ``files`` defaults to the local filesystem."""
        if not base_path:
            msg = (
                "One Pager document store requires a base path "
                "(ONE_PAGER_APP_VOLUME_PATH)."
            )
            raise RuntimeError(msg)
        self._base_path = Path(base_path)
        self._write_path = Path(write_path) if write_path else None
        self._files: FileAccess = files or LocalFiles()

    @property
    def _read_paths(self) -> list[Path]:
        """Folders searched on read, most specific first."""
        if self._write_path is None:
            return [self._base_path]
        return [self._write_path, self._base_path]

    def read(
        self, one_pager_id: str, version: str | None = None
    ) -> OnePagerDocument | None:
        """Read a One Pager document.

        Args:
            one_pager_id: The One Pager identifier (e.g. "OP-0001").
            version: Exact version to read (e.g. "1.0.0"). If None, falls back
                to the highest available version file.

        Returns:
            OnePagerDocument if found and parseable, None if not found or empty.

        Raises:
            RuntimeError: If the file exists but cannot be read or parsed.

        """
        if version:
            path = self._existing_file_for(one_pager_id, version)
            if path is None:
                path = self._file_for(one_pager_id, version)
                logger.info(
                    f"Document for {one_pager_id} v{version} not found at {path}"
                )
                return None
            return self._load(path, one_pager_id)

        latest = self._latest_file(one_pager_id)
        if latest is None:
            logger.info(f"No document files found for {one_pager_id}")
            return None
        logger.warning(f"No version specified for {one_pager_id}; using {latest.name}")
        return self._load(latest, one_pager_id)

    def write(
        self, one_pager_id: str, document: OnePagerDocument, version: str
    ) -> Path:
        """Write a One Pager document as ``{id}_v{version}.yml``.

        Regenerates YAML from the document's structured fields and updates
        ``raw_content`` to match. Returns the written path. Version files are
        immutable (Decision_Log §6): an existing file is never overwritten.

        Raises:
            RuntimeError: If the file already exists or cannot be written.

        """
        path = self._file_for(one_pager_id, version, self._write_root)
        content = document_to_yaml(document)
        try:
            self._files.create(path, content)
        except OSError as e:
            logger.error(f"Failed to write document {path}: {e}")  # noqa: TRY400 - callers log the traceback
            msg = f"Failed to write document for {one_pager_id}: {e}"
            raise RuntimeError(msg) from e
        document.raw_content = content
        return path

    def discard_unreferenced(self, one_pager_id: str, version: str) -> bool:
        """Delete a version file that no status row references (compensation).

        Only for a file written by a save that did not complete: the caller
        guarantees ``one_pager_status.version`` does not point to it. Files in
        the read-only base path (fixtures) are never touched.

        Returns:
            True if a file was deleted.

        """
        path = self._file_for(one_pager_id, version, self._write_root)
        try:
            self._files.delete(path)
        except FileNotFoundError:
            return False
        except OSError:
            logger.exception(f"Failed to discard unreferenced document {path}")
            return False
        logger.warning(f"Discarded unreferenced document {path}")
        return True

    def exists(self, one_pager_id: str, version: str) -> bool:
        """Whether the given version file exists in any read location."""
        return self._existing_file_for(one_pager_id, version) is not None

    def list_ids(self) -> list[str]:
        """List One Pager IDs (folders) available in all read locations."""
        ids: set[str] = set()
        for root in self._read_paths:
            ids.update(self._files.list_dirs(root / ONE_PAGERS_FOLDER))
        return sorted(ids)

    @property
    def _write_root(self) -> Path:
        return self._write_path or self._base_path

    def _dir_for(self, one_pager_id: str, root: Path | None = None) -> Path:
        return (root or self._base_path) / ONE_PAGERS_FOLDER / one_pager_id

    def _file_for(
        self, one_pager_id: str, version: str, root: Path | None = None
    ) -> Path:
        return self._dir_for(one_pager_id, root) / f"{one_pager_id}_v{version}.yml"

    def _existing_file_for(self, one_pager_id: str, version: str) -> Path | None:
        for root in self._read_paths:
            path = self._file_for(one_pager_id, version, root)
            if self._files.exists(path):
                return path
        return None

    def _latest_file(self, one_pager_id: str) -> Path | None:
        candidates: list[Path] = []
        for root in self._read_paths:
            op_dir = self._dir_for(one_pager_id, root)
            try:
                candidates.extend(
                    op_dir / name
                    for name in self._files.list_files(op_dir)
                    if name.startswith(f"{one_pager_id}_v") and name.endswith(".yml")
                )
            except OSError as e:
                logger.warning(f"Cannot list {op_dir}: {e}")
        if not candidates:
            return None
        return max(candidates, key=lambda p: _version_key(p, one_pager_id))

    def _load(self, path: Path, one_pager_id: str) -> OnePagerDocument | None:
        try:
            content = self._files.read_text(path)
            data = yaml.safe_load(content)
        except (OSError, yaml.YAMLError) as e:
            logger.error(f"Failed to read document for {one_pager_id} from {path}: {e}")  # noqa: TRY400 - callers log the traceback
            msg = f"Failed to read document: {e}"
            raise RuntimeError(msg) from e
        if not data:
            logger.warning(f"Document at {path} is empty or invalid YAML")
            return None
        return document_from_dict(data, content)


def _version_key(path: Path, one_pager_id: str) -> tuple[int, ...]:
    """Sort key from a version-stamped filename ({id}_v1.2.3.yml -> (1, 2, 3))."""
    version = path.stem.removeprefix(f"{one_pager_id}_v")
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)
