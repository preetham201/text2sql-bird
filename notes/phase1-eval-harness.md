# Phase 1 — Eval harness + frontier baseline (revision notes)

**Goal:** a harness that runs a model over BIRD dev and reports EX%, valid-SQL rate, p50/p95 latency, and cost per 1k queries — then the first ablation row (frontier model, full schema).

**Why first:** every later component (retrieval, guardrails, routing, fine-tuning) is judged by this harness. Without it you have opinions, not results. A bug here silently corrupts every number after it.

## Progress
| Step | File | Status |
|---|---|---|
| 1. Loader | `eval/data.py` | ✅ |
| 2. Executor | `eval/executor.py` | ✅ |
| 3. Comparator | `eval/compare.py` | ✅ |
| 4. Gold cache + validation | `eval/gold_cache.py`, `eval/validate_harness.py` | ✅ 100% |
| 5. Prompt + model client | `generation/` | ⬜ |
| 6. Runner + metrics | `eval/run.py` | ⬜ |
| 7. Baseline run | — | ⬜ |

---

## Step 1 — Loader (`eval/data.py`)

**Job:** turn `dev.json` (a list of dicts) into a list of `Example` objects.

**Walkthrough**
- `BIRD_DEV = Path(__file__).resolve().parent.parent / "data" / "bird" / "dev"`
  - `__file__` = path of this file (`eval/data.py`). `.parent` → `eval/`, `.parent` again → project root.
  - `/` on `Path` objects joins folders. Result: the dev data folder, wherever you run from.
- `@dataclass class Example:` — a class whose fields are declared with types; Python auto-generates `__init__`, so `Example(question_id=0, db_id=..., ...)` just works. Access with dots: `ex.db_id`.
- `@property def db_path(self)` — a computed attribute. `ex.db_path` (no parentheses) builds `dev_databases/<db_id>/<db_id>.sqlite`. `self` = this particular example.
- `load_dev(limit=None)`:
  1. `with open(...) as f: records = json.load(f)` — read JSON text into Python lists/dicts; `with` closes the file automatically.
  2. Loop over records, build an `Example` from each, append to a list. Note `gold_sql=r["SQL"]` — renaming BIRD's key.
  3. `examples[:limit]` — slicing keeps the first `limit` items. `None` means "all".
- `if __name__ == "__main__":` — code that runs only when you execute this file directly (`uv run python -m eval.data`), not when another file imports it. We use it for quick self-checks.

**Why a dataclass, not dicts:** typos fail loudly (`ex.gold_sq` → error), awkward keys renamed in one place, and derived values (`db_path`) live with the data.

---

## Step 2 — Executor (`eval/executor.py`)

**Job:** run any SQL on a database and return either rows or an error — **never crash**, because model-generated SQL is often broken.

**Walkthrough**
- `ExecResult` dataclass: `ok`, `rows`, `error`, `seconds`. One return type for success and failure, so callers just check `.ok`.
- `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` — opens **read-only**. Verified: `DROP TABLE frpm` → `attempt to write a readonly database`.
- **Timeout via progress handler:** SQLite has no query timeout. `con.set_progress_handler(fn, 10_000)` makes SQLite call `fn` every 10,000 internal steps; if `fn` returns non-zero, SQLite aborts with "interrupted". Our `fn` returns `1` once the deadline has passed.
  - `lambda: int(time.perf_counter() > deadline)` — a tiny inline function; `True/False` → `1/0`.
- `try / except / finally`:
  - `try`: run and `fetchall()` rows.
  - `except sqlite3.OperationalError`: SQL errors (no such column, syntax error, interrupted). Rename "interrupted" to "timeout".
  - `except Exception`: anything else — still just a failed prediction, not a crash.
  - `finally`: always close the connection, success or failure.
- `time.perf_counter()` — high-precision clock for measuring durations.

**Key idea — validation ≠ security:** the read-only connection is the real safety boundary (the database *cannot* be modified). Checking SQL text (Phase 3, sqlglot) is a cheap early filter for mistakes, not security.

---

## Step 3 — Comparator (`eval/compare.py`)

**Job:** decide if predicted rows == gold rows.

`return set(pred_rows) == set(gold_rows)` — BIRD's official rule, copied exactly so our numbers are comparable to published ones.

| Behaviour | Example | Result |
|---|---|---|
| Row order ignored | `[(1,),(2,)]` vs `[(2,),(1,)]` | match |
| Duplicates ignored | `[(1,),(1,)]` vs `[(1,)]` | match |
| Column order matters | `[(1,"a")]` vs `[("a",1)]` | no match |
| No float rounding | `1.0` vs `1.0000001` | no match |
| int vs float | `1` vs `1.0` | match (Python: `1 == 1.0`) |

**Known quirks (good interview material):**
- Ignoring order means a query missing `ORDER BY` can still be "correct" when the question asked for a ranking.
- Ignoring duplicates means `COUNT` mistakes hidden in repeated rows can slip through.
- Extra columns → wrong, even if the needed column is there. Models get penalized for returning `name, id` when gold returns `name`.
- We keep the official rule anyway: comparability with published numbers matters more than "fixing" the metric. Note quirks in the writeup.

---

## Step 4 — Gold cache + harness validation

### `eval/gold_cache.py`
**Job:** run all 1,534 gold queries **once**, save results to `data/bird/dev/gold_results.pkl`.
- Why: gold answers never change; re-running them each eval wastes minutes (two take 36s and 207s).
- `pickle` saves Python objects (dict of lists of tuples) to a file exactly as they are.
- `load_gold()` builds the cache if missing, then loads it → `{question_id: rows}`.
- Raises an error if any gold query fails — a broken gold query would silently mark every prediction wrong.

### `eval/validate_harness.py`
**Job:** feed gold SQL as the prediction. Must score 100%.

**Result:** 1534/1534 = **100.00%** ✅

**What we found:** 2 gold queries exceed BIRD's official 30s timeout on this laptop:
- Q518 (card_games): 36s
- Q701 (codebase_community): 207s

**Decision:** model predictions keep the official 30s limit (comparable to published numbers). Max achievable EX at 30s on this machine = **99.87%**. Report this in the README.

**Why this check matters:** if the harness had a bug (wrong path, bad comparison, timeout too low), every number in the project would be wrong and you'd never know. Debugging a model against a broken harness is the classic eval trap.

---

## Interview questions
1. *How do you know your eval harness is correct?* — Gold-as-prediction must score 100%; we found 2 queries slower than the official timeout and documented the 99.87% ceiling.
2. *How do you enforce a timeout in SQLite?* — Progress handler callback that aborts after a deadline.
3. *What's the real security boundary for executing generated SQL?* — Database permissions (read-only connection/role), not parsing the SQL text.
4. *What are the weaknesses of BIRD's execution accuracy?* — Ignores row order and duplicates; penalizes extra columns; exact float match.
5. *Why cache gold results?* — Deterministic, expensive to recompute; makes every eval run faster.
