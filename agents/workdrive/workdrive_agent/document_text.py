# Copied from rytangle router/app/tools/document_text.py (WorkDrive 1.x stays built into the
# router until every server runs the catalog). Unchanged.
"""Text out of a document file, whatever format it arrived in.

Knows nothing about WorkDrive, or about any other source: a byte string and a
filename go in, text comes out. Kept separate because format handling is the
part that grows -- every store has its own list of what people actually keep in
it -- and none of that belongs next to an API client.

── what is read, and what is refused ──

Readable: Office (.xlsx .docx .pptx), OpenDocument (.ods .odt .odp), PDF, RTF,
HTML, and anything that is already text (.txt .md .csv .json .xml .yaml, source
files). Zoho's own formats export to the Office ones, so a Zoho Sheet arrives as
.xlsx and needs nothing of its own.

Refused, by name rather than by silence: images, audio, video, archives, and the
pre-2007 Office binaries (.doc .xls .ppt), which share nothing with the modern
formats and would need a separate parser each. A scanned PDF holds pictures of
words and no text, and is reported as such -- there is no OCR here.

── why structure is preserved rather than flattened ──

The first version of this matched `<t>` elements across every XML part in the
zip. It read a spreadsheet's labels and silently dropped every NUMBER, because
numeric cells are `<v>` and not `<t>` -- measured on a real file whose Sr.no
column and dates vanished while the answer looked complete. A sheet of figures
would have lost all of them with nothing anywhere saying so.

So each format is walked in its own reading order: rows for a sheet, paragraphs
and table rows for a document, slide by slide for a deck. Flat text loses the
association between a label and its value, which is most of what a spreadsheet
IS, and a model asked "what was the Q3 figure" cannot recover it.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

# Extensions that are text already. Not exhaustive on purpose -- anything that
# decodes and carries no NUL bytes falls through to the same path.
TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "xml", "yaml", "yml", "toml",
    "ini", "cfg", "conf", "log", "sql", "py", "js", "ts", "tsx", "jsx", "java",
    "go", "rb", "rs", "c", "h", "cpp", "cs", "sh", "css", "scss", "svg", "eml",
}

# Formats a person plausibly keeps in a document store and that CANNOT be read
# here. Named individually so the refusal can say why, which is the difference
# between "not supported" and "the file is empty".
REFUSED = {
    "doc": "a pre-2007 Word binary — a different format from .docx, needing its own parser",
    "xls": "a pre-2007 Excel binary — a different format from .xlsx, needing its own parser",
    "ppt": "a pre-2007 PowerPoint binary — a different format from .pptx",
    "pages": "an Apple Pages file",
    "numbers": "an Apple Numbers file",
    "key": "an Apple Keynote file",
    "zip": "an archive — the files inside it are not read",
    "rar": "an archive", "7z": "an archive", "tar": "an archive", "gz": "an archive",
    "png": "an image", "jpg": "an image", "jpeg": "an image", "gif": "an image",
    "bmp": "an image", "tiff": "an image", "webp": "an image", "heic": "an image",
    "mp4": "a video", "mov": "a video", "avi": "a video", "mkv": "a video",
    "mp3": "audio", "wav": "audio", "m4a": "audio", "flac": "audio",
}


class UnreadableDocument(RuntimeError):
    """The bytes are not a format this can turn into text."""


@dataclass(frozen=True)
class Extracted:
    text: str
    """What kind of file it turned out to be, for the log and the error."""
    kind: str
    """The first sheet as a table, for formats that have one. None for prose.

    A spreadsheet was already walked into rows and then joined with tabs one line
    later; this keeps what was being discarded, so a sheet can be charted rather
    than only quoted. The FIRST sheet only: two sheets are two tables with
    different columns, and one payload holds one table. The text keeps every
    sheet, so nothing is hidden.
    """
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    """True when the file held tabular content beyond `rows` -- a second sheet.

    Its own flag rather than reusing the text's truncation, because they are
    different claims: cutting the text at a character limit does not drop a
    single row, and a second sheet is not shown however short the file is.
    """
    more_tables: bool = False


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


# ── OOXML: Office 2007 and later, which Zoho's own formats export to ─────────

_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")


def _is_number(cell: str) -> bool:
    return bool(_NUMBER.match(cell.strip()))


def _looks_like_headings(row: list[str]) -> bool:
    """Whether a first row names its columns rather than holding data.

    A heading is text; a data row usually has at least one number in it. Wrong in
    the safe direction: mistaking data for a heading loses one row from a chart,
    while mistaking a heading for data puts the word "Amount" on a numeric axis
    and makes the whole column unchartable.
    """
    if not row or any(not cell.strip() for cell in row):
        return False
    return not any(_is_number(cell) for cell in row)


def _tabulate(grid: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """A sheet's cells as columns and rows.

    Short rows are padded rather than left ragged: a row shorter than its header
    misaligns every cell after the gap, which is worse than a blank.
    """
    if not grid:
        return [], []
    if _looks_like_headings(grid[0]) and len(grid) > 1:
        columns, body = grid[0], grid[1:]
    else:
        columns = [f"column {n}" for n in range(1, max(len(r) for r in grid) + 1)]
        body = grid
    width = len(columns)
    return columns, [(row + [""] * width)[:width] for row in body]


def _local(tag: str) -> str:
    """A tag without its namespace. OOXML and ODF both namespace everything, and
    the namespace URIs vary by producer while the local names do not."""
    return tag.rsplit("}", 1)[-1]


def _text_of(element: ET.Element, wanted: str) -> str:
    """Every `wanted` descendant's text, in document order, joined.

    Document order is the point: a paragraph's runs are split arbitrarily by
    formatting, so "the **Q3** figure" is three runs that must rejoin without a
    space between them.
    """
    return "".join(
        node.text or "" for node in element.iter() if _local(node.tag) == wanted
    )


def _xlsx(archive: zipfile.ZipFile) -> tuple[str, list[list[str]], int]:
    """A spreadsheet as text, plus the FIRST sheet's cells as a grid.

    The grid is not extra work: every row was already assembled cell by cell and
    then joined with tabs one line later. Keeping it is what lets a sheet be
    charted instead of only quoted.

    One row per line, cells tab-separated.

    Numbers included. The first version of this read `<t>` elements only and so
    returned every label and no value -- a sheet of figures came back as a list
    of column headings, and nothing said the numbers were missing.
    """
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in root:
            if _local(item.tag) == "si":
                shared.append(_text_of(item, "t"))

    # Sheet names, so a multi-sheet workbook is navigable rather than one blur.
    names: list[str] = []
    if "xl/workbook.xml" in archive.namelist():
        root = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [
            sheet.get("name", "")
            for sheet in root.iter()
            if _local(sheet.tag) == "sheet"
        ]

    sheets = sorted(
        (n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
        key=lambda n: int(re.search(r"(\d+)", n).group(1)),  # type: ignore[union-attr]
    )

    lines: list[str] = []
    # The first sheet only. Two sheets are two tables with different columns, and
    # one payload holds one table; the text below keeps every sheet regardless.
    grid: list[list[str]] = []
    for index, path in enumerate(sheets):
        if len(sheets) > 1 or names:
            title = names[index] if index < len(names) else f"Sheet{index + 1}"
            lines.append(f"--- sheet: {title} ---")
        root = ET.fromstring(archive.read(path))
        for row in (n for n in root.iter() if _local(n.tag) == "row"):
            cells: list[str] = []
            for cell in (c for c in row if _local(c.tag) == "c"):
                kind = cell.get("t", "")
                if kind == "s":
                    raw = _text_of(cell, "v")
                    value = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
                elif kind == "inlineStr":
                    value = _text_of(cell, "t")
                else:
                    # Numbers, dates (as serials), booleans, formula RESULTS.
                    # `<v>` is the cached result; `<f>` is the formula itself and
                    # is deliberately not read -- a reader wants the value.
                    value = _text_of(cell, "v")
                cells.append(value.strip())
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                lines.append("\t".join(cells))
                if index == 0:
                    grid.append(cells)
    return "\n".join(lines), grid, len(sheets)


def _docx(archive: zipfile.ZipFile) -> str:
    """A Word document: body paragraphs and table rows, in order.

    Headers, footers, footnotes and comments are deliberately skipped -- they are
    page furniture, and interleaving them by zip order (which is what reading
    every part did) put a footer in the middle of a sentence.
    """
    if "word/document.xml" not in archive.namelist():
        return ""
    root = ET.fromstring(archive.read("word/document.xml"))

    lines: list[str] = []
    for node in root.iter():
        name = _local(node.tag)
        if name == "p":
            # Skip a paragraph inside a table: the row handler below emits it,
            # and emitting both duplicates every cell.
            text = _text_of(node, "t").strip()
            if text:
                lines.append(text)
        elif name == "tr":
            cells = [
                _text_of(cell, "t").strip()
                for cell in node
                if _local(cell.tag) == "tc"
            ]
            if any(cells):
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _pptx(archive: zipfile.ZipFile) -> str:
    """A deck, slide by slide in slide order, which is the reading order."""
    slides = sorted(
        (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: int(re.search(r"(\d+)", n).group(1)),  # type: ignore[union-attr]
    )
    lines: list[str] = []
    for number, path in enumerate(slides, start=1):
        root = ET.fromstring(archive.read(path))
        found = [
            (node.text or "").strip()
            for node in root.iter()
            if _local(node.tag) == "t" and (node.text or "").strip()
        ]
        if found:
            lines.append(f"--- slide {number} ---")
            lines.extend(found)
    return "\n".join(lines)


ODS_MIME = b"application/vnd.oasis.opendocument.spreadsheet"


def _is_ods(archive: zipfile.ZipFile) -> bool:
    """Whether this OpenDocument file is a SPREADSHEET.

    Gated on the declared mimetype rather than on "it contains a table",
    deliberately. A .odt can hold a table inside prose, and presenting that table
    as the document's data would be a confident wrong answer -- the table is an
    illustration in the text, not the record.
    """
    try:
        return archive.read("mimetype").strip() == ODS_MIME
    except KeyError:
        return False


def _odf(archive: zipfile.ZipFile) -> tuple[str, list[list[str]]]:
    """OpenDocument: .odt, .ods, .odp. All three keep their content in
    `content.xml`, with paragraphs as `text:p` and table rows as `table:table-row`.

    Returns the text, plus the rows when this is a spreadsheet and nothing when
    it is not -- see _is_ods.
    """
    if "content.xml" not in archive.namelist():
        return "", []
    root = ET.fromstring(archive.read("content.xml"))
    lines: list[str] = []
    grid: list[list[str]] = []
    tabular = _is_ods(archive)
    for node in root.iter():
        name = _local(node.tag)
        if name == "table-row":
            cells = [
                "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "p").strip()
                for cell in node
                if _local(cell.tag) == "table-cell"
            ]
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                lines.append("\t".join(cells))
                if tabular:
                    grid.append(cells)
        elif name in ("p", "h"):
            # A paragraph inside a row is emitted by the row branch above.
            text = "".join(n.text or "" for n in node.iter()).strip()
            if text and not lines[-1:] == [text]:
                lines.append(text)
    return "\n".join(lines), grid


def _ooxml_or_odf(data: bytes) -> Extracted | None:
    """Whichever zip-based document this is, or None if it is not one."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None

    names = set(archive.namelist())
    try:
        if any(n.startswith("xl/") for n in names):
            text, grid, sheets = _xlsx(archive)
            columns, rows = _tabulate(grid)
            return Extracted(text, "spreadsheet",
                             columns=columns or None, rows=rows or None,
                             more_tables=sheets > 1)
        if "word/document.xml" in names:
            return Extracted(_docx(archive), "document")
        if any(n.startswith("ppt/slides/") for n in names):
            return Extracted(_pptx(archive), "presentation")
        if "content.xml" in names:
            text, grid = _odf(archive)
            columns, rows = _tabulate(grid)
            return Extracted(text, "opendocument",
                             columns=columns or None, rows=rows or None)
    except ET.ParseError as error:
        raise UnreadableDocument(f"the file is damaged or not valid XML inside: {error}") from None
    return None


