"""PDF export (Backend_Design.md §10): the PDF writer, the service and Preview."""

import logging
import re
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from onepagerapp.audit import AUDIT_LOGGER_NAME
from onepagerapp.data_access.base import NotFoundError
from onepagerapp.data_access.mock import MockDataAccess
from onepagerapp.documents import OnePagerDocumentStore
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
from tests.conftest import FIXTURES_DIR
from tests.users import make_user

APP_DIR = Path(__file__).resolve().parents[2] / "app"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _viewer() -> CurrentUser:
    return make_user("ZVI", "Zoe Viewer")


def _texts(pdf: bytes) -> list[str]:
    """Return the text runs of an uncompressed PDF, in drawing order."""
    return [
        m.decode("cp1252").replace("\\(", "(").replace("\\)", ")")
        for m in re.findall(rb"\(((?:[^()\\]|\\.)*)\) Tj", pdf)
    ]


def _check_structure(pdf: bytes) -> None:
    """Header, trailer and every xref offset point at the right objects."""
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
def test__to_winansi__keeps_danish_letters_and_replaces_the_rest() -> None:
    text = "Blåbærgrød \u2013 ÆØÅ €"
    assert to_winansi(text) == text
    assert to_winansi("Draft → Approved ≥ 2") == "Draft -> Approved >= 2"
    assert to_winansi("Łódź") == "?ódz"  # ó is WinAnsi; ź falls back to z
    assert to_winansi("数据") == "??"
    assert to_winansi("a\x00b\nc") == "ab\nc"


@pytest.mark.unit
def test__wrap_text__fits_the_width_and_keeps_line_breaks() -> None:
    text = "word " * 60 + "\n\nSecond paragraph"
    lines = wrap_text(text, 200)

    assert all(text_width(line) <= 200 for line in lines)
    assert lines[-3:] == [lines[-3], "", "Second paragraph"]
    assert " ".join(lines[:-2]).split() == ["word"] * 60


@pytest.mark.unit
def test__wrap_text__breaks_a_word_longer_than_the_line() -> None:
    lines = wrap_text("x" * 300, 100, BOLD)

    assert len(lines) > 1
    assert "".join(lines) == "x" * 300
    assert all(text_width(line, BOLD) <= 100 for line in lines)


@pytest.mark.unit
def test__pdf_document__is_a_valid_multi_page_pdf() -> None:
    pdf = PdfDocument("Title (draft)", footer="OP-0001", compress=False)
    pdf.title_band("Title (draft)", "subtitle")
    for i in range(120):
        pdf.paragraph(f"Paragraph {i} with \\ and (brackets)")
    content = pdf.build()

    _check_structure(content)
    assert pdf.page_count > 1
    assert f"/Count {pdf.page_count}".encode() in content
    texts = _texts(content)
    assert "Paragraph 0 with \\\\ and (brackets)" in texts
    assert f"Page {pdf.page_count} of {pdf.page_count}" in texts
    assert b"/Title (Title \\(draft\\))" in content


@pytest.mark.unit
def test__pdf_document__compresses_page_contents() -> None:
    pdf = PdfDocument("Compressed")
    pdf.paragraph("Hello PDF")
    content = pdf.build()

    _check_structure(content)
    assert b"/FlateDecode" in content
    assert b"Hello PDF" not in content
    stream = content.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0]
    assert b"(Hello PDF) Tj" in zlib.decompress(stream)


@pytest.mark.unit
def test__pdf_document__label_value_wraps_long_values() -> None:
    pdf = PdfDocument("Wrap", compress=False)
    pdf.label_value("Description", "long text " * 40)
    texts = _texts(pdf.build())

    assert texts[0] == "Description: "
    assert len([t for t in texts if t.startswith("long text")]) > 1
    assert all(text_width(t) <= CONTENT_WIDTH for t in texts)


# ============================================================================
# Export service
# ============================================================================


@pytest.fixture
def data_access(tmp_path: Path) -> MockDataAccess:
    return MockDataAccess(OnePagerDocumentStore(FIXTURES_DIR, write_path=tmp_path))


