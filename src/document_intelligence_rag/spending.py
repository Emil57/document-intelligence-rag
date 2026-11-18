"""What Claude calls cost, who pays for them, and daily limits."""

from datetime import UTC, datetime

from document_intelligence_rag import config, store

# Dollars per million tokens: (input, output), from platform.claude.com/pricing.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5-1": (10.00, 50.00),
}
# Haiku 5.5 costs more when one request's prompt is over 100,000 tokens.
LONG_PROMPT_PRICES = {"claude-haiku-5-5": (100_000, (0.50, 2.50))}
# Unknown models are priced like the most expensive one, so limits stay on the safe side.
FALLBACK_PRICE = max(PRICES.values())


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = PRICES.get(model, FALLBACK_PRICE)
    if model in LONG_PROMPT_PRICES:
        threshold, long_price = LONG_PROMPT_PRICES[model]
        if input_tokens > threshold:
            input_price, output_price = long_price
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def record(user_id: int, kind: str, model: str, input_tokens: int, output_tokens: int) -> None:
    """Save one Claude call. `kind` is "chat" or "image"."""
    store.record_usage(user_id, kind, model, input_tokens, output_tokens,
                       cost_usd(model, input_tokens, output_tokens))


class LimitReached(Exception):
    """Today's spending limit is used up."""


def start_of_today() -> str:
    """Midnight UTC, in the same format as the stored times."""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def spent_today(user_id: int | None) -> float:
    """By one user, or with None, by everyone."""
    return store.cost_since(start_of_today(), user_id)


def check(user_id: int) -> None:
    """Raise LimitReached if the user, or the app as a whole, can't spend more today.

    Two requests at the same moment can both pass, so a user can go slightly
    over. That's fine for protecting the budget.
    """
    if spent_today(user_id) >= config.USER_DAILY_LIMIT_USD:
        raise LimitReached(
            f"You've reached today's limit of ${config.USER_DAILY_LIMIT_USD:.2f}. It resets at midnight UTC."
        )
    if spent_today(None) >= config.APP_DAILY_LIMIT_USD:
        raise LimitReached("The app has reached its daily spending limit. Please try again after midnight UTC.")
