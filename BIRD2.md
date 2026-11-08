# Project Specification: Dual-Gate Adaptive Text-to-SQL Inference Engine

## 1. The Core Problem & Engineering Thesis

A user asks an analytical question in English (*"Which patients had a heart rate above 120 for more than 6 hours?"*). The system must generate valid SQL, execute it safely against a database, and return an accurate answer within strict cost and latency bounds. 

Five engineering bottlenecks make production Text-to-SQL hard:
1. **Schemas are massive.** Passing 100–500 tables into an LLM prompt blows up Time-To-First-Token (TTFT), exhausts GPU KV-cache memory, increases API token bills, and degrades attention accuracy.
2. **Small local models write plausible but broken SQL.** Off-the-shelf 7B models hallucinate column names, drop intermediate bridge tables in joins, or miscalculate nested aggregations.
3. **Frontier APIs are expensive, slow, and data-sensitive.** Sending 100% of queries to GPT-4o or Claude 3.5 Sonnet violates cost budgets and P95 latency SLAs.
4. **Naive local-to-cloud cascades ruin P95/P99 tail latency.** If every query is sent to a small local model first, tried 2–3 times upon failure, and only escalated to a frontier model afterward, complex queries suffer **3,000ms–4,500ms of wasted local GPU compute and retry latency** before the frontier API call even begins.
5. **Executing LLM-generated SQL is an operational and security risk.** Unbounded Cartesian joins hang database threads, and unvalidated syntax risks unauthorized mutations.

**The Thesis:** This project treats Text-to-SQL as a **three-dimensional systems optimization problem: Execution Accuracy (EX%), Tail Latency (P95/P99), and Unit Economics ($/1k queries)**. Every subsystem exists to improve one of those three axes, proven empirically via an incremental ablation table on the BIRD benchmark.

---

## 2. End-to-End Request Flow

```text
Incoming English Question + Evidence Hint + Database ID
  │
  ├─► [1] Graph-Expanded Hybrid Schema Pruning
  │     ├── BM25 (lexical) + BGE-small (INT8 ONNX dense) merged via RRF
  │     ├── Cross-Encoder reranking of top candidate tables/columns
  │     └── Foreign-Key Steiner Tree Expansion (injects missing join bridge tables)
  │
  ├─► [2] GATE 1: Pre-Generation Structural Router (< 1ms overhead)
  │     ├── Zero-cost feature reuse: grabs Stage 1 BGE vector + Schema Graph metrics
  │     ├── Predicts P(Local_Pass | q, Schema) via Matrix Factorization / MLP head
  │     └── Dynamic Queue-Aware Threshold: τ_eff(t) = τ_pre + β · QueueDepth(t)
  │           │
  │           ├── If P < τ_eff (Predicted Hard Query OR High Local GPU Queue) ──────┐
  │           │                                                                     ▼
  │           └── If P ≥ τ_eff (Route to Local GPU)                      [Frontier API Escalation]
  │                 │                                                    (Claude 3.5 Sonnet / GPT-4o)
  │                 ▼                                                               │
  │     [3] Local Inference Tier (Qwen2.5-Coder-7B-AWQ on vLLM, 12GB GPU)           │
  │           ├── Automatic Prefix Caching (APC) reuses Schema DDL KV-cache         │
  │           └── Greedy decoding with token-level logprobs enabled                 │
  │                 │                                                               │
  │                 ▼                                                               │
  │     [4] Deterministic AST Guardrails & Bounded Self-Healing                     │
  │           ├── sqlglot AST validation (SELECT-only, schema check, LIMIT inject)  │
  │           └── Read-only SQLite execution (?mode=ro, 5.0s statement timeout)     │
  │                 ├── Syntax / DB Error ──► Self-healing retry (Max 1 retry)      │
  │                 │                           └── Retry fails? ───────────────────┤
  │                 ▼                                                               │
  │     [5] GATE 2: Post-Generation Confidence Verifier                             │
  │           ├── Extracts minimum token logprob over structural AST clauses        │
  │           ├── Low confidence (< τ_post) on silent semantic error risk? ─────────┤
  │           └── High confidence (≥ τ_post)                                        │
  │                 │                                                               │
  │                 ◄───────────────────────────────────────────────────────────────┘
  ▼
Return Result Set + Emit OpenTelemetry Trace (EX%, Stage Latencies, Token Cost, Path)
```

