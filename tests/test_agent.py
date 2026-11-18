import pytest

from conftest import FakeClient, reply, text, tool_use
from document_intelligence_rag.agent import Agent
from document_intelligence_rag.loaders import Document
from document_intelligence_rag.search import SearchIndex


def make_agent(client: FakeClient) -> Agent:
    index = SearchIndex()
    index.add(Document(text="The office opens at 9am.", source="hours.txt", kind="text", added_by="tester"))
    return Agent(index=index, client=client, user="tester", add_document=index.add)


def test_agent_runs_tools_then_answers():
    client = FakeClient(
        reply("tool_use", tool_use("search_library", {"query": "office opens"})),
        reply("end_turn", text("It opens at 9am [hours.txt].")),
    )
    agent = make_agent(client)

    answer, usage = agent.ask("When does the office open?")

    assert answer == "It opens at 9am [hours.txt]."
    assert agent.tool_calls == ["search_library(query='office opens')"]
    assert usage.input_tokens == 20  # two requests of 10 tokens each
    # The second request carried the search result back to Claude.
    tool_result = client.requests[1][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "9am" in tool_result["content"]


def test_agent_remembers_the_conversation():
    client = FakeClient(reply("end_turn", text("Hi!")), reply("end_turn", text("Sure.")))
    agent = make_agent(client)
    agent.ask("Hello")
    agent.ask("Follow-up")
    sent = [m["content"] for m in client.requests[1] if m["role"] == "user"]
    assert sent == ["Hello", "Follow-up"]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_incomplete_answer_is_not_kept_in_the_conversation(stop_reason):
    client = FakeClient(reply(stop_reason), reply("end_turn", text("Fine.")))
    agent = make_agent(client)

    agent.ask("A question that fails")
    assert agent.messages == []

    agent.ask("Next question")
    sent = [m["content"] for m in client.requests[1] if m["role"] == "user"]
    assert sent == ["Next question"]


def test_api_error_puts_the_conversation_back():
    client = FakeClient(
        reply("tool_use", tool_use("list_documents", {})),
        RuntimeError("network down"),
    )
    agent = make_agent(client)
    with pytest.raises(RuntimeError):
        agent.ask("What's in the library?")
    assert agent.messages == []


def test_unknown_document_gives_a_helpful_message():
    agent = make_agent(FakeClient())
    assert "list_documents" in agent.read_document("missing.pdf")
