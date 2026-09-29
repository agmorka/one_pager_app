"""Configuration module for the OnePagerApp application."""

import os
from datetime import timedelta
from enum import Enum

from pydantic import BaseModel, Field


class AppMode(str, Enum):
    DATABRICKS = "databricks"
    LOCAL_MOCK = "local-mock"
    LOCAL_INTEGRATION = "local-integration"


class Environment(str, Enum):
    DEV = "DEV"
    INT = "INT"
    UAT = "UAT"
    PRD = "PRD"


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
    ONE_PAGER_APP_ENVIRONMENT: str = Field(
        "",
        description=(
            "Deployment environment shown in the sidebar badge (DEV/INT/UAT/PRD). "
            "When empty it is derived from the catalog prefix."
        ),
    )
    ONE_PAGER_APP_LOCK_TTL_SECONDS: int = Field(
        1800,
        gt=0,
        description=(
            "Edit lock expiry window in seconds (Backend_Design.md §6). "
            "30 minutes in every environment; tests override it."
        ),
    )
    CLOUD_ROLE_NAME: str = "OnePagerApp"

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build the configuration from the current process environment variables."""
        env_values = {
            name: os.environ[name] for name in cls.model_fields if name in os.environ
        }
        return cls(**env_values)

    @property
    def lock_ttl(self) -> timedelta:
        """How long an edit lock lives after its last heartbeat."""
        return timedelta(seconds=self.ONE_PAGER_APP_LOCK_TTL_SECONDS)

    @property
    def is_mock(self) -> bool:
        return self.APP_MODE == AppMode.LOCAL_MOCK

    @property
    def environment(self) -> Environment:
        """Environment for the sidebar badge (UI_Design.md §2).

        Uses ONE_PAGER_APP_ENVIRONMENT when it names a known environment,
        otherwise the catalog prefix (``prd_one_pager`` -> PRD), otherwise DEV.
        """
        candidates = (
            self.ONE_PAGER_APP_ENVIRONMENT,
            self.ONE_PAGER_APP_DATABRICKS_CATALOG.split("_", 1)[0],
        )
        for candidate in candidates:
            try:
                return Environment(candidate.strip().upper())
            except ValueError:
                continue
        return Environment.DEV

    @property
    def uses_databricks(self) -> bool:
        return self.APP_MODE in (AppMode.DATABRICKS, AppMode.LOCAL_INTEGRATION)
