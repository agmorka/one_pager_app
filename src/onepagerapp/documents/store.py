"""Filesystem store for One Pager YAML documents.

Reads and writes ``{base}/{id}/{id}_v{version}.yml`` where ``base`` comes from
ONE_PAGER_APP_VOLUME_PATH. The current version is supplied by the caller (from
the one_pager_status table); a highest-version fallback is used only when no
version is given.
"""

import logging
from pathlib import Path

import yaml

from onepagerapp.documents.serialization import document_from_dict, document_to_yaml
from onepagerapp.models import OnePagerDocument

logger = logging.getLogger(__name__)


class OnePagerDocumentStore:
    """Reads and writes One Pager YAML documents at a configured base path."""

    def __init__(self, base_path: str | Path) -> None:
        if not base_path:
            msg = (
                "One Pager document store requires a base path "
                "(ONE_PAGER_APP_VOLUME_PATH)."
            )
            raise RuntimeError(msg)
        self._base_path = Path(base_path)

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
            path = self._file_for(one_pager_id, version)
            if not path.exists():
                logger.info(
                    f"Document for {one_pager_id} v{version} not found at {path}"
                )
                return None
            return self._load(path, one_pager_id)

        latest = self._latest_file(one_pager_id)
        if latest is None:
            logger.info(f"No document files found for {one_pager_id}")
            return None
        logger.warning(
            f"No version specified for {one_pager_id}; using {latest.name}"
        )
        return self._load(latest, one_pager_id)

    def write(
        self, one_pager_id: str, document: OnePagerDocument, version: str
    ) -> Path:
        """Write a One Pager document as ``{id}_v{version}.yml``.

        Regenerates YAML from the document's structured fields and updates
        ``raw_content`` to match. Returns the written path.

        Raises:
            RuntimeError: If the file cannot be written.
        """
        op_dir = self._dir_for(one_pager_id)
        path = self._file_for(one_pager_id, version)
        content = document_to_yaml(document)
        try:
            op_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to write document {path}: {e}")
            raise RuntimeError(
                f"Failed to write document for {one_pager_id}: {e}"
            ) from e
        document.raw_content = content
        return path

    def list_ids(self) -> list[str]:
        """List One Pager IDs (subdirectories) available at the base path."""
        if not self._base_path.exists():
            return []
        return sorted(p.name for p in self._base_path.iterdir() if p.is_dir())

    def _dir_for(self, one_pager_id: str) -> Path:
        return self._base_path / one_pager_id

    def _file_for(self, one_pager_id: str, version: str) -> Path:
        return self._dir_for(one_pager_id) / f"{one_pager_id}_v{version}.yml"

    def _latest_file(self, one_pager_id: str) -> Path | None:
        op_dir = self._dir_for(one_pager_id)
        try:
            candidates = list(op_dir.glob(f"{one_pager_id}_v*.yml"))
        except OSError as e:
            logger.warning(f"Cannot list {op_dir}: {e}")
            return None
        if not candidates:
            return None
        return max(candidates, key=lambda p: _version_key(p, one_pager_id))

    def _load(self, path: Path, one_pager_id: str) -> OnePagerDocument | None:
        try:
            content = path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
        except (OSError, yaml.YAMLError) as e:
            logger.error(f"Failed to read document for {one_pager_id} from {path}: {e}")
            raise RuntimeError(f"Failed to read document: {e}") from e
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
