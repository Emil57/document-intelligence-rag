"""The agent: Claude with tools to explore your library, and memory of the conversation.

How it differs from rag.py (step 4):
- rag.py searches once, with the exact question, then asks Claude.
- Here Claude decides what to do. It can list the documents, search
  (again with other words if the first search finds nothing), or read a
  whole document, and it answers when it has enough information.
- The conversation is kept, so follow-up questions like "and who decided that?" work.

The "agent loop":
    1. Send the conversation and the tool descriptions to Claude.
    2. If Claude asks to use tools, run them and send back the results. Go to 1.
    3. If Claude answers, we're done.
"""

from dataclasses import dataclass, field

import anthropic

from document_intelligence_rag import config
from document_intelligence_rag.search import SearchIndex

SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions about the user's document library.
Use the tools to find information. Answer only from what the tools return, never from memory.
If a search finds nothing useful, try again with different words or synonyms before giving up.
For broad questions such as "summarize this file", read the whole document.
File names can be misleading: search or read before saying something isn't in the library.
Cite the file each fact comes from in square brackets, like [meeting_notes.docx].
If the library doesn't contain the answer, say so. Keep answers short."""

# Tool descriptions: Claude reads these to decide which tool to use and how.
TOOLS = [
    {
        "name": "search_library",
        "description": (
            "Keyword search across all documents in the library. Returns the best-matching "
            "passages with their file names. It matches exact words, not meaning, so if it "
            "finds nothing, try synonyms or simpler words."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to search for."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_documents",
        "description": (
            "List every document in the library, with its type, size, who added it "
            "and its first line (often the title)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_document",
        "description": (
            "Read the full text of one document. Use it for summaries or when search "
            "results aren't enough. Use the exact file name from list_documents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_name": {"type": "string", "description": "Exact file name, e.g. notes.pdf"},
            },
            "required": ["file_name"],
        },
    },
]

MAX_TOOL_ROUNDS = 8  # safety limit, so a confused agent can't loop (and spend) forever
MAX_READ_CHARS = 40_000  # about 10,000 tokens: keeps one read_document call affordable


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Agent:
    index: SearchIndex
    client: anthropic.Anthropic
    messages: list = field(default_factory=list)  # the whole conversation so far
    verbose: bool = True  # print each tool call, so you can watch the agent work

    # ---- the tools ---------------------------------------------------
    def search_library(self, query: str) -> str:
        results = self.index.search(query, limit=config.CHUNKS_PER_QUESTION)
        if not results:
            return "No matching passages. Try different words or synonyms."
        parts = []
        for result in results:
            chunk = result.chunk
            parts.append(f"--- {chunk.source} (chunk {chunk.index}) ---\n{chunk.text}")
        return "\n\n".join(parts)

    def list_documents(self) -> str:
        if not self.index.documents:
            return "The library is empty."
        lines = []
        for doc in self.index.documents.values():
            # The first real line is often the title, which says more than the file name.
            first_line = next(
                (line.strip() for line in doc.text.splitlines()
                 if line.strip() and not line.startswith("[Page")),
                "",
            )
            lines.append(
                f"- {doc.source} ({doc.kind}, {len(doc.text):,} characters, added by {doc.added_by})"
                f"\n  starts with: {first_line[:100]}"
            )
        return "\n".join(lines)

    def read_document(self, file_name: str) -> str:
        doc = self.index.documents.get(file_name)
        if doc is None:
            return f"No document named {file_name!r}. Use list_documents to see the exact names."
        if len(doc.text) > MAX_READ_CHARS:
            return (
                doc.text[:MAX_READ_CHARS]
                + f"\n\n[Only the first {MAX_READ_CHARS:,} of {len(doc.text):,} characters are shown. "
                "Use search_library to find specific parts of the rest.]"
            )
        return doc.text

    def run_tool(self, name: str, tool_input: dict) -> tuple[str, bool]:
        """Run one tool. Returns (result text, is_error)."""
        if self.verbose:
            args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
            print(f"  > {name}({args})")
        try:
            if name == "search_library":
                return self.search_library(str(tool_input["query"])), False
            if name == "list_documents":
                return self.list_documents(), False
            if name == "read_document":
                return self.read_document(str(tool_input["file_name"])), False
            return f"Unknown tool: {name}", True
        except KeyError as e:
            return f"Missing input: {e}", True

    # ---- the agent loop ----------------------------------------------
    def ask(self, question: str) -> tuple[str, Usage]:
        """Answer one question, using tools as needed. Remembers earlier questions."""
        self.messages.append({"role": "user", "content": question})
        usage = Usage()

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.messages.create(
                model=config.MODEL,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.messages,
            )
            usage.input_tokens += response.usage.input_tokens
            usage.output_tokens += response.usage.output_tokens

            # A refused or cut-off reply is incomplete, so we leave it out of the conversation.
            if response.stop_reason == "refusal":
                return "Claude declined to answer this question.", usage
            if response.stop_reason == "max_tokens":
                return "The answer was too long and got cut off. Try a narrower question.", usage

            # Keep Claude's full reply (text, thinking and tool calls) in the conversation.
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                # end_turn: Claude has written its answer.
                text = "".join(b.text for b in response.content if b.type == "text")
                return text, usage

            # Claude asked for one or more tools. Run them all and send back
            # all results together in one message.
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result, is_error = self.run_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                        "is_error": is_error,
                    })
            self.messages.append({"role": "user", "content": tool_results})

        # Too many rounds. The conversation ends on tool results, so add a note
        # asking Claude to answer without more tools next time.
        self.messages.append({
            "role": "assistant",
            "content": "I stopped searching because I hit the tool limit.",
        })
        return "I couldn't finish: the agent used too many tool calls. Try a more specific question.", usage

    def reset(self) -> None:
        """Forget the conversation and start fresh."""
        self.messages.clear()
