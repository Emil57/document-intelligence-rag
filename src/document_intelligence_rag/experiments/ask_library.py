"""Step 4: ask a question about all your files and get an answer with sources.

Run with:
    uv run src/document_intelligence_rag/experiments/ask_library.py "your question"
"""

import re
import sys
from pathlib import Path

import anthropic

from document_intelligence_rag import config
from document_intelligence_rag.loaders import UnsupportedFileError, load_file
from document_intelligence_rag.rag import answer_question
from document_intelligence_rag.search import SearchIndex

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"


def build_index(folder: Path) -> SearchIndex:
    """Load and index every supported file in a folder."""
    index = SearchIndex()
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        try:
            index.add(load_file(path, added_by="me"))
        except UnsupportedFileError:
            print(f"Skipped {path.name} (unsupported file type)")
    return index


def main() -> None:
    if len(sys.argv) != 2:
        print('Usage: ask_library.py "your question"')
        return
    question = sys.argv[1]

    index = build_index(SAMPLES_DIR)
    print(f"Library: {len(index.chunks)} chunks from {SAMPLES_DIR.name}/  |  model: {config.MODEL}\n")

    client = anthropic.Anthropic()
    try:
        answer = answer_question(index, question, client)
    except anthropic.AuthenticationError:
        print("Your API key was rejected. Check ANTHROPIC_API_KEY in your .env file.")
        return
    except anthropic.APIConnectionError:
        print("Could not reach the Claude API. Check your internet connection.")
        return
    except anthropic.APIStatusError as e:
        print(f"The API returned an error ({e.status_code}): {e.message}")
        return

    print(f"Question: {question}\n")
    print(f"Answer: {answer.text}\n")

    # Show only the sources Claude actually cited, like [1] or [3].
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", answer.text)}
    if cited:
        print("Sources:")
        for number, result in enumerate(answer.sources, start=1):
            if number in cited:
                print(f"  [{number}] {result.chunk.source} (chunk {result.chunk.index})")
        print()

    if answer.input_tokens:
        print(f"Tokens used: {answer.input_tokens} in, {answer.output_tokens} out")


if __name__ == "__main__":
    main()
