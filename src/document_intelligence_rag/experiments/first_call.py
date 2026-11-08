"""Step 1: the first Claude call.

Give Claude a paragraph and a question, and have it answer using only that paragraph.
This is RAG in its smallest form: we supply the text, Claude answers from it.

Run with:
    uv run src/document_intelligence_rag/experiments/first_call.py
"""

import anthropic
from dotenv import load_dotenv

# Read ANTHROPIC_API_KEY from the .env file in the project folder.
load_dotenv()

# Haiku is the cheapest model, which suits experiments. Switch to
# "claude-opus-5-5" or "claude-sonnet-5-5" later for better answers.
MODEL = "claude-haiku-5-5"

# The "document" we want to ask about. Replace it with any text you like.
DOCUMENT = """\
The Great Barrier Reef is the world's largest coral reef system, made up of over
2,900 individual reefs and 900 islands stretching for over 2,300 kilometres. It is
located in the Coral Sea, off the coast of Queensland, Australia. The reef can be
seen from outer space and is the world's biggest single structure made by living
organisms. It was selected as a World Heritage Site in 1981.
"""

QUESTION = "How long is the Great Barrier Reef, and when did it become a World Heritage Site?"
# Try a question the text can't answer, to check that Claude doesn't make things up:
QUESTION = "How many fish species live on the reef?"

# The system prompt sets the rules Claude follows for the whole conversation.
SYSTEM_PROMPT = """\
You answer questions using only the document the user provides.
If the document does not contain the answer, say "The document doesn't say." and do not guess.
Keep answers short and quote the relevant words from the document when helpful."""


def main() -> None:
    # The client finds ANTHROPIC_API_KEY on its own.
    client = anthropic.Anthropic()

    # Tags like <document> help Claude tell the text apart from the question.
    user_message = f"<document>\n{DOCUMENT}</document>\n\nQuestion: {QUESTION}"

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

    # Claude can decline a request. If it does, there is no answer to print.
    if response.stop_reason == "refusal":
        print("Claude declined to answer this request.")
        return

    # response.content is a list of blocks (thinking, text, ...).
    # Only "text" blocks hold the answer.
    answer = "".join(block.text for block in response.content if block.type == "text")

    print(f"Question: {QUESTION}\n")
    print(f"Answer: {answer}\n")

    # Tokens are what you pay for: input = what we sent, output = what Claude wrote.
    print(f"Tokens used: {response.usage.input_tokens} in, {response.usage.output_tokens} out")


if __name__ == "__main__":
    main()
