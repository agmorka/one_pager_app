"""Factory that selects the data access implementation based on APP_MODE."""

from onepagerapp.config import AppConfig
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.lakehouse import LakehouseAccess
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore


def create_data_access(config: AppConfig) -> DataAccess:
    """Select data access implementation based on APP_MODE.

    Args:
        config: Application configuration.

    Returns:
        DataAccess implementation (LakehouseAccess or MockDataAccess).
    """
    document_store = OnePagerDocumentStore(config.ONE_PAGER_APP_VOLUME_PATH)
    if config.uses_databricks:
        return LakehouseAccess(config, document_store)
    return MockDataAccess(document_store)
