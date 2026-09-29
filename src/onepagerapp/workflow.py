"""One Pager workflow service (Backend_Design.md §2).

Currently implements the ``create`` transition: (new) -> ``Draft``. Guard: the
user may create One Pagers. Side effects: assign the OP ID, write the YAML
document, insert the authorized users, the change log entry and the
``one_pager_status`` row.

Write order and failure handling follow New_One_Pager_Plan D5: the YAML file is
written first and the ``one_pager_status`` row last, so a failure at any step
leaves nothing visible in the Registry or Preview. Pure Python — no Streamlit.
"""

import logging
from datetime import UTC, datetime

import pandas as pd

from onepagerapp.audit import Outcome, log_event, log_permission_denied
from onepagerapp.data_access.base import DataAccess
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.documents.serialization import document_to_dict
from onepagerapp.id_generator import IdGenerationError, next_id
from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    CreateResult,
    CurrentUser,
    NewOnePagerInput,
    OnePagerDocument,
    OnePagerStatusRow,
    ValidationError,
)
from onepagerapp.permissions import PermissionDeniedError, can_create_one_pager
from onepagerapp.validation import (
    CURRENT_STRUCTURE_DEFINITION,
    normalize_new_one_pager,
    validate_create,
    validate_schema,
)

logger = logging.getLogger(__name__)

INITIAL_VERSION = "0.1.0"
INITIAL_OP_STATUS = "Draft"
INITIAL_DP_STATUS = "In Definition"
CREATION_SUMMARY = "Initial draft created"

CREATE_FAILED_MESSAGE = "Create failed — your changes are preserved, please retry."


class CreateError(RuntimeError):
    """A One Pager could not be stored. The message is safe to show to users."""


def active_reference_values(df: pd.DataFrame, column: str) -> list[str]:
    """Return the values of a reference table column, active rows only.

    Rows without an ``active`` column are treated as active. The Statement
    Execution API returns booleans as strings, so "true" is accepted too.
    """
    if df is None or df.empty or column not in df.columns:
        return []
    if "active" in df.columns:
        mask = df["active"].map(lambda v: v is True or str(v).lower() == "true")
        df = df[mask]
    if "sort_order" in df.columns:
        df = df.sort_values("sort_order", key=lambda s: pd.to_numeric(s))
    return [str(v) for v in df[column].tolist()]


def duplicate_error(existing_id: str) -> ValidationError:
    """Field error shown when the data product already has a One Pager."""
    return ValidationError(
        "dataProduct",
        f"A One Pager for this Data Product already exists ({existing_id}).",
    )


def build_initial_document(
    data: NewOnePagerInput, user: CurrentUser, now: datetime
) -> OnePagerDocument:
    """Build the v0.1.0 Draft document (New_One_Pager_Plan D9)."""
    timestamp = now.isoformat(timespec="seconds")
    return OnePagerDocument(
        structure_definition=CURRENT_STRUCTURE_DEFINITION,
        data_product=data.data_product,
        product_name=data.product_name,
        business_domain=data.business_domain,
        data_product_type=data.data_product_type,
        one_pager_status=INITIAL_OP_STATUS,
        data_product_status=INITIAL_DP_STATUS,
        version=INITIAL_VERSION,
        description=data.description,
        owner_name=data.owner.name,
        owner_initials=data.owner.initials,
        owner_email=data.owner.email,
        owner_team=data.owner.team,
        business_problem_statement=data.business_problem_statement,
        smes=[
            {
                k: v
                for k, v in {
                    "name": s.name,
                    "initials": s.initials,
                    "email": s.email,
                    "team": s.team,
                }.items()
                if v
            }
            for s in data.smes
        ],
        created_by=user.display_name,
        created_at=timestamp,
        last_updated=timestamp,
        change_log=[
            {
                "version": INITIAL_VERSION,
                "date": timestamp,
                "author": user.display_name,
                "summary": CREATION_SUMMARY,
            }
        ],
    )


def _authorized_users(
    one_pager_id: str, data: NewOnePagerInput
) -> list[AuthorizedUser]:
    """Build the Owner + SME rows from the document (D3) — not the creator."""
    users = [
        AuthorizedUser(
            one_pager_id=one_pager_id,
            user_initials=data.owner.initials,
            user_name=data.owner.name,
            user_email=data.owner.email,
            user_team=data.owner.team,
            role="owner",
        )
    ]
    users.extend(
        AuthorizedUser(
            one_pager_id=one_pager_id,
            user_initials=s.initials,
            user_name=s.name,
            user_email=s.email,
            user_team=s.team,
            role="sme",
        )
        for s in data.smes
    )
    return users


def _status_row(
    one_pager_id: str, data: NewOnePagerInput, user: CurrentUser, now: datetime
) -> OnePagerStatusRow:
    return OnePagerStatusRow(
        one_pager_id=one_pager_id,
        data_product=data.data_product,
        product_name=data.product_name,
        business_domain=data.business_domain,
        data_product_type=data.data_product_type,
        one_pager_status=INITIAL_OP_STATUS,
        data_product_status=INITIAL_DP_STATUS,
        version=INITIAL_VERSION,
        owner_name=data.owner.name,
        owner_initials=data.owner.initials,
        owner_email=data.owner.email,
        owner_team=data.owner.team,
        created_by=user.initials,
        created_at=now,
        last_updated_at=now,
        last_updated_by=user.initials,
        structure_definition=CURRENT_STRUCTURE_DEFINITION,
    )


