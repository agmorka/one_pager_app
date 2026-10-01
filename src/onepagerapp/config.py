"""Configuration module for the OnePagerApp application."""

import os
import re
from datetime import timedelta
from enum import Enum

from pydantic import BaseModel, Field, field_validator


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
    ONE_PAGER_APP_USER_DOMAINS: str = Field(
        "becoc001.onmicrosoft.com",
        description=(
            "Comma-separated domains of the usernames the app accepts "
            "(Architecture.md §4). A username with another domain gets no "
            "initials."
        ),
    )
    ONE_PAGER_APP_USERNAME_SUFFIXES: str = Field(
        "adm",
        description=(
            "Comma-separated suffixes stripped from the user part of the "
            "username to get the initials (x0wadm -> X0W). An empty entry means "
            "no suffix, so 'adm,' accepts both x0wadm and x0w."
        ),
    )
    ONE_PAGER_APP_INITIALS_PATTERN: str = Field(
        r"^[A-Z0-9]{3}$",
        description=(
            "Regular expression for valid corporate initials, checked after "
            "upper-casing. Used for the logged-in user and for Owner/SME "
            "initials."
        ),
    )
    ONE_PAGER_APP_MOCK_USER: str = Field(
        "lduadm@becoc001.onmicrosoft.com",
        description=(
            "Username of the signed-in user in local-mock mode. It goes through "
            "the same parsing as a real username, so it must match "
            "ONE_PAGER_APP_USER_DOMAINS and ONE_PAGER_APP_USERNAME_SUFFIXES."
        ),
    )
    ONE_PAGER_APP_MOCK_USER_NAME: str = Field(
        "Local Dev User",
        description=(
            "Name shown for the local-mock user, in place of the name read "
            "from the workspace directory in the other modes."
        ),
    )
    ONE_PAGER_APP_APPROVERS: str = Field(
        "",
        description=(
            "Comma-separated initials of the users who act as Approvers. "
            "Interim stand-in for the Approver UC group until group names are "
            "decided (Architecture.md §4)."
        ),
    )
    ONE_PAGER_APP_ADMINS: str = Field(
        "",
        description=(
            "Comma-separated initials of the users who act as Admins. Interim "
            "stand-in for the Admin UC group (Architecture.md §4)."
        ),
    )
    CLOUD_ROLE_NAME: str = "OnePagerApp"

    @field_validator("ONE_PAGER_APP_INITIALS_PATTERN")
    @classmethod
    def _check_initials_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            msg = f"ONE_PAGER_APP_INITIALS_PATTERN is not a valid regex: {exc}"
            raise ValueError(msg) from exc
        return value

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
    def user_domains(self) -> frozenset[str]:
        """Accepted username domains (lower case)."""
        return frozenset(
            part.strip().lower()
            for part in self.ONE_PAGER_APP_USER_DOMAINS.split(",")
            if part.strip()
        )

    @property
    def username_suffixes(self) -> tuple[str, ...]:
        """Suffixes to strip from the user part, longest first (lower case).

        An empty entry (or an empty setting) is kept as ``""``, meaning "no
        suffix". Longest first, so ``""`` never wins over ``"adm"``.
        """
        parts = self.ONE_PAGER_APP_USERNAME_SUFFIXES.split(",")
        suffixes = {part.strip().lower() for part in parts}
        return tuple(sorted(suffixes, key=lambda suffix: (-len(suffix), suffix)))

    @property
    def initials_pattern(self) -> re.Pattern[str]:
        """Compiled ONE_PAGER_APP_INITIALS_PATTERN."""
        return re.compile(self.ONE_PAGER_APP_INITIALS_PATTERN)

    @property
    def approver_initials(self) -> frozenset[str]:
        """Initials configured in ONE_PAGER_APP_APPROVERS (upper case)."""
        return _initials_list(self.ONE_PAGER_APP_APPROVERS)

    @property
    def admin_initials(self) -> frozenset[str]:
        """Initials configured in ONE_PAGER_APP_ADMINS (upper case)."""
        return _initials_list(self.ONE_PAGER_APP_ADMINS)

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


def _initials_list(value: str) -> frozenset[str]:
    return frozenset(
        part.strip().upper() for part in value.split(",") if part.strip()
    )
