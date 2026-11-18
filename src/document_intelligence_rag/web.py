"""The web app: accounts, private and shared libraries, and chat.

Every /api/libraries/{id}/... endpoint first checks that the user is a member.
"""

import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from document_intelligence_rag import auth, config, spending, store
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
from document_intelligence_rag.store import StoreError

STATIC_DIR = Path(__file__).parent / "static"
SUPPORTED_EXTENSIONS = {".pdf", ".docx"} | TEXT_EXTENSIONS | HTML_EXTENSIONS | set(IMAGE_TYPES)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SESSION_COOKIE = "session"

app = FastAPI(title="Document Intelligence")
client = anthropic.Anthropic()
store.init_db()


@dataclass
class Conversation:
    user_id: int
    library_id: int
    agent: Agent


# In memory only, so a restart starts fresh conversations.
conversations: dict[str, Conversation] = {}


# ---- permissions ---------------------------------------------------------------
def current_user(request: Request) -> sqlite3.Row:
    token = request.cookies.get(SESSION_COOKIE)
    user = store.user_for_session(token) if token else None
    if user is None:
        raise HTTPException(401, "Please log in.")
    return user


User = Annotated[sqlite3.Row, Depends(current_user)]


def require_member(library_id: int, user: sqlite3.Row) -> str:
    """The user's role, or 404 rather than 403, so outsiders can't learn which libraries exist."""
    role = store.role_in(library_id, user["id"])
    if role is None:
        raise HTTPException(404, "Library not found.")
    return role


def require_owner(library_id: int, user: sqlite3.Row) -> None:
    if require_member(library_id, user) != "owner":
        raise HTTPException(403, "Only the library's owner can do that.")


def check_spending(user: sqlite3.Row) -> None:
    try:
        spending.check(user["id"])
    except spending.LimitReached as e:
        raise HTTPException(429, str(e)) from e


def start_session(response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        store.create_session(user_id),
        max_age=store.SESSION_DAYS * 24 * 3600,
        httponly=True,  # injected scripts can't read it
        samesite="lax",  # other websites can't send requests with it
        secure=False,  # TODO step 9: True once the app runs on HTTPS
    )


def describe(doc: Document) -> dict:
    return {"source": doc.source, "kind": doc.kind, "characters": len(doc.text), "added_by": doc.added_by}


# ---- the page ----------------------------------------------------------------
@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    """For the server's health check: answers only if the app and its database work."""
    with store.connect() as db:
        db.execute("SELECT 1")
    return {"ok": True}


# ---- accounts ----------------------------------------------------------------
class Credentials(BaseModel):
    username: str
    password: str
    signup_code: str | None = None


@app.get("/api/signup-info")
def signup_info() -> dict:
    return {"code_required": config.SIGNUP_CODE is not None}


@app.post("/api/signup")
def signup(body: Credentials, response: Response) -> dict:
    if config.SIGNUP_CODE and not auth.codes_match(body.signup_code or "", config.SIGNUP_CODE):
        raise HTTPException(403, "Wrong sign-up code.")
    try:
        user_id = store.create_user(body.username, body.password)
    except StoreError as e:
        raise HTTPException(400, str(e))
    start_session(response, user_id)
    return {"ok": True}


@app.post("/api/login")
def login(body: Credentials, response: Response) -> dict:
    user_id = store.check_login(body.username, body.password)
    if user_id is None:
        # Same message either way, so nobody can test which usernames exist.
        raise HTTPException(401, "Wrong username or password.")
    start_session(response, user_id)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_session(token)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: User) -> dict:
    return {
        "username": user["username"],
        "libraries": store.libraries_for(user["id"]),
        "spending": spending.summary(user["id"]),
    }


# ---- libraries and members ----------------------------------------------------
class NewLibrary(BaseModel):
    name: str


@app.post("/api/libraries")
def create_library(body: NewLibrary, user: User) -> dict:
    try:
        library_id = store.create_shared_library(body.name, user["id"])
    except StoreError as e:
        raise HTTPException(400, str(e))
    return {"id": library_id}


@app.get("/api/libraries/{library_id}/members")
def list_members(library_id: int, user: User) -> list[dict]:
    require_member(library_id, user)
    return store.members_of(library_id)


class NewMember(BaseModel):
    username: str


@app.post("/api/libraries/{library_id}/members")
def invite_member(library_id: int, body: NewMember, user: User) -> dict:
    require_owner(library_id, user)
    try:
        store.add_member(library_id, body.username)
    except StoreError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/libraries/{library_id}/members/{username}")
def remove_member(library_id: int, username: str, user: User) -> dict:
    require_owner(library_id, user)
    try:
        store.remove_member(library_id, username)
    except StoreError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---- documents ----------------------------------------------------------------
@app.get("/api/libraries/{library_id}/documents")
def list_documents(library_id: int, user: User) -> list[dict]:
    require_member(library_id, user)
    return [describe(doc) for doc in store.get_index(library_id).documents.values()]


