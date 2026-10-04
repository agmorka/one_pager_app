"""Editor tabs for the array sections of a One Pager (UI_Design.md §4.2).

Each renderer takes the editor's working copy and changes it in place; the
data is only stored on **Save Draft**. Widget keys start with ``edit_`` so
leaving the editor clears them.
"""

import logging
from typing import Any

import streamlit as st

from adapters.cache import get_use_cases, writes_data
from adapters.page import ALERT_ICON, current_roles, current_user
from adapters.repeating import (
    BOOL,
    DATE,
    MULTISELECT,
    SELECT,
    TEXTAREA,
    FieldSpec,
    RepeatingSection,
    render_repeating_items,
    render_string_items,
)
from onepagerapp.data_access.base import DataAccess
from onepagerapp.data_access.connection import user_error_message
from onepagerapp.editing import assign_requirement_id, link_denied_reason
from onepagerapp.models import (
    PRIORITY_OPTIONS,
    CurrentUser,
    OnePagerDocument,
    UseCase,
    UseCaseFilter,
    UseCaseInput,
)
from onepagerapp.permissions import can_manage_use_cases
from onepagerapp.use_cases import (
    USE_CASE_FIELDS,
    clean_use_case_input,
    create_use_case,
    validate_use_case_input,
)
from onepagerapp.validation import MAX_NAME_LENGTH, MAX_TEXT_LENGTH

logger = logging.getLogger(__name__)

CLASSIFICATION_LEVELS = ("Public", "Internal", "Confidential", "Restricted")
QUALITY_DIMENSIONS = (
    "Completeness",
    "Accuracy",
    "Consistency",
    "Timeliness",
    "Validity",
    "Uniqueness",
)
QUESTION_STATUSES = ("Open", "Answered", "Closed")
USE_CASE_PICKER_SIZE = 500


def bound(key: str, value: object) -> str:
    """Seed a widget's session-state value from the document once; return the key.

    Widgets of tabs that are not rendered lose their state, so a tab that is
    shown again is seeded from the working copy.
    """
    if key not in st.session_state:
        st.session_state[key] = value
    return key


# ============================================================================
# Use Cases
# ============================================================================


def _use_case_or_none(data_access: DataAccess, use_case_id: str) -> UseCase | None:
    try:
        return data_access.get_use_case(use_case_id)
    except Exception:
        logger.exception(f"Failed to load Use Case {use_case_id}")
        return None


def _linkable_use_cases(data_access: DataAccess, linked: list[str]) -> list[UseCase]:
    page = get_use_cases(
        data_access, UseCaseFilter(include_deprecated=False), 1, USE_CASE_PICKER_SIZE
    )
    return [uc for uc in page.rows if uc.use_case_id not in linked]


def _render_linked_use_cases(doc: OnePagerDocument, data_access: DataAccess) -> None:
    references = [uc for uc in doc.use_cases if uc.get("useCaseId")]
    legacy = [uc for uc in doc.use_cases if not uc.get("useCaseId")]
    if not references:
        st.caption("No Use Cases linked yet.")
    for item in references:
        use_case_id = str(item["useCaseId"])
        use_case = _use_case_or_none(data_access, use_case_id)
        col_text, col_unlink = st.columns([6, 1])
        if use_case is None:
            col_text.write(f"**{use_case_id}** — (not available)")
        else:
            deprecated = " · *deprecated*" if use_case.deprecated else ""
            col_text.markdown(
                f"**{use_case_id}** · {use_case.persona} — {use_case.goal} "
                f"· {use_case.priority}{deprecated}"
            )
            col_text.caption(f"Decision enabled: {use_case.decision_enabled}")
        if col_unlink.button("Unlink", key=f"edit_uc_unlink_{use_case_id}"):
            doc.use_cases = [
                uc for uc in doc.use_cases if uc.get("useCaseId") != use_case_id
            ]
            st.rerun()
    if legacy:
        st.info(
            f"This One Pager still holds {len(legacy)} inline Use Case(s) from an "
            "older format. Link the matching shared Use Cases and remove the old "
            "entries."
        )
        if st.button("Remove the old inline Use Cases", key="edit_uc_drop_legacy"):
            doc.use_cases = references
            st.rerun()


