# Learning Roadmap

Your map of the codebase. Use it to study at your own pace: go phase by phase, and within each phase read the files **in the order listed**. Each file only depends on files above it.

**How to study a file**
1. Read the matching section in the phase notes (the "Walkthrough").
2. Rewrite the file by hand on paper.
3. Run its self-check (`uv run python -m <module>`) and compare the output with what you expected.
4. Try the "Check yourself" question without looking.

Legend: ✅ built · 🔨 in progress · ⬜ planned

---

## The big picture: how the pieces connect

```
                     ┌──────────────────────── eval/ (Phase 1) ───────────────────────┐
dev.json ──► data.py ──► Example ──┬──► executor.py ──► ExecResult ──► compare.py ──► correct?
                                   │                                     ▲
                                   └──► gold_cache.py ──► gold rows ─────┘
                                                                 validate_harness.py (proves the above = 100%)

Planned flow once complete (one question):
Example ─► retrieval/ ─► generation/ (prompt + model) ─► guardrails/ ─► executor ─► routing/ ─► answer
 (P2: pick tables)   (P1/P4: write SQL)          (P3: check SQL)              (P5: escalate?)
                         ▲                                            │
                         └────────── error → retry (P3) ◄─────────────┘
Everything logged by eval/run.py → results/  →  metrics.py (EX%, latency, cost)
```

---

## Phase 0 — Setup ✅
Notes: [phase0-setup.md](phase0-setup.md)

No code to study. Concepts: git, uv environments, `.env` secrets, BIRD layout, execution accuracy.

---

## Phase 1 — Eval harness + baseline ✅
Notes: [phase1-eval-harness.md](phase1-eval-harness.md) · model choice: [phase1-model-choice.md](phase1-model-choice.md)

**Result (row 1):** Sonnet 5.5, full schema: **68.38% EX**, valid SQL 100%, p95 4.46s, **$1.72 per 1k questions**.

| # | File | What it does | Python concepts to know first | Depends on | Status |
|---|---|---|---|---|---|
| 1 | [eval/data.py](../eval/data.py) | Loads questions into `Example` objects | dicts, lists, `for` loops, `json.load`, `with open`, `@dataclass`, `@property`, `pathlib.Path`, f-strings, slicing, `if __name__ == "__main__"` | — | ✅ |
| 2 | [eval/executor.py](../eval/executor.py) | Runs SQL read-only with a timeout, never crashes | `sqlite3`, `try/except/finally`, `lambda`, `time.perf_counter`, `None` as "no value" | data.py (self-check only) | ✅ |
| 3 | [eval/compare.py](../eval/compare.py) | Decides if two results match (BIRD rule) | `set`, tuples, `assert` | — | ✅ |
| 4 | [eval/gold_cache.py](../eval/gold_cache.py) | Runs gold SQL once, saves results | `pickle`, dict building, `raise` | data, executor | ✅ |
| 5 | [eval/validate_harness.py](../eval/validate_harness.py) | Gold-as-prediction must = 100% | `enumerate`, tuple unpacking, f-string formatting (`:.2f`) | data, executor, compare, gold_cache | ✅ |
| 6 | [generation/cost.py](../generation/cost.py) | Tokens → dollars | nested dicts, keyword arguments with defaults | — | ✅ |
| 7 | [generation/prompt.py](../generation/prompt.py) | Schema DDL + hint + question → `(system, user)` | `sqlite_master`, `str.join`, conditional expressions, `@lru_cache`, returning tuples | data | ✅ |
| 8 | [generation/claude_client.py](../generation/claude_client.py) | Calls Claude, extracts SQL, records tokens/cost/time | Anthropic SDK, `load_dotenv`, regex (`re.compile`, `findall`, `DOTALL`), generator expressions, `*args` unpacking | cost | ✅ |
| 9 | [eval/metrics.py](../eval/metrics.py) | EX%, valid-SQL rate, p50/p95, cost/1k, by difficulty | pandas: `DataFrame`, column maths, `.mean()`, `.quantile()`, `groupby().agg()` | — | ✅ |
| 10 | [eval/run.py](../eval/run.py) | Full loop: generate → execute → compare → save; `--rescore` | `argparse`, `ThreadPoolExecutor`, `as_completed`, `asdict`, `**dict` merging, CSV/JSON/JSONL writing | all of the above | ✅ |
| 11 | [eval/official_check.py](../eval/official_check.py) | Our verdicts vs the official scorer's logic | `random.seed`, `random.sample`, `df.isin` | data | ✅ |
| 12 | [eval/failure_sample.py](../eval/failure_sample.py) | Fixed sample of failures for labelling | boolean indexing (`df[~df["correct"]]`), `.iloc` | data, executor, gold_cache | ✅ |

**Check yourself**
- Why does `execute()` return an `ExecResult` instead of raising errors?
- What does `mode=ro` protect against, and why isn't checking the SQL text enough?
- Give two cases where `set(pred) == set(gold)` gives a misleading answer.
- Why did validation need a longer timeout than the official 30s?
- Prompt caching cut cost in half. Why does it never change the model's answer?
- The model isn't deterministic. What exactly makes the 68.38% reproducible?
- Why does `generate_sql()` catch API errors instead of letting them crash the run?
- Half the labelled failures weren't really model mistakes. Name the two categories and an example of each.

---

## Phase 2 — Schema retrieval ⬜
Notes: `phase2-retrieval.md` (written as we build)

**Question it answers (H1):** can we send the model only the relevant part of the schema, cutting tokens and cost, without losing accuracy?
**Done when:** a full-vs-pruned ablation shows tokens, cost, schema recall and EX%, broken down by schema size.

