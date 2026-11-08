# Failures log: what broke, why, and what we did about it

Every real failure in the project, newest phase last. For each one: **symptom → root cause → fix →
how we keep it from happening again**. This file is also interview material: "tell me about a time
something broke" answers live here.

Severity: 🔴 crashed / lost work · 🟠 wrong numbers (silent) · 🟡 wasted time or money · ⚪ cosmetic

---

## Phase 1: eval harness + baseline

### F1 🟠 Token metric under-counted after enabling prompt caching
- **Symptom:** after turning on caching, the summary said "59 input tokens per question" (the real prompt is ~1,670).
- **Cause:** the API splits the prompt into three counters: `input_tokens` (uncached), `cache_creation_input_tokens`,
  `cache_read_input_tokens`. We only reported the first.
- **Fix:** `prompt_tokens = input + cache_write + cache_read`, plus a new `cache_read_pct` metric (`eval/metrics.py`).
- **Prevention:** when a provider adds a feature (caching), re-check what every usage field means.

### F2 🟠 `--rescore` reported the wrong "questions changed" count
- **Symptom:** the count of changed answers didn't match reality.
- **Cause:** it compared the re-executed results against a fresh re-read of the CSV, which had already been updated in place.
- **Fix:** copy the saved column first (`saved_correct = df["correct"].copy()`), compare against the copy.
- **Prevention:** never compare a mutated object with itself; snapshot before mutating.

### F3 🟡 `import pandas` failed
- **Cause:** commands ran outside the project's uv environment (the user's `ml` venv was active).
- **Fix:** always `uv run python -m ...`; set `VIRTUAL_ENV=` to silence uv's warning.

### F4 🟡 Long run's log file stayed empty mid-run
- **Cause:** Python buffers stdout when it's redirected to a file.
- **Fix:** `print(..., flush=True)` / `python -u` for long jobs; wait for the completion notice instead of polling.

### F5 🔴 (avoided) Official BIRD evaluation script opens databases read-write
- **Risk:** `sqlite3.connect(db_path)` in BIRD's `evaluation.py`: a generated `DROP TABLE` would modify the dev databases.
- **Fix:** we did not run it. `eval/official_check.py` copies its logic with a read-only connection (`?mode=ro`);
  200/200 questions agree with our scorer.
- **Prevention:** read third-party code before running it against your data.

### F6 🟠 Same settings, different EX% (95% then 90% on the same 20 questions)
- **Cause:** Sonnet 5.5 doesn't accept `temperature`, so outputs vary run to run.
- **Fix:** reproducibility = saved predictions + `--rescore` (re-executing saved SQL reproduces EX% exactly).
  Report the variance honestly in the README.

### F7 🟠 Two gold queries can never finish in 30 s
- **Cause:** Q518 (36 s) and Q701 (207 s) on this laptop; the official 30 s limit makes them unscoreable.
- **Fix:** gold cache runs gold once with a 600 s limit; documented max EX on this machine = 99.87%.

### F8 🟡 Cost estimate for Opus was too high in the notes
- **Cause:** estimated before caching was measured.
- **Fix:** corrected to $5–8 (Opus) / ~$13+ (Fable) using measured cache rates. Lesson: re-estimate after measuring.

---

## Phase 2: schema retrieval

### F9 🟡 Pickled schema cache couldn't be loaded by other modules
- **Symptom:** `AttributeError: Can't get attribute 'Column' on <module 'eval.gold_columns'>`.
- **Cause:** the cache was first written by running `retrieval/schema.py` as a script, so pickle recorded the class as
  `__main__.Column`. Any other module looking for `__main__.Column` fails.
- **Fix:** the self-check imports `load_schemas` by module name (`from retrieval.schema import ...`) before building,
  so pickle records `retrieval.schema.Column`. Deleted and rebuilt the cache.
- **Prevention:** build caches through the importable module, never from `__main__` objects.

### F10 ⚪ Hack: monkey-patched `__hash__` on a dataclass to use `lru_cache`
- **Cause:** `DbSchema` holds lists, so it isn't hashable, and `lru_cache` needs hashable arguments.
- **Fix:** replaced with an explicit dict cache keyed by `db_id`. Clearer and no surprising global side effect.

