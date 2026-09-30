"""Minimal PDF writer for the One Pager export (Backend_Design.md §10).

Writes A4 PDF 1.4 files with the standard Helvetica fonts, so it needs no
third-party library and no font files (Decision_Log.md §18). Text is encoded
as WinAnsi (cp1252), which covers Danish and the other Western European
letters; characters outside it are replaced by a close ASCII form or "?".

The layout is a simple top-to-bottom flow: headings, wrapped paragraphs,
label/value lines and bullet lists. Pages break automatically and get a
footer with the page number when the document is built.

Pure Python — no Streamlit, no storage.
"""

import unicodedata
import zlib
from dataclasses import dataclass, field
from datetime import datetime

PAGE_WIDTH = 595.28  # A4 in points
PAGE_HEIGHT = 841.89
MARGIN = 50.0
FOOTER_HEIGHT = 30.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
LINE_SPACING = 1.3

BODY_SIZE = 10.0
SMALL_SIZE = 8.5
TEXT_COLOR = "#343333"
MUTED_COLOR = "#6B6B6B"
HEADING_COLOR = "#0C1C49"
RULE_COLOR = "#D0D0D0"

REGULAR = "F1"
BOLD = "F2"
_BASE_FONTS = {REGULAR: "Helvetica", BOLD: "Helvetica-Bold"}

# Glyph widths (1/1000 em) of the printable ASCII characters 32-126, from the
# Adobe Helvetica and Helvetica-Bold font metrics.
_HELVETICA_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278,
    278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584,
    584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
    833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
    278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222,
    500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500,
    500, 334, 260, 334, 584,
)  # fmt: skip
_HELVETICA_BOLD_WIDTHS = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278,
    278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584,
    584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611,
    833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333,
    278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278,
    556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556,
    500, 389, 280, 389, 584,
)  # fmt: skip
_WIDTHS = {REGULAR: _HELVETICA_WIDTHS, BOLD: _HELVETICA_BOLD_WIDTHS}

# WinAnsi punctuation outside ASCII (same width in both fonts).
_EXTRA_WIDTHS = {
    "\u2013": 556,  # en dash
    "\u2014": 1000,  # em dash
    "\u2018": 222,
    "\u2019": 222,
    "\u201c": 333,
    "\u201d": 333,
    "\u2022": 350,  # bullet
    "\u2026": 1000,  # ellipsis
    "\u20ac": 556,  # euro
    "\u00a0": 278,  # no-break space
}
_DEFAULT_WIDTH = 556

# Common characters that WinAnsi lacks, written in a readable ASCII form.
_REPLACEMENTS = {
    "\u2192": "->",
    "\u2190": "<-",
    "\u2194": "<->",
    "\u2264": "<=",
    "\u2265": ">=",
    "\u2260": "!=",
    "\u2713": "v",
    "\u2714": "v",
    "\u2717": "x",
    "\u2212": "-",
    "\t": "    ",
}


def to_winansi(text: object) -> str:
    """Return ``text`` with only characters the standard PDF fonts can show.

    Line breaks are kept; other control characters are dropped. Accented
    letters outside WinAnsi fall back to their base letter, anything else
    becomes "?".
    """
    result: list[str] = []
    for char in str(text):
        if char == "\n":
            result.append(char)
            continue
        if char in _REPLACEMENTS:
            result.append(_REPLACEMENTS[char])
            continue
        if unicodedata.category(char) == "Cc":
            continue
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", char)[:1]
            result.append(base if base.isascii() and base.isprintable() else "?")
        else:
            result.append(char)
    return "".join(result)


def _char_width(char: str, font: str) -> int:
    code = ord(char)
    if 32 <= code <= 126:  # noqa: PLR2004 - printable ASCII range
        return _WIDTHS[font][code - 32]
    if char in _EXTRA_WIDTHS:
        return _EXTRA_WIDTHS[char]
    base = unicodedata.normalize("NFKD", char)[:1]
    if base and base.isascii() and 32 <= ord(base) <= 126:  # noqa: PLR2004
        return _WIDTHS[font][ord(base) - 32]
    return _DEFAULT_WIDTH