# ── PDF ──────────────────────────────────────────────────────────────────────

def _pdf(data: bytes) -> Extracted:
    """Selectable text from a PDF.

    Imported here rather than at module load: a router configured without
    WorkDrive never reads a document, and should not pay the import.

    A SCANNED pdf holds pictures of words and yields nothing. That is reported
    rather than returned as an empty document, because "this file is empty" and
    "this file needs OCR, which we do not have" lead a reader somewhere very
    different.
    """
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared
        raise UnreadableDocument(
            "reading PDFs needs the pypdf package, which is not installed"
        ) from None

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # An empty password opens the common "protected from editing" case.
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - any failure means the same thing
                raise UnreadableDocument("the PDF is password-protected") from None
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except UnreadableDocument:
        raise
    except Exception as error:  # noqa: BLE001 - pypdf raises many shapes
        raise UnreadableDocument(f"the PDF could not be parsed: {error}") from None

    if not any(pages):
        raise UnreadableDocument(
            f"the PDF has {len(pages)} page(s) and no selectable text — it is "
            "probably a scan, and reading it would need OCR, which is not available "
            "here"
        )
    body = "\n".join(
        f"--- page {number} ---\n{text}"
        for number, text in enumerate(pages, start=1)
        if text
    )
    return Extracted(body, "pdf")