---

## 3. Component-by-Component Engineering Specification

### Component 0: Deterministic Evaluation & Telemetry Harness (Build First)
Runs any pipeline configuration over the **BIRD `dev` split** (1,534 queries across 11 databases), executes both the predicted SQL and gold SQL against read-only SQLite instances, and compares result sets.
* **Metrics Tracked:**
  * **Execution Accuracy ($\text{EX}\%$):** Percentage of queries whose returned result set matches the gold SQL result set.
  * **Valid SQL Rate ($\%$):** Percentage of queries that parse and execute within the 5.0s timeout without throwing an exception.
  * **Stage-Wise Latency ($\text{P50}, \text{P95}, \text{P99}$ in ms):** Independent timers for schema retrieval, Gate 1 routing, local vLLM prefill/decode, self-healing retries, and cloud API escalation.
  * **Cost per 1,000 Queries ($\$$):** Exact input/output API token spend plus amortized local GPU compute cost (calculated from active GPU execution seconds $\times$ hourly cloud GPU rate).

### Component 1: Graph-Expanded Hybrid Schema Pruning
* **Indexing:** Index every table and column with its name, data type, BIRD column description, and **3 distinct sample cell values** (critical so the generator sees whether status flags are `'A'` vs `'Active'`).
* **Hybrid Retrieval:** Retrieve candidates using **BM25** + **INT8 ONNX `bge-small-en-v1.5`** dense embeddings, fuse rankings via Reciprocal Rank Fusion (RRF), and rerank the top candidates with a lightweight cross-encoder.
* **Foreign-Key Graph Expansion:** Naive top-$k$ pruning breaks multi-table SQL queries whenever it drops an intermediate bridge table. Construct an undirected schema graph $G = (V, E)$ where tables are vertices $V$ and foreign-key constraints are edges $E$. Given the initial retrieved table set $V_{\text{ret}} \subset V$, compute the **Steiner tree / shortest paths** connecting all nodes in $V_{\text{ret}}$ and automatically include every intermediate bridge table and join key in the pruned DDL.
* **Standalone Metric:** Measure **Schema Recall** (the fraction of queries where 100% of gold SQL tables are present in the pruned DDL) and **Prompt Token Compression Ratio** independently of $\text{EX}\%$. If Schema Recall is $92\%$, the hard ceiling on $\text{EX}\%$ is $92\%$.

### Component 2: Gate 1 — Sub-Millisecond Pre-Generation Router
Instead of sending doomed complex queries to the local 7B model—where they burn GPU compute, fail execution, retry, and blow up P95 tail latency—Gate 1 predicts whether the local model will succeed *before* generating a single token.
* **Zero-Latency Feature Reuse:** Stock routing frameworks (like default RouteLLM) call external embedding APIs, adding 80ms–200ms of network latency. Instead, Gate 1 reuses the **384-dimensional `bge-small` embedding $v_q$ already computed in Component 1** (0ms extra embedding latency) and concatenates an explicit **Schema & Query Complexity Vector $s_q$**:
  * Number of tables and total columns in the pruned DDL.
  * Number of foreign-key join edges in the expanded schema subgraph.
  * Cross-encoder margin/entropy across top retrieved columns (measures schema ambiguity).
  * Lexical complexity flags (nested aggregations, ratios/percentages, window/ranking terms).
