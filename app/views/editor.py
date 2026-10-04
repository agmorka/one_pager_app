"""Editor page — create a new One Pager, or edit an existing one.

Create mode (``editor_mode == "create"``) implements the Editor's "New (empty
form)" state from UI_Design.md §4.2 for the Basics section
(New_One_Pager_Plan D1). Edit mode (``editor_mode == "edit"``, opened from
Preview → Edit) lives in ``adapters.edit_mode``.

The view is thin: it collects input, calls ``workflow.create_one_pager`` and
renders the outcome. Validation, ID generation and storage live in the core
package. Form values are kept in ``st.session_state`` (keys prefixed
``create_``) so they survive re-runs and failed saves.
"""

import logging

import pandas as pd
import streamlit as st

from adapters.cache import writes_data
from adapters.edit_mode import render_edit_mode
from adapters.navigation import EDITOR_MODE_KEY, go_to_registry, open_preview
from adapters.page import (
    ALERT_ICON,
    current_roles,
    current_user,
    render_retry_banner,
    require_data_access,
    require_document_store,
)
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.documents import OnePagerDocumentStore
from onepagerapp.models import CurrentUser, NewOnePagerInput, PersonRef, ValidationError
from onepagerapp.permissions import PermissionDeniedError, can_create_one_pager
from onepagerapp.validation import (
    DATA_PRODUCT_RULE,
    EMAIL_PATTERN,
    MAX_NAME_LENGTH,
    MAX_TEXT_LENGTH,
)
from onepagerapp.workflow import (
    CREATE_FAILED_MESSAGE,
    CreateError,
    active_reference_values,
    create_one_pager,
)

logger = logging.getLogger(__name__)

SME_COLUMNS = ["name", "initials", "email", "team"]
_ERRORS_KEY = "create_errors"
_BANNER_KEY = "create_banner"


# ============================================================================
# Cached Data
# ============================================================================


@st.cache_data(ttl=3600)
def _get_domain_options(_data_access: DataAccess) -> list[str]:
    return active_reference_values(_data_access.get_ref_business_domains(), "domain")


@st.cache_data(ttl=3600)
def _get_type_options(_data_access: DataAccess) -> list[str]:
    return active_reference_values(_data_access.get_ref_data_product_types(), "type")


# ============================================================================
# Helpers
# ============================================================================


def _prefill_email(user: CurrentUser | None) -> str:
    """Return the directory email, else the username when it looks like one."""
    if user is None:
        return ""
    for candidate in (user.email, user.username):
        if candidate and EMAIL_PATTERN.match(candidate):
            return candidate
    return ""


def _init_form_state(user: CurrentUser | None) -> None:
    """Initialize blank form values once; Owner is pre-filled with the user (D3)."""
    if "create_initialized" in st.session_state:
        return
    defaults = {
        "create_data_product": "",
        "create_product_name": "",
        "create_business_domain": None,
        "create_data_product_type": None,
        "create_description": "",
        "create_problem": "",
        "create_owner_name": user.display_name if user else "",
        "create_owner_initials": user.initials if user else "",
        "create_owner_email": _prefill_email(user),
        "create_owner_team": "",
    }
    for key, value in defaults.items():
        st.session_state[key] = value
    # The grid's source data must stay the same object across re-runs,
    # otherwise the data editor loses the user's edits.
    st.session_state["create_smes_initial"] = pd.DataFrame(
        {c: pd.Series(dtype="str") for c in SME_COLUMNS}
    )
    st.session_state[_ERRORS_KEY] = []
    st.session_state["create_initialized"] = True


def _clear_form_state() -> None:
    for key in [k for k in st.session_state if str(k).startswith("create_")]:
        del st.session_state[key]


