"""Seed data of ``MockDataAccess`` (matches tests/fixtures/sample_one_pagers/).

Timestamps are naive, like Delta TIMESTAMP values read back without an offset.
"""

from datetime import datetime
from typing import Any

from onepagerapp.models import (
    AuthorizedUser,
    ChangeLogEntry,
    OnePagerStatusRow,
    ReviewComment,
    UseCase,
)
from onepagerapp.validation import CURRENT_STRUCTURE_DEFINITION


def seed_use_case_references() -> set[tuple[str, str]]:
    """(one_pager_id, use_case_id) rows of the use_case_references table.

    The sample YAML documents store Use Cases inline without IDs, so the
    references are seeded here rather than derived from the documents.
    """
    return {
        ("OP-0001", "UC-001"),
        ("OP-0001", "UC-002"),
        ("OP-0002", "UC-002"),
        ("OP-0002", "UC-003"),
        ("OP-0002", "UC-004"),
    }


def seed_status_rows() -> list[OnePagerStatusRow]:
    return [
        OnePagerStatusRow(
            one_pager_id="OP-0001",
            data_product="person",
            product_name="Person Master Data",
            business_domain="Customer",
            data_product_type="Foundational",
            one_pager_status="Approved",
            data_product_status="Ready for Development",
            version="1.0.0",
            owner_name="Alice Brown",
            owner_initials="ABR",
            owner_email="alice.brown@company.com",
            owner_team="Data Platform",
            created_by="ABR",
            created_at=datetime(2026, 8, 1, 9, 0),
            last_updated_at=datetime(2026, 9, 20, 14, 30),
            last_updated_by="ABR",
            structure_definition=CURRENT_STRUCTURE_DEFINITION,
            reviewed_at=datetime(2026, 9, 20, 14, 30),
            reviewed_by="CJ",
        ),
        OnePagerStatusRow(
            one_pager_id="OP-0002",
            data_product="order",
            product_name="Order Master Data",
            business_domain="Sales",
            data_product_type="Foundational",
            one_pager_status="In Review",
            data_product_status="In Definition",
            version="0.3.0",
            owner_name="Bob Smith",
            owner_initials="BSM",
            owner_email="bob.smith@company.com",
            owner_team="Sales Analytics",
            created_by="BSM",
            created_at=datetime(2026, 9, 1, 9, 0),
            last_updated_at=datetime(2026, 9, 19, 10, 15),
            last_updated_by="BSM",
            structure_definition=CURRENT_STRUCTURE_DEFINITION,
        ),
    ]


def seed_authorized_users() -> dict[str, list[AuthorizedUser]]:
    return {
        "OP-0001": [
            AuthorizedUser(
                one_pager_id="OP-0001",
                user_initials="ABR",
                user_name="Alice Brown",
                user_email="alice.brown@company.com",
                user_team="Data Platform",
                role="owner",
            ),
        ],
        "OP-0002": [
            AuthorizedUser(
                one_pager_id="OP-0002",
                user_initials="BSM",
                user_name="Bob Smith",
                user_email="bob.smith@company.com",
                user_team="Sales Analytics",
                role="owner",
            ),
            AuthorizedUser(
                one_pager_id="OP-0002",
                user_initials="DPI",
                user_name="Diana Prince",
                user_email="diana.prince@company.com",
                user_team="Finance",
                role="sme",
            ),
        ],
    }


def seed_change_logs() -> dict[str, list[ChangeLogEntry]]:
    return {
        "OP-0001": [
            ChangeLogEntry(
                id=3,
                one_pager_id="OP-0001",
                version="1.0.0",
                event_type="status_transition",
                author_initials="ADMIN",
                author_name="Approval System",
                summary="Document approved and published to Git",
                created_at=datetime(2026, 9, 20, 14, 30),
                from_status="In Review",
                to_status="Approved",
                status_field="one_pager_status",
            ),
            ChangeLogEntry(
                id=2,
                one_pager_id="OP-0001",
                version="0.9.0",
                event_type="content_save",
                author_initials="ABR",
                author_name="Alice Brown",
                summary="Addressed review comments on data sources",
                created_at=datetime(2026, 9, 15, 10, 0),
            ),
            ChangeLogEntry(
                id=1,
                one_pager_id="OP-0001",
                version="0.1.0",
                event_type="creation",
                author_initials="ABR",
                author_name="Alice Brown",
                summary="Initial One Pager created",
                created_at=datetime(2026, 8, 1, 9, 0),
            ),
        ],
    }


def seed_review_comments() -> dict[str, list[ReviewComment]]:
    return {
        "OP-0001": [
            ReviewComment(
                id=1,
                one_pager_id="OP-0001",
                version="0.1.0",
                section="businessRequirements",
                reviewer_initials="CJ",
                reviewer_name="Charlie Jones",
                comment="Add a requirement for audit trail compliance.",
                resolved=True,
                created_at=datetime(2026, 9, 1, 9, 0),
                resolved_by="ABR",
                resolved_at=datetime(2026, 9, 5, 14, 0),
            ),
            ReviewComment(
                id=2,
                one_pager_id="OP-0001",
                version="0.9.0",
                section="dataSources",
                reviewer_initials="BSM",
                reviewer_name="Bob Smith",
                comment="Need to clarify the refreshFrequency for Salesforce.",
                resolved=True,
                created_at=datetime(2026, 9, 12, 11, 0),
                resolved_by="ABR",
                resolved_at=datetime(2026, 9, 15, 10, 30),
            ),
        ],
    }


