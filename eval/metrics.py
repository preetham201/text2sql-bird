"""Summarize a run's per-question results into the numbers we report.

Input: the per-question DataFrame that eval/run.py saves (one row per question).
"""
import pandas as pd


def summarize(df: pd.DataFrame) -> dict:
    """Headline metrics for one run."""
    n = len(df)
    total_cost = df["cost_usd"].sum()
    # The API splits the prompt into three counts: uncached, written to cache, read from cache.
    # Their sum is the real prompt size; the cached share shows how much caching saved.
    prompt_tokens = df["input_tokens"] + df["cache_write_tokens"] + df["cache_read_tokens"]
    return {
        "n": n,
        "ex_pct": 100 * df["correct"].mean(),                # execution accuracy
        "valid_sql_pct": 100 * df["exec_ok"].mean(),         # ran without error (right or wrong)
        "api_error_count": int(df["gen_error"].notna().sum()),
        # Latency = model call + running the SQL: what a user would actually wait.
        # p50 = median; p95 = 95% of questions were faster than this (the "slow tail").
        "latency_p50_s": df["total_seconds"].quantile(0.50),
        "latency_p95_s": df["total_seconds"].quantile(0.95),
        "prompt_tokens_mean": prompt_tokens.mean(),
        "cache_read_pct": 100 * df["cache_read_tokens"].sum() / max(prompt_tokens.sum(), 1),
        "output_tokens_mean": df["output_tokens"].mean(),
        "total_cost_usd": total_cost,
        "cost_per_1k_usd": 1000 * total_cost / n,
    }


def by_difficulty(df: pd.DataFrame) -> pd.DataFrame:
    """EX% and counts split into simple / moderate / challenging."""
    order = ["simple", "moderate", "challenging"]
    table = df.groupby("difficulty").agg(
        n=("correct", "size"),
        ex_pct=("correct", lambda s: 100 * s.mean()),
        valid_sql_pct=("exec_ok", lambda s: 100 * s.mean()),
    )
    return table.reindex([d for d in order if d in table.index])  # fixed row order


def format_summary(s: dict) -> str:
    return (f"EX: {s['ex_pct']:.2f}%  |  valid SQL: {s['valid_sql_pct']:.2f}%  |  n={s['n']}"
            f"  |  API errors: {s['api_error_count']}\n"
            f"latency p50 {s['latency_p50_s']:.2f}s  p95 {s['latency_p95_s']:.2f}s\n"
            f"tokens/question: {s['prompt_tokens_mean']:.0f} in ({s['cache_read_pct']:.0f}% read from cache),"
            f" {s['output_tokens_mean']:.0f} out\n"
            f"cost: ${s['total_cost_usd']:.4f} total  ->  ${s['cost_per_1k_usd']:.3f} per 1k questions")


if __name__ == "__main__":
    # Self-check on fake data: `uv run python -m eval.metrics`
    fake = pd.DataFrame({
        "difficulty": ["simple", "simple", "moderate", "challenging"],
        "correct": [True, False, True, False],
        "exec_ok": [True, True, True, False],
        "gen_error": [None, None, None, "refusal"],
        "total_seconds": [1.0, 2.0, 3.0, 10.0],
        "input_tokens": [1000, 1000, 1200, 1500],
        "output_tokens": [100, 120, 300, 0],
        "cache_write_tokens": [0, 0, 0, 0],
        "cache_read_tokens": [0, 0, 0, 0],
        "cost_usd": [0.001, 0.001, 0.002, 0.0],
    })
    print(format_summary(summarize(fake)))  # expect EX 50%, valid 75%, cost/1k $1.000
    print(by_difficulty(fake))
