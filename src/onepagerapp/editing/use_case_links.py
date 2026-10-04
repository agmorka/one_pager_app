"""Use Case links of a One Pager (Backend_Design.md §9).

Links are the rows of ``use_case_references``.
"""

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import CurrentUser, ValidationError
from onepagerapp.permissions import (
    PermissionDeniedError,
    is_owner_or_sme,
    require_identity,
)

DEPRECATED_LINK_MESSAGE = "{use_case_id} is deprecated and cannot be linked."
UNKNOWN_LINK_MESSAGE = "{use_case_id} does not exist."


def link_denied_reason(data_access: DataAccess, use_case_id: str) -> str | None:
    """Why a Use Case cannot be linked to a One Pager, or None if it can."""
    use_case = data_access.get_use_case(use_case_id)
    if use_case is None:
        return UNKNOWN_LINK_MESSAGE.format(use_case_id=use_case_id)
    if use_case.deprecated:
        return DEPRECATED_LINK_MESSAGE.format(use_case_id=use_case_id)
    return None


def validate_new_use_case_links(
    data_access: DataAccess, linked_before: list[str], use_case_ids: list[str]
) -> list[ValidationError]:
    """Errors for Use Cases the save would newly link that cannot be linked.

    Links that already exist stay valid when their Use Case is deprecated
    later (it simply cannot be linked to new One Pagers).
    """
    errors: list[ValidationError] = []
    seen: set[str] = set()
    for use_case_id in use_case_ids:
        if use_case_id in seen:
            errors.append(
                ValidationError("useCases", f"{use_case_id} is linked twice.")
            )
        seen.add(use_case_id)
        if use_case_id in linked_before:
            continue
        reason = link_denied_reason(data_access, use_case_id)
        if reason:
            errors.append(ValidationError("useCases", reason))
    return errors


def _check_can_link(
    data_access: DataAccess, one_pager_id: str, user: CurrentUser, action: str
) -> None:
    if not is_owner_or_sme(user, data_access.get_authorized_users(one_pager_id)):
        log_permission_denied(action, user=user.initials, one_pager_id=one_pager_id)
        msg = "Only the Owner or an SME of this One Pager can change its Use Cases."
        raise PermissionDeniedError(msg)


def link_use_case(
    data_access: DataAccess, one_pager_id: str, use_case_id: str, user: CurrentUser
) -> None:
    """Link a Use Case to a One Pager (insert into ``use_case_references``).

    Raises:
        PermissionDeniedError: The user is not Owner/SME of the One Pager.
        ValueError: The Use Case does not exist or is deprecated.

    """
    require_identity(user, "link_use_case", one_pager_id)
    _check_can_link(data_access, one_pager_id, user, "link_use_case")
    reason = link_denied_reason(data_access, use_case_id)
    if reason:
        raise ValueError(reason)
    data_access.add_use_case_reference(one_pager_id, use_case_id)
    log_event(
        "link_use_case",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        use_case_id=use_case_id,
    )


def unlink_use_case(
    data_access: DataAccess, one_pager_id: str, use_case_id: str, user: CurrentUser
) -> None:
    """Unlink a Use Case from a One Pager (delete from ``use_case_references``).

    Raises:
        PermissionDeniedError: The user is not Owner/SME of the One Pager.

    """
    require_identity(user, "unlink_use_case", one_pager_id)
    _check_can_link(data_access, one_pager_id, user, "unlink_use_case")
    data_access.remove_use_case_reference(one_pager_id, use_case_id)
    log_event(
        "unlink_use_case",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
        use_case_id=use_case_id,
    )


def sync_use_case_references(
    data_access: DataAccess,
    one_pager_id: str,
    linked_before: list[str],
    use_case_ids: list[str],
    user: CurrentUser,
) -> None:
    """Link the Use Cases a save added and unlink the ones it removed."""
    for use_case_id in sorted(set(use_case_ids) - set(linked_before)):
        link_use_case(data_access, one_pager_id, use_case_id, user)
    for use_case_id in sorted(set(linked_before) - set(use_case_ids)):
        unlink_use_case(data_access, one_pager_id, use_case_id, user)
