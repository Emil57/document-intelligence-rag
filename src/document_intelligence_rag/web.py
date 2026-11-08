"""Step 7: the web app.

A small web server (FastAPI) with:
- one page (static/index.html) for uploading files, adding links and chatting
- an API the page calls:
    GET    /api/documents          list the library
    POST   /api/documents/upload   upload a file or image
    POST   /api/documents/link     add a web link
    DELETE /api/documents?source=  remove a document
    POST   /api/chat               ask the agent a question

Run with:
    uv run uvicorn document_intelligence_rag.web:app --reload
then open http://127.0.0.1:8000 in your browser.
"""

import threading
import uuid
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from document_intelligence_rag import config
from document_intelligence_rag.agent import Agent
from document_intelligence_rag.loaders import (
    HTML_EXTENSIONS,
    IMAGE_TYPES,
    TEXT_EXTENSIONS,
    Document,
    LoadError,
    UnsupportedFileError,
    is_url,
    load_file,
    load_url,
)
from document_intelligence_rag.search import SearchIndex

STATIC_DIR = Path(__file__).parent / "static"
SUPPORTED_EXTENSIONS = {".pdf", ".docx"} | TEXT_EXTENSIONS | HTML_EXTENSIONS | set(IMAGE_TYPES)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

# Until we add sign-in (step 8), everyone is "me".
USER = "me"

app = FastAPI(title="Document Intelligence")
client = anthropic.Anthropic()
index = SearchIndex.load(config.LIBRARY_PATH)  # the saved library, or an empty one

# One Agent per conversation, so each chat has its own memory.
# Kept in memory only: restarting the server starts fresh conversations.
conversations: dict[str, Agent] = {}

# The server handles requests in parallel. The lock makes sure two requests
# never change the library (or write library.json) at the same time.
library_lock = threading.Lock()


def save_library() -> None:
    index.save(config.LIBRARY_PATH)


def describe(doc: Document) -> dict:
    """What the page shows about each document."""
    return {
        "source": doc.source,
        "kind": doc.kind,
        "characters": len(doc.text),
        "added_by": doc.added_by,
    }


# ---- the page ----------------------------------------------------------
@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ---- documents -----------------------------------------------------------
@app.get("/api/documents")
def list_documents() -> list[dict]:
    return [describe(doc) for doc in index.documents.values()]


@app.post("/api/documents/upload")
def upload_document(file: UploadFile) -> dict:
    # Keep only the file name: a name like "../../secret.txt" must not
    # let anyone write outside our folder.
    name = Path(file.filename or "").name
    ext = Path(name).suffix.lower()
    if not name or ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Can't read {ext or 'this kind of'} files yet.")
    if name in index.documents:
        raise HTTPException(409, f"{name} is already in the library.")

    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than 20 MB.")

    config.FILES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.FILES_DIR / name
    path.write_bytes(data)

    try:
        doc = load_file(path, added_by=USER, client=client)
    except (UnsupportedFileError, LoadError) as e:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    except anthropic.APIError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(502, f"Claude couldn't describe the image: {e}")
    except Exception as e:  # e.g. a damaged PDF
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Couldn't read {name}: {e}")

    if not doc.text.strip():
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"No text found in {name}. Is it a scanned PDF?")

    with library_lock:
        chunks = index.add(doc)
        save_library()
    return {**describe(doc), "chunks": chunks}


class LinkRequest(BaseModel):
    url: str


@app.post("/api/documents/link")
def add_link(request: LinkRequest) -> dict:
    url = request.url.strip()
    if not is_url(url):
        raise HTTPException(400, "Links must start with http:// or https://")
    if url in index.documents:
        raise HTTPException(409, "That link is already in the library.")
    try:
        doc = load_url(url, added_by=USER)
    except (UnsupportedFileError, LoadError) as e:
        raise HTTPException(400, str(e))

    with library_lock:
        chunks = index.add(doc)
        save_library()
    return {**describe(doc), "chunks": chunks}


@app.delete("/api/documents")
def delete_document(source: str) -> dict:
    # `source` is a query parameter (?source=...) because URLs contain slashes.
    with library_lock:
        if not index.remove(source):
            raise HTTPException(404, "No such document.")
        save_library()
    if not is_url(source):
        (config.FILES_DIR / Path(source).name).unlink(missing_ok=True)
    return {"deleted": source}


# ---- chat ------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # None starts a new conversation


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    message = request.message.strip()
    if not message:
        raise HTTPException(400, "Please type a question.")

    conversation_id = request.conversation_id
    agent = conversations.get(conversation_id) if conversation_id else None
    if agent is None:
        conversation_id = uuid.uuid4().hex
        agent = Agent(index=index, client=client, user=USER, verbose=False)
        conversations[conversation_id] = agent

    documents_before = len(index.documents)
    try:
        answer, usage = agent.ask(message)
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Couldn't reach the Claude API. Check the internet connection.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"The Claude API returned an error ({e.status_code}): {e.message}")

    # The agent can add links during a chat (add_link), so save if it did.
    if len(index.documents) != documents_before:
        with library_lock:
            save_library()

    return {
        "conversation_id": conversation_id,
        "answer": answer,
        "tools": agent.tool_calls,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
    }
