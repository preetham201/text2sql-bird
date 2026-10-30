"""Print a fixed random sample of failed questions, for labelling with the failure taxonomy.

For each failure it shows the question, hint, predicted SQL, gold SQL, and the first rows each
returns. Labels go by hand into results/failure_labels_<run>.csv (question_id, category, note).

Run: `uv run python -m eval.failure_sample results/<run>.csv --n 40 > failures.txt`
"""
import argparse
import random
from pathlib import Path

import pandas as pd

from eval.data import load_dev
from eval.executor import execute
from eval.gold_cache import load_gold


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("csv", type=Path)
    p.add_argument("--n", type=int, default=40)
    args = p.parse_args()

    df = pd.read_csv(args.csv, keep_default_na=False, na_values=[""])
    examples = {ex.question_id: ex for ex in load_dev()}
    gold = load_gold()
    failed = df[~df["correct"]]
    print(f"{len(failed)} failures, {int((~failed['exec_ok']).sum())} of them raised an error")

    random.seed(0)  # same seed -> same sample, so labels stay tied to the right questions
    for qid in sorted(random.sample(list(failed["question_id"]), min(args.n, len(failed)))):
        ex = examples[qid]
        row = failed[failed["question_id"] == qid].iloc[0]
        pred = execute(ex.db_path, row["pred_sql"])
        print(f"\n### Q{qid} [{ex.db_id}/{ex.difficulty}]")
        print(f"Q: {ex.question}\nHINT: {ex.evidence}\nPRED: {row['pred_sql']}\nGOLD: {ex.gold_sql}")
        print("PRED_ROWS:", pred.rows[:4] if pred.ok else pred.error, f"(n={len(pred.rows) if pred.ok else 0})")
        print("GOLD_ROWS:", gold[qid][:4], f"(n={len(gold[qid])})")


if __name__ == "__main__":
    main()
