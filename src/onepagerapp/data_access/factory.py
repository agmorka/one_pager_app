"""Factory that selects the data access implementation based on APP_MODE."""

import tempfile

from onepagerapp.config import AppConfig
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore


def create_document_store(config: AppConfig) -> OnePagerDocumentStore:
    """Create the One Pager YAML document store for the configured mode.

    In ``local-mock`` mode ONE_PAGER_APP_VOLUME_PATH points at the
    version-controlled fixtures, so new documents are written to a temporary
    directory that is read before the fixtures (New_One_Pager_Plan D12).
    """
    if config.uses_databricks:
        return OnePagerDocumentStore(config.ONE_PAGER_APP_VOLUME_PATH)
    return OnePagerDocumentStore(
        config.ONE_PAGER_APP_VOLUME_PATH,
        write_path=tempfile.mkdtemp(prefix="onepager-mock-"),
    )


def create_data_access(
    config: AppConfig, document_store: OnePagerDocumentStore | None = None
) -> DataAccess:
    """Select data access implementation based on APP_MODE.

    Args:
        config: Application configuration.
        document_store: Document store to use; created from ``config`` if None.

    Returns:
        DataAccess implementation (LakehouseAccess or MockDataAccess).

    """
    store = document_store or create_document_store(config)
    if config.uses_databricks:
        return LakehouseAccess(config, store)
    return MockDataAccess(store, current_user=config.ONE_PAGER_APP_MOCK_USER)