### F11 🟠 (avoided) Description CSVs in mixed encodings
- **Cause:** BIRD's `database_description/*.csv` are UTF-8, UTF-8 with BOM, or Windows-1252, varying by file.
- **Fix:** `_read_csv_any_encoding` tries `utf-8-sig` → `cp1252` → `latin-1`. Without it, descriptions would be silently
  garbled or the build would crash.

### F12 🟠 BIRD's foreign keys are incomplete
- **Symptom:** FK expansion couldn't connect `customers` and `products` in `debit_card_specializing`.
- **Cause:** `transactions_1k` declares no FKs at all; 3 train DBs declare none. BIRD's `*_tables.json` is no better (checked).
- **Fix:** `fk_expand.links()` adds inferred links (same column name, primary key on one side; generic names like `id`
  skipped). A follow-up bug: inferred links duplicated declared FKs in reverse; fixed by checking both directions.
- **Prevention:** verify dataset metadata against the data before trusting it.

### F13 🟠 First recall sweep used a biased sample
- **Cause:** `--limit 1000` takes the *first* 1,000 train questions, which come from only a few databases (the file is grouped by DB).
- **Fix:** `--sample N` = seeded random sample; final numbers use all 9,242 parseable train questions.
- **Prevention:** never use "first N" as a sample when the file is sorted by something.

### F14 🟠 (caught before the paid run) Ablation would have changed two things at once
- **Symptom:** rebuilt DDL was ~40% shorter than the original CREATE TABLE text *for the same tables*.
- **Cause:** formatting differences between `render_ddl` and SQLite's stored DDL.
- **Fix:** table-level pruning reuses the database's original CREATE TABLE text (`original_ddl`). Verified that the
  refactored Phase 1 prompt is byte-identical to the saved prompts of 200 questions.
- **Prevention:** in an ablation, diff the actual prompts, not just the code.

### F15 🟡 (caught before the paid run) Wrong assumption: "pruned prompts never repeat, so turn caching off"
- **Cause:** assumed each question gets a unique schema. Measured: 1,534 dev questions → only 253 distinct pruned prompts.
- **Fix:** caching stays on. Estimated row 2 cost: **$3.36 with caching vs $6.34 without**. The assumption would have
  nearly doubled the bill.
- **Prevention:** measure an assumption that drives cost before building on it.

### F16 🔴 Out-of-memory kill: train gold cache reached 26 GB and crashed the session
- **Symptom:** the session died; kernel log: `Out of memory: Killed process 30404 (python) ... anon-rss:26275036kB`
  at 23:38:59. A 3.3 GB, probably truncated `gold_results.pkl` was left behind. Running background jobs were lost.
- **Cause:** the cache kept **every train gold result as rows** in one dict (some BIRD train gold queries return millions
  of rows), then pickled the whole dict at the end. Memory grew with total rows across 9,428 queries. The pickle step
  briefly needs even more.
- **Fix (`eval/gold_cache.py`, `eval/compare.py`, `eval/executor.py`):**
  1. **Fingerprints instead of rows** for train: (number of distinct rows, order-independent hash = sum of 64-bit row
     hashes). Matches the official set rule exactly (1 == 1.0 normalised; tested against `results_match` on the
     edge cases). ~100 bytes per question instead of up to GBs.
  2. **Streaming:** `execute_fingerprint` reads 10,000 rows at a time with `fetchmany` and drops them. Peak memory = one
     query's row hashes, never its rows.
  3. **Checkpoint + resume** every 500 questions (`gold_results.partial.pkl`), so a crash loses minutes, not the run.
  4. **Atomic writes** (write `.tmp`, then rename): a crash can't leave a half-written cache again.
  5. 4 worker processes, so the rebuild is also faster.
  6. Deleted the corrupt 3.3 GB file.
- **Result (measured with `/usr/bin/time -v`):**

  | | v1 | v2 |
  |---|---|---|
  | peak memory | 26 GB+, killed | **383 MB** largest process (4 workers → ≤1.5 GB total) |
  | wall time | crashed after ~40 min | **8 min 5 s** |
  | cache file | 3.3 GB (corrupt) | **0.6 MB** |

  The largest train gold result has **3,077,976 distinct rows**: that one query alone is why rows can't be kept.
- **Validation:** re-scoring all 1,534 dev row-1 predictions with fingerprints instead of rows: 68.38% both ways,
  agreement on 1,534/1,534 questions.