* **Router Formulation & Training (Zero Leakage):**
  Run the base 7B model over the **BIRD `train` split** (never `dev`) to collect ground-truth execution labels $y_i \in \{0, 1\}$, where $y_i = 1$ iff the local 7B model produces $\text{EX} = 1$ without frontier help. Train and compare two lightweight routing heads over the fused feature vector $\phi(q, S) = [v_q \parallel s_q]$:
  1. **Bilinear Matrix Factorization Router:** Projects $\phi(q, S)$ into a low-rank latent space to score model capability $\delta(M, q, S) = w_2^T \left( v_m \odot (W_1 \phi(q, S) + b) \right)$ and predicts win probability via Bradley-Terry preference loss:
     $$P(\text{win}_s \mid q, S) = \sigma\left(\delta(M_s, q, S) - \delta(M_w, q, S)\right)$$
  2. **Calibrated Gradient-Boosted / MLP Classifier:** Directly outputs calibrated local pass probability $P(\text{Local Pass} \mid q, S)$.
* **Queue-Aware Dynamic Thresholding:**
  In production, a static threshold $\tau_{\text{pre}}$ ignores hardware saturation. When concurrent requests hit the 12GB local GPU, vLLM's KV-cache fills up and waiting queues spike TTFT. Define an effective threshold tied to real-time vLLM queue depth:
  $$\tau_{\text{eff}}(t) = \tau_{\text{pre}} + \beta \cdot \text{QueueDepth}(t)$$
  If $P(\text{Local Pass} \mid q, S) < \tau_{\text{eff}}(t)$, bypass the local GPU immediately and route directly to the Frontier API.

### Component 3: Hardware-Aligned Local Inference (12GB VRAM Budget)
* **Why 7B 4-bit AWQ Instead of 14B:** On a 12GB GPU, a 14B 4-bit model consumes ~8.5GB for weights alone, leaving <2GB for vLLM's KV-cache once CUDA overhead and local retrieval embeddings are loaded—causing immediate OOM or CPU swap under concurrent requests. **`Qwen2.5-Coder-7B-Instruct-AWQ`** consumes **~4.3GB**, leaving **~6.5GB for vLLM's PagedAttention KV-cache**.
* **Automatic Prefix Caching (APC):** Structure the prompt so the static Database Pruned DDL and few-shot examples sit at the prefix, followed by the user question. Enable `--enable-prefix-caching` in vLLM so multiple queries against the same database reuse cached KV blocks in 0ms.

### Component 4: Deterministic AST Guardrails & Bounded Self-Healing
Before any generated SQL touches SQLite, parse it into an Abstract Syntax Tree (AST) using **`sqlglot`**:
1. **Statement Policy:** Verify the root AST node is strictly `exp.Select` (reject `DROP`, `DELETE`, `UPDATE`, `INSERT`, `PRAGMA`, and multi-statement queries).
2. **Schema Hallucination Check:** Walk the AST to extract all `exp.Table` and `exp.Column` identifiers and verify they exist in the real database schema dictionary.
3. **Resource Bounding:** Inject `LIMIT 1000` if no limit is present and execute against a **read-only SQLite URI (`file:db.sqlite?mode=ro`)** with a strict **5.0-second timeout**. *(Note: Database-level read-only permissions are the actual security boundary; the `sqlglot` AST check is a fast, zero-cost syntax and hallucination filter).*
4. **Bounded Retry Loop (Max 1 Retry):** If `sqlglot` validation or SQLite execution raises an error, feed `(Failed SQL + Exact Error Message + Pruned DDL)` back to the local 7B model for **at most 1 retry**. Most syntax/column typos resolve on the first retry; subsequent retries burn P95 latency on queries the 7B model fundamentally misunderstood. If Retry 1 fails, escalate immediately to the Frontier API.

