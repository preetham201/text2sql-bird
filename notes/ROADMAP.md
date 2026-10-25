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
Everything logged by eval/run.py → results/  →  metrics (EX%, latency, cost)
```

---

## Phase 0 — Setup ✅
Notes: [phase0-setup.md](phase0-setup.md)

No code to study. Concepts: git, uv environments, `.env` secrets, BIRD layout, execution accuracy.

---

## Phase 1 — Eval harness + baseline 🔨
Notes: [phase1-eval-harness.md](phase1-eval-harness.md)

| # | File | What it does | Python concepts to know first | Depends on | Status |
|---|---|---|---|---|---|
| 1 | [eval/data.py](../eval/data.py) | Loads questions into `Example` objects | dicts, lists, `for` loops, `json.load`, `with open`, `@dataclass`, `@property`, `pathlib.Path`, f-strings, slicing, `if __name__ == "__main__"` | — | ✅ |
| 2 | [eval/executor.py](../eval/executor.py) | Runs SQL read-only with a timeout, never crashes | `sqlite3`, `try/except/finally`, `lambda`, `time.perf_counter`, `None` as "no value" | data.py (self-check only) | ✅ |
| 3 | [eval/compare.py](../eval/compare.py) | Decides if two results match (BIRD rule) | `set`, tuples, `assert` | — | ✅ |
| 4 | [eval/gold_cache.py](../eval/gold_cache.py) | Runs gold SQL once, saves results | `pickle`, dict building, `raise` | data, executor | ✅ |
| 5 | [eval/validate_harness.py](../eval/validate_harness.py) | Gold-as-prediction must = 100% | `enumerate`, tuple unpacking, f-string formatting (`:.2f`) | data, executor, compare, gold_cache | ✅ |
| 6 | `generation/prompt.py` | Turns a DB schema + question into a prompt | string building, SQLite schema queries | data | ⬜ |
| 7 | `generation/claude_client.py` | Calls Claude, extracts SQL, records tokens | Anthropic SDK, regex, API errors | — | ⬜ |
| 8 | `eval/run.py` | Full loop: generate → execute → compare → log | `argparse`, writing CSV/JSONL, percentiles | all of the above | ⬜ |
| 9 | `eval/metrics.py` | EX%, valid-SQL rate, p50/p95, cost/1k | pandas basics, `groupby` | run.py output | ⬜ |

**Check yourself**
- Why does `execute()` return an `ExecResult` instead of raising errors?
- What does `mode=ro` protect against, and why isn't checking the SQL text enough?
- Give two cases where `set(pred) == set(gold)` gives a misleading answer.
- Why did validation need a longer timeout than the official 30s?

---

## Phase 2 — Schema retrieval ⬜
Notes: `phase2-retrieval.md` (written when we start)

| File | What it does | New concepts |
|---|---|---|
| `retrieval/schema.py` | Extract tables, columns, types, FKs, descriptions, sample values | SQLite `PRAGMA`, CSV reading |
| `retrieval/bm25.py` | Keyword search over schema | BM25, tokenization |
| `retrieval/dense.py` | Embedding search over schema | embeddings, cosine similarity, FAISS |
| `retrieval/fusion.py` | Merge rankings, rerank | reciprocal rank fusion, cross-encoders |
| `retrieval/fk_expand.py` | Add join keys + bridge tables | graphs, foreign keys |
| `eval/schema_recall.py` | Did we retrieve the tables gold SQL uses? | parsing SQL with sqlglot |

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
