"""Load narratives, performance plans, and guidance from .md, .txt, .docx, or .pdf files."""

import hashlib
import re
import unicodedata
from pathlib import Path

MARKDOWN_SUFFIXES = {".md", ".markdown"}
SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES | {".txt", ".docx", ".pdf"}

# Backslash escapes that Google Docs and other exporters add to Markdown, e.g. "\[OV\]".
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!=|>~])")
_MD_EMPHASIS = re.compile(r"\*\*|__")
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
_MD_BULLET = re.compile(r"^[ \t]*[*+-][ \t]+", re.MULTILINE)


def load_text(path: Path) -> str:
    """Read a document as Unicode (NFC) text."""
    suffix = path.suffix.lower()
    if suffix in MARKDOWN_SUFFIXES:
        text = _MD_ESCAPE.sub(r"\1", path.read_text(encoding="utf-8"))
    elif suffix == ".txt":
        text = path.read_text(encoding="utf-8")
    elif suffix == ".docx":
        text = _load_docx(path)
    elif suffix == ".pdf":
        text = _load_pdf(path)
    else:
        raise ValueError(
            f"Unsupported file type {suffix!r} for {path}; "
            f"use one of {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )
    if not text.strip():
        raise ValueError(f"No text found in {path}. If it is a scanned PDF, run OCR on it first.")
    return unicodedata.normalize("NFC", text)


def _load_docx(path: Path) -> str:
    import docx
    from docx.table import Table

    document = docx.Document(str(path))
    parts: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                cells, seen = [], set()
                for cell in row.cells:
                    # Merged cells appear once per grid column; keep the first.
                    if id(cell._tc) not in seen:
                        seen.add(id(cell._tc))
                        cells.append(cell.text.strip())
                parts.append(" | ".join(cells))
        else:
            parts.append(_docx_paragraph(block))
    return "\n".join(parts)


def _docx_paragraph(paragraph) -> str:
    """Keep Word headings and list items as Markdown so plans and narratives keep their structure."""
    style = (paragraph.style.name if paragraph.style is not None else "").lower()
    if style == "title" or style.startswith("heading"):
        level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
        return "#" * min(level, 6) + " " + paragraph.text
    num = paragraph._p.pPr.numPr if paragraph._p.pPr is not None else None
    if style.startswith("list") or num is not None:
        depth = int(num.ilvl.val) if num is not None and num.ilvl is not None else 0
        return "  " * depth + "- " + paragraph.text
    return paragraph.text


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def plain_text(text: str, path: Path) -> str:
    """Strip Markdown markup so the length matches what gets pasted into the HHS form."""
    if path.suffix.lower() not in MARKDOWN_SUFFIXES:
        return text
    text = _MD_EMPHASIS.sub("", text)
    text = _MD_HEADING.sub("", text)
    return _MD_BULLET.sub("", text)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
