# Phase 1 — Study guide: what to learn deeply vs. what to skim

Rule of thumb: **learn deeply the code you will edit in later phases.** For everything else,
knowing *what it does and why* is enough. Interviews ask about decisions, not plumbing.

---

## 1. Understand properly (read line by line, be able to rewrite)

| File / part | What to understand | Why it matters later |
|---|---|---|
| `eval/data.py` | `Example` dataclass, `db_path` property, `load_split()` (train has no question_id/difficulty → defaults) | Every phase loads questions with it; Phase 2/4 use train |
| `generation/prompt.py` | `schema_ddl()` (reads CREATE TABLEs from sqlite_master), `build_prompt()` → (system, user) | Phase 2 swaps the full schema for the retrieved one; you'll edit this |
| `generation/claude_client.py` | `generate_sql()`: the API call, `cache_control`, `effort`, reading usage tokens, `extract_sql()` regex, never-raise error handling | Phase 3 retry wraps it; Phase 5 cloud fallback calls it |
| `eval/run.py` → `score_sql()` and `run_one()` | The core loop: question → prompt → model → SQL → execute → compare → one result row | Phases 2, 3, 4 each add a step here |

**Check:** explain the full path from one question to `correct=True/False` without opening the code.

---

## 2. Skim: know the logic, not the code

| File / part | The one idea to remember |
|---|---|
| `eval/executor.py` | Runs SQL **read-only** with a **30s timeout**. Timeout = SQLite calls a progress handler every N steps; if time's up, it aborts. Returns `ExecResult(ok, rows, error, seconds)` |
| `eval/compare.py` | Correct = `set(pred_rows) == set(gold_rows)`. Row order and duplicates don't matter (official BIRD rule) |
| `eval/metrics.py` | Which numbers we report: EX%, valid SQL%, p50/p95 latency, prompt tokens, cache read %, cost per 1k |
| `generation/cost.py` | cost = Σ(tokens × price) / 1,000,000, with separate prices for cache write/read |
| `eval/run.py` → rest | Thread pool = 4 questions in parallel. Budget stop = halt if cost > `--max-cost`. `--rescore` = re-execute saved SQL with no API calls (that's how results stay reproducible) |

---

## 3. Skip (one-off checks, nothing depends on them)

| File | What it was for |
|---|---|
| `eval/validate_harness.py` | Proved the harness scores gold SQL as 100% correct |
| `eval/gold_cache.py` | Saves gold query results once so we don't re-run them |
| `eval/official_check.py` | Proved our scorer agrees with BIRD's official script (200/200) |
| `eval/failure_sample.py` | Printed failures for hand-labelling the taxonomy |

Just know these checks **exist and passed**. That's a talking point.

---

## 4. Decisions to be able to explain (interview material)

Answers are in `notes/phase1-eval-harness.md` and `notes/phase1-model-choice.md`.

1. Why open the database read-only?
2. Why set comparison, and why a 30s timeout? (Q518/Q701 → max EX 99.87%)
3. Why Sonnet 5.5 at effort `low`, and not Haiku/Opus/Fable?
4. How did prompt caching halve cost without changing answers?
5. Model is non-deterministic, so how is the result reproducible? (saved predictions + `--rescore`)
6. Why does 68.38% EX not mean the model was wrong 31.6% of the time? (failure taxonomy: about half the failures aren't model errors)
7. Why validate the harness against the official script before trusting any number?
