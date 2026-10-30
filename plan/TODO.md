# Text-to-SQL Project — Checklist

Source of truth: `text2sql-project.md` (phases match its numbering).
Work top to bottom. A phase is finished only when its **Done when** is true.
Each phase lists **You'll learn** — the concepts to understand well enough to explain in an interview.

**Rules (from the kill list):**
- No Phase 8/9 work until v1 (Phases 0–7) ships.
- Nothing goes in the README without a run behind it.
- Train on train, tune on a held-out dev slice, report on the rest.
- If a phase takes >2× its estimate, cut scope — don't stall.

---

## Phase 0 — Setup (a few days)
**You'll learn:** project structure, env management, how BIRD is laid out, reading SQLite from Python.

- [x] `git init`, `.gitignore` (data/, .env, __pycache__, results/raw/)
- [x] Python env + dependency file
- [ ] Repo layout: `eval/ retrieval/ generation/ guardrails/ routing/ serving/ training/ results/`
- [x] Download BIRD dev (questions JSON + SQLite DBs + `database_description/` CSVs) → `data/bird/dev/`
- [ ] Download BIRD train → `data/bird/train/` (few-shot pool, later SFT)
- [x] Explore: open a dev DB, list tables, read 3 questions + their gold SQL + evidence
- [x] Frontier model chosen, API key in `.env`
- [x] Cost-logging utility (tokens in/out → $)
- [ ] *(Optional)* Start PhysioNet credentialing for MIMIC — approval takes days

**Done when:** you can load a BIRD database and run a gold query from code.

---

## Phase 1 — Eval harness + frontier baseline (weeks 1–2)
**You'll learn:** execution accuracy vs exact-match, result-set comparison edge cases, latency percentiles, cost accounting, prompt construction from a schema.

### Harness
- [x] Loader: question, evidence, db_id, gold SQL, difficulty
- [x] Executor: read-only SQLite connection, per-query timeout
- [x] Result-set comparison (handle ordering, duplicates, NULLs, float rounding)
- [x] **Validate: gold SQL as predictions must score 100%**
- [x] Cross-check against BIRD's official eval script on a sample
- [x] Metrics: EX%, valid-SQL rate, p50/p95 latency, tokens, cost/1k queries
- [x] Per-question results saved to versioned CSV/Parquet in `results/`
- [x] Breakdown by difficulty (simple / moderate / challenging)
- [x] `--limit N` flag for quick subset runs

### Baseline
- [x] Prompt: full schema DDL + evidence + question
- [x] Run on full dev → **ablation row 1**
- [x] Failure taxonomy started: wrong table / join / column / aggregation / filter value / syntax / ambiguity
- [x] Label ~30–50 failures with the taxonomy

**Done when:** row 1 exists and re-running gives the same results.

✅ **Done (2026-10-30):** Sonnet 5.5 full schema = 68.38% EX, $1.72/1k, p95 4.46s; `--rescore` reproduces exactly; 200/200 agree with official scorer logic.

---

## Phase 2 — Schema retrieval (weeks 3–4)
**You'll learn:** BM25 (keyword search), dense embeddings, reciprocal rank fusion, cross-encoder reranking, recall as a ceiling on accuracy, FK graphs.

- [ ] Fixed tune/report split of dev (`eval/splits.py`), used through Phase 5
- [ ] Extract per DB: tables, columns, types, FKs
- [ ] Enrich: column descriptions + 3–5 sample values per column
- [ ] BM25 index
- [ ] Dense index (BGE embeddings, FAISS)
- [ ] Fuse with RRF
- [ ] Cross-encoder rerank of top-K
- [ ] FK expansion: join keys + bridge tables
- [ ] Schema recall metric (gold tables/columns via sqlglot ⊆ retrieved?)
- [ ] Tune K vs recall
- [ ] Results broken down by schema size (small DBs may not benefit — report it)
- [ ] Rerun frontier with pruned schema → **row 2**

**Done when:** full-vs-pruned ablation shows tokens, cost, schema recall, EX%.

---

## Phase 3 — Guardrails + self-healing (weeks 3–4, parallel)
**You'll learn:** SQL parsing into an AST, why validation ≠ security, execution-feedback loops, recovery rate.

