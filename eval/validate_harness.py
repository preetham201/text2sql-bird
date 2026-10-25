"""Sanity check: feed the gold SQL in as the prediction. The harness must score 100%.

If it doesn't, the bug is in the harness (executor/comparison), not in any model,
and every EX% number we report later would be wrong.

Two gold queries take longer than the official 30s limit on this laptop (35s, 200s),
so we validate the logic with a generous timeout and separately report how many
gold queries would pass at the official 30s limit.

Run: `uv run python -m eval.validate_harness`
"""
from eval.compare import results_match
from eval.data import load_dev
from eval.executor import DEFAULT_TIMEOUT_S, execute
from eval.gold_cache import load_gold

VALIDATION_TIMEOUT_S = 600.0


def main() -> None:
    examples = load_dev()
    gold = load_gold()
    correct, over_limit, failures = 0, [], []

    for i, ex in enumerate(examples, 1):
        pred = execute(ex.db_path, ex.gold_sql, timeout_s=VALIDATION_TIMEOUT_S)

        if pred.ok and results_match(pred.rows, gold[ex.question_id]):
            correct += 1
        else:
            failures.append((ex.question_id, pred.error or "result mismatch"))

        if pred.seconds > DEFAULT_TIMEOUT_S:
            over_limit.append((ex.question_id, pred.seconds))

        if i % 250 == 0:
            print(f"  {i}/{len(examples)}")

    n = len(examples)
    print(f"\nEX with gold-as-prediction: {correct}/{n} = {100 * correct / n:.2f}%  (must be 100%)")
    for qid, err in failures:
        print(f"  FAILED question {qid}: {err}")
    print(f"Gold queries slower than the {DEFAULT_TIMEOUT_S:.0f}s limit: {len(over_limit)}"
          f"  -> max achievable EX at that limit on this machine: {100 * (n - len(over_limit)) / n:.2f}%")
    for qid, secs in over_limit:
        print(f"  question {qid}: {secs:.1f}s")


if __name__ == "__main__":
    main()
