"""Cross-check our scorer against BIRD's official evaluation script on a sample.

Official script: DAMO-ConvAI/bird/llm/src/evaluation.py. Its core is `execute_sql()`:
run predicted SQL, run gold SQL, score 1 if set(pred) == set(gold). We copy that function
line for line, with ONE change: the official one opens the database read-write
(`sqlite3.connect(db_path)`), so a generated DROP/UPDATE would modify the dev databases.
We open it read-only instead. That cannot change any score: a SELECT reads the same rows either way.

Also note: the official 30s timeout covers predicted + gold together, so Q518 and Q701
(gold alone > 30s) always score 0 there. We skip those two here; they're documented separately.

Run: `uv run python -m eval.official_check results/<run>.csv --n 200`
"""
import argparse
import random
import sqlite3
from pathlib import Path

import pandas as pd

from eval.data import load_dev

SLOW_GOLD = {518, 701}  # gold queries that exceed 30s on this machine


def official_execute_sql(predicted_sql: str, ground_truth: str, db_path: Path) -> int:
    """Copy of the official execute_sql(), except the read-only connection."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute(predicted_sql)
    predicted_res = cursor.fetchall()
    cursor.execute(ground_truth)
    ground_truth_res = cursor.fetchall()
    res = 0
    if set(predicted_res) == set(ground_truth_res):
        res = 1
    return res


def official_score(predicted_sql: str, ground_truth: str, db_path: Path) -> int:
    """The official wrapper: any exception (bad SQL, etc.) scores 0."""
    try:
        return official_execute_sql(predicted_sql, ground_truth, db_path)
    except Exception:
        return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("csv", type=Path, help="per-question results CSV from eval/run.py")
    p.add_argument("--n", type=int, default=200, help="sample size")
    args = p.parse_args()

    df = pd.read_csv(args.csv, keep_default_na=False, na_values=[""])
    examples = {ex.question_id: ex for ex in load_dev()}
    ids = [q for q in df["question_id"] if q not in SLOW_GOLD]
    random.seed(0)  # fixed seed: the same sample every time
    sample = set(random.sample(ids, min(args.n, len(ids))))

    disagree = []
    for _, row in df[df["question_id"].isin(sample)].iterrows():
        ex = examples[row["question_id"]]
        sql = row["pred_sql"] if isinstance(row["pred_sql"], str) else " "  # official uses " " for missing
        official = official_score(sql, ex.gold_sql, ex.db_path)
        if official != int(row["correct"]):
            disagree.append((row["question_id"], int(row["correct"]), official))

    print(f"checked {len(sample)} questions: {len(sample) - len(disagree)} agree, {len(disagree)} disagree")
    for qid, ours, theirs in disagree:
        print(f"  question {qid}: ours={ours} official={theirs}")


if __name__ == "__main__":
    main()