@app.post("/api/libraries/{library_id}/documents/upload")
def upload_document(library_id: int, file: UploadFile, user: User) -> dict:
    require_member(library_id, user)

    # Only the name, so "../../secret.txt" can't escape the folder.
    name = Path(file.filename or "").name
    ext = Path(name).suffix.lower()
    if not name or ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Can't read {ext or 'this kind of'} files yet.")
    if ext in IMAGE_TYPES:  # only images cost money: Claude describes them
        check_spending(user)
    if name in store.get_index(library_id).documents:
        raise HTTPException(409, f"{name} is already in this library.")

    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than 20 MB.")

    folder = config.FILES_DIR / str(library_id)
    # Move the upload into the library only after the database accepts it, so
    # a rejected same-name upload can't overwrite or delete the existing file.
    # A folder per upload keeps the real file name, which the loaders use.
    upload_dir = config.FILES_DIR / "uploading" / uuid.uuid4().hex
    upload_dir.mkdir(parents=True)
    temp_path = upload_dir / name
    temp_path.write_bytes(data)

    try:
        doc = load_file(
            temp_path,
            added_by=user["username"],
            client=client,
            record_usage=lambda model, i, o: spending.record(user["id"], "image", model, i, o),
        )
        if not doc.text.strip():
            raise LoadError(f"No text found in {name}. Is it a scanned PDF?")
        chunks = store.add_document(library_id, doc)
        folder.mkdir(parents=True, exist_ok=True)
        temp_path.replace(folder / name)
    except (UnsupportedFileError, LoadError, StoreError) as e:
        raise HTTPException(400, str(e))
    except anthropic.APIError as e:
        raise HTTPException(502, f"Claude couldn't describe the image: {e}")
    except Exception as e:  # e.g. a damaged PDF
        raise HTTPException(400, f"Couldn't read {name}: {e}")
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)
    return {**describe(doc), "chunks": chunks, "spending": spending.summary(user["id"])}


class LinkRequest(BaseModel):
    url: str


@app.post("/api/libraries/{library_id}/documents/link")
def add_link(library_id: int, body: LinkRequest, user: User) -> dict:
    require_member(library_id, user)
    url = body.url.strip()
    if not is_url(url):
        raise HTTPException(400, "Links must start with http:// or https://")
    if url in store.get_index(library_id).documents:
        raise HTTPException(409, "That link is already in this library.")
    try:
        doc = load_url(url, added_by=user["username"])
        chunks = store.add_document(library_id, doc)
    except (UnsupportedFileError, LoadError, StoreError) as e:
        raise HTTPException(400, str(e))
    return {**describe(doc), "chunks": chunks}


@app.delete("/api/libraries/{library_id}/documents")
def delete_document(library_id: int, source: str, user: User) -> dict:
    role = require_member(library_id, user)
    doc = store.get_index(library_id).documents.get(source)
    if doc is None:
        raise HTTPException(404, "No such document.")
    # Members can only remove what they added; the owner can remove anything.
    if role != "owner" and doc.added_by != user["username"]:
        raise HTTPException(403, f"Only {doc.added_by} or the library's owner can remove this.")
    store.remove_document(library_id, source)
    if not is_url(source):
        (config.FILES_DIR / str(library_id) / Path(source).name).unlink(missing_ok=True)
    return {"deleted": source}


# ---- chat ------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # None starts a new one


@app.post("/api/libraries/{library_id}/chat")
def chat(library_id: int, body: ChatRequest, user: User) -> dict:
    require_member(library_id, user)
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Please type a question.")

    # Only continue a conversation that belongs to this user and library.
    conversation = conversations.get(body.conversation_id or "")
    if conversation is None or conversation.user_id != user["id"] or conversation.library_id != library_id:
        agent = Agent(
            index=store.get_index(library_id),
            client=client,
            user=user["username"],
            add_document=lambda doc: store.add_document(library_id, doc),
            # The person asking pays, even in a shared library.
            record_usage=lambda model, i, o: spending.record(user["id"], "chat", model, i, o),
            check_budget=lambda: spending.check(user["id"]),
        )
        conversation = Conversation(user_id=user["id"], library_id=library_id, agent=agent)
        conversation_id = uuid.uuid4().hex
        conversations[conversation_id] = conversation
    else:
        conversation_id = body.conversation_id

    try:
        answer, usage = conversation.agent.ask(message)
    except spending.LimitReached as e:
        raise HTTPException(429, str(e)) from e
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Couldn't reach the Claude API. Check the internet connection.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"The Claude API returned an error ({e.status_code}): {e.message}")

    return {
        "conversation_id": conversation_id,
        "answer": answer,
        "tools": conversation.agent.tool_calls,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        "spending": spending.summary(user["id"]),
    }