**What Phase 1 already tells us (set expectations honestly)**
- BIRD dev schemas are small: 3–14 tables, about 300–2,000 tokens. The full prompt is ~1,700 tokens and 94% of it is already served from cache. So the **cost saving on Sonnet will be small**. Report it anyway; the bigger payoff is Phase 4, where a 7B model's accuracy suffers from long, noisy prompts.
- Only ~8% of labelled failures were wrong_table, so retrieval is unlikely to add much accuracy. Its first job is to **not lose** a table or column the query needs. That's why **schema recall** is the key metric: if recall is 95%, ~95% is the ceiling.
- **Most of Phase 2 costs nothing:** schema recall is computed offline from gold SQL, with no API calls. Only the final ablation runs spend credit (~$2.6 per full-dev Sonnet run; ~$6.7 left).

**Build order** (each file depends only on files above it)

| # | File | What it does | Concepts to know first | Depends on | Status |
|---|---|---|---|---|---|
| 1 | `eval/splits.py` | Fixed split of dev into a **tune** slice (~20%) and a **report** slice (~80%), stratified by database and difficulty. Used from here to Phase 5 | stratified sampling, `random.seed`, why tuning on the test set is cheating | data | ⬜ |
| 2 | `retrieval/schema.py` | Per DB: tables, columns, types, PK/FKs (`PRAGMA table_info` / `foreign_key_list`), column descriptions from `database_description/*.csv`, 3–5 sample values per column | SQLite `PRAGMA`, `csv`/pandas reading, dataclasses for structured data | data | ⬜ |
| 3 | `eval/schema_recall.py` | Parse gold SQL with sqlglot → the tables/columns it uses; recall = share of those that retrieval kept | ASTs (a tree view of SQL), sqlglot `parse_one`, `find_all`, aliases (`T1`) | schema | ⬜ |
| 4 | `retrieval/bm25.py` | Keyword search: question → ranked columns | tokenization, TF-IDF intuition, BM25 (`rank_bm25`) | schema | ⬜ |
| 5 | `retrieval/dense.py` | Embedding search: question → ranked columns | embeddings, cosine similarity, BGE models (`sentence-transformers`), FAISS index, GPU vs CPU | schema | ⬜ |
| 6 | `retrieval/fusion.py` | Merge BM25 + dense rankings with RRF, then rerank the top-K with a cross-encoder | reciprocal rank fusion (`1 / (k + rank)`), bi-encoder vs cross-encoder | bm25, dense | ⬜ |
| 7 | `retrieval/fk_expand.py` | Add join keys and bridge tables so the kept tables can actually be joined | graphs, FK edges, shortest paths (BFS) | schema | ⬜ |
| 8 | `retrieval/pipeline.py` | question → pruned schema DDL (+ descriptions/sample values) | composing the steps, config objects | 4–7 | ⬜ |
| 9 | `generation/prompt.py` (extend) | Option to build the prompt from the pruned schema | keeping old behaviour as the default | pipeline | ⬜ |
| 10 | `eval/run.py` (extend) | `--schema full|pruned`, `--split report`, recall + schema size in results | — | all above | ⬜ |

**Experiments, in order**
1. **Recall only (free):** each retriever's schema recall at several K, measured on the **tune** slice. Pick K and the fusion setup there.
2. **Recall on report (free):** confirm the chosen setup on the report slice.
3. **Ablation rows (paid, on the report slice):** run each variant on the report slice and compare it with the baseline on the same questions, so only one thing changes at a time.
   - row 1 (re-scored on the report slice): full schema (we already have these predictions, so this costs nothing)
   - row 2: pruned schema, same DDL format
   - row 2b: pruned schema + descriptions + sample values, only if budget allows
4. Break results down by schema size (number of tables/columns per DB).

**New dependencies:** `rank-bm25`, `sentence-transformers`, `faiss-cpu` (the GPU isn't needed at this scale: a few hundred columns).

**Check yourself (answer after building)**
- Why is schema recall a ceiling on EX%?
- Why does BM25 find `CDSCode` when dense search might not, and the reverse for "pupils" → `Enrollment`?
- What does RRF do that averaging the two scores doesn't?
- Why can pruning to the top-K columns break a query even when every needed column is retrieved? (Hint: joins.)
- Why do we choose K on the tune slice and report on the other slice?

---

## Phase 3 — Guardrails + retries ⬜
| File | What it does | New concepts |
|---|---|---|
| `guardrails/validate.py` | SELECT-only, tables/columns exist, inject LIMIT | ASTs, sqlglot |
| `guardrails/retry.py` | Error → feed back to model → retry | feedback loops, recovery rate |
| `tests/test_guardrails.py` | Unit tests for the checks | pytest |

## Phase 4 — Self-hosted small model ⬜
| File | What it does | New concepts |
|---|---|---|
| `generation/vllm_client.py` | Call local Qwen-Coder via vLLM | vLLM, quantization (AWQ/GPTQ), OpenAI-compatible APIs |
| `eval/cost.py` | GPU-hour cost per query | cost modeling |

## Phase 5 — Confidence routing ⬜
| File | What it does | New concepts |
|---|---|---|
| `routing/signals.py` | logprobs, retry count, self-consistency | log probabilities, sampling |
| `routing/calibrate.py` | Accuracy vs signal, threshold sweep | calibration, held-out splits |
| `routing/router.py` | Small model or escalate? | cost/accuracy trade-off |

## Phase 6 — Serving ⬜
`serving/app.py` (FastAPI), tracing, caching, audit log, `Dockerfile`, `docker-compose.yml`

## Phase 7 — Writeup ⬜
README, results table, cost/accuracy curve, failure analysis

## Phase 8–9 — Optional ⬜
`training/sft.py`, `training/dpo.py` (QLoRA, TRL) · MIMIC port

---

*This file is updated as each file gets built. Planned file names may change slightly when we build them.*
