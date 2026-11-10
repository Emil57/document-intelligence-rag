"""Storage: users, login sessions, libraries and documents, in a SQLite database.

SQLite is a full database kept in a single file (data/app.db). It comes with
Python, so there's nothing to install or run.

How the tables relate:

    users ──< memberships >── libraries ──< documents
    users ──< sessions

- Every user gets a private "personal" library when they sign up.
- A shared library can have many members. Its creator is the "owner",
  who can invite and remove members.
- Documents belong to one library.

Searching still uses the in-memory SearchIndex from step 3: we build one per
library from the database the first time it's needed, and keep it up to date.
"""

import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from document_intelligence_rag import auth, config
from document_intelligence_rag.loaders import Document
from document_intelligence_rag.search import SearchIndex

SESSION_DAYS = 30
USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,32}$")
MIN_PASSWORD_LENGTH = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS libraries (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    is_personal INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    PRIMARY KEY (library_id, user_id)
);
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    source     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    added_by   TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    UNIQUE (library_id, source)
);
"""


class StoreError(Exception):
    """A problem we can explain to the user, like "that username is taken"."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Open the database for one task. Changes are saved at the end, or undone on an error."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row  # rows act like dicts: row["username"]
    db.execute("PRAGMA foreign_keys = ON")
    try:
        with db:  # commit on success, roll back on error
            yield db
    finally:
        db.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(SCHEMA)


# ---- users -----------------------------------------------------------------
def create_user(username: str, password: str) -> int:
    """Create an account and its personal library. Returns the user id."""
    username = username.strip().lower()
    if not USERNAME_PATTERN.match(username):
        raise StoreError("Usernames need 3 to 32 characters: letters, numbers, '.', '-' or '_'.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise StoreError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")

    with connect() as db:
        try:
            cursor = db.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, auth.hash_password(password), now()),
            )
        except sqlite3.IntegrityError:
            raise StoreError("That username is taken.")
        user_id = cursor.lastrowid
        _create_library(db, f"{username}'s library", user_id, personal=True)
    return user_id


def check_login(username: str, password: str) -> int | None:
    """Return the user id if the username and password match, else None."""
    with connect() as db:
        row = db.execute(
            "SELECT id, password_hash FROM users WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()
    if row and auth.verify_password(password, row["password_hash"]):
        return row["id"]
    return None


# ---- sessions (staying logged in) ----------------------------------------
def create_session(user_id: int) -> str:
    """Start a login session. Returns the token to put in the browser's cookie."""
    token = auth.new_token()
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with connect() as db:
        db.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (auth.hash_token(token), user_id, expires.isoformat(timespec="seconds")),
        )
    return token


