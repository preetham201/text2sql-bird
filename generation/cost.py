"""Turn token counts into dollars.

Prices are per 1 million tokens, from Anthropic's pricing page (checked 2026-10).
If prices change, update this table: every cost number in results/ comes from here.
"""

# $ per 1M tokens. Cache reads are billed far below normal input; cache writes a bit above.
PRICES = {
    "claude-haiku-5-5":  {"input": 0.10, "output": 0.50,  "cache_write": 0.125, "cache_read": 0.01},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50,  "cache_read": 0.20},
    "claude-opus-5-5":   {"input": 4.00, "output": 20.00, "cache_write": 5.00,  "cache_read": 0.20},
}


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Dollar cost of one API call. Output tokens include any thinking tokens."""
    p = PRICES[model]  # KeyError on an unknown model is deliberate: never guess a price
    return (input_tokens * p["input"]
            + output_tokens * p["output"]
            + cache_write_tokens * p["cache_write"]
            + cache_read_tokens * p["cache_read"]) / 1_000_000


if __name__ == "__main__":
    # Self-check: `uv run python -m generation.cost`
    # 1,500 tokens in + 500 out on Haiku = 0.00015 + 0.00025 = $0.0004
    print(f"haiku 1500 in / 500 out: ${cost_usd('claude-haiku-5-5', 1500, 500):.6f}")
    print(f"sonnet same call:        ${cost_usd('claude-sonnet-5-5', 1500, 500):.6f}")
