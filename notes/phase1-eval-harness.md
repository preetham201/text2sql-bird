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
| 5. Prompt + model client | `generation/cost.py`, `prompt.py`, `claude_client.py` | ✅ |
| 6. Runner + metrics | `eval/run.py`, `eval/metrics.py` | ✅ |
| 7. Baseline run + checks | `eval/official_check.py` | ✅ **68.38% EX** |
| 8. Failure taxonomy | `eval/failure_sample.py`, `results/failure_labels_baseline.csv` | ✅ 40 labelled |

Model choice is explained separately in [phase1-model-choice.md](phase1-model-choice.md).

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

## Step 5 — Prompt + model client (`generation/`)

### `generation/cost.py`
**Job:** tokens to dollars. One price table (`PRICES`), one function `cost_usd()`.
- Prices are **per 1M tokens**, so: `tokens * price / 1_000_000`.
- Four token kinds: normal input, output (includes thinking), cache write (1.25× input), cache read (~0.1× input).
- An unknown model raises `KeyError` on purpose: a guessed price would silently corrupt every cost number.

### `generation/prompt.py`
**Job:** build `(system, user)` for one question.
- **system** = instructions + full schema DDL. Same for every question on one database, so it's the cacheable part.
- **user** = `Hint: <evidence>` + `Question: ...`. The hint line is skipped when evidence is empty.
- `schema_ddl()` reads the original `CREATE TABLE` text from SQLite's built-in `sqlite_master` table, so there's no hand-built DDL to get wrong.
- `@lru_cache` remembers a function's result per argument: 11 schemas are read once instead of 1,534 times.

