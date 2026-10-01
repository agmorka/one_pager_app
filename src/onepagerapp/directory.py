"""The signed-in user's entry in the workspace directory (SCIM ``Me``).

First name and surname are not part of the username (Architecture.md §4), so
they are read from ``GET /api/2.0/preview/scim/v2/Me`` with the **user's**
token (Databricks Apps user authorization, scope ``iam.current-user:read``).
The name is for display only; it is never used for authorization. The groups
are read for the role check (User_Identity_And_Access_Plan.md Phase 6).

A failed lookup never blocks the app: callers get None and fall back to the
initials.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.iam import ComplexValue, User

from onepagerapp.config import AppConfig, AppMode
from onepagerapp.data_access.connection import USER_TOKEN_HEADER

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectoryUser:
    """What the directory knows about the signed-in user.

    Attributes:
        display_name: ``displayName``, e.g. "Agnieszka Kępkowska".
        given_name: ``name.givenName``.
        family_name: ``name.familyName``.
        emails: Email addresses, the primary one first.
        groups: Names of the groups the user is a direct member of.

    """

    display_name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    emails: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()

    @property
    def full_name(self) -> str | None:
        """``givenName familyName``, or None when both are missing."""
        parts = [p for p in (self.given_name, self.family_name) if p]
        return " ".join(parts) or None

    @property
    def email(self) -> str | None:
        """The primary email address, or None."""
        return self.emails[0] if self.emails else None


def _text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _values(items: list[ComplexValue] | None, attribute: str) -> tuple[str, ...]:
    """Non-empty ``attribute`` of SCIM multi-valued items, primary first."""
    ordered = sorted(items or [], key=lambda item: not item.primary)
    values = (_text(getattr(item, attribute)) for item in ordered)
    return tuple(v for v in values if v)


def directory_user_from_scim(user: User) -> DirectoryUser:
    """Convert the SCIM ``Me`` response into a ``DirectoryUser``."""
    name = user.name
    return DirectoryUser(
        display_name=_text(user.display_name),
        given_name=_text(name.given_name) if name else None,
        family_name=_text(name.family_name) if name else None,
        emails=_values(user.emails, "value"),
        groups=_values(user.groups, "display"),
    )


def get_me(token: str | None, host: str | None = None) -> DirectoryUser | None:
    """Read the signed-in user's directory entry with their token.

    Args:
        token: The user's OAuth token (``x-forwarded-access-token``). None
            uses the default authentication (the Databricks CLI profile);
            only for ``local-integration``, where that is the developer.
        host: Workspace URL; None reads it from the environment
            (``DATABRICKS_HOST``, set by the Databricks Apps runtime).

    Returns:
        The directory entry, or None when the lookup fails (logged).

    """
    try:
        # auth_type is required: the Databricks Apps runtime sets OAuth env
        # vars, which conflict with this explicit token otherwise.
        client = (
            WorkspaceClient(host=host, token=token, auth_type="pat")
            if token
            else WorkspaceClient()
        )
        return directory_user_from_scim(client.current_user.me())
    except Exception:  # noqa: BLE001 - a lookup failure must not block the app
        logger.warning("Reading the user from the directory (SCIM Me) failed")
        logger.debug("SCIM Me failure", exc_info=True)
        return None


def lookup_directory_user(
    config: AppConfig, headers: Mapping[str, str]
) -> DirectoryUser | None:
    """Look up the signed-in user's directory entry for the session, by mode.

    - ``databricks``: SCIM ``Me`` with the forwarded user token. Without the
      token there is no lookup; never with the service principal, which
      would return the app's own entry.
    - ``local-integration``: SCIM ``Me`` with the CLI profile (the developer).
    - ``local-mock``: no lookup.

    Returns:
        The entry, or None (no lookup, or it failed); the caller then shows
        the initials.

    """
    if config.APP_MODE is AppMode.DATABRICKS:
        token = (headers.get(USER_TOKEN_HEADER) or "").strip()
        if not token:
            logger.warning("No user token: the name is not read from the directory")
            return None
        return get_me(token)
    if config.APP_MODE is AppMode.LOCAL_INTEGRATION:
        return get_me(None)
    return None
