"""Preview page — read-only view of a single One Pager.

Displays the complete One Pager document with header, status timeline, content sections,
change log, review comments, and lock indicator. All state-changing actions are disabled in v1.

The view:
1. Resolves one_pager_id from query params (preferred) or a selector (v1 fallback)
2. Fetches PreviewData from DataAccess layer
3. Renders regions in order: header, timeline, content, change log, comments, lock
4. Handles all page states: Loading, Populated, Not found, Error
5. Does not cache dynamic data (document, lock, comments) — fresh on every re-run

Per Backend_Design.md §2, read permission is universal (authenticated users only).
Per UI_Design.md §4.4, v1 renders action buttons as disabled with "coming soon" tooltips.
"""

import logging
from datetime import datetime

import streamlit as st

from onepagerapp.data_access.base import DataAccess
from onepagerapp.models import PreviewData
from onepagerapp.permissions import can_view_one_pager, get_action_states, get_status_timeline_stages
from adapters.theme import get_op_status_colors, get_dp_status_colors, DEFAULT_BADGE_COLOR

logger = logging.getLogger(__name__)


def extract_initials(user_string: str) -> str:
    """Extract initials from a user string (email or name).
    
    Examples:
        "alice.brown@company.com" → "AB"
        "Alice Brown" → "AB"
        "local-dev-user@mock" → "LD"
    
    Args:
        user_string: User identifier string.
        
    Returns:
        Uppercase initials (2-3 chars).
    """
    if not user_string:
        return "?"
    
    # If it's an email, extract the local part
    if "@" in user_string:
        email_part = user_string.split("@")[0]
    else:
        email_part = user_string
    
    # Try to extract from dot-separated parts (e.g., "alice.brown" → "AB")
    if "." in email_part:
        parts = email_part.split(".")
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
    
    # Try to extract from hyphen-separated parts (e.g., "local-dev-user" → "LD")
    if "-" in email_part:
        parts = email_part.split("-")
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
    
    # Try to extract from space-separated parts (e.g., "Alice Brown" → "AB")
    parts = user_string.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    
    # Fallback: first 3 chars
    return email_part[:3].upper()

# ============================================================================
# Helpers: Render Components
# ============================================================================


