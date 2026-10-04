"""PDF export (Backend_Design.md §10): the PDF writer, the service and Preview."""

import logging
import re
import zlib
from datetime import UTC, datetime

import pytest

from onepagerapp.audit import AUDIT_LOGGER_NAME
from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.export import (
    cell_text,
    export_filename,
    export_one_pager_pdf,
    resolve_use_cases,
)
from onepagerapp.models import CurrentUser
from onepagerapp.pdf import (
    BOLD,
    CONTENT_WIDTH,
    PdfDocument,
    text_width,
    to_winansi,
    wrap_text,
)
from onepagerapp.permissions import PermissionDeniedError, get_action_states
from tests.helpers import APPROVED_ID, IN_REVIEW_ID, make_user, page_app

EXPORTED_AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
VIEWER = make_user("ZVI", "Zoe Viewer")
SECTION_HEADINGS = (
    "Person Master Data",
    "Overview",
    "Description",
    "Business Problem Statement",
    "Use Cases",
    "Business Requirements",
    "Data Sources",
    "Data Product Preview",
    "Classification",
    "Retention Requirements",
    "Governance",
    "Scope & Questions",
    "Out of Scope",
    "Open Questions",
    "Assumptions",
    "Change Log",
)


def _texts(pdf: bytes) -> list[str]:
    """Return the text runs of an uncompressed PDF, in drawing order."""
    return [
        m.decode("cp1252").replace("\\(", "(").replace("\\)", ")")
        for m in re.findall(rb"\(((?:[^()\\]|\\.)*)\) Tj", pdf)
    ]


def _assert_valid_structure(pdf: bytes) -> None:
    """Assert header, trailer and that every xref offset points at its object."""
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    startxref = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n", 1)[0])
    xref = pdf[startxref:].split(b"trailer", 1)[0].splitlines()
    assert xref[0] == b"xref"
    count = int(xref[1].split()[1])
    offsets = [int(line.split()[0]) for line in xref[3 : 2 + count]]
    for number, offset in enumerate(offsets, start=1):
        assert pdf[offset:].startswith(f"{number} 0 obj".encode())


# ============================================================================
# PDF writer
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Blåbærgrød \u2013 ÆØÅ €", "Blåbærgrød \u2013 ÆØÅ €"),
        ("Draft → Approved ≥ 2", "Draft -> Approved >= 2"),
        ("Łódź", "?ódz"),  # ó is WinAnsi; ź falls back to z
        ("数据", "??"),
        ("a\x00b\nc", "ab\nc"),
    ],
    ids=["danish", "arrows", "polish", "chinese", "control"],
)
def test__text__to_winansi__danish_kept_rest_replaced(text: str, expected: str) -> None:
    """WinAnsi characters stay; others get a fallback; controls are dropped."""
    # When
    result = to_winansi(text)

    # Then
    assert result == expected


@pytest.mark.unit
def test__long_text_with_paragraphs__wrap_text__fits_and_keeps_breaks() -> None:
    """Lines fit the width; blank lines between paragraphs are kept."""
    # Given
    text = "word " * 60 + "\n\nSecond paragraph"

    # When
    lines = wrap_text(text, 200)

    # Then
    assert all(text_width(line) <= 200 for line in lines)
    assert lines[-2:] == ["", "Second paragraph"]
    assert " ".join(lines[:-2]).split() == ["word"] * 60


@pytest.mark.unit
def test__word_longer_than_line__wrap_text__word_broken() -> None:
    """A word wider than the line is split across lines."""
    # When
    lines = wrap_text("x" * 300, 100, BOLD)

    # Then
    assert len(lines) > 1
    assert "".join(lines) == "x" * 300
    assert all(text_width(line, BOLD) <= 100 for line in lines)


@pytest.mark.unit
def test__many_paragraphs__build__valid_multi_page_pdf() -> None:
    """Long content flows onto several pages with page numbers and a title."""
    # Given
    pdf = PdfDocument("Title (draft)", footer="OP-0001", compress=False)
    pdf.title_band("Title (draft)", "subtitle")
    for i in range(120):
        pdf.paragraph(f"Paragraph {i} with \\ and (brackets)")

    # When
    content = pdf.build()

    # Then
    _assert_valid_structure(content)
    assert pdf.page_count > 1
    assert f"/Count {pdf.page_count}".encode() in content
    texts = _texts(content)
    assert "Paragraph 0 with \\\\ and (brackets)" in texts
    assert f"Page {pdf.page_count} of {pdf.page_count}" in texts
    assert b"/Title (Title \\(draft\\))" in content


@pytest.mark.unit
def test__default_settings__build__page_contents_compressed() -> None:
    """Page streams are Flate-compressed by default."""
    # Given
    pdf = PdfDocument("Compressed")
    pdf.paragraph("Hello PDF")

    # When
    content = pdf.build()

    # Then
    _assert_valid_structure(content)
    assert b"/FlateDecode" in content
    assert b"Hello PDF" not in content
    stream = content.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0]
    assert b"(Hello PDF) Tj" in zlib.decompress(stream)


@pytest.mark.unit
def test__long_value__label_value__wrapped_within_content_width() -> None:
    """The label is drawn once and the value wraps inside the content width."""
    # Given
    pdf = PdfDocument("Wrap", compress=False)

    # When
    pdf.label_value("Description", "long text " * 40)

    # Then
    texts = _texts(pdf.build())
    assert texts[0] == "Description: "
    assert len([t for t in texts if t.startswith("long text")]) > 1
    assert all(text_width(t) <= CONTENT_WIDTH for t in texts)


