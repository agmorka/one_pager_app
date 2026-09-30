"""Administration: reference data and status definitions (UI_Design.md §4.7).

Admins maintain the reference tables that feed the Editor dropdowns and the
Registry filters (Data_Model.md §7): business domains, data product types and
source systems. They add values, change their order, deactivate and
reactivate them, and delete values no One Pager uses. A value's name is its
key (One Pagers store it), so it is never renamed: add the new name and
deactivate the old one instead.

Status definitions (``ref_op_status`` / ``ref_dp_status``): Admins change the
display label, order and badge color. The statuses themselves and whether
they are final belong to the state machine and cannot be changed here.

Pending PRs: approved One Pagers whose Git PR could not be created
(``pending_pr``, Backend_Design.md §8). Creating and retrying PRs comes with
the Git integration (Phase 8, items 7.1-7.2); until then the list is read
only and **Retry PR** is not available.

Every function checks the Admin role (logged when denied) and every change is
logged as a security event. Pure Python — no Streamlit.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import (
    DataAccess,
    check_reference_table,
    check_status_table,
)
from onepagerapp.models import (
    CurrentUser,
    OnePagerStatusRow,
    RegistryFilter,
    StatusRef,
)
from onepagerapp.permissions import PermissionDeniedError, can_administer
from onepagerapp.state_machine import DP_STATUSES, OP_STATUSES, Actor
from onepagerapp.validation import sanitize_text

ADMIN_DENIED_MESSAGE = "You don't have access to this page."
SAVE_FAILED_MESSAGE = "The change could not be saved. Please retry."
MAX_VALUE_LENGTH = 100
MAX_LABEL_LENGTH = 50
MAX_SORT_ORDER = 9999
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class AdminError(ValueError):
    """An Admin change was refused. The message is safe to show to users."""


@dataclass(frozen=True)
class ReferenceKind:
    """One Admin-managed reference table.

    Attributes:
        table: Table name (a ``REFERENCE_TABLES`` key).
        title: Plural name for headings, e.g. "Business Domains".
        noun: Singular name for messages, e.g. "business domain".
        usage_filter: ``RegistryFilter`` field that finds the One Pagers
            using a value, or None when One Pagers do not reference the
            table (source systems are free text in the documents).

    """

    table: str
    title: str
    noun: str
    usage_filter: str | None = None


REFERENCE_KINDS: tuple[ReferenceKind, ...] = (
    ReferenceKind(
        "ref_business_domains", "Business Domains", "business domain", "domain"
    ),
    ReferenceKind(
        "ref_data_product_types",
        "Data Product Types",
        "data product type",
        "data_product_type",
    ),
    ReferenceKind("ref_source_systems", "Source Systems", "source system"),
)

STATUS_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ref_op_status": ("One Pager statuses", OP_STATUSES),
    "ref_dp_status": ("Data Product statuses", DP_STATUSES),
}


def reference_kind(table: str) -> ReferenceKind:
    """Return the ``ReferenceKind`` of a reference table.

    Raises:
        ValueError: Unknown table.

    """
    check_reference_table(table)
    return next(k for k in REFERENCE_KINDS if k.table == table)


@dataclass(frozen=True)
class ReferenceValue:
    """A row of a reference table, with how many One Pagers use it.

    ``in_use`` is None when usage is not tracked for the table.
    """

    value: str
    sort_order: int
    active: bool
    in_use: int | None = None


def check_can_administer(user: CurrentUser | None, roles: Collection[Actor]) -> None:
    """Raise ``PermissionDeniedError`` (logged) unless the user is an Admin."""
    if can_administer(roles):
        return
    log_permission_denied("administer", user=user.initials if user else None)
    raise PermissionDeniedError(ADMIN_DENIED_MESSAGE)


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _usage(data_access: DataAccess, kind: ReferenceKind, value: str) -> int | None:
    """Count the One Pagers whose ``kind.usage_filter`` column is ``value``."""
    if kind.usage_filter is None:
        return None
    counts = data_access.get_registry_status_counts(
        RegistryFilter(**{kind.usage_filter: value})
    )
    return sum(counts.values())


def get_reference_values(
    data_access: DataAccess,
    table: str,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> list[ReferenceValue]:
    """All values of a reference table (active and inactive), in display order.

    Raises:
        PermissionDeniedError: The user is not an Admin.

    """
    check_can_administer(user, roles)
    kind = reference_kind(table)
    key = check_reference_table(table)
    frame = data_access.get_reference_values(table)
    values = [
        ReferenceValue(
            value=str(row[key]),
            sort_order=int(row.get("sort_order") or 0),
            active=_truthy(row.get("active", True)),
            in_use=_usage(data_access, kind, str(row[key])),
        )
        for row in frame.to_dict("records")
    ]
    return sorted(values, key=lambda v: (v.sort_order, v.value.lower()))


def _check_sort_order(sort_order: int) -> int:
    if not 1 <= int(sort_order) <= MAX_SORT_ORDER:
        msg = f"The order must be a number from 1 to {MAX_SORT_ORDER}."
        raise AdminError(msg)
    return int(sort_order)


def _log_change(
    action: str, user: CurrentUser | None, table: str, **details: object
) -> None:
    log_event(
        action,
        Outcome.SUCCESS,
        user=user.initials if user else None,
        table=table,
        **details,
    )


def add_reference_value(  # noqa: PLR0913 - the parts of one new value
    data_access: DataAccess,
    table: str,
    value: str,
    sort_order: int,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> str:
    """Add an active value to a reference table; return the stored value.

    Raises:
        PermissionDeniedError: The user is not an Admin.
        AdminError: Empty, too long, or already there (in any letter case).

    """
    check_can_administer(user, roles)
    kind = reference_kind(table)
    key = check_reference_table(table)
    cleaned = " ".join(sanitize_text(value).split())
    if not cleaned:
        msg = f"Enter the {kind.noun}."
        raise AdminError(msg)
    if len(cleaned) > MAX_VALUE_LENGTH:
        msg = f"Use at most {MAX_VALUE_LENGTH} characters."
        raise AdminError(msg)
    order = _check_sort_order(sort_order)
    existing = data_access.get_reference_values(table)
    if any(str(v).lower() == cleaned.lower() for v in existing.get(key, [])):
        msg = f"The {kind.noun} {cleaned!r} already exists."
        raise AdminError(msg)
    if not data_access.insert_reference_value(
        table, cleaned, sort_order=order, active=True
    ):
        msg = f"The {kind.noun} {cleaned!r} already exists."
        raise AdminError(msg)
    _log_change("add_reference_value", user, table, value=cleaned)
    return cleaned


def update_reference_value(  # noqa: PLR0913 - the parts of one value
    data_access: DataAccess,
    table: str,
    value: str,
    *,
    sort_order: int,
    active: bool,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> None:
    """Change the order of a value, or deactivate / reactivate it.

    Deactivated values stay on the One Pagers that use them but cannot be
    chosen for new ones (``workflow.active_reference_values``).

    Raises:
        PermissionDeniedError: The user is not an Admin.
        AdminError: Invalid order, or the value no longer exists.

    """
    check_can_administer(user, roles)
    kind = reference_kind(table)
    order = _check_sort_order(sort_order)
    if not data_access.update_reference_value(
        table, value, sort_order=order, active=active
    ):
        msg = f"The {kind.noun} {value!r} no longer exists."
        raise AdminError(msg)
    _log_change(
        "update_reference_value",
        user,
        table,
        value=value,
        sort_order=order,
        active=active,
    )


def delete_reference_value(
    data_access: DataAccess,
    table: str,
    value: str,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> None:
    """Delete a value that no One Pager uses.

    Raises:
        PermissionDeniedError: The user is not an Admin.
        AdminError: One Pagers use it (deactivate it instead), or it no
            longer exists.

    """
    check_can_administer(user, roles)
    kind = reference_kind(table)
    in_use = _usage(data_access, kind, value)
    if in_use:
        msg = (
            f"{in_use} One Pager(s) use the {kind.noun} {value!r}, so it cannot "
            "be deleted. Deactivate it instead."
        )
        raise AdminError(msg)
    if not data_access.delete_reference_value(table, value):
        msg = f"The {kind.noun} {value!r} no longer exists."
        raise AdminError(msg)
    _log_change("delete_reference_value", user, table, value=value)


# Why Retry PR is disabled: nothing can create a PR before the Git integration.
RETRY_PR_UNAVAILABLE = (
    "Retry PR becomes available with the Git integration, which creates the "
    "PRs on approval."
)


def get_pending_prs(
    data_access: DataAccess,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> list[OnePagerStatusRow]:
    """Approved One Pagers still waiting for their Git PR, oldest first.

    Raises:
        PermissionDeniedError: The user is not an Admin.

    """
    check_can_administer(user, roles)
    return data_access.get_pending_pr_rows()


def get_status_definitions(
    data_access: DataAccess,
    table: str,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> list[StatusRef]:
    """Rows of ``ref_op_status`` or ``ref_dp_status``, in display order.

    Raises:
        PermissionDeniedError: The user is not an Admin.

    """
    check_can_administer(user, roles)
    check_status_table(table)
    frame = (
        data_access.get_ref_op_status()
        if table == "ref_op_status"
        else data_access.get_ref_dp_status()
    )
    rows = [
        StatusRef(
            status=str(row["status"]),
            display_label=str(row.get("display_label") or row["status"]),
            sort_order=int(row.get("sort_order") or 0),
            badge_color=row.get("badge_color") or None,
            is_terminal=_truthy(row.get("is_terminal")),
        )
        for row in frame.to_dict("records")
    ]
    return sorted(rows, key=lambda r: (r.sort_order, r.status))


def update_status_definition(  # noqa: PLR0913 - the display columns of one status
    data_access: DataAccess,
    table: str,
    status: str,
    *,
    display_label: str,
    sort_order: int,
    badge_color: str,
    user: CurrentUser | None,
    roles: Collection[Actor],
) -> None:
    """Change the display label, order and badge color of a status.

    Raises:
        PermissionDeniedError: The user is not an Admin.
        AdminError: Invalid values, or an unknown status.

    """
    check_can_administer(user, roles)
    check_status_table(table)
    _, statuses = STATUS_KINDS[table]
    if status not in statuses:
        msg = f"Unknown status {status!r}."
        raise AdminError(msg)
    label = " ".join(sanitize_text(display_label).split())
    if not label:
        msg = "Enter a display label."
        raise AdminError(msg)
    if len(label) > MAX_LABEL_LENGTH:
        msg = f"Use at most {MAX_LABEL_LENGTH} characters for the label."
        raise AdminError(msg)
    color = badge_color.strip()
    if not _HEX_COLOR.match(color):
        msg = "The badge color must look like #65B676."
        raise AdminError(msg)
    order = _check_sort_order(sort_order)
    if not data_access.update_status_definition(
        table, status, display_label=label, sort_order=order, badge_color=color.upper()
    ):
        msg = f"The status {status!r} no longer exists."
        raise AdminError(msg)
    _log_change(
        "update_status_definition",
        user,
        table,
        status=status,
        sort_order=order,
        badge_color=color.upper(),
    )