### F16b ⚪ 352 train gold queries fail (data issue, found while rebuilding)
- **Cause:** 330 of them are in **one database, `retail_world`**: its gold SQL references tables that don't exist in
  the shipped `.sqlite` (`no such table: Order Details`, `EmployeeTerritories`, `Territories`). The rest are mostly
  wrong column names in gold SQL (`works_cycles`, `app_store`, `shooting`). Not timeouts.
- **Fix:** stored as `None` and skipped wherever train is scored (`eval/run.py` filters them out). 9,076 of 9,428 train
  questions remain usable for Phase 4/5 labels. Report it; don't "repair" BIRD's gold.
- **Prevention:** see the rules at the end of this file.

### F17 🟠 Value index was memory-heavy (same root cause, smaller scale)
- **Cause:** one dict for all 69 train DBs, with a separate `"table.column"` string stored for each of 4.9M values:
  a 254 MB pickle that needed several GB of RAM once loaded, even when a run used one database.
- **Fix (`retrieval/exact.py`):** one file per database, values map to **int column indices** (a bare int when the value
  is in one column), loaded **lazily** with an LRU cache of 4 databases, built by **iterating the cursor**, resumable per DB.
- **Result:** loading **2.09 GB → ≤93 MB** (worst case: the biggest DB, `donor`, 673k values; most DBs are far smaller);
  file size 254 MB → 126 MB (train, 69 files); build peak **165 MB**, 1 min 38 s. Recall unchanged (dev: 98.6%).

### F18 🟡 Recall sweep held more than it needed
- **Cause:** kept every question's *full* ranking (up to 455 columns) for each method and boosted variant, plus one dict per result row (~740k).
- **Fix (`eval/schema_recall.py`):** keep only the top max(K)=30 of each ranking (selection never looks deeper), compact
  tuples instead of dicts, questions grouped by database so per-DB caches stay warm.
- Also: `DbSchema.columns` and `Column.key` were recomputed on every access (thousands of times per question); now `cached_property`.
- **Result:** full train sweep (3 methods × 9,242 questions × 20 settings = 554k rows) peaks at **1.9 GB** in 89 s, and
  most of that is PyTorch + the embedding model. Runs with the cross-encoder peak at ~3 GB (the reranker model). The
  first version wasn't measured, so there's no honest "before" number; the change removes memory that grew with the
  number of columns × questions.

### F20 🟠 Retrieval results changed slightly between identical runs
- **Symptom:** re-running the sweep with no relevant code change moved some recall numbers by ±0.1 point
  (e.g. train bm25 K=10: 90.7% → 90.6%).
- **Cause:** FK expansion's BFS iterated over a Python `set` of table names. String hashes are **randomised per
  process** (`PYTHONHASHSEED`), so set iteration order changes from run to run. When two bridge paths were equally
  short, a different one won, which kept a different table.
- **Fix:** iterate neighbours (and BFS start nodes) in `sorted()` order (`retrieval/fk_expand.py`).
- **Verified:** two runs with `PYTHONHASHSEED=1` and `=2` give identical per-question results (61,360 rows).
- **Prevention:** never let output depend on set/dict-of-set iteration order; sort before iterating when order can matter.

### F19 ⚪ 186 train gold queries (2%) can't be parsed
- **Cause:** broken gold SQL in BIRD train (e.g. alias `T2` used for two different tables).
- **Fix:** excluded from train recall and counted, not guessed. Dev parses 100%.

---

## Rules we now follow (from the failures above)

1. **Never hold an unbounded result in RAM.** Stream (`fetchmany` / iterate the cursor), store fingerprints or aggregates.
2. **Big caches = one file per entity, loaded lazily**, not one giant pickle.
3. **Long jobs checkpoint and resume; writes are atomic** (tmp + rename).
4. **Estimate peak memory before a big job, and measure it** (`/usr/bin/time -v` → "Maximum resident set size").
5. **Measure assumptions that drive cost or results** before building on them (F15, F14).
6. **Samples are random and seeded**, never "first N" (F13).
7. **Read third-party code before running it on your data** (F5).
8. **Verify dataset metadata against the data** (F11, F12, F16b).
9. **Results must not depend on set iteration order**: sort when ties can matter; test with two `PYTHONHASHSEED`s (F20).
