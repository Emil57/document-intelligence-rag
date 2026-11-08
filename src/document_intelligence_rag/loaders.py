"""Loaders: turn files, images and web links into plain text.

Each kind of source stores text differently, so each gets its own reader.
`load_source` decides whether it's a link or a file, and `load_file` picks
a reader by file extension. Every reader returns plain text wrapped in the
same `Document` record, so the rest of the app doesn't care where it came from.
"""

import base64
import io
from dataclasses import dataclass
from pathlib import Path

import anthropic
import docx
import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from document_intelligence_rag import config

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json"}
HTML_EXTENSIONS = {".html", ".htm"}
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # the API accepts images up to 5 MB

# Sites like Wikipedia block downloads from unnamed programs. Naming the app
# and linking the project is the polite, expected way to identify ourselves.
USER_AGENT = "document-intelligence-rag/0.1 (https://github.com/Emil57/document-intelligence-rag)"


@dataclass
class Document:
    text: str
    source: str    # file name or URL
    kind: str      # "pdf", "docx", "text", "html", "image" or "web"
    added_by: str  # who shared it (used for sharing in a later step)


class UnsupportedFileError(Exception):
    pass


class LoadError(Exception):
    """A source exists but couldn't be read (bad link, image too big, ...)."""


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def load_source(source: str, added_by: str, client: anthropic.Anthropic | None = None) -> Document:
    """Load a web link or a file path."""
    if is_url(source):
        return load_url(source, added_by)
    return load_file(source, added_by, client)


# ---- files -----------------------------------------------------------
def load_file(path: str | Path, added_by: str, client: anthropic.Anthropic | None = None) -> Document:
    """Pick the right reader based on the file extension."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No file at {path}")

    ext = path.suffix.lower()
    if ext == ".pdf":
        kind, text = "pdf", read_pdf(path.read_bytes())
    elif ext == ".docx":
        kind, text = "docx", read_docx(path)
    elif ext in HTML_EXTENSIONS:
        kind, text = "html", html_to_text(path.read_text(encoding="utf-8", errors="replace"))
    elif ext in TEXT_EXTENSIONS:
        kind, text = "text", read_text(path)
    elif ext in IMAGE_TYPES:
        kind, text = "image", describe_image(path, client)
    else:
        raise UnsupportedFileError(f"Can't read {ext or 'files without an extension'} yet")

    return Document(text=text, source=path.name, kind=kind, added_by=added_by)


def read_text(path: Path) -> str:
    # errors="replace" keeps going if the file has odd characters.
    return path.read_text(encoding="utf-8", errors="replace")


def read_pdf(data: bytes) -> str:
    # Takes bytes, so it works for files on disk and PDFs downloaded from a link.
    reader = PdfReader(io.BytesIO(data))
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


# ---- images ----------------------------------------------------------
IMAGE_PROMPT = """\
Describe this image so it can be found by a text search later.
- Transcribe all visible text exactly, keeping its layout where it matters (lists, tables).
- Describe people, objects, places, and what is happening.
- For charts or diagrams, give the title, labels and key numbers.
Start with a one-line title for the image. Plain text only, no preamble."""


def describe_image(path: Path, client: anthropic.Anthropic | None = None) -> str:
    """Ask Claude to describe an image, so its content becomes searchable text."""
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise LoadError(f"{path.name} is larger than 5 MB. Please resize it first.")

    client = client or anthropic.Anthropic()
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": IMAGE_TYPES[path.suffix.lower()],
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                },
                {"type": "text", "text": IMAGE_PROMPT},
            ],
        }],
    )
    if response.stop_reason == "refusal":
        raise LoadError(f"Claude declined to describe {path.name}.")
    description = "".join(b.text for b in response.content if b.type == "text")
    # Note that this is a description, so answers can say so.
    return f"{description}\n\n(This is an AI-written description of the image file {path.name}.)"


# ---- web links -------------------------------------------------------
def html_to_text(html: str) -> str:
    """Keep the readable text of a web page, without menus, scripts and styles."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "form", "aside"]):
        tag.decompose()
    # Most pages put their real content in <main> or <article>.
    main = soup.find("main") or soup.find("article") or soup.body or soup
    lines = (line.strip() for line in main.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def load_url(url: str, added_by: str) -> Document:
    """Download a web page (or a PDF/text file at a link) and keep its text."""
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=20,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise LoadError(f"Couldn't download {url}: {e}") from e

    content_type = response.headers.get("content-type", "").lower()
    if "application/pdf" in content_type:
        text = read_pdf(response.content)
    elif "html" in content_type:
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        body = html_to_text(response.text)
        # Put the page title first: list_documents shows the first line.
        text = f"{title}\n\n{body}" if title else body
    elif content_type.startswith("text/") or "json" in content_type:
        text = response.text
    else:
        raise UnsupportedFileError(f"Can't read {content_type or 'this kind of link'} yet")

    if not text.strip():
        raise LoadError(f"No readable text found at {url}")
    return Document(text=text, source=url, kind="web", added_by=added_by)