# ── markup and plain text ────────────────────────────────────────────────────

def _html(data: bytes) -> Extracted:
    raw = data.decode("utf-8", "replace")
    # Script and style hold code, not content, and their text would otherwise
    # dominate the extraction.
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</tr>|</li>|</h[1-6]>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        raw = raw.replace(entity, char)
    lines = [line.strip() for line in raw.splitlines()]
    return Extracted("\n".join(line for line in lines if line), "html")


def _rtf(data: bytes) -> Extracted:
    """RTF is text wrapped in control words. Stripping them is imperfect and
    good enough for reading -- the alternative is a parser for a format almost
    nothing is authored in any more."""
    raw = data.decode("latin-1", "replace")
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)          # escaped bytes
    raw = re.sub(r"\\par[d]?\b", "\n", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", raw)        # control words
    raw = raw.replace("{", "").replace("}", "")
    lines = [line.strip() for line in raw.splitlines()]
    return Extracted("\n".join(line for line in lines if line), "rtf")


def _plain(data: bytes) -> Extracted | None:
    # A NUL byte in the first pages means binary; no text format contains one.
    if b"\x00" in data[:4096]:
        return None
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return Extracted(data.decode(encoding), "text")
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None


# ── the one entry point ──────────────────────────────────────────────────────

def extract(data: bytes, filename: str = "") -> Extracted:
    """Text from a document, or `UnreadableDocument` saying what it was instead.

    The extension decides first and the CONTENT decides when the extension is
    absent or lying: Zoho names its exports correctly, but a filename is not a
    guarantee and sniffing a zip or a `%PDF` header costs nothing.
    """
    extension = extension_of(filename)

    if extension in REFUSED:
        raise UnreadableDocument(f"{filename} is {REFUSED[extension]}")

    if extension == "pdf" or data[:5] == b"%PDF-":
        return _pdf(data)

    zipped = _ooxml_or_odf(data)
    if zipped is not None:
        if not zipped.text.strip():
            raise UnreadableDocument(
                f"{filename or 'the file'} is a {zipped.kind} with no text in it"
            )
        return zipped

    if extension in ("html", "htm"):
        return _html(data)
    if extension == "rtf" or data[:5] == b"{\\rtf":
        return _rtf(data)

    plain = _plain(data)
    if plain is not None:
        return plain

    raise UnreadableDocument(
        f"{filename or 'the file'} is not a format this can read. Office and "
        "OpenDocument files, PDFs with selectable text, HTML, RTF and plain text "
        "are readable; images, audio, video, archives and pre-2007 Office "
        "binaries are not."
    )
