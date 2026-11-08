"""Loaders: turn files into plain text.

Each file type stores text differently, so each gets its own reader function.
`load_file` looks at the file extension and picks the right one.
Every reader returns plain text, wrapped in the same `Document` record,
so the rest of the app doesn't care what kind of file it came from.
"""

from dataclasses import dataclass
from pathlib import Path

import docx
from pypdf import PdfReader

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json"}


@dataclass
class Document:
    text: str
    source: str    # file name
    kind: str      # "pdf", "docx" or "text"
    added_by: str  # who shared it (used for sharing in a later step)


class UnsupportedFileError(Exception):
    pass


def load_file(path: str | Path, added_by: str) -> Document:
    """Pick the right reader based on the file extension."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No file at {path}")

    ext = path.suffix.lower()
    if ext == ".pdf":
        kind, text = "pdf", read_pdf(path)
    elif ext == ".docx":
        kind, text = "docx", read_docx(path)
    elif ext in TEXT_EXTENSIONS:
        kind, text = "text", read_text(path)
    else:
        raise UnsupportedFileError(f"Can't read {ext or 'files without an extension'} yet")

    return Document(text=text, source=path.name, kind=kind, added_by=added_by)


def read_text(path: Path) -> str:
    # errors="replace" keeps going if the file has odd characters.
    return path.read_text(encoding="utf-8", errors="replace")


def read_pdf(path: Path) -> str:
    reader = PdfReader(path)
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            # The page marker lets Claude say where it found an answer.
            pages.append(f"[Page {number}]\n{page_text}")
    return "\n\n".join(pages)


def read_docx(path: Path) -> str:
    document = docx.Document(path)
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    # Tables are stored separately from paragraphs, so read them too.
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)