def text_width(text: str, font: str = REGULAR, size: float = BODY_SIZE) -> float:
    """Width of ``text`` in points (``text`` already WinAnsi-safe)."""
    return sum(_char_width(c, font) for c in text) * size / 1000


def wrap_text(
    text: str, width: float, font: str = REGULAR, size: float = BODY_SIZE
) -> list[str]:
    """Split ``text`` into lines no wider than ``width`` points.

    Breaks at spaces; a word longer than a line is broken inside the word.
    Explicit line breaks are kept (an empty line stays empty).
    """
    lines: list[str] = []
    for paragraph in to_winansi(text).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, font, size) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            rest = word
            while text_width(rest, font, size) > width:
                cut = _longest_prefix(rest, width, font, size)
                lines.append(rest[:cut])
                rest = rest[cut:]
            line = rest
        lines.append(line)
    return lines


def _longest_prefix(word: str, width: float, font: str, size: float) -> int:
    total = 0.0
    for i, char in enumerate(word):
        total += _char_width(char, font) * size / 1000
        if total > width:
            return max(i, 1)
    return len(word)


def _rgb(color: str) -> str:
    """PDF color operands ("r g b") of a "#RRGGBB" color."""
    value = color.lstrip("#")
    try:
        channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        channels = [0.0, 0.0, 0.0]
    return " ".join(f"{c:.3f}" for c in channels)