def sample_use_cases() -> list[UseCase]:
    """Return sample Use Cases for local testing (mirrors seed_use_cases_dev.sql)."""
    samples = [
        (
            "UC-001",
            "Analytics Manager",
            "Understand customer lifetime value trends",
            "Aggregate spending, engagement, and product usage "
            "without duplicate records",
            "Identify high-value customer segments for targeted marketing",
            "Must Have",
            False,
            "JD",
            datetime(2026, 6, 1, 9, 0),
        ),
        (
            "UC-002",
            "Compliance Officer",
            "Fulfill GDPR data subject access requests quickly",
            "Query Person dataset with unique ID and get all attributes in one place",
            "Respond to GDPR requests within 30 days",
            "Must Have",
            False,
            "ABR",
            datetime(2026, 6, 1, 9, 30),
        ),
        (
            "UC-003",
            "Finance Director",
            "Reconcile revenue across channels and time periods",
            "Query unified order data by date range, channel, product, and customer",
            "Close accounting books on time with full audit trail",
            "High",
            False,
            "BSM",
            datetime(2026, 7, 2, 11, 0),
        ),
        (
            "UC-004",
            "Operations Manager",
            "Track fulfillment status and predict delivery dates",
            "See order status, warehouse inventory, and shipping progress in one view",
            "Proactively communicate delivery estimates to customers",
            "Medium",
            False,
            "BSM",
            datetime(2026, 7, 2, 11, 15),
        ),
        (
            "UC-005",
            "Branch Advisor",
            "See a customer summary before meetings",
            "Open a printed customer summary prepared by the back office",
            "Prepare advice for scheduled customer meetings",
            "Low",
            True,
            "ABR",
            datetime(2026, 5, 20, 8, 45),
        ),
    ]
    return [
        UseCase(
            use_case_id=uc_id,
            persona=persona,
            goal=goal,
            scenario=scenario,
            decision_enabled=decision,
            priority=priority,
            deprecated=deprecated,
            created_by=initials,
            created_at=created_at,
            last_updated_by=initials,
            last_updated_at=created_at,
        )
        for (
            uc_id,
            persona,
            goal,
            scenario,
            decision,
            priority,
            deprecated,
            initials,
            created_at,
        ) in samples
    ]


def seed_op_statuses() -> list[dict[str, Any]]:
    """``ref_op_status`` rows (the Liquibase seed)."""
    return [
        {
            "status": "Draft",
            "display_label": "Draft",
            "sort_order": 1,
            "badge_color": "#808080",
            "is_terminal": False,
        },
        {
            "status": "Ready for Review",
            "display_label": "Ready for Review",
            "sort_order": 2,
            "badge_color": "#F9BD00",
            "is_terminal": False,
        },
        {
            "status": "In Review",
            "display_label": "In Review",
            "sort_order": 3,
            "badge_color": "#FFA500",
            "is_terminal": False,
        },
        {
            "status": "Approved",
            "display_label": "Approved",
            "sort_order": 4,
            "badge_color": "#65B676",
            "is_terminal": False,
        },
        {
            "status": "Draft Update",
            "display_label": "Draft Update",
            "sort_order": 5,
            "badge_color": "#7E57C2",
            "is_terminal": False,
        },
        {
            "status": "Cancelled",
            "display_label": "Cancelled",
            "sort_order": 6,
            "badge_color": "#F34421",
            "is_terminal": True,
        },
    ]


def seed_dp_statuses() -> list[dict[str, Any]]:
    """``ref_dp_status`` rows (the Liquibase seed)."""
    return [
        {
            "status": "In Definition",
            "display_label": "In Definition",
            "sort_order": 1,
            "badge_color": "#808080",
            "is_terminal": False,
        },
        {
            "status": "Ready for Development",
            "display_label": "Ready for Development",
            "sort_order": 2,
            "badge_color": "#65B676",
            "is_terminal": False,
        },
        {
            "status": "In Development",
            "display_label": "In Development",
            "sort_order": 3,
            "badge_color": "#3599B8",
            "is_terminal": False,
        },
        {
            "status": "Active",
            "display_label": "Active",
            "sort_order": 4,
            "badge_color": "#00975f",
            "is_terminal": False,
        },
        {
            "status": "In Enhancement",
            "display_label": "In Enhancement",
            "sort_order": 5,
            "badge_color": "#F9BD00",
            "is_terminal": False,
        },
        {
            "status": "Deprecated",
            "display_label": "Deprecated",
            "sort_order": 6,
            "badge_color": "#7E57C2",
            "is_terminal": True,
        },
        {
            "status": "Cancelled",
            "display_label": "Cancelled",
            "sort_order": 7,
            "badge_color": "#F34421",
            "is_terminal": True,
        },
    ]


def seed_reference_data() -> dict[str, list[dict[str, Any]]]:
    """Rows of the Admin-managed reference tables (Data_Model.md §7)."""

    def rows(key: str, values: list[str]) -> list[dict[str, Any]]:
        return [
            {key: value, "sort_order": order, "active": True}
            for order, value in enumerate(values, start=1)
        ]

    return {
        "ref_business_domains": rows(
            "domain",
            [
                "Finance",
                "Operations",
                "HR",
                "Technology",
                "Marketing",
                "Sales",
                "Customer",
            ],
        ),
        "ref_data_product_types": rows(
            "type", ["Foundational", "Integrated", "Augmented"]
        ),
        "ref_source_systems": rows(
            "system_name", ["SAP ERP", "Salesforce CRM", "Workday", "Core Banking"]
        ),
    }
