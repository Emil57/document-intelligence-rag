import pytest

from document_intelligence_rag.spending import cost_usd


def test_cost_of_a_haiku_call():
    # 10,000 input tokens at $0.10/M plus 1,000 output tokens at $0.50/M.
    assert cost_usd("claude-haiku-5-5", 10_000, 1_000) == pytest.approx(0.0015)


def test_long_haiku_prompts_cost_more():
    assert cost_usd("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)  # $0.50/M


def test_unknown_models_are_priced_like_the_most_expensive():
    assert cost_usd("some-future-model", 1_000_000, 0) == pytest.approx(10.00)
