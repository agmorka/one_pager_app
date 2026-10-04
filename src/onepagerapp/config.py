"""Configuration module for the OnePagerApp application."""

import os
import re
from datetime import timedelta
from enum import Enum

from pydantic import BaseModel, Field, SecretStr, field_validator


class AppMode(str, Enum):
    DATABRICKS = "databricks"
    LOCAL_MOCK = "local-mock"
    LOCAL_INTEGRATION = "local-integration"


class Environment(str, Enum):
    DEV = "DEV"
    INT = "INT"
    TST = "TST"
    UAT = "UAT"
    PRD = "PRD"


# Interim group for every role until the dedicated role groups exist
# (User_Identity_And_Access_Plan.md §4.1, Decision_Log §21). ``{env}`` is
# replaced with the environment (DEV, INT, TST, UAT, PRD).
INTERIM_ROLE_GROUP = "PAG-BEC-LHX-{env}-DataPlatEng-Base"
ROLE_GROUP_SETTINGS = (
    "ONE_PAGER_APP_GROUP_OWNER_SME",
    "ONE_PAGER_APP_GROUP_APPROVER",
    "ONE_PAGER_APP_GROUP_ADMIN",
)


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
    ONE_PAGER_APP_GROUP_OWNER_SME: str = Field(
        INTERIM_ROLE_GROUP,
        description=(
            "Group whose members may create One Pagers and manage Use Cases "
            "(Owner/SME role). {env} is replaced with the environment."
        ),
    )
    ONE_PAGER_APP_GROUP_APPROVER: str = Field(
        INTERIM_ROLE_GROUP,
        description=(
            "Group whose members review One Pagers (Approver role). {env} is "
            "replaced with the environment."
        ),
    )
    ONE_PAGER_APP_GROUP_ADMIN: str = Field(
        INTERIM_ROLE_GROUP,
        description=(
            "Group whose members use the Admin page (Admin role). {env} is "
            "replaced with the environment."
        ),
    )
    ONE_PAGER_APP_MOCK_GROUPS: str = Field(
        INTERIM_ROLE_GROUP,
        description=(
            "Comma-separated groups the local-mock user belongs to, so roles "
            "can be tried locally. {env} is replaced with the environment. The "
            "default is the interim role group: every role. Empty: Viewer only."
        ),
    )
    ONE_PAGER_APP_EMAIL_DOMAIN: str = Field(
        "bec.dk",
        description=(
            "Domain of the corporate email addresses: the email of a user "
            "with initials X0W is x0w@bec.dk."
        ),
    )
    ONE_PAGER_APP_SP_CLIENT_ID: str = Field(
        "",
        description=(
            "Application (client) ID of the service principal that writes "
            "the Delta tables and the registry volume, e.g. the one of "
            "bp-spn-lhx-opa-dev-001. Empty: the app's own service principal "
            "(the one Databricks Apps creates for the app)."
        ),
    )
    ONE_PAGER_APP_SP_CLIENT_SECRET: SecretStr = Field(
        SecretStr(""),
        description=(
            "OAuth secret of ONE_PAGER_APP_SP_CLIENT_ID. Set from a secret "
            "resource of the app, never as a plain value."
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

    @field_validator(*ROLE_GROUP_SETTINGS)
    @classmethod
    def _check_role_group(cls, value: str) -> str:
        group = value.strip()
        if not group or "," in group:
            msg = f"A role group setting must be one group name, got {value!r}."
            raise ValueError(msg)
        return group

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
    def role_groups(self) -> dict[str, str]:
        """Group name per role (``owner_sme``, ``approver``, ``admin``).

        ``{env}`` is replaced with ``environment``; a value without it is used
        as is.
        """
        env = self.environment.value
        return {
            "owner_sme": self.ONE_PAGER_APP_GROUP_OWNER_SME.replace("{env}", env),
            "approver": self.ONE_PAGER_APP_GROUP_APPROVER.replace("{env}", env),
            "admin": self.ONE_PAGER_APP_GROUP_ADMIN.replace("{env}", env),
        }

    @property
    def interim_roles(self) -> list[str]:
        """Roles (``owner_sme``, ``approver``, ``admin``) still on the interim group.

        True while a role group setting resolves to the interim DataPlatEng
        group; empty once all three dedicated groups are configured.
        """
        interim = INTERIM_ROLE_GROUP.replace("{env}", self.environment.value)
        return [role for role, group in self.role_groups.items() if group == interim]

    @property
    def mock_groups(self) -> frozenset[str]:
        """Groups of the local-mock user, with ``{env}`` replaced."""
        env = self.environment.value
        return frozenset(
            part.strip().replace("{env}", env)
            for part in self.ONE_PAGER_APP_MOCK_GROUPS.split(",")
            if part.strip()
        )

    @property
    def is_mock(self) -> bool:
        return self.APP_MODE == AppMode.LOCAL_MOCK

    @property
    def environment(self) -> Environment:
        """Environment for the sidebar badge and the role group names.

        Uses ONE_PAGER_APP_ENVIRONMENT when it names a known environment,
        otherwise the catalog prefix of the registry volume
        (``/Volumes/prd_bia_meta/...`` -> PRD), otherwise the prefix of
        ONE_PAGER_APP_DATABRICKS_CATALOG, otherwise DEV.

        The volume comes first because deployed, ONE_PAGER_APP_VOLUME_PATH is
        set per environment by the app resource (``app.yml``), while the
        catalog setting falls back to its DEV default.
        """
        candidates = (
            self.ONE_PAGER_APP_ENVIRONMENT,
            _volume_catalog(self.ONE_PAGER_APP_VOLUME_PATH).split("_", 1)[0],
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

    @property
    def uses_volume_files(self) -> bool:
        """Whether the registry is a UC volume read through the Files API.

        Databricks Apps do not mount volumes, so ``/Volumes/...`` cannot be
        opened as a local folder.
        """
        return self.uses_databricks and bool(
            _volume_catalog(self.ONE_PAGER_APP_VOLUME_PATH)
        )

    @property
    def service_principal_credentials(self) -> tuple[str, str] | None:
        """(client ID, secret) of the configured service principal, or None.

        None means the default authentication: the app's own service
        principal deployed, the CLI profile locally.
        """
        client_id = self.ONE_PAGER_APP_SP_CLIENT_ID.strip()
        secret = self.ONE_PAGER_APP_SP_CLIENT_SECRET.get_secret_value().strip()
        if client_id and secret:
            return client_id, secret
        return None

    def email_for(self, initials: str) -> str:
        """Corporate email address of the initials (X0W -> x0w@bec.dk), or ""."""
        domain = self.ONE_PAGER_APP_EMAIL_DOMAIN.strip().lstrip("@")
        return f"{initials.lower()}@{domain}" if initials and domain else ""


def _volume_catalog(volume_path: str) -> str:
    """Catalog of a Unity Catalog volume path ``/Volumes/<catalog>/...``, or ""."""
    parts = volume_path.strip().strip("/").split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "Volumes" else ""
