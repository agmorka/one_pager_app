"""Configuration module for the OnePagerApp application."""

import os
from enum import Enum

from pydantic import BaseModel, Field


class AppMode(str, Enum):
    DATABRICKS = "databricks"
    LOCAL_MOCK = "local-mock"
    LOCAL_INTEGRATION = "local-integration"


class AppConfig(BaseModel):
    """Runtime configuration loaded from environment variables."""

    APP_MODE: AppMode = AppMode.DATABRICKS
    ONE_PAGER_APP_VOLUME_PATH: str = Field(
        ..., description="Volume path for the One Pager Registry."
    )
    ONE_PAGER_APP_DATABRICKS_CATALOG: str = Field(
        "dev_bia_meta", description="Databricks catalog for the One Pager App."
    )
    ONE_PAGER_APP_DATABRICKS_SCHEMA: str = Field(
        "onepager_app", description="Databricks schema for the One Pager App."
    )
    DATABRICKS_WAREHOUSE_ID: str = ""
    CLOUD_ROLE_NAME: str = "OnePagerApp"

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build the configuration from the current process environment variables."""
        env_values = {
            name: os.environ[name] for name in cls.model_fields if name in os.environ
        }
        return cls(**env_values)

    @property
    def is_mock(self) -> bool:
        return self.APP_MODE == AppMode.LOCAL_MOCK

    @property
    def uses_databricks(self) -> bool:
        return self.APP_MODE in (AppMode.DATABRICKS, AppMode.LOCAL_INTEGRATION)
