"""Step 5: chat with the agent about your files.

Run with:
    uv run src/document_intelligence_rag/experiments/chat.py

Type a question and press Enter. Follow-up questions remember the conversation.
Commands: "new" starts a fresh conversation, "quit" exits.
"""

from pathlib import Path

import anthropic

from document_intelligence_rag import config
from document_intelligence_rag.agent import Agent
from document_intelligence_rag.loaders import UnsupportedFileError, load_file
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
    index = build_index(SAMPLES_DIR)
    agent = Agent(index=index, client=anthropic.Anthropic())
    print(f"Library: {len(index.documents)} documents  |  model: {config.MODEL}")
    print('Ask a question. Type "new" for a fresh conversation, "quit" to exit.\n')

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