def _render_link_existing(doc: OnePagerDocument, data_access: DataAccess) -> None:
    try:
        options = _linkable_use_cases(data_access, doc.use_case_ids)
    except Exception as e:
        logger.exception("Failed to load Use Cases")
        st.error(
            user_error_message(e, "Couldn't load the Use Cases. Please retry."),
            icon=ALERT_ICON,
        )
        return
    if not options:
        st.caption("Every active Use Case is already linked.")
        return
    labels = {
        uc.use_case_id: f"{uc.use_case_id} · {uc.persona} — {uc.goal}" for uc in options
    }
    col_pick, col_link = st.columns([5, 1])
    choice = col_pick.selectbox(
        "Link an existing Use Case",
        options=list(labels),
        format_func=labels.get,
        index=None,
        placeholder="Search by ID, persona or goal…",
        key="edit_uc_pick",
    )
    col_link.write("")
    if col_link.button("Link", key="edit_uc_link", disabled=choice is None):
        reason = link_denied_reason(data_access, str(choice))
        if reason:
            st.error(reason)
            return
        doc.use_cases.append({"useCaseId": str(choice)})
        st.session_state.pop("edit_uc_pick", None)
        st.rerun()


def _render_create_use_case(doc: OnePagerDocument, data_access: DataAccess) -> None:
    user = current_user()
    if user is None or not can_manage_use_cases(user.initials, current_roles()):
        return
    with st.expander("Create a new Use Case"):
        for name, (label, max_length) in USE_CASE_FIELDS.items():
            st.text_area(f"{label} *", key=f"edit_uc_new_{name}", max_chars=max_length)
        st.selectbox(
            "Priority *",
            options=PRIORITY_OPTIONS,
            index=None,
            placeholder="Select…",
            key="edit_uc_new_priority",
        )
        for message in st.session_state.get("edit_uc_new_errors", []):
            st.error(message, icon=ALERT_ICON)
        if st.button("Create and link", key="edit_uc_create", type="primary"):
            _create_use_case(doc, data_access, user)


def _create_use_case(
    doc: OnePagerDocument, data_access: DataAccess, user: CurrentUser
) -> None:
    """Create a shared Use Case (stored at once) and link it in the working copy."""
    data = clean_use_case_input(
        UseCaseInput(
            **{
                name: st.session_state.get(f"edit_uc_new_{name}") or ""
                for name in USE_CASE_FIELDS
            },
            priority=st.session_state.get("edit_uc_new_priority") or "",
        )
    )
    errors = validate_use_case_input(data)
    if errors:
        st.session_state["edit_uc_new_errors"] = list(errors.values())
        st.rerun()
    try:
        with writes_data():
            use_case_id = create_use_case(
                data_access,
                data,
                user,
                roles=current_roles(),
            )
    except Exception:
        logger.exception("Failed to create a Use Case from the editor")
        st.session_state["edit_uc_new_errors"] = [
            "Couldn't create the Use Case. Please retry."
        ]
        st.rerun()
    doc.use_cases.append({"useCaseId": use_case_id})
    for key in [k for k in st.session_state if str(k).startswith("edit_uc_new_")]:
        del st.session_state[key]
    st.session_state["edit_flash"] = (
        f"Use Case {use_case_id} created and linked. Save the draft to keep the link."
    )
    st.rerun()


def render_use_cases_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:
    """Render the Use Cases tab: linked Use Cases, link existing, create new."""
    st.caption(
        "Use Cases are shared across One Pagers. Links are stored when you save "
        "the draft; a new Use Case is added to the shared registry immediately."
    )
    _render_linked_use_cases(doc, data_access)
    st.divider()
    _render_link_existing(doc, data_access)
    _render_create_use_case(doc, data_access)


# ============================================================================
# Business Requirements, Data Sources, Data Product Preview
# ============================================================================


def requirements_section(data_access: DataAccess) -> RepeatingSection:
    def on_add(item: dict[str, Any]) -> None:
        try:
            assign_requirement_id(data_access, item)
        except Exception as e:
            logger.exception("Failed to assign a BR ID")
            msg = "Couldn't assign a requirement ID. Please retry."
            raise RuntimeError(msg) from e

    return RepeatingSection(
        key="edit_br",
        fields=[
            FieldSpec("id", "ID", editable=False, help="Assigned automatically."),
            FieldSpec(
                "requirement",
                "Requirement",
                kind=TEXTAREA,
                required=True,
                max_chars=MAX_TEXT_LENGTH,
            ),
            FieldSpec("priority", "Priority", kind=SELECT, options=PRIORITY_OPTIONS),
            FieldSpec("notes", "Notes", kind=TEXTAREA, max_chars=MAX_TEXT_LENGTH),
        ],
        item_label="Requirement",
        title_field="requirement",
        on_add=on_add,
        empty="No business requirements yet.",
    )


def render_requirements_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:
    """Render the Business Requirements tab (IDs are assigned automatically)."""
    render_repeating_items(requirements_section(data_access), doc.business_requirements)