# ============================================================================
# Export service
# ============================================================================


@pytest.mark.unit
def test__approved_one_pager__export_pdf__every_section_and_footer(
    mock_data_access: MockDataAccess,
) -> None:
    """The PDF is named after the version and holds every section."""
    # When
    export = export_one_pager_pdf(
        mock_data_access, APPROVED_ID, VIEWER, now=EXPORTED_AT, compress=False
    )

    # Then
    assert export.filename == "OP-0001_v1.0.0.pdf"
    _assert_valid_structure(export.content)
    texts = _texts(export.content)
    for heading in SECTION_HEADINGS:
        assert heading in texts, heading
    assert "Approved" in texts
    assert "BR-001" in texts
    assert "Exported 2026-09-30 12:00 UTC by Zoe Viewer." in texts


@pytest.mark.unit
def test__known_and_unknown_use_case__resolve_use_cases__details_or_placeholder(
    mock_data_access: MockDataAccess,
) -> None:
    """Use Case IDs are resolved; unknown ones keep their ID."""
    # Given
    document = mock_data_access.get_one_pager(APPROVED_ID).document
    document.use_cases = [{"useCaseId": "UC-001"}, {"useCaseId": "UC-999"}]

    # When
    rows = resolve_use_cases(mock_data_access, document)

    # Then
    use_case = mock_data_access.get_use_case("UC-001")
    assert (rows[0]["persona"], rows[0]["goal"]) == (use_case.persona, use_case.goal)
    assert rows[1] == {"useCaseId": "UC-999", "persona": "(not available)"}


@pytest.mark.unit
def test__status_colors__export_pdf__badge_in_that_color(
    mock_data_access: MockDataAccess,
) -> None:
    """The status badge is drawn in the configured colour (#65B676)."""
    # When
    export = export_one_pager_pdf(
        mock_data_access,
        APPROVED_ID,
        VIEWER,
        status_colors={"Approved": "#65B676"},
        compress=False,
    )

    # Then
    assert b"0.396 0.714 0.463 rg" in export.content


@pytest.mark.unit
def test__viewer__export_pdf__audited(
    mock_data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    """Every export is a security event."""
    # When
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        export_one_pager_pdf(mock_data_access, IN_REVIEW_ID, VIEWER)

    # Then
    assert (
        "action=export_pdf outcome=success one_pager_id=OP-0002 user=ZVI "
        "version=0.3.0" in caplog.messages
    )


@pytest.mark.unit
def test__unknown_id__export_pdf__raises_not_found(
    mock_data_access: MockDataAccess,
) -> None:
    """An unknown One Pager cannot be exported."""
    # When / Then
    with pytest.raises(NotFoundError):
        export_one_pager_pdf(mock_data_access, "OP-9999", VIEWER)


@pytest.mark.unit
@pytest.mark.parametrize(
    "user",
    [None, CurrentUser(username="", initials="", display_name="")],
    ids=["none", "anonymous"],
)
def test__no_authenticated_user__export_pdf__raises_permission_denied(
    mock_data_access: MockDataAccess, user: CurrentUser | None
) -> None:
    """Exporting needs an authenticated user."""
    # When / Then
    with pytest.raises(PermissionDeniedError):
        export_one_pager_pdf(mock_data_access, APPROVED_ID, user)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("columns", "expected"),
    [("a|b", "x"), ("flag", "No"), ("items", "UC-001, UC-002"), ("missing", "")],
)
def test__row_values__cell_text__formatted(columns: str, expected: str) -> None:
    """Alternatives, booleans, lists and missing values are formatted."""
    # Given
    row = {"a": "", "b": "x", "flag": False, "items": ["UC-001", "UC-002"]}

    # When
    text = cell_text(row, columns)

    # Then
    assert text == expected


@pytest.mark.unit
def test__id_and_version__export_filename__id_v_version_pdf() -> None:
    """The file name carries the ID and version."""
    # When
    name = export_filename("OP-0007", "2.1.0")

    # Then
    assert name == "OP-0007_v2.1.0.pdf"


# ============================================================================
# Preview
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Draft", "In Review", "Approved", "Cancelled"])
def test__any_status__action_states_for_viewer__export_enabled(status: str) -> None:
    """Everybody can export, whatever the status."""
    # When
    state = get_action_states("XYZ", "ABR", status, False, None)["export_pdf"]

    # Then
    assert state.visible
    assert state.enabled


@pytest.mark.unit
def test__viewer_on_preview__click_export_pdf__download_offered(
    mock_data_access: MockDataAccess, switched: list[str]
) -> None:
    """Export PDF builds the file and offers it as a download."""
    # Given
    at = page_app(
        "preview.py",
        mock_data_access,
        VIEWER,
        frozenset(),
        preview_one_pager_id=APPROVED_ID,
    ).run()
    assert not at.button(key="preview_export_pdf").disabled

    # When
    at.button(key="preview_export_pdf").click().run()

    # Then
    assert not at.exception
    assert not at.error
    downloads = at.get("download_button")
    assert len(downloads) == 1
    assert "OP-0001_v1.0.0.pdf" in downloads[0].proto.label