def _cell(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _smes_from_grid(df: pd.DataFrame) -> list[PersonRef]:
    """Convert the SME grid to PersonRefs (blank rows are dropped by validation)."""
    return [
        PersonRef(
            name=_cell(row.get("name")),
            initials=_cell(row.get("initials")),
            email=_cell(row.get("email")),
            team=_cell(row.get("team")) or None,
        )
        for _, row in df.iterrows()
    ]


def _form_has_content() -> bool:
    keys = [
        "create_data_product",
        "create_product_name",
        "create_business_domain",
        "create_data_product_type",
        "create_description",
        "create_problem",
    ]
    return any(st.session_state.get(k) for k in keys)


def _show_errors(prefix: str) -> None:
    """Render the errors whose field path equals or starts with ``prefix``."""
    for error in st.session_state.get(_ERRORS_KEY, []):
        if error.field_path == prefix or error.field_path.startswith(prefix + "."):
            st.error(error.message, icon=ALERT_ICON)


def _show_sme_errors() -> None:
    for error in st.session_state.get(_ERRORS_KEY, []):
        path = error.field_path
        if path == "smes":
            st.error(error.message, icon=ALERT_ICON)
        elif path.startswith("smes["):
            index = int(path[5 : path.index("]")])
            field = path.split(".", 1)[1] if "." in path else ""
            st.error(f"SME row {index + 1} ({field}): {error.message}", icon=ALERT_ICON)


_FIELD_LABELS = {
    "dataProduct": "Data Product",
    "productName": "Product Name",
    "businessDomain": "Business Domain",
    "dataProductType": "Product Type",
    "description": "Description",
    "businessProblemStatement": "Business Problem Statement",
    "dataProductOwner": "Owner",
    "smes": "SMEs",
}


def _render_error_summary(errors: list[ValidationError]) -> None:
    """Render the validation summary panel at the bottom (UI_Design §4.2)."""
    if not errors:
        return
    lines = []
    for error in errors:
        section = error.field_path.split(".")[0].split("[")[0]
        label = _FIELD_LABELS.get(section, section or "Form")
        lines.append(f"- **{label}:** {error.message}")
    st.warning(
        f"⚠ {len(errors)} issue(s) must be fixed before the One Pager can be "
        "created:\n\n" + "\n".join(lines)
    )


def _go_to_registry() -> None:
    _clear_form_state()
    st.session_state.pop(EDITOR_MODE_KEY, None)
    go_to_registry()


@st.dialog("Discard new One Pager?")
def _confirm_cancel() -> None:
    st.write("The information you entered will be lost.")
    col1, col2 = st.columns(2)
    if col1.button("Discard", type="primary", use_container_width=True):
        _go_to_registry()
    if col2.button("Keep editing", use_container_width=True):
        st.rerun()


def _submit(
    data_access: DataAccess,
    document_store: OnePagerDocumentStore,
    user: CurrentUser,
    smes_df: pd.DataFrame,
) -> None:
    """Run the create action and route to Preview on success."""
    data = NewOnePagerInput(
        data_product=st.session_state.create_data_product,
        product_name=st.session_state.create_product_name,
        business_domain=st.session_state.create_business_domain or "",
        data_product_type=st.session_state.create_data_product_type or "",
        description=st.session_state.create_description,
        business_problem_statement=st.session_state.create_problem,
        owner=PersonRef(
            name=st.session_state.create_owner_name,
            initials=st.session_state.create_owner_initials,
            email=st.session_state.create_owner_email,
            team=st.session_state.create_owner_team or None,
        ),
        smes=_smes_from_grid(smes_df),
    )
    st.session_state[_BANNER_KEY] = None
    try:
        with st.spinner("Creating One Pager..."), writes_data():
            result = create_one_pager(
                data, user, data_access, document_store, roles=current_roles()
            )
    except PermissionDeniedError as e:
        st.session_state[_BANNER_KEY] = str(e)
        st.session_state[_ERRORS_KEY] = []
        return
    except CreateError as e:
        st.session_state[_BANNER_KEY] = str(e)
        st.session_state[_ERRORS_KEY] = []
        return
    except Exception:
        logger.exception("Unexpected error creating a One Pager")
        st.session_state[_BANNER_KEY] = CREATE_FAILED_MESSAGE
        st.session_state[_ERRORS_KEY] = []
        return

    if not result.ok:
        st.session_state[_ERRORS_KEY] = result.errors
        return

    # Success: remember the ID first so a re-run can never create a second one.
    _clear_form_state()
    st.session_state.pop(EDITOR_MODE_KEY, None)
    open_preview(
        str(result.one_pager_id),
        flash=f"One Pager {result.one_pager_id} created as Draft (v{result.version}).",
    )


# ============================================================================
# Editor Page
# ============================================================================

data_access = require_data_access()
document_store = require_document_store()
user = current_user()

if st.session_state.get(EDITOR_MODE_KEY) == "edit":
    render_edit_mode(data_access, document_store, user)
    st.stop()

if st.session_state.get(EDITOR_MODE_KEY) != "create":
    st.title("Editor")
    st.info(
        "Start from the Registry (➕ New) or from a One Pager's Edit action."  # noqa: RUF001
    )
    if st.button("📋 Go to the Registry"):
        go_to_registry()
    st.stop()

if not can_create_one_pager(user, current_roles()):
    st.title("New One Pager")
    st.error("You don't have permission to create One Pagers.")
    st.stop()

_init_form_state(user)

try:
    domain_options = _get_domain_options(data_access)
    type_options = _get_type_options(data_access)
except Exception as e:
    logger.exception("Failed to load reference data for the editor")
    if render_retry_banner(
        user_error_message(e, "Couldn't load the reference data. Please retry."),
        key="editor_retry",
    ):
        st.cache_data.clear()
        st.rerun()
    st.stop()

# Header
st.title("New One Pager")
st.markdown(
    "**Status:** ● Draft &nbsp;·&nbsp; **Data Product status:** ● In Definition "
    "&nbsp;·&nbsp; **Version:** v0.1.0"
)

banner = st.session_state.get(_BANNER_KEY)
if banner:
    st.error(banner)

st.caption(
    "Fill in the basics to create a Draft. The remaining sections are completed "
    "in the Editor after creation. Fields marked * are required."
)

# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------
st.subheader("Basics")

st.text_input(
    "Data Product *",
    key="create_data_product",
    max_chars=63,
    placeholder="e.g. customer_master",
    help="Registered unique name of the Data Product. "
    + DATA_PRODUCT_RULE
    + " It cannot be changed later.",
)
_show_errors("dataProduct")

st.text_input(
    "Product Name *",
    key="create_product_name",
    max_chars=MAX_NAME_LENGTH,
    placeholder="Human-readable name, e.g. Customer Master Data",
)
_show_errors("productName")

col_domain, col_type = st.columns(2)
with col_domain:
    st.selectbox(
        "Business Domain *",
        options=domain_options,
        index=None,
        placeholder="Select…",
        key="create_business_domain",
    )
    _show_errors("businessDomain")
with col_type:
    st.selectbox(
        "Product Type *",
        options=type_options,
        index=None,
        placeholder="Select…",
        key="create_data_product_type",
    )
    _show_errors("dataProductType")

st.text_area(
    "Description *",
    key="create_description",
    max_chars=MAX_TEXT_LENGTH,
    placeholder="What the Data Product is and what it provides.",
)
_show_errors("description")

# ---------------------------------------------------------------------------
# Owner & SMEs
# ---------------------------------------------------------------------------
st.subheader("Data Product Owner")
col_name, col_initials = st.columns([3, 1])
with col_name:
    st.text_input("Name *", key="create_owner_name", max_chars=MAX_NAME_LENGTH)
    _show_errors("dataProductOwner.name")
with col_initials:
    st.text_input(
        "Initials *",
        key="create_owner_initials",
        max_chars=5,
        help="Corporate initials, e.g. X0W (3 letters or digits) — used to grant "
        "edit access.",
    )
    _show_errors("dataProductOwner.initials")
col_email, col_team = st.columns(2)
with col_email:
    st.text_input("Email *", key="create_owner_email", max_chars=254)
    _show_errors("dataProductOwner.email")
with col_team:
    st.text_input("Team", key="create_owner_team", max_chars=MAX_NAME_LENGTH)
    _show_errors("dataProductOwner.team")

st.subheader("Subject Matter Experts")
st.caption(
    "SMEs can edit this One Pager. Add a row per SME (name, initials and email "
    "are required). If you are not the Owner, add yourself here."
)
smes_df = st.data_editor(
    st.session_state["create_smes_initial"],
    key="create_smes_editor",
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    column_config={
        "name": st.column_config.TextColumn("Name", max_chars=MAX_NAME_LENGTH),
        "initials": st.column_config.TextColumn("Initials", max_chars=5),
        "email": st.column_config.TextColumn("Email", max_chars=254),
        "team": st.column_config.TextColumn("Team", max_chars=MAX_NAME_LENGTH),
    },
)
_show_sme_errors()

# ---------------------------------------------------------------------------
# Business problem (optional)
# ---------------------------------------------------------------------------
st.subheader("Business Problem Statement")
st.text_area(
    "Business Problem Statement (optional)",
    key="create_problem",
    max_chars=MAX_TEXT_LENGTH,
    placeholder="Current state, desired state and impact of inaction.",
)
_show_errors("businessProblemStatement")

# ---------------------------------------------------------------------------
# Bottom bar
# ---------------------------------------------------------------------------
st.divider()
_render_error_summary(st.session_state.get(_ERRORS_KEY, []))

col_create, col_cancel, _ = st.columns([1, 1, 4])
with col_create:
    create_clicked = st.button("Create Draft", type="primary", use_container_width=True)
with col_cancel:
    cancel_clicked = st.button("Cancel", use_container_width=True)

if create_clicked and user is not None:
    _submit(data_access, document_store, user, smes_df)
    st.rerun()

if cancel_clicked:
    if _form_has_content() or not smes_df.dropna(how="all").empty:
        _confirm_cancel()
    else:
        _go_to_registry()