### `generation/claude_client.py`
**Job:** one API call, then a `Generation` with the SQL, tokens, cost, time and any error.
- `output_config={"effort": effort}`: how much the model thinks. Set explicitly so runs don't change if the API default does.
- `cache_control: ephemeral` on the system block turns on **prompt caching**, which changes cost only, never the answer.
- `extract_sql()`: a regex takes the **last** ```sql block (`.*?` = shortest match, `re.DOTALL` = `.` also matches newlines). If there are no fences, it uses the whole text.
- **Never raises:** API errors (after the SDK's own 2 retries), refusals and `max_tokens` cut-offs come back in `error`. One bad call must not kill a 1,534-question run.
- `response.content` is a list of blocks (thinking, text); we join only the `text` blocks.

---

## Step 6 — Runner + metrics (`eval/run.py`, `eval/metrics.py`)

**Flow per question (`run_one`)**: build prompt → generate → `score_sql` (execute with 30s limit, compare to cached gold) → one CSV row + one raw JSONL record.

- **Threads (`ThreadPoolExecutor`)**: API calls mostly wait on the network, so 4 in flight ≈ 4× faster. `as_completed` hands back results in finishing order; we sort by `question_id` before saving.
- **Budget stop (`--max-cost`)**: running total of cost; above the cap → `pool.shutdown(cancel_futures=True)` drops questions not yet started.
- **`--rescore`**: re-executes the saved SQL with no API calls. This is our reproducibility check.
- **Outputs**: `results/<run>.csv` (committed evidence), `_summary.json` (metrics + settings), `results/raw/<run>.jsonl` (full prompts/answers, gitignored).

**Metrics (`summarize`)**
- **EX%**: share of questions where result sets match.
- **Valid-SQL %**: share that executed without error (right or wrong).
- **p50 / p95 latency**: median, and the time 95% of questions beat (the slow tail users notice). Latency = API call + SQL execution.
- **Prompt tokens** = uncached + cache-write + cache-read (the API reports them separately).
- **Cost per 1k** = total cost / n × 1000, which is comparable across runs of different sizes.

---

## Step 7 — Baseline result (row 1)

Config: `claude-sonnet-5-5`, effort `low`, full schema DDL + evidence + question, prompt caching on, 30s timeout.

| Metric | Value |
|---|---|
| **EX** | **68.38%** (1,049 / 1,534) |
| simple / moderate / challenging | 74.59% / 59.27% / 57.93% (n = 925 / 464 / 145) |
| Valid SQL | 100.00% |
| Latency p50 / p95 | 2.08s / 4.46s |
| Prompt tokens / question | 1,669 (94% read from cache) |
| Output tokens / question | 121 |
| Cost | $2.64 total, **$1.72 per 1k questions** |
| Wall time | 15.5 min, 4 workers |

**Checks**
- **Reproducible:** `--rescore` on the saved SQL → 68.38% again, 0 questions changed.
- **Matches the official scorer:** `eval/official_check.py` copies the official `execute_sql()` line for line (but read-only). On 200 sampled questions, 200/200 verdicts agree.
- Found while reading the official script: it opens the DB **read-write** (a generated `DROP` would damage the data), and its 30s timeout covers pred + gold together, which is why Q518/Q701 can never score there.

---

## Step 8 — Failure taxonomy (40 of 485 failures, seed 0)

| Category | Count | Meaning |
|---|---|---|
| gold_questionable | 12 (30%) | the gold SQL contradicts the question or hint (e.g. counts lab rows when asked "how many patients") |
| output_format | 9 (22%) | right logic, but extra / missing / reordered columns |
| ambiguity | 4 (10%) | question supports more than one reasonable reading |
| wrong_join | 3 (8%) | missing/extra join or join type that changes row counts |
| filter_value | 3 (8%) | wrong literal or extra filter (incl. a hint with a typo) |
| wrong_table | 3 (8%) | right idea, value taken from the wrong table |
| aggregation | 2 (5%) | wrong numerator/denominator or aggregate |
| superlative_ties | 2 (5%) | `= (SELECT MAX…)` returns all ties; gold `ORDER BY … LIMIT 1` returns one |
| wrong_column | 2 (5%) | wrong column for the asked attribute |
| syntax | 0 | valid SQL was 100% |

We added three categories to the planned list (gold_questionable, output_format, superlative_ties) because they kept showing up and fit none of the originals.

**What this means for later phases (the important part)**
1. **About half of "failures" aren't really model errors** (questionable gold + output format = 52%). BIRD's gold SQL has known noise. Real headroom is smaller than 100 − 68.
2. **Zero syntax/runtime errors.** Phase 3's error-feedback retry loop can't recover anything for Sonnet on this prompt. That's a valid result for H2: the retry loop is for the small model in Phase 4, not this one.
3. **Output format is cheap to fix** with a prompt rule ("return only the columns asked, in the order mentioned"). But tuning prompts on dev is exactly the overfitting trap, so any such change must be chosen on a held-out slice.
4. Only ~8% are wrong_table. Schema retrieval (Phase 2) mainly has to **not lose** needed tables; it's unlikely to add much accuracy on these small schemas. Its win should be cost/tokens.

---

## Interview questions
1. *How do you know your eval harness is correct?* — Gold-as-prediction must score 100%; we found 2 queries slower than the official timeout and documented the 99.87% ceiling.
2. *How do you enforce a timeout in SQLite?* — Progress handler callback that aborts after a deadline.
3. *What's the real security boundary for executing generated SQL?* — Database permissions (read-only connection/role), not parsing the SQL text.
4. *What are the weaknesses of BIRD's execution accuracy?* — Ignores row order and duplicates; penalizes extra columns; exact float match.
5. *Why cache gold results?* — Deterministic, expensive to recompute; makes every eval run faster.
6. *What's your baseline and how much did it cost?* — Sonnet 5.5, full schema: 68.38% EX, $1.72 per 1k questions, p95 4.5s.
7. *How did you verify your scorer matches the official one?* — Copied the official compare function, ran it on 200 sampled predictions: 200/200 agree.
8. *What do your baseline's failures look like?* — Zero syntax errors; about half are gold-label noise or column-format mismatches, not reasoning errors.
9. *Why don't you just fix the output-format failures with the prompt?* — Tuning on the reporting set is overfitting; it has to be selected on a held-out slice.
