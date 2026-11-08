"""Step 3: search your files, without any AI.

Loads every file in samples/, splits them into chunks, and shows
the chunks that best match your question.

Run with:
    uv run src/document_intelligence_rag/experiments/search_files.py "your question"
"""

import sys
from pathlib import Path

from document_intelligence_rag.loaders import UnsupportedFileError, load_file
from document_intelligence_rag.search import SearchIndex

# The project folder is 3 levels above this file: experiments -> package -> src -> project.
SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"


def main() -> None:
    if len(sys.argv) != 2:
        print('Usage: search_files.py "your question"')
        return
    question = sys.argv[1]

    # 1. Load and index every file.
    index = SearchIndex()
    for path in sorted(SAMPLES_DIR.iterdir()):
        if not path.is_file():
            continue
        try:
            doc = load_file(path, added_by="me")
        except UnsupportedFileError:
            print(f"Skipped {path.name} (unsupported file type)")
            continue
        count = index.add(doc)
        print(f"Indexed {path.name}: {count} chunk(s)")

    # 2. Search.
    print(f"\nQuestion: {question}\n")
    results = index.search(question, limit=3)
    if not results:
        print("No matching chunks.")
        return

    for rank, result in enumerate(results, start=1):
        chunk = result.chunk
        preview = chunk.text[:250].replace("\n", " ")
        print(f"#{rank}  score {result.score:.2f}  {chunk.source} (chunk {chunk.index})")
        print(f"    {preview}...\n")


if __name__ == "__main__":
    main()
