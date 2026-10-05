"""AppTest smoke tests for the Preview page states (UI_Design.md §4.4)."""

from datetime import timedelta
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from onepagerapp.data_access.connection import (
    ReadAccessDeniedError,
    SessionExpiredError,
)
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.models import CurrentUser
from onepagerapp.workflow import reject_one_pager
from tests.helpers import (
    ALICE,
    APPROVED_ID,
    APPROVER,
    APPROVER_ROLES,
    BOB,
    DIANA,
    IN_REVIEW_ID,
    MAJA,
    failing,
    live_lock,
    page_app,
    update_status_row,
)

LOAD_ERROR = "Couldn't load this One Pager. Please retry."


def _preview(
    data_access: MockDataAccess, one_pager_id: str | None = APPROVED_ID
) -> AppTest:
    """Return the Preview page for Alice (no group roles) on ``one_pager_id``."""
    state = {"preview_one_pager_id": one_pager_id} if one_pager_id else {}
    return page_app("preview.py", data_access, ALICE, frozenset(), **state)


def _review(data_access: MockDataAccess, user: CurrentUser = APPROVER) -> AppTest:
    """Return the Preview page of OP-0002 in review mode for an Approver."""
    return page_app(
        "preview.py",
        data_access,
        user,
        APPROVER_ROLES,
        preview_one_pager_id=IN_REVIEW_ID,
        preview_review_mode=IN_REVIEW_ID,
    )


def _expander(at: AppTest, label: str) -> Any:  # noqa: ANN401 - Expander
    """Return the section expander with ``label``."""
    return next(e for e in at.expander if e.label == label)


def _reject_op_0002(data_access: MockDataAccess) -> None:
    """Reject OP-0002 with one comment ("Needs work")."""
    reject_one_pager(
        data_access, IN_REVIEW_ID, APPROVER, "Needs work", roles=APPROVER_ROLES
    )


# ============================================================================
# Loading
# ============================================================================