- [ ] sqlglot: single SELECT only (reject DDL/DML, multi-statement)
- [ ] Every table/column exists in the real schema
- [ ] Inject LIMIT, statement timeout
- [ ] Unit tests (DROP, UPDATE, `; DROP`, fake column, etc.)
- [ ] Retry loop: error + failed SQL + schema → model, max 2–3
- [ ] Metrics: error rate before/after, recovery rate, extra latency
- [ ] → **row 3**
- [ ] README with results so far, repo public
- [ ] **Start applying to jobs**

**Done when:** repo is public with README and ablations.

---

## Phase 4 — Self-hosted small model (weeks 5–6)
**You'll learn:** vLLM, 4-bit quantization (AWQ/GPTQ), GPU memory budgeting, OpenAI-compatible endpoints, GPU cost per query.

- [ ] Serve Qwen-Coder 7B (4-bit) with vLLM on the 12GB GPU
- [ ] Check fit at your context length; measure throughput
- [ ] Plug into harness via OpenAI-compatible API
- [ ] Cost model: GPU $/hr ÷ queries/hr (include rental hours)
- [ ] Full pipeline run → **row 4**; note the accuracy gap

**Done when:** small-model row exists and you know the gap.

---

## Phase 5 — Confidence routing (week 7)
**You'll learn:** logprobs, self-consistency, calibration, threshold sweeps, train/test discipline on dev.

- [ ] Split dev: threshold-selection slice vs reporting slice
- [ ] Collect per query: mean/min logprob, retry count, self-consistency (k samples agree?)
- [ ] Calibration plots: accuracy vs each signal
- [ ] Sweep thresholds on selection slice; report on the other
- [ ] Cost vs accuracy curve
- [ ] If logprobs are poorly calibrated, say so and switch signals
- [ ] → **row 5**

**Done when:** you can say "at threshold T, N% goes to frontier, retaining X% of its accuracy at Y% of its cost."

---

## Phase 6 — Serving + observability (week 8)
**You'll learn:** FastAPI, tracing, caching, audit logging, Docker/compose.

- [ ] FastAPI `/query` endpoint wrapping the pipeline
- [ ] Per-stage latency + tracing (Langfuse or OpenTelemetry)
- [ ] Cache repeated questions
- [ ] Audit log of every generated SQL
- [ ] Read-only DB role
- [ ] Dockerfile + docker-compose
- [ ] Small demo UI

**Done when:** `docker compose up` and someone can ask a question.

---

## Phase 7 — Writeup + publish (weeks 8–9) → **v1 complete**
**You'll learn:** presenting technical results honestly.

- [ ] README leads with results table + cost/accuracy curve
- [ ] Failure analysis with real examples from the taxonomy
- [ ] Limitations section (incl. wrong-but-executes queries)
- [ ] Design-decisions section (why each component exists)
- [ ] Short write-up; send to recruiters + HSBC contact

---

## Phase 8 (optional) — Fine-tuning
**You'll learn:** QLoRA, SFT data formatting, DPO preference pairs.

- [ ] SFT with QLoRA on BIRD **train** only; evaluate on dev → **row 6**
- [ ] DPO only if SFT plateaus (sample → execute → correct vs incorrect pairs)
- [ ] Report the delta, even if small or negative

## Phase 9 (optional) — MIMIC-IV port
- [ ] PhysioNet access + check external-API policy
- [ ] Write own eval set (100–200 question/SQL pairs), ideally clinician-checked
- [ ] Compliant model access only (self-hosted or permitted route)

---

## Final deliverable table

| Configuration | EX% | Schema recall | p95 latency | Cost / 1k queries |
|---|---|---|---|---|
| Frontier, full schema (baseline) | 68.38% | n/a | 4.46s | $1.72 |
| + schema retrieval | | | | |
| + guardrails and retries | | | | |
| Small model, same pipeline | | | | |
| Small model + routing | | | | |
| + SFT | | | | |

**Interview story (fill in):**
> I treated Text-to-SQL as a reliability and cost problem. Retrieval changed prompt size by __ at schema recall __. Guardrails and retries recovered __% of failures. A self-hosted model handles __% of traffic and escalates the rest, cutting cost by __% for a __-point accuracy drop. Here is where it still fails and why.
