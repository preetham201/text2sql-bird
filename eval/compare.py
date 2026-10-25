"""Decide whether a predicted result matches the gold result (execution accuracy)."""


def results_match(pred_rows: list[tuple], gold_rows: list[tuple]) -> bool:
    """BIRD's official rule: compare the results as SETS of rows.

    - Row order is ignored     ([(1,), (2,)] == [(2,), (1,)])
    - Duplicate rows are ignored ([(1,), (1,)] == [(1,)])
    - Values must match exactly, including column order within a row

    We copy the official rule exactly so our EX% is comparable to published BIRD numbers,
    even though it has known quirks (see notes/phase1-eval-harness.md).
    """
    return set(pred_rows) == set(gold_rows)


if __name__ == "__main__":
    # Self-check: `uv run python -m eval.compare`
    assert results_match([(1,), (2,)], [(2,), (1,)])          # order ignored
    assert results_match([(1,), (1,)], [(1,)])                # duplicates ignored
    assert not results_match([(1, "a")], [("a", 1)])          # column order matters
    assert not results_match([(1.0,)], [(1.0000001,)])        # no float rounding
    assert results_match([(1,)], [(1.0,)])                    # Python treats 1 == 1.0
    print("all comparison checks passed")
