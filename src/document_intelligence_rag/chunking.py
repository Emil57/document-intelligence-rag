"""Chunking: split long text into small, overlapping pieces.

Search works on chunks, not whole documents, so we can send Claude
only the few pieces that match a question.
"""

from dataclasses import dataclass

from document_intelligence_rag.loaders import Document

CHUNK_SIZE = 1000    # characters per chunk (a few paragraphs)
CHUNK_OVERLAP = 150  # characters repeated from the end of the previous chunk


@dataclass
class Chunk:
    text: str
    source: str    # file it came from
    index: int     # position in that file: 0, 1, 2, ...
    added_by: str  # who shared the file


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into chunks of about `size` characters that overlap a little."""
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))

        # Unless this is the last chunk, move the end back to a clean break:
        # a paragraph end, a line end, a sentence end, or at least a space.
        # Only accept breaks in the second half, so chunks don't get too small.
        if end < len(text):
            window = text[start:end]
            for separator in ("\n\n", "\n", ". ", " "):
                cut = window.rfind(separator)
                if cut > size // 2:
                    end = start + cut + len(separator)
                    break

        chunks.append(text[start:end].strip())
        if end >= len(text):
            break

        # Start the next chunk a bit before this one ended (the overlap),
        # moved forward to the beginning of a word.
        next_start = end - overlap
        space = text.find(" ", next_start, end)
        if space != -1:
            next_start = space + 1
        start = max(next_start, start + 1)  # always move forward

    return [c for c in chunks if c]


def chunk_document(doc: Document) -> list[Chunk]:
    """Split a Document into Chunks that remember where they came from."""
    return [
        Chunk(text=piece, source=doc.source, index=i, added_by=doc.added_by)
        for i, piece in enumerate(chunk_text(doc.text))
    ]
