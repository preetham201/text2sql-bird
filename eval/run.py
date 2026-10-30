"""Run a model on BIRD dev: generate SQL -> execute -> compare with gold -> save results.

Examples:
  uv run python -m eval.run --model claude-haiku-5-5 --limit 20        # cheap test
  uv run python -m eval.run --model claude-sonnet-5-5 --max-cost 5     # full dev, stops at $5
  uv run python -m eval.run --rescore results/<run>.csv                # re-execute saved SQL, no API calls

Outputs (run name = model_effort_timestamp unless --name is given):
  results/<run>.csv            one row per question (committed: this is the evidence)
  results/<run>_summary.json   headline metrics + the settings used
  results/raw/<run>.jsonl      full model answers (gitignored, for debugging)
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from eval.compare import results_match
from eval.data import Example, load_dev
from eval.executor import execute
from eval.gold_cache import load_gold
from eval.metrics import by_difficulty, format_summary, summarize
from generation.claude_client import generate_sql
from generation.prompt import build_prompt

RESULTS = Path(__file__).resolve().parent.parent / "results"


def score_sql(ex: Example, sql: str, gold_rows: list[tuple]) -> dict:
    """Execute predicted SQL and compare with gold. Shared by normal runs and --rescore."""
    if not sql:  # generation failed: nothing to run
        return {"exec_ok": False, "exec_error": "no SQL generated", "exec_seconds": 0.0, "correct": False}
    r = execute(ex.db_path, sql)
    return {
        "exec_ok": r.ok,
        "exec_error": r.error,
        "exec_seconds": r.seconds,
        "correct": r.ok and results_match(r.rows, gold_rows),
    }


def run_one(ex: Example, gold_rows: list[tuple], model: str, effort: str) -> tuple[dict, dict]:
    """Everything for one question. Returns (row for the CSV, raw record for the JSONL)."""
    system, user = build_prompt(ex)
    g = generate_sql(system, user, model=model, effort=effort)
    scored = score_sql(ex, g.sql, gold_rows)

    row = {
        "question_id": ex.question_id,
        "db_id": ex.db_id,
        "difficulty": ex.difficulty,
        "correct": scored["correct"],
        "exec_ok": scored["exec_ok"],
        "exec_error": scored["exec_error"],
        "gen_error": g.error,
        "pred_sql": g.sql,
        "gold_sql": ex.gold_sql,
        "input_tokens": g.input_tokens,
        "output_tokens": g.output_tokens,
        "cache_write_tokens": g.cache_write_tokens,
        "cache_read_tokens": g.cache_read_tokens,
        "cost_usd": g.cost_usd,
        "gen_seconds": g.seconds,
        "exec_seconds": scored["exec_seconds"],
        "total_seconds": g.seconds + scored["exec_seconds"],
    }
    raw = {"question_id": ex.question_id, "system": system, "user": user, **asdict(g)}
    return row, raw


def run(model: str, effort: str, limit: int | None, workers: int, max_cost: float, name: str | None) -> None:
    examples = load_dev(limit=limit)
    gold = load_gold()
    run_name = name or f"{model}_{effort}_{datetime.now():%Y%m%d-%H%M%S}"
    print(f"run {run_name}: {len(examples)} questions, {workers} workers, stop at ${max_cost:.2f}")

    rows, raws, spent, stopped_early = [], [], 0.0, False
    start = time.perf_counter()

    # API calls spend most of their time waiting on the network, so threads let several
    # questions be in flight at once. Each thread opens its own SQLite connection.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one, ex, gold[ex.question_id], model, effort) for ex in examples]
        for i, fut in enumerate(as_completed(futures), 1):  # as_completed: in finishing order
            row, raw = fut.result()
            rows.append(row)
            raws.append(raw)
            spent += row["cost_usd"]
            if i % 50 == 0 or i == len(examples):
                ex_so_far = 100 * sum(r["correct"] for r in rows) / len(rows)
                print(f"  {i}/{len(examples)}  EX so far {ex_so_far:.1f}%  spent ${spent:.4f}")
            if spent > max_cost:
                print(f"  budget ${max_cost:.2f} exceeded at ${spent:.4f}: stopping (partial run)")
                stopped_early = True
                pool.shutdown(cancel_futures=True)  # drop questions that haven't started yet
                break

    df = pd.DataFrame(rows).sort_values("question_id").reset_index(drop=True)
    settings = {"model": model, "effort": effort, "limit": limit, "workers": workers,
                "stopped_early": stopped_early, "wall_seconds": time.perf_counter() - start,
                "prompt": "full schema DDL + evidence + question"}
    save(run_name, df, settings, raws)


def rescore(csv_path: Path) -> None:
    """Re-execute the saved predicted SQL (no API calls). Should reproduce the same EX%."""
    df = pd.read_csv(csv_path, keep_default_na=False, na_values=[""])
    gold = load_gold()
    examples = {ex.question_id: ex for ex in load_dev()}
    saved_correct = df["correct"].copy()

    for i, row in df.iterrows():
        sql = row["pred_sql"] if isinstance(row["pred_sql"], str) else ""
        scored = score_sql(examples[row["question_id"]], sql, gold[row["question_id"]])
        df.loc[i, "correct"] = scored["correct"]
        df.loc[i, "exec_ok"] = scored["exec_ok"]

    changed = int((df["correct"] != saved_correct).sum())
    print(f"saved EX {100 * saved_correct.mean():.2f}%  ->  re-executed EX {100 * df['correct'].mean():.2f}%"
          f"  ({changed} questions changed)")


def save(run_name: str, df: pd.DataFrame, settings: dict, raws: list[dict]) -> None:
    (RESULTS / "raw").mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULTS / f"{run_name}.csv", index=False)

    summary = {"run": run_name, **settings, **summarize(df),
               "by_difficulty": by_difficulty(df).round(2).to_dict(orient="index")}
    with open(RESULTS / f"{run_name}_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=float)  # default=float handles numpy numbers

    with open(RESULTS / "raw" / f"{run_name}.jsonl", "w") as f:
        for r in sorted(raws, key=lambda r: r["question_id"]):
            f.write(json.dumps(r) + "\n")  # JSONL = one JSON object per line

    print("\n" + format_summary(summarize(df)))
    print(by_difficulty(df).round(2).to_string())
    print(f"\nsaved results/{run_name}.csv and _summary.json")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="claude-haiku-5-5")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--limit", type=int, default=None, help="only the first N questions")
    p.add_argument("--workers", type=int, default=4, help="parallel API calls")
    p.add_argument("--max-cost", type=float, default=1.0, help="stop once this many $ are spent")
    p.add_argument("--name", default=None, help="run name (default: model_effort_timestamp)")
    p.add_argument("--rescore", type=Path, default=None, help="re-execute SQL from a saved results CSV")
    args = p.parse_args()

    if args.rescore:
        rescore(args.rescore)
    else:
        run(args.model, args.effort, args.limit, args.workers, args.max_cost, args.name)


if __name__ == "__main__":
    main()
