"""Step 2: load a real file and ask Claude about it.

Run with:
    uv run src/document_intelligence_rag/experiments/ask_a_file.py <file> "<question>"

Example:
    uv run src/document_intelligence_rag/experiments/ask_a_file.py samples/coffee_shop.pdf "What are the opening hours?"
"""

import sys

import anthropic
from dotenv import load_dotenv

from document_intelligence_rag.loaders import UnsupportedFileError, load_file

load_dotenv()

MODEL = "claude-haiku-5-5"

SYSTEM_PROMPT = """\
You answer questions using only the document the user provides.
If the document does not contain the answer, say "The document doesn't say." and do not guess.
Keep answers short. If the document has [Page N] markers, say which page the answer is on."""


def main() -> None:
    if len(sys.argv) != 3:
        print('Usage: ask_a_file.py <file> "<question>"')
        return
    path, question = sys.argv[1], sys.argv[2]

    # 1. Load the file into a Document.
    try:
        doc = load_file(path, added_by="me")
    except (FileNotFoundError, UnsupportedFileError) as e:
        print(f"Could not load the file: {e}")
        return

    print(f"Loaded {doc.source} ({doc.kind}, {len(doc.text):,} characters)\n")

    # Scanned PDFs are pictures of pages, so they have no text to extract.
    if not doc.text.strip():
        print("No text found in this file. If it's a scanned PDF, that's expected for now.")
        return

    # 2. Ask Claude about it.
    client = anthropic.Anthropic()
    user_message = f'<document source="{doc.source}">\n{doc.text}\n</document>\n\nQuestion: {question}'

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.AuthenticationError:
        print("Your API key was rejected. Check ANTHROPIC_API_KEY in your .env file.")
        return
    except anthropic.APIConnectionError:
        print("Could not reach the Claude API. Check your internet connection.")
        return
    except anthropic.APIStatusError as e:
        print(f"The API returned an error ({e.status_code}): {e.message}")
        return

    if response.stop_reason == "refusal":
        print("Claude declined to answer this request.")
        return

    answer = "".join(block.text for block in response.content if block.type == "text")
    print(f"Question: {question}\n")
    print(f"Answer: {answer}\n")
    print(f"Tokens used: {response.usage.input_tokens} in, {response.usage.output_tokens} out")


if __name__ == "__main__":
    main()