DATA_SOURCES = RepeatingSection(
    key="edit_ds",
    fields=[
        FieldSpec("name", "Source name", required=True, max_chars=MAX_NAME_LENGTH),
        FieldSpec("sourceSystem", "Source system", max_chars=MAX_NAME_LENGTH),
        FieldSpec("epoId", "EPO ID", max_chars=MAX_NAME_LENGTH),
        FieldSpec(
            "dataProvided",
            "Data provided",
            kind=TEXTAREA,
            required=True,
            max_chars=MAX_TEXT_LENGTH,
        ),
        FieldSpec("refreshFrequency", "Refresh frequency", max_chars=MAX_NAME_LENGTH),
    ],
    item_label="Source",
    title_field="name",
    empty="No data sources yet.",
)


def render_data_sources_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:  # noqa: ARG001
    """Render the Data Sources tab."""
    render_repeating_items(DATA_SOURCES, doc.data_sources)


def data_elements_section(use_case_ids: list[str]) -> RepeatingSection:
    return RepeatingSection(
        key="edit_dpp",
        fields=[
            FieldSpec(
                "elementName", "Element name", required=True, max_chars=MAX_NAME_LENGTH
            ),
            FieldSpec(
                "dataType", "Data type", required=True, max_chars=MAX_NAME_LENGTH
            ),
            FieldSpec("isPrimaryKey", "Primary key", kind=BOOL),
            FieldSpec("containsPII", "Contains PII", kind=BOOL),
            FieldSpec(
                "isCriticalDataElement", "Critical Data Element (CDE)", kind=BOOL
            ),
            FieldSpec(
                "cdeCriticalityTiering",
                "CDE criticality tiering",
                only_if="isCriticalDataElement",
                max_chars=MAX_NAME_LENGTH,
            ),
            FieldSpec(
                "description",
                "Description",
                kind=TEXTAREA,
                required=True,
                max_chars=MAX_TEXT_LENGTH,
            ),
            FieldSpec("example", "Example", max_chars=MAX_NAME_LENGTH, in_table=False),
            FieldSpec("source", "Source", max_chars=MAX_NAME_LENGTH, in_table=False),
            FieldSpec(
                "useCaseLinks",
                "Use Case links",
                kind=MULTISELECT,
                options=tuple(use_case_ids),
                only_if="isCriticalDataElement",
                help="Use Cases this CDE supports (only the linked Use Cases).",
            ),
        ],
        item_label="Element",
        title_field="elementName",
        empty="No data elements yet.",
    )


def render_data_product_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:  # noqa: ARG001
    """Render the Data Product Preview tab (the data element grid)."""
    st.caption(
        "CDE tiering and Use Case links only apply to Critical Data Elements; "
        "they are cleared for other elements."
    )
    render_repeating_items(
        data_elements_section(doc.use_case_ids), doc.data_product_preview
    )


# ============================================================================
# Classification
# ============================================================================

RETENTION = RepeatingSection(
    key="edit_ret",
    fields=[
        FieldSpec(
            "dataCategory", "Data category", required=True, max_chars=MAX_NAME_LENGTH
        ),
        FieldSpec(
            "retentionPeriod",
            "Retention period",
            required=True,
            max_chars=MAX_NAME_LENGTH,
        ),
        FieldSpec(
            "legalBasis", "Legal basis", kind=TEXTAREA, max_chars=MAX_TEXT_LENGTH
        ),
    ],
    item_label="Retention requirement",
    title_field="dataCategory",
    empty="No retention requirements yet.",
)


def retention_required(classification: dict[str, Any]) -> bool:
    """Whether the business rule requires retention requirements (Req §5)."""
    level = classification.get("classificationLevel")
    return bool(
        classification.get("containsPII")
        or classification.get("containsSensitiveData")
        or (level and level != "Public")
    )


def render_classification_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:  # noqa: ARG001
    """Render the Classification tab: level, sensitivity flags and retention."""
    dc = doc.data_classification
    level = st.selectbox(
        "Classification level *",
        options=CLASSIFICATION_LEVELS,
        index=None,
        placeholder="Select…",
        key=bound(
            "edit_class_level",
            dc.get("classificationLevel")
            if dc.get("classificationLevel") in CLASSIFICATION_LEVELS
            else None,
        ),
    )
    pii = st.checkbox(
        "Contains PII", key=bound("edit_class_pii", bool(dc.get("containsPII")))
    )
    sensitive = st.checkbox(
        "Contains sensitive data",
        key=bound("edit_class_sensitive", bool(dc.get("containsSensitiveData"))),
    )
    updated: dict[str, Any] = {"containsPII": pii, "containsSensitiveData": sensitive}
    if level:
        updated["classificationLevel"] = level
    if dc.get("retentionRequirements"):  # v1 location, kept until replaced
        updated["retentionRequirements"] = dc["retentionRequirements"]
    doc.data_classification = updated

    st.subheader("Retention requirements")
    if retention_required(updated):
        st.info(
            "Required: the data contains PII or sensitive data, or is not "
            "classified as Public."
        )
    if dc.get("retentionRequirements"):
        st.caption(f"Older format: {dc['retentionRequirements']}")
    render_repeating_items(RETENTION, doc.retention_requirements)


