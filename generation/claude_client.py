"""Call Claude, pull the SQL out of its answer, and record tokens, cost and latency."""
import re
import time
from dataclasses import dataclass

import anthropic
from dotenv import load_dotenv

from generation.cost import cost_usd

load_dotenv()  # puts ANTHROPIC_API_KEY from .env into the environment
_client = anthropic.Anthropic()  # one client for the whole run (it reuses connections)

# Matches a ```sql ... ``` block. (.*?) is "as little as possible", so two blocks don't merge.
# re.DOTALL lets "." match newlines, because SQL usually spans several lines.
SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass
class Generation:
    sql: str                  # extracted SQL ("" if the call failed)
    raw_text: str             # the model's full text answer, kept for debugging
    error: str | None         # API error or refusal, None if the call worked
    input_tokens: int
    output_tokens: int        # includes thinking tokens (billed as output)
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: float
    seconds: float            # wall-clock time of the API call
    stop_reason: str | None


def extract_sql(text: str) -> str:
    """Take the last ```sql block. If the model forgot the fences, use the whole answer."""
    blocks = SQL_BLOCK.findall(text)
    sql = blocks[-1] if blocks else text
    return sql.strip().rstrip(";").strip()


def generate_sql(system: str, user: str, model: str, effort: str, max_tokens: int = 16000) -> Generation:
    """Ask `model` for SQL. Never raises: API failures come back as a Generation with an error."""
    start = time.perf_counter()
    try:
        response = _client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # Prompt caching: the system prompt (instructions + schema) is the same for every
            # question on one database. Marking it lets the API reuse it for ~10% of the input
            # price on later calls (within 5 minutes). It changes cost only, never the answer.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            # effort controls how much the model thinks before answering (and so the cost).
            # We set it explicitly so a run is reproducible even if the API default changes.
            output_config={"effort": effort},
        )
    except anthropic.APIError as e:  # the SDK already retried 429/5xx twice before we get here
        return Generation("", "", f"{type(e).__name__}: {e}", 0, 0, 0, 0, 0.0,
                          time.perf_counter() - start, None)
    seconds = time.perf_counter() - start

    # response.content is a list of blocks (thinking, text, ...). We only want the text.
    text = "".join(b.text for b in response.content if b.type == "text")

    u = response.usage
    cache_write = u.cache_creation_input_tokens or 0  # these can be None when caching is unused
    cache_read = u.cache_read_input_tokens or 0
    cost = cost_usd(model, u.input_tokens, u.output_tokens, cache_write, cache_read)

    error = None
    if response.stop_reason == "refusal":
        error = "refusal"
    elif response.stop_reason == "max_tokens":
        error = "hit max_tokens"  # answer was cut off; the SQL is probably incomplete

    return Generation(extract_sql(text) if text else "", text, error, u.input_tokens, u.output_tokens,
                      cache_write, cache_read, cost, seconds, response.stop_reason)


if __name__ == "__main__":
    # Self-check: `uv run python -m generation.claude_client`  (makes ONE cheap Haiku call)
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
    assert extract_sql("```sql\nSELECT 1\n```\nfixed:\n```sql\nSELECT 3\n```") == "SELECT 3"
    print("extract_sql checks passed")

    from eval.data import load_dev
    from generation.prompt import build_prompt
    ex = load_dev(limit=1)[0]
    g = generate_sql(*build_prompt(ex), model="claude-haiku-5-5", effort="low")
    print(g.sql)
    print(f"in={g.input_tokens} out={g.output_tokens} cost=${g.cost_usd:.6f} time={g.seconds:.2f}s error={g.error}")
