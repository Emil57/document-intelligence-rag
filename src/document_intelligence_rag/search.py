"""Search: find the chunks that best match a question, using BM25.

BM25 scores a chunk higher when:
- it contains the question's words,
- those words are rare across all chunks (rare words are better clues),
- a word appears several times (with less extra credit each time),
and it doesn't let long chunks win just because they are long.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from document_intelligence_rag.chunking import Chunk, chunk_document
from document_intelligence_rag.loaders import Document

# Letters and digits only, so "meeting_notes" becomes "meeting" and "notes".
WORD = re.compile(r"[^\W_]+")

# Very common words that say nothing about the topic. Ignoring them
# stops "what is the ..." from matching every chunk.
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "of", "on", "or", "should", "that",
    "the", "this", "to", "was", "what", "when", "where", "which", "who", "why",
    "will", "with", "you", "your",
}


def tokenize(text: str) -> list[str]:
    """Turn text into a list of lowercase words, without stop words."""
    return [w for w in WORD.findall(text.lower()) if w not in STOP_WORDS]


@dataclass
class SearchResult:
    chunk: Chunk
    score: float


class SearchIndex:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        # k1: how much extra credit repeated words get.
        # b: how much long chunks are held back (0 = not at all, 1 = fully).
        self.k1 = k1
        self.b = b
        self.chunks: list[Chunk] = []
        self.word_counts: list[Counter] = []  # per chunk: how often each word appears
        self.doc_freq: Counter = Counter()    # per word: how many chunks contain it

    def add(self, doc: Document) -> int:
        """Chunk a document and add it to the index. Returns the number of chunks."""
        new_chunks = chunk_document(doc)
        for chunk in new_chunks:
            counts = Counter(tokenize(chunk.text))
            self.chunks.append(chunk)
            self.word_counts.append(counts)
            self.doc_freq.update(counts.keys())
        return len(new_chunks)

    def search(self, question: str, limit: int = 5) -> list[SearchResult]:
        """Return the best-matching chunks, best first."""
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
                tf = counts[word]  # how often the word appears in this chunk
                if tf == 0:
                    continue
                df = self.doc_freq[word]  # how many chunks contain the word
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))  # rare word = big number
                length_factor = 1 - self.b + self.b * length / avg_length
                score += idf * tf * (self.k1 + 1) / (tf + self.k1 * length_factor)
            if score > 0:
                results.append(SearchResult(chunk=chunk, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]
