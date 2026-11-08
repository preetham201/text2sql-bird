"""Decide whether a predicted result matches the gold result (execution accuracy)."""
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass


def results_match(pred_rows: list[tuple], gold_rows: list[tuple]) -> bool:
    """BIRD's official rule: compare the results as SETS of rows.

    - Row order is ignored     ([(1,), (2,)] == [(2,), (1,)])
    - Duplicate rows are ignored ([(1,), (1,)] == [(1,)])
    - Values must match exactly, including column order within a row

    We copy the official rule exactly so our EX% is comparable to published BIRD numbers,
    even though it has known quirks (see notes/phase1-eval-harness.md).
    """
    return set(pred_rows) == set(gold_rows)


# ---------- memory-light version for big result sets (train gold cache) ----------
# Storing every gold result as rows needs the rows in RAM: on BIRD train some gold queries return
# millions of rows, and caching them all used 26 GB and got the process killed. Instead we keep a
# FINGERPRINT of the result *set*: (number of distinct rows, order-independent hash of them).
# Two results match under the official set rule exactly when their fingerprints match
# (up to a ~1-in-10^19 hash collision).

def _norm(v):
    """Python treats 1 == 1.0, so the set rule does too; make their hashes equal as well."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def row_hash(row: tuple) -> int:
    """Stable 64-bit hash of one row (Python's built-in hash() changes between runs; this doesn't)."""
    return int.from_bytes(hashlib.blake2b(repr(tuple(_norm(v) for v in row)).encode(), digest_size=8).digest(), "big")


@dataclass(frozen=True)
class Fingerprint:
    n_distinct: int
    digest: int                      # sum of distinct row hashes mod 2^64: order doesn't matter
    sample: tuple | None = None      # first rows, kept only for small results (for debugging/printing)


def fingerprint(rows: Iterable[tuple], keep_sample: int = 20) -> Fingerprint:
    """Fingerprint a result, consuming `rows` one at a time (works on a streaming cursor).

    Memory: one 64-bit hash per DISTINCT row (~70 bytes in a Python set), never the rows themselves.
    """
    hashes: set[int] = set()
    sample = []
    for row in rows:
        hashes.add(row_hash(row))
        if len(sample) < keep_sample:
            sample.append(row)
    small = len(hashes) <= keep_sample
    return Fingerprint(len(hashes), sum(hashes) % (1 << 64), tuple(sample) if small else None)


def matches(pred_rows: list[tuple], gold: "list[tuple] | Fingerprint") -> bool:
    """results_match that also accepts a gold Fingerprint (train) instead of gold rows (dev)."""
    if isinstance(gold, Fingerprint):
        fp = fingerprint(pred_rows, keep_sample=0)
        return (fp.n_distinct, fp.digest) == (gold.n_distinct, gold.digest)
    return results_match(pred_rows, gold)


if __name__ == "__main__":
    # Self-check: `uv run python -m eval.compare`
    assert results_match([(1,), (2,)], [(2,), (1,)])          # order ignored
    assert results_match([(1,), (1,)], [(1,)])                # duplicates ignored
    assert not results_match([(1, "a")], [("a", 1)])          # column order matters
    assert not results_match([(1.0,)], [(1.0000001,)])        # no float rounding
    assert results_match([(1,)], [(1.0,)])                    # Python treats 1 == 1.0
    # fingerprints must agree with results_match on the same cases
    fp = lambda r: fingerprint(r)
    assert (fp([(1,), (2,)]).digest, fp([(1,), (2,)]).n_distinct) == (fp([(2,), (1,)]).digest, 2)
    assert matches([(1,), (1,)], fp([(1,)]))
    assert not matches([(1, "a")], fp([("a", 1)]))
    assert not matches([(1.0,)], fp([(1.0000001,)]))
    assert matches([(1,)], fp([(1.0,)]))
    assert not matches([("1",)], fp([(1,)]))                  # text "1" != number 1, same as sets
    print("all comparison checks passed (rows and fingerprints)")
