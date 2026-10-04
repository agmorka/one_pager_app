"""One Pager workflow service (Backend_Design.md §2-3).

``create_one_pager`` makes a new One Pager: (new) -> ``Draft``. Guard: the user
may create One Pagers. Side effects: assign the OP ID, write the YAML document,
insert the authorized users, the change log entry and the
``one_pager_status`` row.

Every other status change goes through ``apply_transitions``, which executes
rules from ``state_machine.TRANSITIONS`` (the single source of truth for
allowed transitions) and enforces the valid OP/DP status combinations.

Modules:

- ``create``: create a new Draft One Pager.
- ``transitions``: execute status transitions (shared by every action).
- ``submit``: Submit for Review.
- ``cancel``: Cancel a One Pager.
- ``data_product``: Owner-initiated Data Product status changes.
- ``review_decisions``: Approve and Reject.
- ``update``: start an update of an approved One Pager.

Everything is re-exported here, so callers import from ``onepagerapp.workflow``.
Pure Python — no Streamlit.
"""

from onepagerapp.versioning import next_major
from onepagerapp.workflow.cancel import MAX_REASON_LENGTH, cancel_one_pager
from onepagerapp.workflow.create import (
    CREATE_FAILED_MESSAGE,
    CREATION_SUMMARY,
    INITIAL_DP_STATUS,
    INITIAL_OP_STATUS,
    INITIAL_VERSION,
    CreateError,
    active_reference_values,
    build_initial_document,
    create_one_pager,
    duplicate_error,
)
from onepagerapp.workflow.data_product import (
    change_data_product_status,
    data_product_options,
)
from onepagerapp.workflow.review_decisions import (
    REJECT_COMMENT_REQUIRED,
    ApprovalPlan,
    CommentRequiredError,
    approve_one_pager,
    plan_approval,
    reject_one_pager,
)
from onepagerapp.workflow.submit import (
    SUBMIT_LOCK_MESSAGE,
    SubmitResult,
    submit_for_review,
)
from onepagerapp.workflow.transitions import (
    TRANSITION_CONFLICT_MESSAGE,
    TRANSITION_FAILED_MESSAGE,
    ConfirmationRequiredError,
    TransitionError,
    apply_transitions,
    get_workflow_reference,
    plan_transitions,
)
from onepagerapp.workflow.update import start_update

__all__ = [
    "CREATE_FAILED_MESSAGE",
    "CREATION_SUMMARY",
    "INITIAL_DP_STATUS",
    "INITIAL_OP_STATUS",
    "INITIAL_VERSION",
    "MAX_REASON_LENGTH",
    "REJECT_COMMENT_REQUIRED",
    "SUBMIT_LOCK_MESSAGE",
    "TRANSITION_CONFLICT_MESSAGE",
    "TRANSITION_FAILED_MESSAGE",
    "ApprovalPlan",
    "CommentRequiredError",
    "ConfirmationRequiredError",
    "CreateError",
    "SubmitResult",
    "TransitionError",
    "active_reference_values",
    "apply_transitions",
    "approve_one_pager",
    "build_initial_document",
    "cancel_one_pager",
    "change_data_product_status",
    "create_one_pager",
    "data_product_options",
    "duplicate_error",
    "get_workflow_reference",
    "next_major",
    "plan_approval",
    "plan_transitions",
    "reject_one_pager",
    "start_update",
    "submit_for_review",
]
