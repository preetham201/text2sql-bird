"""Compute every gold query's result once and save it to disk.

Gold answers never change, so re-running 1,534 gold queries on every eval run is wasted
time (two of them take 35s and 200s on this laptop). We run them once with a generous
timeout and load the saved results afterwards.

Run once: `uv run python -m eval.gold_cache`
"""
import pickle

from eval.data import BIRD_DEV, load_dev
from eval.executor import execute

CACHE_PATH = BIRD_DEV / "gold_results.pkl"  # lives under data/, so it's gitignored
GOLD_TIMEOUT_S = 600.0


def build_cache() -> None:
    examples = load_dev()
    cache = {}
    for i, ex in enumerate(examples, 1):
        r = execute(ex.db_path, ex.gold_sql, timeout_s=GOLD_TIMEOUT_S)
        if not r.ok:
            raise RuntimeError(f"gold SQL failed for question {ex.question_id}: {r.error}")
        cache[ex.question_id] = r.rows
        if i % 250 == 0:
            print(f"  {i}/{len(examples)}")

    # pickle saves Python objects (here: a dict of lists of tuples) exactly as they are
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(cache, f)
    print(f"saved {len(cache)} gold results to {CACHE_PATH}")


def load_gold() -> dict[int, list[tuple]]:
    """Return {question_id: gold rows}. Build the cache first if it's missing."""
    if not CACHE_PATH.exists():
        build_cache()
    with open(CACHE_PATH, "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    build_cache()
