"""Shared test setup.

The tests never call the real Claude API: they use FakeClient, which
returns replies we write in advance. So they're free and need no API key.
"""

import os
from types import SimpleNamespace

import pytest

# Set before the app is imported: web.py creates an API client and a database on import.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")


def reply(stop_reason: str, *blocks) -> SimpleNamespace:
    """A pretend Claude response."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool_use(name: str, tool_input: dict, id: str = "tool_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id, name=name, input=tool_input)


class FakeClient:
    """Stands in for anthropic.Anthropic(). Returns the given replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []  # a copy of the messages sent with each request
        self.messages = self  # so client.messages.create(...) works

    def create(self, **kwargs):
        self.requests.append(list(kwargs["messages"]))
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """A test browser for the web app, using an empty database in a temporary folder."""
    from fastapi.testclient import TestClient

    from document_intelligence_rag import config, store, web

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "SIGNUP_CODE", None)
    monkeypatch.setattr(store, "_indexes", {})
    monkeypatch.setattr(web, "conversations", {})
    store.init_db()
    return TestClient(web.app)
