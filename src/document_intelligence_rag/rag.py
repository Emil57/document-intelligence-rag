"""RAG: search the library, then have Claude answer from the best chunks.

1. Search finds the chunks that best match the question.
2. We number them and send them to Claude together with the question.
3. Claude answers using only those chunks and cites them by number, like [1].
"""

from dataclasses import dataclass

import anthropic

from document_intelligence_rag import config
from document_intelligence_rag.search import SearchIndex, SearchResult

SYSTEM_PROMPT = """\
You answer questions using only the numbered sources the user provides.
After each fact, cite the source it came from with its number in square brackets, like [1] or [2][3].
If the sources don't contain the answer, say "I couldn't find that in your documents." and don't guess.
If sources disagree, say so. Keep answers short."""


@dataclass
class Answer:
    text: str
    sources: list[SearchResult]  # the chunks we sent, in the order they were numbered
    input_tokens: int
    output_tokens: int


def build_prompt(question: str, results: list[SearchResult]) -> str:
    """Put the numbered chunks and the question into one message."""
    parts = []
    for number, result in enumerate(results, start=1):
        chunk = result.chunk
        parts.append(
            f'<source id="{number}" file="{chunk.source}" chunk="{chunk.index}">\n'
            f"{chunk.text}\n"
            f"</source>"
        )
    sources = "\n\n".join(parts)
    return f"<sources>\n{sources}\n</sources>\n\nQuestion: {question}"


def answer_question(
    index: SearchIndex,
    question: str,
    client: anthropic.Anthropic,
    limit: int = config.CHUNKS_PER_QUESTION,
) -> Answer:
    # 1. Search. If nothing matches, don't spend money asking Claude.
    results = index.search(question, limit=limit)
    if not results:
        return Answer("I couldn't find anything about that in your documents.", [], 0, 0)

    # 2. Ask Claude, giving it only the matching chunks.
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(question, results)}],
    )

    if response.stop_reason == "refusal":
        text = "Claude declined to answer this question."
    else:
        text = "".join(block.text for block in response.content if block.type == "text")

    return Answer(
        text=text,
        sources=results,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