@pytest.mark.unit
def test__export__renders_every_section(data_access: MockDataAccess) -> None:
    export = export_one_pager_pdf(
        data_access, "OP-0001", _viewer(), now=NOW, compress=False
    )

    assert export.filename == "OP-0001_v1.0.0.pdf"
    _check_structure(export.content)
    texts = _texts(export.content)
    for heading in (
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
    ):
        assert heading in texts, heading
    assert "Approved" in texts
    assert "BR-001" in texts
    assert "Exported 2026-09-30 12:00 UTC by Zoe Viewer." in texts


@pytest.mark.unit
def test__export__resolves_use_case_references(
    data_access: MockDataAccess,
) -> None:
    preview = data_access.get_one_pager("OP-0001")
    preview.document.use_cases = [{"useCaseId": "UC-001"}, {"useCaseId": "UC-999"}]
    rows = resolve_use_cases(data_access, preview.document)

    use_case = data_access.get_use_case("UC-001")
    assert rows[0]["persona"] == use_case.persona
    assert rows[0]["goal"] == use_case.goal
    assert rows[1] == {"useCaseId": "UC-999", "persona": "(not available)"}


@pytest.mark.unit
def test__export__prints_status_colors(data_access: MockDataAccess) -> None:
    export = export_one_pager_pdf(
        data_access,
        "OP-0001",
        _viewer(),
        status_colors={"Approved": "#65B676"},
        compress=False,
    )

    assert b"0.396 0.714 0.463 rg" in export.content  # #65B676


@pytest.mark.unit
def test__export__is_audited(
    data_access: MockDataAccess, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER_NAME):
        export_one_pager_pdf(data_access, "OP-0002", _viewer())

    assert (
        "action=export_pdf outcome=success one_pager_id=OP-0002 user=ZVI "
        "version=0.3.0" in caplog.messages
    )


@pytest.mark.unit
def test__export__not_found(data_access: MockDataAccess) -> None:
    with pytest.raises(NotFoundError):
        export_one_pager_pdf(data_access, "OP-9999", _viewer())


@pytest.mark.unit
def test__export__needs_an_authenticated_user(data_access: MockDataAccess) -> None:
    with pytest.raises(PermissionDeniedError):
        export_one_pager_pdf(data_access, "OP-0001", None)
    anonymous = CurrentUser(username="", initials="", display_name="")
    with pytest.raises(PermissionDeniedError):
        export_one_pager_pdf(data_access, "OP-0001", anonymous)


@pytest.mark.unit
def test__cell_text__formats_values() -> None:
    row = {"a": "", "b": "x", "flag": False, "items": ["UC-001", "UC-002"]}

    assert cell_text(row, "a|b") == "x"
    assert cell_text(row, "flag") == "No"
    assert cell_text(row, "items") == "UC-001, UC-002"
    assert cell_text(row, "missing") == ""
    assert export_filename("OP-0007", "2.1.0") == "OP-0007_v2.1.0.pdf"


# ============================================================================
# Preview
# ============================================================================


@pytest.mark.unit
@pytest.mark.parametrize("status", ["Draft", "In Review", "Approved", "Cancelled"])
def test__export_pdf_action__enabled_for_everyone(status: str) -> None:
    state = get_action_states("XYZ", "ABR", status, False, None)["export_pdf"]

    assert state.visible
    assert state.enabled


@pytest.fixture
def switched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    targets: list[str] = []
    monkeypatch.setattr(st, "switch_page", targets.append)
    monkeypatch.syspath_prepend(str(APP_DIR))
    return targets


@pytest.mark.unit
def test__preview__export_pdf_offers_the_download(
    data_access: MockDataAccess, switched: list[str]
) -> None:
    user = _viewer()
    at = AppTest.from_file(str(APP_DIR / "views" / "preview.py"), default_timeout=30)
    state = {
        "services_initialized": True,
        "data_access": data_access,
        "document_store": data_access._document_store,
        "current_user": user.username,
        "current_user_info": user,
        "preview_one_pager_id": "OP-0001",
    }
    for key, value in state.items():
        at.session_state[key] = value
    at.run()

    assert not at.button(key="preview_export_pdf").disabled
    at.button(key="preview_export_pdf").click().run()

    assert not at.exception
    assert not at.error
    downloads = at.get("download_button")
    assert len(downloads) == 1
    assert "OP-0001_v1.0.0.pdf" in downloads[0].proto.label
