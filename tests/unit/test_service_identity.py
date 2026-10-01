"""Every service entry point refuses a call without a recognised user.

Identity plan Phase 2, step 3: app.py refuses unrecognised users, and the
service layer checks again so a missing identity never reaches a write.
"""

from collections.abc import Callable

import pytest

from onepagerapp import admin, editing, locking, review, workflow
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput, PersonRef
from onepagerapp.permissions import (
    UNRECOGNISED_USER_MESSAGE,
    PermissionDeniedError,
    require_identity,
)
from onepagerapp.state_machine import Actor
from tests.users import make_user

ALL_ROLES = frozenset({Actor.APPROVER, Actor.ADMIN})
UNRECOGNISED = CurrentUser("guest@example.com", "", "Guest")

# Placeholders replaced by the test's data access, document store and user.
DA, STORE, USER = object(), object(), object()
EMPTY_INPUT = NewOnePagerInput("", "", "", "", "", PersonRef("", "", ""))

# (logged action, entry point, positional args, keyword args). Without the
# identity check, each call would read or write data.
ENTRY_POINTS: list[tuple[str, Callable[..., object], tuple, dict]] = [
    ("create_one_pager", workflow.create_one_pager, (EMPTY_INPUT, USER, DA, STORE), {}),
    ("submit_for_review", workflow.submit_for_review, (DA, "OP-0001", USER, "s1"), {}),
    (
        "cancel_one_pager",
        workflow.cancel_one_pager,
        (DA, "OP-0001", USER),
        {"reason": "x", "roles": ALL_ROLES},
    ),
    (
        "change_data_product_status",
        workflow.change_data_product_status,
        (DA, "OP-0001", "In Development", USER),
        {"confirmed": True},
    ),
    (
        "reject_one_pager",
        workflow.reject_one_pager,
        (DA, "OP-0002", USER, "x"),
        {"roles": ALL_ROLES},
    ),
    (
        "approve_one_pager",
        workflow.approve_one_pager,
        (DA, STORE, "OP-0002", USER),
        {"roles": ALL_ROLES},
    ),
    ("start_update", workflow.start_update, (DA, "OP-0001", USER), {"confirmed": True}),
    ("load_for_edit", editing.open_for_edit, (DA, "OP-0001", USER, "s1"), {}),
    ("link_use_case", editing.link_use_case, (DA, "OP-0001", "UC-001", USER), {}),
    ("unlink_use_case", editing.unlink_use_case, (DA, "OP-0001", "UC-001", USER), {}),
    ("review", review.get_review_queue, (DA, USER, ALL_ROLES), {}),
    (
        "add_review_comment",
        review.add_review_comment,
        (DA, "OP-0002", USER, None, "x"),
        {"roles": ALL_ROLES},
    ),
    (
        "resolve_review_comment",
        review.resolve_review_comment,
        (DA, "OP-0002", 1, USER),
        {},
    ),
    ("acquire_lock", locking.acquire_lock, (DA, "OP-0001", USER, "s1"), {}),
    ("heartbeat", locking.heartbeat, (DA, "OP-0001", USER, "s1"), {}),
    ("release_lock", locking.release_lock, (DA, "OP-0001", USER), {}),
    (
        "administer",
        admin.add_reference_value,
        (DA, "ref_business_domains", "Risk", 1, USER, ALL_ROLES),
        {},
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("user", [UNRECOGNISED, None], ids=["no-initials", "none"])
@pytest.mark.parametrize(
    ("action", "entry_point", "args", "kwargs"),
    ENTRY_POINTS,
    ids=[entry[0] for entry in ENTRY_POINTS],
)
def test__entry_point__refuses_unrecognised_user(
    action: str,
    entry_point: Callable[..., object],
    args: tuple,
    kwargs: dict,
    user: CurrentUser | None,
    mock_data_access: MockDataAccess,
    document_store: OnePagerDocumentStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    placeholders = {id(DA): mock_data_access, id(STORE): document_store, id(USER): user}
    rows_before = dict(mock_data_access._status_rows)
    locks_before = dict(mock_data_access._locks)

    with pytest.raises(PermissionDeniedError, match="not recognised"):
        entry_point(*(placeholders.get(id(a), a) for a in args), **kwargs)

    assert mock_data_access._status_rows == rows_before
    assert mock_data_access._locks == locks_before
    assert any(
        f"action={action} outcome=permission_denied" in m and "user=-" in m
        for m in caplog.messages
    )


@pytest.mark.unit
def test__require_identity__returns_recognised_user() -> None:
    user = make_user("X0W")

    assert require_identity(user, "anything") is user


@pytest.mark.unit
def test__require_identity__message() -> None:
    with pytest.raises(PermissionDeniedError) as raised:
        require_identity(UNRECOGNISED, "anything", "OP-0001")

    assert str(raised.value) == UNRECOGNISED_USER_MESSAGE