@pytest.mark.unit
def test__no_id__open_preview__asks_to_choose_without_default(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Without an ID no One Pager is shown by default."""
    # When
    at = _preview(mock_data_access, one_pager_id=None).run()

    # Then
    assert not at.exception
    assert "No One Pager selected" in at.info[0].value
    assert "preview_one_pager_id" not in at.session_state
    assert all(t.value != APPROVED_ID for t in at.title)


@pytest.mark.unit
def test__no_id__click_go_to_registry__registry_opened(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The empty state links to the Registry."""
    # Given
    at = _preview(mock_data_access, one_pager_id=None).run()

    # When
    at.button(key="preview_go_to_registry").click().run()

    # Then
    assert switched == ["views/registry.py"]


@pytest.mark.unit
def test__id__open_preview__one_pager_rendered(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A One Pager renders without errors."""
    # When
    at = _preview(mock_data_access).run()

    # Then
    assert not at.exception
    assert not at.error
    assert at.title


@pytest.mark.unit
def test__preview_opened_twice__status_colors__read_once(
    mock_data_access: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The badge colours are cached, not read from the tables on every run."""
    # Given
    reads: list[str] = []
    for name in ("get_ref_op_status", "get_ref_dp_status"):
        read = getattr(mock_data_access, name)
        monkeypatch.setattr(
            mock_data_access,
            name,
            lambda read=read, name=name: reads.append(name) or read(),
        )

    # When
    first = _preview(mock_data_access).run()
    second = _preview(mock_data_access).run()

    # Then
    assert not first.exception
    assert not second.exception
    assert sorted(reads) == ["get_ref_dp_status", "get_ref_op_status"]


@pytest.mark.unit
def test__load_fails__open_preview_and_retry__friendly_error_each_time(
    mock_data_access: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A load error hides the internals and Retry tries again."""
    # Given
    monkeypatch.setattr(
        mock_data_access,
        "get_one_pager",
        failing("warehouse 4efe1f3d3f86e320 unreachable: secret internals"),
    )
    at = _preview(mock_data_access).run()
    assert at.error[0].value == LOAD_ERROR
    page_text = " ".join(e.value for e in at.error)
    assert "secret internals" not in page_text
    assert "RuntimeError" not in page_text
    retry = at.button(key="preview_retry_load")
    assert retry.label == "Retry"

    # When
    retry.click().run()

    # Then
    assert not at.exception
    assert at.error[0].value == LOAD_ERROR


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            ReadAccessDeniedError(
                "Your role does not have access to table cat.sch.one_pager_status."
            ),
            "Your role does not have access to table cat.sch.one_pager_status.",
        ),
        (
            SessionExpiredError(),
            "Your session has expired. Please reload the page.",
        ),
    ],
    ids=["access-denied", "session-expired"],
)
def test__user_facing_read_error__open_preview__message_shown(
    mock_data_access: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    """Unity Catalog denials and expired sessions are explained as they are."""

    # Given
    def raise_error(_one_pager_id: str) -> None:
        raise error

    monkeypatch.setattr(mock_data_access, "get_one_pager", raise_error)

    # When
    at = _preview(mock_data_access).run()

    # Then
    assert not at.exception
    assert at.error[0].value == message


# ============================================================================
# Content
# ============================================================================


@pytest.mark.unit
def test__complete_one_pager__open_preview__every_section_shown(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Each content section has its expander."""
    # When
    at = _preview(mock_data_access).run()

    # Then
    assert not at.exception
    labels = [e.label for e in at.expander]
    for label in (
        "Description",
        "Business Problem Statement",
        "Use Cases",
        "Business Requirements",
        "Data Sources",
        "Data Product Preview",
        "Classification",
        "Governance",
        "Scope & Questions",
    ):
        assert label in labels


@pytest.mark.unit
def test__linked_use_cases__open_preview__resolved_with_ids(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Use Case IDs are resolved to their persona."""
    # When
    at = _preview(mock_data_access).run()

    # Then
    table = _expander(at, "Use Cases").dataframe[0].value
    assert list(table["ID"]) == ["UC-001", "UC-002"]
    assert list(table["Persona"]) == ["Analytics Manager", "Compliance Officer"]


@pytest.mark.unit
def test__unknown_use_case__open_preview__id_kept_persona_not_available(
    mock_data_access: MockDataAccess,
    switched: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Use Case that cannot be read still shows its ID."""
    # Given
    monkeypatch.setattr(mock_data_access, "get_use_case", lambda _id: None)

    # When
    at = _preview(mock_data_access).run()

    # Then
    table = _expander(at, "Use Cases").dataframe[0].value
    assert list(table["ID"]) == ["UC-001", "UC-002"]
    assert set(table["Persona"]) == {"(not available)"}


@pytest.mark.unit
def test__v2_document__open_preview__requirement_ids_and_new_sections(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Requirement IDs, governance, scope and retention tables are shown."""
    # When
    at = _preview(mock_data_access).run()

    # Then
    requirements = _expander(at, "Business Requirements").dataframe[0].value
    assert list(requirements["ID"]) == ["BR-001", "BR-002"]
    governance = _expander(at, "Governance")
    assert len(governance.dataframe) == 3
    assert list(governance.dataframe[1].value["Dimension"]) == [
        "Uniqueness",
        "Validity",
    ]
    scope = _expander(at, "Scope & Questions")
    markdown = " ".join(m.value for m in scope.markdown)
    assert "Corporate customers" in markdown
    assert "SAP ERP remains the system of record" in markdown
    assert list(scope.dataframe[0].value["Status"]) == ["Answered"]
    retention = _expander(at, "Classification").dataframe[0].value
    assert list(retention["Legal Basis"]) == ["Danish Bookkeeping Act"]


@pytest.mark.unit
def test__v1_document__open_preview__legacy_fields_shown(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A v1 version is rendered from its legacy fields."""
    # Given
    update_status_row(mock_data_access, APPROVED_ID, version="0.2.0")

    # When
    at = _preview(mock_data_access).run()

    # Then
    assert not at.exception
    use_cases = _expander(at, "Use Cases").dataframe[0].value
    assert list(use_cases["Persona"]) == ["Analytics Manager", "Compliance Officer"]
    requirements = _expander(at, "Business Requirements").dataframe[0].value
    assert requirements["Requirement"][0].startswith("Person records must be updated")
    sources = _expander(at, "Data Sources").dataframe[0].value
    assert list(sources["Source System"]) == ["Enterprise System", "SaaS Application"]
    elements = _expander(at, "Data Product Preview").dataframe[0].value
    assert "person_id" in list(elements["Element"])
    classification = _expander(at, "Classification")
    assert any("7 years" in m.value for m in classification.markdown)


# ============================================================================
# Locks
# ============================================================================


@pytest.mark.unit
def test__own_lock__click_release__lock_removed(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The holder sees their lock and can release it."""
    # Given
    mock_data_access._locks[APPROVED_ID] = live_lock(
        APPROVED_ID, ALICE, timedelta(minutes=25)
    )
    at = _preview(mock_data_access).run()
    assert any("Locked by you" in i.value for i in at.info)

    # When
    at.button(key="preview_release_lock").click().run()

    # Then
    assert not at.exception
    assert mock_data_access.get_lock(APPROVED_ID) is None
    assert "Your lock was released." in [s.value for s in at.success]
    assert not any("Locked by you" in i.value for i in at.info)


@pytest.mark.unit
def test__lock_of_other_user__open_preview__warning_without_release(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Another user's lock is shown read-only."""
    # Given
    mock_data_access._locks[APPROVED_ID] = live_lock(
        APPROVED_ID, MAJA, timedelta(minutes=25)
    )

    # When
    at = _preview(mock_data_access).run()

    # Then
    assert not at.exception
    assert any("Locked by MJO** (MJO)" in w.value for w in at.warning)
    assert not [b for b in at.button if b.key == "preview_release_lock"]


@pytest.mark.unit
def test__expired_lock__open_preview__not_shown(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """An expired lock is ignored."""
    # Given
    mock_data_access._locks[APPROVED_ID] = live_lock(
        APPROVED_ID, MAJA, -timedelta(minutes=1)
    )

    # When
    at = _preview(mock_data_access).run()

    # Then
    assert not at.exception
    assert not [w for w in at.warning if "Locked by" in w.value]


# ============================================================================
# Review mode
# ============================================================================


@pytest.mark.unit
def test__approver_in_review_mode__open_preview__review_actions_enabled(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """An Approver gets Reject and Approve in review mode."""
    # When
    at = _review(mock_data_access).run()

    # Then
    assert not at.exception
    assert any("Review mode" in i.value for i in at.info)
    assert {"preview_reject", "preview_approve"} <= {b.key for b in at.button}
    assert not at.button(key="preview_reject").disabled


@pytest.mark.unit
def test__review_mode__click_back_to_queue__review_page_and_mode_left(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Back to queue leaves review mode."""
    # Given
    at = _review(mock_data_access).run()

    # When
    at.button(key="preview_back_to_queue").click().run()

    # Then
    assert switched == ["views/review.py"]
    assert "preview_review_mode" not in at.session_state


@pytest.mark.unit
def test__sme_in_review_mode__open_preview__no_review_actions(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The SME of the One Pager cannot review it."""
    # When
    at = _review(mock_data_access, DIANA).run()

    # Then
    assert not at.exception
    keys = {b.key for b in at.button}
    assert "preview_reject" not in keys
    assert "preview_approve" not in keys


@pytest.mark.unit
def test__review_mode__click_reject__reason_dialog_open(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Reject asks for a reason."""
    # Given
    at = _review(mock_data_access).run()

    # When
    at.button(key="preview_reject").click().run()

    # Then
    assert not at.exception
    assert at.text_area(key="preview_reject_reason")


@pytest.mark.unit
def test__review_mode__click_approve__dialog_shows_new_version_and_dp(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Approve shows what will happen before confirming."""
    # Given
    at = _review(mock_data_access).run()
    assert not at.button(key="preview_approve").disabled

    # When
    at.button(key="preview_approve").click().run()

    # Then
    assert not at.exception
    assert any("v1.0.0" in m.value for m in at.markdown)
    assert any("Ready for Development" in m.value for m in at.markdown)


@pytest.mark.unit
def test__review_mode__click_add_comment__dialog_on_whole_document(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """A new comment defaults to the whole document."""
    # Given
    at = _review(mock_data_access).run()

    # When
    at.button(key="preview_add_comment").click().run()

    # Then
    assert not at.exception
    assert at.selectbox(key="preview_comment_section").value is None
    assert at.text_area(key="preview_comment_text")


@pytest.mark.unit
def test__rejected_one_pager__owner_clicks_resolve__comment_resolved(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """The Owner resolves a review comment on the Preview."""
    # Given
    _reject_op_0002(mock_data_access)
    [comment] = mock_data_access.get_review_comments(IN_REVIEW_ID)
    at = _review(mock_data_access, BOB).run()
    resolve = at.button(key=f"preview_resolve_{comment.id}")
    assert not resolve.disabled

    # When
    resolve.click().run()

    # Then
    assert not at.exception
    assert mock_data_access.get_review_comments(IN_REVIEW_ID)[0].resolved_by == "BSM"
    assert "The comment was marked as resolved." in [s.value for s in at.success]
    assert not [b for b in at.button if b.key == f"preview_resolve_{comment.id}"]


@pytest.mark.unit
def test__rejected_one_pager__viewer_opens_preview__comments_without_resolve(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Somebody who is neither Owner nor SME sees but cannot resolve comments."""
    # Given
    _reject_op_0002(mock_data_access)

    # When
    at = _review(mock_data_access, MAJA).run()

    # Then
    assert not at.exception
    assert not [b for b in at.button if str(b.key).startswith("preview_resolve_")]
    assert any("Unresolved" in i.value for i in at.info)