def _creation_entry(
    one_pager_id: str, user: CurrentUser, now: datetime
) -> ChangeLogEntry:
    """Build the single system-generated creation entry (D10)."""
    return ChangeLogEntry(
        id=0,
        one_pager_id=one_pager_id,
        version=INITIAL_VERSION,
        event_type="creation",
        author_initials=user.initials,
        author_name=user.display_name,
        summary=CREATION_SUMMARY,
        created_at=now,
        from_status=None,
        to_status=INITIAL_OP_STATUS,
        status_field="one_pager_status",
    )


def _compensate(data_access: DataAccess, one_pager_id: str, user: CurrentUser) -> None:
    """Best-effort removal of the rows of a create that did not complete."""
    try:
        data_access.delete_one_pager_records(one_pager_id)
    except Exception:
        logger.exception(f"Compensation failed for {one_pager_id}")
        log_event(
            "create_one_pager",
            Outcome.COMPENSATION_FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
        )


def create_one_pager(
    data: NewOnePagerInput,
    user: CurrentUser,
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    now: datetime | None = None,
) -> CreateResult:
    """Create a new Draft One Pager.

    Args:
        data: Raw form input (sanitized here).
        user: The authenticated creator.
        data_access: Tabular data access (Delta or mock).
        document_store: YAML document store (volume or local folder).
        now: Creation instant (defaults to the current UTC time).

    Returns:
        CreateResult with the new ID, or with validation errors (nothing written).

    Raises:
        PermissionDeniedError: The user may not create One Pagers.
        CreateError: Storing failed; partial writes were compensated.

    """
    if not can_create_one_pager(user):
        log_permission_denied("create_one_pager", user=user.initials if user else None)
        msg = "You are not allowed to create One Pagers."
        raise PermissionDeniedError(msg)

    data = normalize_new_one_pager(data)
    errors = validate_create(
        data,
        user,
        allowed_domains=active_reference_values(
            data_access.get_ref_business_domains(), "domain"
        ),
        allowed_types=active_reference_values(
            data_access.get_ref_data_product_types(), "type"
        ),
    )
    if errors:
        return CreateResult(errors=errors)

    existing = data_access.get_one_pager_ids_for_data_product(data.data_product)
    if existing:
        return CreateResult(errors=[duplicate_error(existing[0])])

    try:
        one_pager_id = next_id(data_access, "OP")
    except (IdGenerationError, RuntimeError) as e:
        logger.exception("Failed to reserve a One Pager ID")
        raise CreateError(CREATE_FAILED_MESSAGE) from e

    now = now or datetime.now(UTC)
    document = build_initial_document(data, user, now)
    schema_errors = validate_schema(document_to_dict(document))
    if schema_errors:
        # The create tier should make this impossible; treat as a bug.
        details = "; ".join(f"{e.field_path}: {e.message}" for e in schema_errors)
        logger.error(
            f"Generated document for {one_pager_id} is not schema-valid: {details}"
        )
        raise CreateError(CREATE_FAILED_MESSAGE)

    # D5 step 2: the file lives under an OP ID nothing references yet.
    try:
        document_store.write(one_pager_id, document, INITIAL_VERSION)
    except RuntimeError as e:
        logger.exception(f"Writing the document for {one_pager_id} failed")
        log_event(
            "create_one_pager",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step="write_document",
        )
        raise CreateError(CREATE_FAILED_MESSAGE) from e

    # D5 steps 3-5: status row last, it makes the One Pager visible.
    step = "insert_authorized_users"
    try:
        data_access.insert_authorized_users(_authorized_users(one_pager_id, data))
        step = "append_change_log"
        data_access.append_change_log(_creation_entry(one_pager_id, user, now))
        step = "insert_one_pager_status"
        data_access.insert_one_pager_status(_status_row(one_pager_id, data, user, now))
    except Exception as e:
        logger.exception(f"Create of {one_pager_id} failed at {step}")
        log_event(
            "create_one_pager",
            Outcome.FAILED,
            user=user.initials,
            one_pager_id=one_pager_id,
            step=step,
        )
        _compensate(data_access, one_pager_id, user)
        raise CreateError(CREATE_FAILED_MESSAGE) from e

    # D4: Delta does not enforce UNIQUE — the lower OP ID wins a race.
    try:
        ids = data_access.get_one_pager_ids_for_data_product(data.data_product)
    except Exception:
        logger.exception(f"Uniqueness re-check failed for {one_pager_id}")
        ids = [one_pager_id]
    if ids and ids[0] != one_pager_id:
        log_event(
            "create_one_pager",
            Outcome.DUPLICATE_RACE,
            user=user.initials,
            one_pager_id=one_pager_id,
            winner=ids[0],
        )
        _compensate(data_access, one_pager_id, user)
        return CreateResult(errors=[duplicate_error(ids[0])])

    log_event(
        "create_one_pager",
        Outcome.SUCCESS,
        user=user.initials,
        one_pager_id=one_pager_id,
    )
    return CreateResult(one_pager_id=one_pager_id, version=INITIAL_VERSION)
