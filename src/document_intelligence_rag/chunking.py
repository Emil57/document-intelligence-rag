"""Split documents into small, overlapping chunks, so search can return just the relevant part."""

from dataclasses import dataclass

from document_intelligence_rag.loaders import Document

CHUNK_SIZE = 1000  # characters
CHUNK_OVERLAP = 150  # characters shared with the previous chunk


@dataclass
class Chunk:
    text: str
    source: str
    index: int  # position in the document


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into chunks of about `size` characters that overlap a little."""
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))

        # Prefer to end at a paragraph, line, sentence or word break, but only
        # in the second half of the chunk, so chunks don't get too small.
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

        # Overlap with this chunk, starting at a word.
        next_start = end - overlap
        space = text.find(" ", next_start, end)
        if space != -1:
            next_start = space + 1
        start = max(next_start, start + 1)  # guarantees progress

    return [c for c in chunks if c]


def chunk_document(doc: Document) -> list[Chunk]:
    return [
        Chunk(text=piece, source=doc.source, index=i)
        for i, piece in enumerate(chunk_text(doc.text))
    ]
