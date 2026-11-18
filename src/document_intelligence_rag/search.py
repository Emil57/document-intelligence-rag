"""Keyword search with BM25.

BM25 favours chunks that contain the question's rarer words, gives less extra
credit for each repeat, and doesn't let long chunks win just for being long.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from document_intelligence_rag.chunking import Chunk, chunk_document
from document_intelligence_rag.loaders import Document

# Excludes "_", so "meeting_notes" becomes "meeting" and "notes".
WORD = re.compile(r"[^\W_]+")

# Ignored, so "what is the ..." doesn't match every chunk.
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "of", "on", "or", "should", "that",
    "the", "this", "to", "was", "what", "when", "where", "which", "who", "why",
    "will", "with", "you", "your",
}


def tokenize(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP_WORDS]


@dataclass
class SearchResult:
    chunk: Chunk
    score: float


class SearchIndex:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        # k1: extra credit for repeated words. b: how much long chunks are held back (0 to 1).
        self.k1 = k1
        self.b = b
        self.chunks: list[Chunk] = []
        self.word_counts: list[Counter] = []  # one per chunk
        self.doc_freq: Counter = Counter()  # word -> number of chunks containing it
        self.documents: dict[str, Document] = {}  # by source

    def add(self, doc: Document) -> int:
        """Returns the number of chunks."""
        self.documents[doc.source] = doc
        new_chunks = chunk_document(doc)
        for chunk in new_chunks:
            counts = Counter(tokenize(chunk.text))
            self.chunks.append(chunk)
            self.word_counts.append(counts)
            self.doc_freq.update(counts.keys())
        return len(new_chunks)

    def remove(self, source: str) -> bool:
        """Returns False if the document wasn't in the index."""
        if source not in self.documents:
            return False
        remaining = [doc for name, doc in self.documents.items() if name != source]
        # doc_freq mixes all documents, so rebuilding is the simplest correct way.
        self.chunks, self.word_counts, self.doc_freq, self.documents = [], [], Counter(), {}
        for doc in remaining:
            self.add(doc)
        return True

    def search(self, question: str, limit: int = 5) -> list[SearchResult]:
        query_words = set(tokenize(question))
        if not query_words or not self.chunks:
            return []

        n = len(self.chunks)
        avg_length = sum(sum(c.values()) for c in self.word_counts) / n

        results = []
        for chunk, counts in zip(self.chunks, self.word_counts):
            length = sum(counts.values())
            score = 0.0
            for word in query_words:
                tf = counts[word]
                if tf == 0:
                    continue
                df = self.doc_freq[word]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                length_factor = 1 - self.b + self.b * length / avg_length
                score += idf * tf * (self.k1 + 1) / (tf + self.k1 * length_factor)
            if score > 0:
                results.append(SearchResult(chunk=chunk, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]