def user_for_session(token: str) -> sqlite3.Row | None:
    """Who does this login token belong to? None if it's unknown or expired."""
    with connect() as db:
        return db.execute(
            """SELECT users.id, users.username FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (auth.hash_token(token), now()),
        ).fetchone()


def delete_session(token: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (auth.hash_token(token),))


# ---- libraries and members -------------------------------------------------
def _create_library(db: sqlite3.Connection, name: str, owner_id: int, personal: bool = False) -> int:
    cursor = db.execute(
        "INSERT INTO libraries (name, is_personal, created_at) VALUES (?, ?, ?)",
        (name, int(personal), now()),
    )
    library_id = cursor.lastrowid
    db.execute(
        "INSERT INTO memberships (library_id, user_id, role) VALUES (?, ?, 'owner')",
        (library_id, owner_id),
    )
    return library_id


def create_shared_library(name: str, owner_id: int) -> int:
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise StoreError("Library names need 1 to 80 characters.")
    with connect() as db:
        return _create_library(db, name, owner_id)


def libraries_for(user_id: int) -> list[dict]:
    """Every library the user belongs to, personal first."""
    with connect() as db:
        rows = db.execute(
            """SELECT libraries.id, libraries.name, libraries.is_personal, memberships.role,
                      (SELECT COUNT(*) FROM memberships AS m WHERE m.library_id = libraries.id) AS members
               FROM libraries JOIN memberships ON memberships.library_id = libraries.id
               WHERE memberships.user_id = ?
               ORDER BY libraries.is_personal DESC, libraries.name""",
            (user_id,),
        ).fetchall()
    return [dict(row) | {"is_personal": bool(row["is_personal"])} for row in rows]


def role_in(library_id: int, user_id: int) -> str | None:
    """'owner', 'member', or None if the user isn't in this library."""
    with connect() as db:
        row = db.execute(
            "SELECT role FROM memberships WHERE library_id = ? AND user_id = ?",
            (library_id, user_id),
        ).fetchone()
    return row["role"] if row else None


def members_of(library_id: int) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """SELECT users.username, memberships.role FROM memberships
               JOIN users ON users.id = memberships.user_id
               WHERE memberships.library_id = ?
               ORDER BY memberships.role DESC, users.username""",
            (library_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_member(library_id: int, username: str) -> None:
    username = username.strip().lower()
    with connect() as db:
        library = db.execute("SELECT is_personal FROM libraries WHERE id = ?", (library_id,)).fetchone()
        if library is None:
            raise StoreError("No such library.")
        if library["is_personal"]:
            raise StoreError("Personal libraries are private. Create a shared library to work with others.")
        user = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if user is None:
            raise StoreError(f"There's no user named {username}. They need to sign up first.")
        try:
            db.execute(
                "INSERT INTO memberships (library_id, user_id, role) VALUES (?, ?, 'member')",
                (library_id, user["id"]),
            )
        except sqlite3.IntegrityError:
            raise StoreError(f"{username} is already a member.")


def remove_member(library_id: int, username: str) -> None:
    with connect() as db:
        row = db.execute(
            """SELECT memberships.user_id, memberships.role FROM memberships
               JOIN users ON users.id = memberships.user_id
               WHERE memberships.library_id = ? AND users.username = ?""",
            (library_id, username.strip().lower()),
        ).fetchone()
        if row is None:
            raise StoreError(f"{username} isn't a member.")
        if row["role"] == "owner":
            raise StoreError("The owner can't be removed.")
        db.execute(
            "DELETE FROM memberships WHERE library_id = ? AND user_id = ?",
            (library_id, row["user_id"]),
        )


# ---- documents and search indexes ------------------------------------------
_indexes: dict[int, SearchIndex] = {}  # library id -> its search index
_index_lock = threading.Lock()  # one change at a time, across all requests


def get_index(library_id: int) -> SearchIndex:
    """The search index for a library, built from the database on first use."""
    with _index_lock:
        if library_id not in _indexes:
            index = SearchIndex()
            with connect() as db:
                rows = db.execute(
                    "SELECT source, kind, text, added_by FROM documents WHERE library_id = ? ORDER BY id",
                    (library_id,),
                ).fetchall()
            for row in rows:
                index.add(Document(text=row["text"], source=row["source"], kind=row["kind"], added_by=row["added_by"]))
            _indexes[library_id] = index
        return _indexes[library_id]


def add_document(library_id: int, doc: Document) -> int:
    """Save a document and add it to the library's search. Returns its number of chunks."""
    index = get_index(library_id)
    with _index_lock:
        with connect() as db:
            try:
                db.execute(
                    """INSERT INTO documents (library_id, source, kind, text, added_by, added_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (library_id, doc.source, doc.kind, doc.text, doc.added_by, now()),
                )
            except sqlite3.IntegrityError:
                raise StoreError(f"{doc.source} is already in this library.")
        return index.add(doc)


def remove_document(library_id: int, source: str) -> bool:
    index = get_index(library_id)
    with _index_lock:
        with connect() as db:
            cursor = db.execute(
                "DELETE FROM documents WHERE library_id = ? AND source = ?",
                (library_id, source),
            )
        if cursor.rowcount == 0:
            return False
        index.remove(source)
        return True
