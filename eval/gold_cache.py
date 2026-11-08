"""Compute every gold query's result once and save it to disk.

Gold answers never change, so re-running gold queries on every eval run is wasted time
(two dev ones take 35s and 200s on this laptop). We run them once and load the saved results.

dev   (1,534 questions): stores the actual rows. Small, and Phase 1 tools print them.
train (9,428 questions): stores a Fingerprint per question (eval/compare.py), NOT the rows.
      Some train gold queries return millions of rows; keeping all rows in RAM reached 26 GB and
      the process was killed by the OS. A fingerprint is ~100 bytes and still answers the only
      question we need ("does a prediction return the same set of rows?").
      Train runs 4 queries in parallel (separate processes) and saves a checkpoint every 500
      questions, so a crash or Ctrl-C resumes where it stopped instead of starting over.

Run once per split: `uv run python -m eval.gold_cache dev` / `... train`
"""
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from eval.data import BIRD_ROOT, load_split
from eval.executor import execute, execute_fingerprint

GOLD_TIMEOUT_S = 600.0     # dev: the slowest gold query needs 207s
TRAIN_TIMEOUT_S = 60.0     # train is only for tuning; a gold query slower than this is skipped
TRAIN_WORKERS = 4          # each worker holds at most one query's row hashes in memory
CHECKPOINT_EVERY = 500


def cache_path(split: str) -> Path:
    return BIRD_ROOT / split / "gold_results.pkl"  # lives under data/, so it's gitignored


CACHE_PATH = cache_path("dev")  # kept for Phase 1 code that imports it


def _save(obj, path: Path) -> None:
    """Write to a temp file, then rename: a crash mid-write can't leave a half-written cache."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(obj, f)
    os.replace(tmp, path)


def build_dev_cache() -> None:
    examples = load_split("dev")
    cache = {}
    for i, ex in enumerate(examples, 1):
        r = execute(ex.db_path, ex.gold_sql, timeout_s=GOLD_TIMEOUT_S)
        if not r.ok:
            raise RuntimeError(f"gold SQL failed for question {ex.question_id}: {r.error}")
        cache[ex.question_id] = r.rows
        if i % 250 == 0:
            print(f"  {i}/{len(examples)}", flush=True)
    _save(cache, cache_path("dev"))
    print(f"saved {len(cache)} gold results to {cache_path('dev')}")


def _train_one(args):
    """Runs in a worker process: (question_id, Fingerprint or None, error)."""
    qid, db_path, sql = args
    fp, error, _ = execute_fingerprint(db_path, sql, timeout_s=TRAIN_TIMEOUT_S)
    return qid, fp, error


def build_train_cache() -> None:
    path = cache_path("train")
    partial = path.with_suffix(".partial.pkl")
    done = {}
    if partial.exists():  # resume after a crash
        with open(partial, "rb") as f:
            done = pickle.load(f)
        print(f"resuming: {len(done)} already done")

    todo = [(ex.question_id, ex.db_path, ex.gold_sql) for ex in load_split("train") if ex.question_id not in done]
    errors = sum(v is None for v in done.values())
    with ProcessPoolExecutor(max_workers=TRAIN_WORKERS) as pool:
        # map() with chunksize sends work in small batches; results come back in order
        for i, (qid, fp, error) in enumerate(pool.map(_train_one, todo, chunksize=8), 1):
            done[qid] = fp
            errors += fp is None
            if i % CHECKPOINT_EVERY == 0:
                _save(done, partial)
                print(f"  {len(done)}/{len(done) + len(todo) - i} done ({errors} failed)", flush=True)

    _save(done, path)
    partial.unlink(missing_ok=True)
    print(f"saved {len(done)} gold fingerprints to {path} ({errors} gold queries failed or timed out)")


def build_cache(split: str = "dev") -> None:
    build_dev_cache() if split == "dev" else build_train_cache()


def load_gold(split: str = "dev") -> dict:
    """dev: {question_id: rows}. train: {question_id: Fingerprint or None (gold failed)}."""
    if not cache_path(split).exists():
        build_cache(split)
    with open(cache_path(split), "rb") as f:
        return pickle.load(f)


if __name__ == "__main__":
    build_cache(sys.argv[1] if len(sys.argv) > 1 else "dev")