### Component 5: Gate 2 — Post-Generation Confidence Verifier
The dominant failure mode in Text-to-SQL is **silent semantic failure**: a query that parses and executes without error (`Valid SQL = 100%`) but returns the wrong numbers due to a bad `JOIN` condition or missing filter.
* **Clause-Level Token Logprobs:** Extract token logprobs from vLLM's single greedy forward pass (`logprobs=1`, 0ms extra latency). Using the `sqlglot` token spans, compute:
  1. **Sequence Mean Logprob** across the full SQL query.
  2. **Structural Minimum Logprob** over table/column identifiers, `JOIN ... ON` keys, and `GROUP BY / HAVING` clauses.
* **Calibration & Escalation:** Plot empirical accuracy against logprob quantiles on `train`/`dev`. If a query executes cleanly on pass 1 and its structural minimum logprob exceeds $\tau_{\text{post}}$, return the result immediately ($0 API cost). If confidence falls below $\tau_{\text{post}}$ (or if you optionally trigger $k=3$ execution self-consistency *only* inside the narrow uncertain logprob band and the result sets disagree), escalate to the Frontier model.

### Component 6: Closed-Loop QLoRA Fine-Tuning (Model-Systems Co-Design)
Instead of treating fine-tuning as an isolated accuracy exercise, use fine-tuning to **shift the router's cost-latency Pareto curve**:
* **Failure Mining on `train`:** Inspect the exact queries in BIRD `train` where the base 7B model failed and forced Gate 1 / Gate 2 to escalate (e.g., conditional ratios `SUM(CASE WHEN ...)/COUNT(*)`, date diff formatting, and 3-table joins).
* **QLoRA SFT:** Fine-tune `Qwen2.5-Coder-7B-Instruct` on BIRD `train` using QLoRA ($r=16, \alpha=32$, all linear layers).
* **Router Re-Calibration:** Re-evaluate the Dual-Gate pipeline with the fine-tuned 7B weights and re-calibrate $\tau_{\text{pre}}$ and $\tau_{\text{post}}$. Measure how much **Cloud Escalation Rate ($\%$)** drops at a fixed target accuracy (e.g., maintaining 93% of Frontier EX% while reducing cloud API calls from 48% down to 22%).

### Component 7: Serving, Load Testing & Observability
Wrap the pipeline in an asynchronous **FastAPI** gateway, instrument stage-level spans with **OpenTelemetry / Prometheus**, add an exact + semantic Redis query cache in front of Stage 1, containerize via **Docker Compose**, and run a **Locust** concurrent load benchmark (e.g., 1 to 30 concurrent users) to demonstrate queue-aware routing spillover ($\beta > 0$) protecting P95 latency under GPU saturation.

---

## 4. The Master Deliverable: 7-Row Ablation Table & Pareto Curve

Every architectural claim is backed by filling in this table on the **BIRD `dev` split** (1,534 queries):

| # | System Configuration | Schema Recall (%) | Valid SQL (%) | Execution Acc (EX%) | P50 Latency (ms) | P95 Latency (ms) | Cloud Escalation (%) | Cost / 1k Queries ($) |
|---|---|---|---|---|---|---|---|---|
| 1 | **Frontier Baseline** (Full Schema) | 100.0% | ? | ? | ? | ? | 100.0% | ? |
| 2 | **Frontier + Hybrid FK-Graph Retrieval** | ? | ? | ? | ? | ? | 100.0% | ? |
| 3 | **Base 7B Local Only** (vLLM 4-bit AWQ) | ? | ? | ? | ? | ? | 0.0% | ? |
| 4 | **Base 7B + `sqlglot` AST + 1 Retry** | ? | ? | ? | ? | ? | 0.0% | ? |
| 5 | **Naive Cascade** (7B $\rightarrow$ Retry $\rightarrow$ Gate 2 $\rightarrow$ Frontier) | ? | ? | ? | ? | ? *(High)* | ? | ? |
| 6 | **Dual-Gate** (Gate 1 Pre-Router + Gate 2 Cascade) | ? | ? | ? | ? | ? *(Low)* | ? | ? |
| 7 | **Dual-Gate + QLoRA SFT 7B** (Closed-Loop) | ? | ? | ? | ? | ? *(Lowest)* | ? *(Lowest)* | ? *(Best)* |

