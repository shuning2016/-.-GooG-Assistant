"""
Text extraction for email attachments and Drive files (used by /api/gmail and /api/drive).

PDF → pypdf; XLSX → openpyxl; PPTX / DOCX → stdlib zip + XML (no Pillow/lxml needed);
plain text types → decoded as UTF-8. Charts and pictures are not OCR'd — text only.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_SHEET_ROWS = 2_000

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_EXT_KIND = {
    "pdf": "pdf", "pptx": "pptx", "docx": "docx", "xlsx": "xlsx", "xlsm": "xlsx",
    "txt": "text", "csv": "text", "md": "text", "json": "text", "xml": "text",
    "html": "text", "htm": "text", "eml": "text", "ics": "text",
}
_MIME_KIND = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": "xlsx",
    "application/json": "text", "application/xml": "text", "application/csv": "text",
}


class UnsupportedType(Exception):
    pass


def kind_of(mime: str, filename: str) -> str | None:
    """Return pdf / pptx / docx / xlsx / text, or None if we can't extract it.

    Gmail often labels attachments application/octet-stream, so the file
    extension wins over a generic MIME type.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXT_KIND:
        return _EXT_KIND[ext]
    mime = (mime or "").lower()
    if mime in _MIME_KIND:
        return _MIME_KIND[mime]
    if mime.startswith("text/"):
        return "text"
    return None


def extract_text(data: bytes, mime: str, filename: str) -> tuple[str, str]:
    """Return (text, content_format). Raises UnsupportedType."""
    kind = kind_of(mime, filename)
    if kind == "pdf":
        return _pdf(data), "pdf-text"
    if kind == "pptx":
        return _pptx(data), "pptx-text"
    if kind == "docx":
        return _docx(data), "docx-text"
    if kind == "xlsx":
        return _xlsx(data), "xlsx-csv"
    if kind == "text":
        return data.decode("utf-8", "replace").lstrip("﻿"), "text"
    raise UnsupportedType(mime or filename or "unknown type")


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = "[page text could not be extracted]"
        pages.append(f"--- page {i} ---\n{text.strip()}")
    return "\n\n".join(pages)


def _paragraphs(root: ET.Element, para_tag: str, text_tag: str) -> list[str]:
    out = []
    for p in root.iter(para_tag):
        line = "".join(t.text or "" for t in p.iter(text_tag)).strip()
        if line:
            out.append(line)
    return out


def _slide_number(name: str) -> int:
    m = re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def _pptx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=_slide_number)
        out = []
        for name in slides:
            num = _slide_number(name)
            root = ET.fromstring(z.read(name))
            parts = []
            # Tables: one line per row, cells joined with " | ".
            table_cells = set()
            for tbl in root.iter(f"{_A}tbl"):
                for tr in tbl.iter(f"{_A}tr"):
                    cells = []
                    for tc in tr.iter(f"{_A}tc"):
                        cells.append(" ".join(_paragraphs(tc, f"{_A}p", f"{_A}t")))
                        table_cells.update(id(p) for p in tc.iter(f"{_A}p"))
                    parts.append(" | ".join(cells))
            body = [
                "".join(t.text or "" for t in p.iter(f"{_A}t")).strip()
                for p in root.iter(f"{_A}p") if id(p) not in table_cells
            ]
            text = "\n".join([line for line in body if line] + parts)
            notes_name = f"ppt/notesSlides/notesSlide{num}.xml"
            if notes_name in names:
                notes = _paragraphs(ET.fromstring(z.read(notes_name)), f"{_A}p", f"{_A}t")
                # Notes pages repeat the slide number as a lone digit; drop it.
                notes = [n for n in notes if n != str(num)]
                if notes:
                    text += "\n[notes] " + "\n".join(notes)
            out.append(f"--- slide {num} ---\n{text}")
        return "\n\n".join(out)


def _docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{_W}body")
    out = []
    for block in (body if body is not None else []):
        if block.tag == f"{_W}p":
            line = "".join(t.text or "" for t in block.iter(f"{_W}t")).strip()
            if line:
                out.append(line)
        elif block.tag == f"{_W}tbl":
            for tr in block.iter(f"{_W}tr"):
                cells = [" ".join(_paragraphs(tc, f"{_W}p", f"{_W}t")) for tc in tr.iter(f"{_W}tc")]
                out.append(" | ".join(cells))
    return "\n".join(out)


def _cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if any(c in text for c in ',"\n'):
        text = '"' + text.replace('"', '""') + '"'
    return text


def _xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_SHEET_ROWS:
                rows.append(f"[… more rows omitted after {MAX_SHEET_ROWS}]")
                break
            line = ",".join(_cell(v) for v in row).rstrip(",")
            if line:
                rows.append(line)
        out.append(f"--- sheet {ws.title} ---\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(out)