def _pdf_string(text: str) -> str:
    """Return WinAnsi-safe text as a PDF literal string."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({escaped})"


def _num(value: float) -> str:
    return f"{value:.2f}"


@dataclass
class _Page:
    operations: list[str] = field(default_factory=list)


class PdfDocument:
    """A PDF built from top to bottom; ``build()`` returns the file bytes.

    Args:
        title: Document title (PDF metadata and page footer).
        footer: Text at the bottom left of every page.
        compress: Compress the page contents (turned off in tests to read them).
        created_at: Creation date written to the metadata.

    """

    def __init__(
        self,
        title: str,
        *,
        footer: str = "",
        compress: bool = True,
        created_at: datetime | None = None,
    ) -> None:
        """Start the document with its first page."""
        self.title = title
        self.footer = footer
        self.compress = compress
        self.created_at = created_at
        self._pages: list[_Page] = []
        self._y = 0.0
        self.add_page()

    # ------------------------------------------------------------------
    # Page flow
    # ------------------------------------------------------------------

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def add_page(self) -> None:
        """Start a new page; the cursor goes to the top margin."""
        self._pages.append(_Page())
        self._y = PAGE_HEIGHT - MARGIN

    def ensure_space(self, height: float) -> None:
        """Break the page if less than ``height`` points are left on it."""
        if self._y - height < MARGIN + FOOTER_HEIGHT:
            self.add_page()

    def _emit(self, operation: str) -> None:
        self._pages[-1].operations.append(operation)

    def _text_at(  # noqa: PLR0913 - the parts of one text run
        self,
        x: float,
        y: float,
        text: str,
        font: str = REGULAR,
        size: float = BODY_SIZE,
        color: str = TEXT_COLOR,
    ) -> None:
        self._emit(
            f"BT /{font} {_num(size)} Tf {_rgb(color)} rg {_num(x)} {_num(y)} Td "
            f"{_pdf_string(text)} Tj ET"
        )

    # ------------------------------------------------------------------
    # Content blocks
    # ------------------------------------------------------------------

    def title_band(self, title: str, subtitle: str = "") -> None:
        """Dark blue band with the document title across the top of the page."""
        height = 62.0 if subtitle else 46.0
        self.ensure_space(height + 12)
        top = self._y + 18
        self._emit(
            f"{_rgb(HEADING_COLOR)} rg {_num(0)} {_num(top - height)} "
            f"{_num(PAGE_WIDTH)} {_num(height + 30)} re f"
        )
        title_lines = wrap_text(title, CONTENT_WIDTH, BOLD, 18)
        self._text_at(MARGIN, top - 30, title_lines[0], BOLD, 18, "#FFFFFF")
        if subtitle:
            self._text_at(
                MARGIN, top - 48, to_winansi(subtitle), REGULAR, BODY_SIZE, "#FFFFFF"
            )
        self._y = top - height - 18

    def heading(self, text: str, size: float = 13.0) -> None:
        """Section heading with a rule underneath; kept with the next lines."""
        self.ensure_space(size * 2 + 3 * BODY_SIZE * LINE_SPACING)
        self._y -= size * 0.6
        self._text_at(
            MARGIN, self._y - size, to_winansi(text), BOLD, size, HEADING_COLOR
        )
        self._y -= size * LINE_SPACING + 3
        self._emit(
            f"{_rgb(RULE_COLOR)} RG 0.6 w {_num(MARGIN)} {_num(self._y)} m "
            f"{_num(PAGE_WIDTH - MARGIN)} {_num(self._y)} l S"
        )
        self._y -= 6

    def subheading(self, text: str) -> None:
        """Bold label inside a section, kept with the next lines."""
        self.ensure_space(4 * BODY_SIZE * LINE_SPACING)
        self._y -= 3
        self.paragraph(text, font=BOLD, color=HEADING_COLOR)

    def paragraph(  # noqa: PLR0913 - layout options of one paragraph
        self,
        text: str,
        *,
        font: str = REGULAR,
        size: float = BODY_SIZE,
        color: str = TEXT_COLOR,
        indent: float = 0.0,
        after: float = 4.0,
    ) -> None:
        """Write wrapped text; it breaks across pages as needed."""
        leading = size * LINE_SPACING
        for line in wrap_text(text, CONTENT_WIDTH - indent, font, size):
            self.ensure_space(leading)
            self._y -= leading
            baseline = self._y + size * 0.25
            self._text_at(MARGIN + indent, baseline, line, font, size, color)
        self._y -= after

    def label_value(
        self, label: str, value: str, *, indent: float = 0.0, after: float = 1.0
    ) -> None:
        """Write a "Label: value" line; the value wraps under itself."""
        size = BODY_SIZE
        leading = size * LINE_SPACING
        label_text = to_winansi(f"{label}: ")
        label_width = min(text_width(label_text, BOLD, size), CONTENT_WIDTH / 3)
        lines = wrap_text(value, CONTENT_WIDTH - indent - label_width, REGULAR, size)
        for i, line in enumerate(lines):
            self.ensure_space(leading)
            self._y -= leading
            baseline = self._y + size * 0.25
            if i == 0:
                self._text_at(MARGIN + indent, baseline, label_text, BOLD, size)
            self._text_at(MARGIN + indent + label_width, baseline, line, REGULAR, size)
        self._y -= after

    def bullets(self, items: list[str], *, indent: float = 0.0) -> None:
        """Bulleted list; each item wraps under its own text."""
        size = BODY_SIZE
        leading = size * LINE_SPACING
        bullet_width = 12.0
        for item in items:
            width = CONTENT_WIDTH - indent - bullet_width
            lines = wrap_text(item, width, REGULAR, size)
            for i, line in enumerate(lines):
                self.ensure_space(leading)
                self._y -= leading
                baseline = self._y + size * 0.25
                if i == 0:
                    self._text_at(MARGIN + indent, baseline, "\u2022", REGULAR, size)
                self._text_at(
                    MARGIN + indent + bullet_width, baseline, line, REGULAR, size
                )
            self._y -= 1
        self._y -= 3

    def status_badges(self, badges: list[tuple[str, str, str]]) -> None:
        """One line of "label ● status" badges: (label, status, "#RRGGBB").

        The status is always written as text; the dot only adds its color
        (UI_Design.md §7: color is never the only indicator).
        """
        size = BODY_SIZE
        leading = size * LINE_SPACING + 2
        self.ensure_space(leading)
        self._y -= leading
        baseline = self._y + size * 0.25
        x = MARGIN
        for label, status, color in badges:
            label_text = to_winansi(f"{label}: ")
            self._text_at(x, baseline, label_text, BOLD, size)
            x += text_width(label_text, BOLD, size)
            self._dot(x + 3.5, baseline + size * 0.33, 3.5, color)
            x += 11
            status_text = to_winansi(status)
            self._text_at(x, baseline, status_text, REGULAR, size)
            x += text_width(status_text, REGULAR, size) + 22
        self._y -= 4

    def _dot(self, cx: float, cy: float, r: float, color: str) -> None:
        k = 0.5523 * r  # Bezier approximation of a circle
        self._emit(
            f"{_rgb(color)} rg {_num(cx + r)} {_num(cy)} m "
            f"{_num(cx + r)} {_num(cy + k)} {_num(cx + k)} {_num(cy + r)} "
            f"{_num(cx)} {_num(cy + r)} c "
            f"{_num(cx - k)} {_num(cy + r)} {_num(cx - r)} {_num(cy + k)} "
            f"{_num(cx - r)} {_num(cy)} c "
            f"{_num(cx - r)} {_num(cy - k)} {_num(cx - k)} {_num(cy - r)} "
            f"{_num(cx)} {_num(cy - r)} c "
            f"{_num(cx + k)} {_num(cy - r)} {_num(cx + r)} {_num(cy - k)} "
            f"{_num(cx + r)} {_num(cy)} c f"
        )

    def spacer(self, height: float = 6.0) -> None:
        self._y -= height

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _footer_operations(self, number: int) -> list[str]:
        y = MARGIN / 2 + 4
        right = to_winansi(f"Page {number} of {self.page_count}")
        left = to_winansi(self.footer)
        ops = []
        if left:
            ops.append(
                f"BT /{REGULAR} {_num(SMALL_SIZE)} Tf {_rgb(MUTED_COLOR)} rg "
                f"{_num(MARGIN)} {_num(y)} Td {_pdf_string(left)} Tj ET"
            )
        x = PAGE_WIDTH - MARGIN - text_width(right, REGULAR, SMALL_SIZE)
        ops.append(
            f"BT /{REGULAR} {_num(SMALL_SIZE)} Tf {_rgb(MUTED_COLOR)} rg "
            f"{_num(x)} {_num(y)} Td {_pdf_string(right)} Tj ET"
        )
        return ops

    def _info_dict(self) -> str:
        entries = [
            f"/Title {_pdf_string(to_winansi(self.title))}",
            "/Producer (One Pager App)",
        ]
        if self.created_at:
            stamp = self.created_at.strftime("%Y%m%d%H%M%S")
            entries.append(f"/CreationDate (D:{stamp}Z)")
        return f"<< {' '.join(entries)} >>"

    def build(self) -> bytes:
        """Return the complete PDF file."""
        # Object numbers: 1 catalog, 2 page tree, 3-4 fonts, 5 info, then a
        # page object and its content stream per page.
        objects: list[bytes] = []
        first_page = 6
        page_refs = [f"{first_page + 2 * i} 0 R" for i in range(self.page_count)]
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objects.append(
            f"<< /Type /Pages /Kids [{' '.join(page_refs)}] "
            f"/Count {self.page_count} >>".encode()
        )
        objects.extend(
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{_BASE_FONTS[name]} "
            f"/Encoding /WinAnsiEncoding >>".encode()
            for name in (REGULAR, BOLD)
        )
        objects.append(self._info_dict().encode("cp1252"))
        for number, page in enumerate(self._pages, start=1):
            content_ref = first_page + 2 * (number - 1) + 1
            objects.append(
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {_num(PAGE_WIDTH)} {_num(PAGE_HEIGHT)}] "
                f"/Resources << /Font << /{REGULAR} 3 0 R /{BOLD} 4 0 R >> >> "
                f"/Contents {content_ref} 0 R >>".encode()
            )
            operations = page.operations + self._footer_operations(number)
            stream = "\n".join(operations).encode("cp1252")
            if self.compress:
                stream = zlib.compress(stream)
                head = f"<< /Length {len(stream)} /Filter /FlateDecode >>"
            else:
                head = f"<< /Length {len(stream)} >>"
            objects.append(head.encode() + b"\nstream\n" + stream + b"\nendstream")

        output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(output))
            output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(output)
        output += f"xref\n0 {len(objects) + 1}\n".encode()
        output += b"0000000000 65535 f \n"
        for offset in offsets:
            output += f"{offset:010d} 00000 n \n".encode()
        output += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 5 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
        return bytes(output)