### Key Plots to Generate for the README:
1. **Cost ($/1k) vs. Execution Accuracy (EX%) Pareto Curve:** Sweeping router thresholds $(\tau_{\text{pre}}, \tau_{\text{post}})$ from $0.0$ to $1.0$, plotted against a **Random Router baseline** and **Naive Cascade baseline**, reporting **APGR (Average Performance Gap Recovered)**.
2. **P95 Latency Breakdown Bar Chart (Row 5 vs. Row 6):** Proving visually how Gate 1 eliminates wasted local GPU retry latency on complex queries.
3. **Concurrent Load vs. P95 TTFT Curve (`locust`):** Showing static routing saturating the 12GB GPU queue vs. dynamic queue-aware thresholding ($\tau_{\text{eff}}(t)$) spilling burst traffic to the cloud API to keep P95 bounded.

---

## 5. Build Order & Milestone Schedule

| Phase | Timeline | Scope | Definition of Done |
|---|---|---|---|
| **Phase 1** | Week 1 | **Component 0 & 1:** SQLite eval harness + BM25/BGE/FK-Steiner schema retriever | Rows 1 & 2 of the ablation table populated; Schema Recall & Token Compression logged. |
| **Phase 2** | Week 2 | **Component 3 & 4:** vLLM 7B AWQ setup + `sqlglot` AST guardrails + 1-retry loop | Rows 3 & 4 populated; recovery rate of retry loop quantified. |
| **Phase 3** | Weeks 3–4 | **Component 2 & 5:** Gate 1 Pre-Router + Gate 2 Logprob Verifier + Pareto curves | Rows 5 & 6 populated; Cost-vs-EX% and P95 latency charts added to public GitHub README. **(Start applying here!)** |
| **Phase 4** | Weeks 5–6 | **Component 6 & 7:** QLoRA SFT on BIRD `train` + FastAPI/Docker + Locust load test | Row 7 populated; shift in router escalation rate and queue-aware spillover benchmarked. |

---

## 6. Explicit Risks & Honest Engineering Boundaries

Stating these limitations proactively in the README and interviews signals production maturity:
1. **Silent Semantic Failures:** `sqlglot` and the retry loop only catch syntax errors, schema hallucinations, and runtime exceptions. A syntactically valid query that computes the wrong business logic passes Component 4. Gate 2 mitigates this via structural logprobs, and the README will categorize the remaining false-confident failures by SQL pattern.
2. **Schema Recall Ceiling:** If Component 1 drops a required table, neither the 7B model nor the Frontier model can write correct SQL. Schema Recall is tracked separately so retrieval errors are never misdiagnosed as generation errors.
3. **Strict Split Hygiene:** Gate 1 router weights, Gate 2 calibration thresholds, and QLoRA adapters are trained exclusively on **BIRD `train`**. **BIRD `dev`** is touched strictly for final evaluation table reporting.

---

## 7. The 30-Second Interview Pitch

> *"I built a Text-to-SQL inference engine on the BIRD benchmark designed around three production constraints: Execution Accuracy, P95 Tail Latency, and Cost per 1,000 queries. First, I built a hybrid BM25 + BGE retriever with foreign-key Steiner tree expansion that cut prompt tokens by **X%** while preserving **Y%** schema recall. Second, I proved that a naive local-to-cloud cascade ruins P95 latency because hard queries waste seconds failing and retrying on the local GPU before escalating. To fix that, I trained a sub-millisecond Pre-Generation Router that reuses the Stage 1 query embedding and schema-graph complexity metrics to route doomed queries straight to the cloud, while keeping **N%** of traffic on a self-hosted 4-bit 7B model with `sqlglot` AST guardrails and clause-logprob verification. Finally, I used the router's failure logs to QLoRA fine-tune the 7B model on BIRD train, which increased local traffic retention from **A%** to **B%** and recovered **C%** of frontier accuracy at **D%** of the API cost."*