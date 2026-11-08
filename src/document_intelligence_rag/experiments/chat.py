"""Step 5-6: chat with the agent about your files, images and links.

Run with:
    uv run src/document_intelligence_rag/experiments/chat.py

Type a question and press Enter. Follow-up questions remember the conversation.
You can also paste a link in your question, and the agent can add it.
Commands:
    add <file path or link>   add a file, image or web page to the library
    new                       start a fresh conversation
    quit                      exit
"""

from pathlib import Path

import anthropic

from document_intelligence_rag import config
from document_intelligence_rag.agent import Agent
from document_intelligence_rag.loaders import LoadError, UnsupportedFileError, load_file, load_source
from document_intelligence_rag.search import SearchIndex

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"


def build_index(folder: Path, client: anthropic.Anthropic) -> SearchIndex:
    """Load and index every supported file in a folder."""
    index = SearchIndex()
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        try:
            index.add(load_file(path, added_by="me", client=client))
        except UnsupportedFileError:
            print(f"Skipped {path.name} (unsupported file type)")
        except LoadError as e:
            print(f"Skipped {path.name}: {e}")
    return index


def add_to_library(index: SearchIndex, source: str, client: anthropic.Anthropic) -> None:
    source = source.strip().strip('"')  # allow pasted paths with quotes
    try:
        doc = load_source(source, added_by="me", client=client)
    except (FileNotFoundError, UnsupportedFileError, LoadError) as e:
        print(f"Couldn't add it: {e}\n")
        return
    if doc.source in index.documents:
        print(f"{doc.source} is already in the library.\n")
        return
    chunks = index.add(doc)
    print(f"Added {doc.source} ({doc.kind}, {chunks} chunks).\n")


def main() -> None:
    client = anthropic.Anthropic()
    print("Loading library (images get described by Claude, which takes a moment)...")
    index = build_index(SAMPLES_DIR, client)
    agent = Agent(index=index, client=client)
    print(f"Library: {len(index.documents)} documents  |  model: {config.MODEL}")
    print('Ask a question. Commands: "add <file or link>", "new", "quit".\n')

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question.lower() in {"quit", "exit"}:
            break
        if question.lower() == "new":
            agent.reset()
            print("(Started a new conversation.)\n")
            continue
        if question.lower().startswith("add "):
            add_to_library(index, question[4:], client)
            continue

        try:
            answer, usage = agent.ask(question)
        except anthropic.AuthenticationError:
            print("Your API key was rejected. Check ANTHROPIC_API_KEY in your .env file.")
            break
        except anthropic.APIConnectionError:
            print("Could not reach the Claude API. Check your internet connection.\n")
            continue
        except anthropic.APIStatusError as e:
            print(f"The API returned an error ({e.status_code}): {e.message}\n")
            continue

        print(f"\nClaude: {answer}")
        print(f"  ({usage.input_tokens:,} tokens in, {usage.output_tokens:,} out)\n")


if __name__ == "__main__":
    main()