def render_status_badge(status: str, color: str) -> None:
    """Render a colored status badge inline.
    
    Args:
        status: Status text to display.
        color: Hex color code for the dot.
    """
    html = f"""
    <span style="display: inline-flex; align-items: center; gap: 6px;">
        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background-color: {color}; flex-shrink: 0;"></span>
        <span style="font-weight: 500; font-size: 0.95em;">{status}</span>
    </span>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_status_timeline(current_status: str) -> None:
    """Render the One Pager status timeline (Draft → Ready → In Review → Approved).
    
    Shows the linear progression with current stage highlighted.
    
    Args:
        current_status: Current one_pager_status value.
    """
    stages = get_status_timeline_stages()
    # Find current stage index
    current_idx = next(
        (i for i, s in enumerate(stages) if s["status"] == current_status),
        None,
    )
    
    st.subheader("Status Timeline")
    
    # Render as: ● Draft  →  ● Ready for Review  →  ● In Review  →  ● Approved
    # Current stage is bold/highlighted
    timeline_html = '<div style="display: flex; align-items: center; gap: 10px; font-size: 0.9em; margin: 1rem 0;">'
    
    for i, stage in enumerate(stages):
        is_current = i == current_idx
        is_past = current_idx is not None and i < current_idx
        
        # Dot color: blue if current, green if past, gray if future
        if is_current:
            dot_color = "#3599B8"
            font_weight = "bold"
        elif is_past:
            dot_color = "#65B676"
            font_weight = "normal"
        else:
            dot_color = "#CCCCCC"
            font_weight = "normal"
        
        timeline_html += f'<span style="font-weight: {font_weight}; color: {dot_color};">● {stage["label"]}</span>'
        
        # Add arrow between stages (not after last one)
        if i < len(stages) - 1:
            arrow_color = "#65B676" if is_past else "#CCCCCC"
            timeline_html += f'<span style="color: {arrow_color}; margin: 0 5px;">→</span>'
    
    timeline_html += '</div>'
    st.markdown(timeline_html, unsafe_allow_html=True)


def render_header(preview_data: PreviewData, op_colors: dict, dp_colors: dict) -> None:
    """Render the One Pager header with metadata and status badges.
    
    Args:
        preview_data: Complete preview data.
        op_colors: Map of One Pager status → color.
        dp_colors: Map of Data Product status → color.
    """
    header = preview_data.header
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        st.title(header.product_name)
        st.markdown(f"**ID:** {header.one_pager_id} | **Version:** {header.version}")
        st.markdown(
            f"**Owner:** {header.owner_name} ({header.owner_email}) | "
            f"**Last Updated:** {header.last_updated_at.strftime('%Y-%m-%d %H:%M')} by {header.last_updated_by}"
        )
    
    with col2:
        st.markdown("**One Pager Status**")
        color = op_colors.get(header.one_pager_status, DEFAULT_BADGE_COLOR)
        render_status_badge(header.one_pager_status, color)
    
    with col3:
        st.markdown("**Data Product Status**")
        color = dp_colors.get(header.data_product_status, DEFAULT_BADGE_COLOR)
        render_status_badge(header.data_product_status, color)


def render_content_sections(preview_data: PreviewData) -> None:
    """Render the One Pager document content as collapsible sections.
    
    Sections are displayed in order: Description, Business Problem, Use Cases,
    Business Requirements, Data Sources, Data Product Preview, Classification.
    
    Args:
        preview_data: Complete preview data.
    """
    doc = preview_data.document
    
    st.subheader("Content")
    
    # Description
    with st.expander("📝 Description", expanded=True):
        st.write(doc.description or "*No description provided.*")
    
    # Business Problem Statement
    with st.expander("🎯 Business Problem Statement"):
        if doc.business_problem_statement:
            st.write(doc.business_problem_statement)
        else:
            st.write("*No business problem statement provided.*")
    
    # Use Cases
    with st.expander("💼 Use Cases"):
        if doc.use_cases:
            for i, uc in enumerate(doc.use_cases, 1):
                persona = uc.get("persona", "")
                goal = uc.get("goal", "")
                header_txt = " — ".join(t for t in [persona, goal] if t) or f"Use Case {i}"
                st.markdown(f"**{i}. {header_txt}**")
                if uc.get("scenario"):
                    st.write(f"_Scenario:_ {uc['scenario']}")
                if uc.get("decisionEnabled"):
                    st.write(f"_Decision enabled:_ {uc['decisionEnabled']}")
                if uc.get("priority"):
                    st.caption(f"Priority: {uc['priority']}")
        else:
            st.write("*No use cases provided.*")
    
    # Business Requirements
    with st.expander("✅ Business Requirements"):
        if doc.business_requirements:
            for i, br in enumerate(doc.business_requirements, 1):
                text = br.get("requirement", "")
                priority = br.get("priority")
                line = f"{i}. {text}"
                if priority:
                    line += f" _(Priority: {priority})_"
                st.write(line)
        else:
            st.write("*No business requirements provided.*")
    
    # Data Sources
    with st.expander("📊 Data Sources"):
        if doc.data_sources:
            for i, ds in enumerate(doc.data_sources, 1):
                name = ds.get("sourceName", "")
                source_type = ds.get("sourceType", "")
                label = f"{name} ({source_type})" if source_type else name
                st.markdown(f"**{i}. {label}**")
                if ds.get("description"):
                    st.write(f"_{ds['description']}_")
        else:
            st.write("*No data sources provided.*")
    
    # Data Element Preview
    with st.expander("🔍 Data Product Preview"):
        if doc.data_element_preview:
            for elem in doc.data_element_preview:
                name = elem.get("elementName", "?")
                elem_type = elem.get("dataType", "?")
                cde = "CDE" if elem.get("isCriticalDataElement") else ""
                desc = elem.get("description", "")
                st.write(f"**{name}** ({elem_type}) {f'[{cde}]' if cde else ''}")
                if desc:
                    st.write(f"_{desc}_")
        else:
            st.write("*No data product preview provided.*")
    
    # Data Classification & Governance
    with st.expander("🔐 Classification & Governance"):
        if doc.data_classification:
            st.write(f"**Classification Level:** {doc.data_classification.get('classificationLevel', 'N/A')}")
            st.write(f"**Contains PII:** {doc.data_classification.get('containsPII', False)}")
            st.write(f"**Contains Sensitive Data:** {doc.data_classification.get('containsSensitiveData', False)}")
            if doc.data_classification.get("retentionRequirements"):
                st.write(f"**Retention:** {doc.data_classification['retentionRequirements']}")
        else:
            st.write("*No classification provided.*")


def render_change_log(preview_data: PreviewData) -> None:
    """Render the change log (newest-first).
    
    Args:
        preview_data: Complete preview data.
    """
    st.subheader("Change Log")
    
    if not preview_data.change_log:
        st.info("No changes recorded yet.")
        return
    
    for entry in preview_data.change_log:
        # Render each entry as a container
        with st.container(border=True):
            col1, col2, col3 = st.columns([1, 2, 2])
            
            with col1:
                st.markdown(f"**v{entry.version}**")
                st.caption(entry.created_at.strftime("%Y-%m-%d %H:%M"))
            
            with col2:
                st.markdown(f"**{entry.event_type.replace('_', ' ').title()}**")
                st.caption(f"By {entry.author_name} ({entry.author_initials})")
            
            with col3:
                st.write(entry.summary)
                
                # For status transitions, show the from/to
                if entry.event_type == "status_transition":
                    st.caption(
                        f"{entry.from_status} → {entry.to_status} "
                        f"({entry.status_field.replace('_', ' ').title()})"
                    )


def render_review_comments(preview_data: PreviewData) -> None:
    """Render review comments grouped by section.
    
    Shows resolved and unresolved comments separately.
    
    Args:
        preview_data: Complete preview data.
    """
    st.subheader("Review Comments")
    
    if not preview_data.review_comments:
        st.info("No review comments yet.")
        return
    
    # Group by section
    by_section = {}
    for comment in preview_data.review_comments:
        section = comment.section or "(Document-level)"
        if section not in by_section:
            by_section[section] = []
        by_section[section].append(comment)
    
    # Render by section
    for section in sorted(by_section.keys()):
        with st.expander(f"🗨️ {section}"):
            comments = by_section[section]
            
            # Separate resolved and unresolved
            unresolved = [c for c in comments if not c.resolved]
            resolved = [c for c in comments if c.resolved]
            
            # Unresolved first
            if unresolved:
                st.markdown("**Unresolved:**")
                for comment in unresolved:
                    with st.container(border=True):
                        st.markdown(f"**{comment.reviewer_name}** ({comment.reviewer_initials})")
                        st.caption(f"v{comment.version} • {comment.created_at.strftime('%Y-%m-%d %H:%M')}")
                        st.write(comment.comment)
                        st.info("⚠️ Unresolved — awaiting action")
            
            # Then resolved
            if resolved:
                st.markdown("**Resolved:**")
                for comment in resolved:
                    with st.container(border=True):
                        st.markdown(f"**{comment.reviewer_name}** ({comment.reviewer_initials})")
                        st.caption(
                            f"v{comment.version} • {comment.created_at.strftime('%Y-%m-%d %H:%M')} "
                            f"• Resolved by {comment.resolved_by}"
                        )
                        st.write(comment.comment)
                        st.success("✅ Resolved")


def render_lock_indicator(preview_data: PreviewData) -> None:
    """Render the lock status indicator.
    
    Shows either "Locked by {name}" (read-only notice) or nothing if not locked.
    v1: Release lock button is disabled.
    
    Args:
        preview_data: Complete preview data.
    """
    if not preview_data.lock:
        return
    
    lock = preview_data.lock
    acquired = lock.acquired_at.strftime("%Y-%m-%d %H:%M")
    expires = lock.expires_at.strftime("%H:%M")
    
    st.warning(
        f"🔒 **Locked by {lock.locked_by_name}** ({lock.locked_by_initials}) "
        f"since {acquired} (expires {expires}). Read-only mode.",
        icon="🔒",
    )


def render_action_bar(preview_data: PreviewData, current_user_initials: str) -> None:
    """Render the action button bar (disabled in v1).
    
    Per UI_Design.md §4.4, all state-changing actions are disabled with "coming soon" tooltips.
    
    Args:
        preview_data: Complete preview data.
        current_user_initials: Current user's initials.
    """
    header = preview_data.header
    
    actions = get_action_states(
        current_user_initials=current_user_initials,
        owner_initials=header.owner_initials,
        one_pager_status=header.one_pager_status,
        is_locked=preview_data.lock is not None,
        lock_holder_initials=preview_data.lock.locked_by_initials if preview_data.lock else None,
    )
    
    st.subheader("Actions")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.button(
            "✏️ Edit",
            disabled=not actions["edit"].enabled,
            help=actions["edit"].tooltip if actions["edit"].tooltip else None,
        )
    
    with col2:
        st.button(
            "✅ Approve",
            disabled=not actions["approve"].enabled,
            help=actions["approve"].tooltip if actions["approve"].tooltip else None,
        )
    
    with col3:
        st.button(
            "❌ Reject",
            disabled=not actions["reject"].enabled,
            help=actions["reject"].tooltip if actions["reject"].tooltip else None,
        )
    
    with col4:
        st.button(
            "📝 Add Comment",
            disabled=not actions["add_comment"].enabled,
            help=actions["add_comment"].tooltip if actions["add_comment"].tooltip else None,
        )
    
    with col5:
        st.button(
            "📄 Export PDF",
            disabled=not actions["export_pdf"].enabled,
            help=actions["export_pdf"].tooltip if actions["export_pdf"].tooltip else None,
        )


# ============================================================================
# Preview Page
# ============================================================================

# Initialize services from session state
if not st.session_state.get("services_initialized"):
    st.error("Services not initialized. Please refresh the page.")
    st.stop()

data_access: DataAccess = st.session_state.data_access
current_user = st.session_state.get("current_user", "unknown")

# Resolve one_pager_id: internal navigation (session_state) takes priority since
# st.switch_page clears query params; fall back to a query param (deep link), then default.
selected_id = st.session_state.get("preview_one_pager_id")
query_one_pager_id = st.query_params.get("one_pager_id")
if selected_id:
    one_pager_id = selected_id
elif query_one_pager_id:
    one_pager_id = query_one_pager_id[0] if isinstance(query_one_pager_id, list) else query_one_pager_id
else:
    one_pager_id = "OP-0001"  # Default to OP-0001 for auto-load

# Keep session_state and the URL in sync so refresh/bookmark resolves the same One Pager.
st.session_state["preview_one_pager_id"] = one_pager_id
st.query_params["one_pager_id"] = one_pager_id


# Fetch preview data
try:
    with st.spinner("Loading One Pager..."):
        preview_data = data_access.get_one_pager(one_pager_id)
except RuntimeError as e:
    st.error(f"❌ Error loading One Pager: {e}")
    st.stop()
except Exception as e:
    logger.exception(f"Unexpected error loading {one_pager_id}")
    st.error(f"❌ Unexpected error: {e}")
    st.stop()

# Not found state
if not preview_data:
    st.error(f"❌ One Pager **{one_pager_id}** not found.")
    st.info("Use the **Registry** tab in the sidebar to browse available One Pagers.")
    st.stop()

# Load status colors
try:
    op_colors = get_op_status_colors(data_access)
    dp_colors = get_dp_status_colors(data_access)
except RuntimeError as e:
    logger.error(f"Failed to load status colors: {e}")
    st.error(f"Failed to load color scheme: {e}")
    st.stop()

# Populated state: render all regions
render_header(preview_data, op_colors, dp_colors)

st.divider()

render_status_timeline(preview_data.header.one_pager_status)

st.divider()

render_action_bar(preview_data, extract_initials(current_user))

st.divider()

render_content_sections(preview_data)

st.divider()

render_change_log(preview_data)

st.divider()

render_review_comments(preview_data)

st.divider()

render_lock_indicator(preview_data)