# ============================================================================
# Governance
# ============================================================================

BUSINESS_CONCEPTS = RepeatingSection(
    key="edit_bc",
    fields=[
        FieldSpec("name", "Concept", required=True, max_chars=MAX_NAME_LENGTH),
        FieldSpec(
            "definition",
            "Definition",
            kind=TEXTAREA,
            required=True,
            max_chars=MAX_TEXT_LENGTH,
        ),
    ],
    item_label="Business concept",
    title_field="name",
    empty="No business concepts yet.",
)
CDE_QUALITY = RepeatingSection(
    key="edit_cq",
    fields=[
        FieldSpec("elementName", "Element", required=True, max_chars=MAX_NAME_LENGTH),
        FieldSpec(
            "dimension",
            "Dimension",
            kind=SELECT,
            required=True,
            options=QUALITY_DIMENSIONS,
        ),
        FieldSpec(
            "rule", "Rule", kind=TEXTAREA, required=True, max_chars=MAX_TEXT_LENGTH
        ),
        FieldSpec("threshold", "Threshold", max_chars=MAX_NAME_LENGTH),
    ],
    item_label="Quality rule",
    title_field="elementName",
    empty="No CDE quality rules yet.",
)
CDE_LINEAGE = RepeatingSection(
    key="edit_cl",
    fields=[
        FieldSpec("elementName", "Element", required=True, max_chars=MAX_NAME_LENGTH),
        FieldSpec(
            "sourceSystem", "Source system", required=True, max_chars=MAX_NAME_LENGTH
        ),
        FieldSpec("sourceField", "Source field", max_chars=MAX_NAME_LENGTH),
        FieldSpec(
            "transformation", "Transformation", kind=TEXTAREA, max_chars=MAX_TEXT_LENGTH
        ),
    ],
    item_label="Lineage entry",
    title_field="elementName",
    empty="No CDE lineage yet.",
)

GOVERNANCE_SECTIONS = {
    "businessConcepts": ("Business concepts", BUSINESS_CONCEPTS),
    "cdeQuality": ("CDE quality", CDE_QUALITY),
    "cdeLineage": ("CDE lineage", CDE_LINEAGE),
}


def render_governance_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:  # noqa: ARG001
    """Render the Governance tab: business concepts, CDE quality and lineage."""
    artifacts = doc.data_governance_artifacts
    for name, (title, section) in GOVERNANCE_SECTIONS.items():
        st.subheader(title)
        render_repeating_items(section, artifacts.setdefault(name, []))


# ============================================================================
# Scope & Questions
# ============================================================================

OPEN_QUESTIONS = RepeatingSection(
    key="edit_oq",
    fields=[
        FieldSpec(
            "question",
            "Question",
            kind=TEXTAREA,
            required=True,
            max_chars=MAX_TEXT_LENGTH,
        ),
        FieldSpec("owner", "Owner", max_chars=MAX_NAME_LENGTH),
        FieldSpec("dueDate", "Due date", kind=DATE),
        FieldSpec(
            "status", "Status", kind=SELECT, required=True, options=QUESTION_STATUSES
        ),
        FieldSpec("answer", "Answer", kind=TEXTAREA, max_chars=MAX_TEXT_LENGTH),
    ],
    item_label="Question",
    title_field="question",
    empty="No open questions yet.",
)


def render_scope_tab(doc: OnePagerDocument, data_access: DataAccess) -> None:  # noqa: ARG001
    """Render the Scope & Questions tab: out of scope, questions, assumptions."""
    st.subheader("Out of scope")
    render_string_items(
        "edit_oos", doc.out_of_scope, "Out-of-scope item", "Nothing listed yet."
    )
    st.subheader("Open questions")
    render_repeating_items(OPEN_QUESTIONS, doc.open_questions)
    st.subheader("Assumptions")
    render_string_items("edit_as", doc.assumptions, "Assumption", "No assumptions yet.")
