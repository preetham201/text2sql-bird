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

## Phase 2 — Schema retrieval ✅
Notes: **`phase2-retrieval.md`**: full end-to-end explanation, results, decisions, ⭐ code to study.
Failures + fixes (all phases): **`failures-log.md`** (incl. the OOM crash during the train gold cache and how it was fixed).

**Question it answers (H1):** can we send the model only the relevant part of the schema, cutting tokens and cost, without losing accuracy?
**Result so far (free, offline):** hybrid (BM25 + BGE, RRF) + exact name/value boost, table granularity, K=20, FK expansion:
**97.5% column recall on train → 98.6% on dev**, schema text at 83% / 88% of full. Large schemas (>150 cols): 42% of full at 93% recall.
Counter-intuitive finding: with prompt caching, pruning should make Sonnet ~27% *more* expensive on dev (est. $3.36 vs $2.64).

**Files built (in dependency order)**

| # | File | What it does | Study level |
|---|---|---|---|
| 1 | `eval/data.py` (extended) | `load_split("train"/"dev")` | ⭐ |
| 2 | `retrieval/schema.py` | Per DB: columns, types, PK/FK (PRAGMA), descriptions (mixed-encoding CSVs), sample values; `original_ddl` / `render_ddl` | ⭐ `Column.key`, `original_ddl` |
| 3 | `eval/gold_columns.py` | sqlglot parse + `qualify` → tables/columns the gold SQL uses (dev 100%, train 98% parse) | ⭐ |
| 4 | `retrieval/text.py` | column → searchable text, identifier splitting, tokenizer | skim |
| 5 | `retrieval/bm25.py` | keyword ranking per DB | idea |
| 6 | `retrieval/dense.py` | BGE embeddings + cosine (no FAISS: ≤455 vectors per DB) | idea |
| 7 | `retrieval/fusion.py` | RRF (k=60) + optional cross-encoder rerank | ⭐ `rrf()` |
| 8 | `retrieval/exact.py` | column-name mentions + cell-value matches (4.9M-value index) → top of ranking | idea |
| 9 | `retrieval/fk_expand.py` | FK graph + inferred links, BFS shortest paths, join columns | ⭐ idea of `connect_tables` |
| 10 | `retrieval/pipeline.py` | `RetrievalConfig`, rank → exact → select → render | ⭐ whole file |
| 11 | `eval/schema_recall.py` | offline sweep: recall, compression, by schema size | ⭐ metric part |
| 12 | `generation/prompt.py`, `claude_client.py`, `eval/run.py`, `metrics.py`, `gold_cache.py`, `compare.py`, `executor.py` (extended) | `--schema pruned`, `--split`, retrieval stats per question, train gold cache as streamed fingerprints | ⭐ new `run_one`, `fingerprint()` |

**Experiments done:** train sweep (4 methods × exact on/off × K × granularity), reranker on a 2k sample, by-size breakdown,
dev confirmation, cost estimate with/without caching, Haiku 20-question smoke test (<1 cent).
**Row 2 (paid, full dev):** 68.45% EX vs row 1 68.38% (McNemar p = 1.00), prompt tokens −14%, cost +28% ($3.37), noise floor 3.2% flips measured on 502 identical-prompt questions.

**Check yourself**
- Why is schema recall a ceiling on EX%?
- Why did BM25 beat dense here, and what closed the gap?
- What does RRF do that averaging the two scores doesn't?
- Why can pruning break a query even when every needed column is retrieved? (joins → FK expansion; BIRD FKs incomplete)
- Why do we choose K on train and report on dev?
- The prompt got smaller but the cost went up. Why?

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

## Phase 5 — Two-gate routing ⬜
| File | What it does | New concepts |
|---|---|---|
| `routing/cascade.py` | Naive cascade: small → 1 retry → Gate 2 → frontier (the p95 problem) | cascades, tail latency |
| `routing/gate1.py` | Pre-generation router: P(small model correct) from retrieval features, trained on train | feature engineering, logistic regression / LightGBM, calibration |
| `routing/gate2.py` | Post-generation verifier: min logprob over structural clauses | log probabilities, sqlglot token spans, self-consistency |
| `routing/sweep.py` | Sweep (τ1, τ2), simulate escalation offline, cost vs EX% vs random router, APGR | Pareto curves, offline simulation |

## Phase 6 — Serving ⬜
`serving/app.py` (FastAPI), tracing, caching, audit log, `Dockerfile`, `docker-compose.yml`

## Phase 7 — Writeup ⬜
README, results table, cost/accuracy curve, failure analysis

## Phase 8–9 — Optional ⬜
`training/sft.py`, `training/dpo.py` (QLoRA, TRL) · MIMIC port

---

*This file is updated as each file gets built. Planned file names may change slightly when we build them.*
