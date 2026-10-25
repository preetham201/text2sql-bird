# Project explanation: a Text-to-SQL system with measured reliability and cost

## The problem

A user asks a question in English ("which patients had a heart rate above 120 for more than 6 hours?"). The system must produce SQL, run it safely, and return a correct answer. Four things make this hard:

1. **Schemas are huge.** You can't paste 500 tables into a prompt. It's expensive, slow, and accuracy drops.
2. **LLMs write plausible but wrong SQL.** Wrong joins, hallucinated columns, wrong aggregation.
3. **Frontier APIs are expensive and slow**, and for sensitive data you often can't send it to them at all.
4. **Executing generated SQL is a security risk.**

Your project is a system that handles all four, with a number proving each part helps.

## Request flow

```
Question
  → [1] Schema retrieval (prune to relevant tables/columns)
  → [2] SQL generation (small self-hosted model)
  → [3] Guardrails (parse + validate before execution)
  → [4] Execute (read-only, timeout)
        ├─ error → feed error back → regenerate (max 2-3 retries)
        └─ success → [5] Confidence check
                        ├─ high → return result
                        └─ low  → escalate to frontier model → return result
  → Log everything (latency, cost, path taken, retries)
```

## Component by component

### 0. Eval harness (build this first)
Runs a model over BIRD dev, executes predicted and gold SQL, and compares result sets. It reports:
- **Execution accuracy (EX%)**
- Valid SQL rate
- p50/p95 latency
- Cost per 1k queries (API tokens plus your GPU rental hours)

Every other component gets judged by this harness. Without it, you have opinions instead of results.

### 1. Hybrid schema retrieval
- **Index** every table and column with name, type, description, and a few sample values. Sample values matter, because that's how the model learns that `status = 'A'` means active.
- **Retrieve** with BM25 (keyword) plus dense embeddings (e.g. a BGE model), merge the rankings (reciprocal rank fusion), then rerank the top candidates with a cross-encoder.
- **Expand with foreign keys.** If you retrieve `orders` and `customers`, you must also include the join keys and any bridge table. This is where naive pruning breaks: drop one join table and the query is impossible.
- **Measure schema recall** (were the gold SQL's tables in the retrieved set?) separately from EX%. If recall is 85%, your ceiling is 85%, and you'll know why.

### 2. Generation
- Prompt = pruned schema (as DDL) + sample values + BIRD's evidence hint + the question + a few examples.
- **Order of models:** frontier baseline first, then an off-the-shelf open model (Qwen-Coder class, 7-8B), served with **vLLM** at 4-bit (AWQ/GPTQ) on your 12GB GPU. Only then consider fine-tuning.

### 3. Guardrails
Parse with **sqlglot** before anything touches the database:
- Allow only `SELECT`; reject DDL/DML and multi-statement input.
- Check that every referenced table and column exists in the real schema.
- Inject a `LIMIT` and set a statement timeout.

**Don't confuse this with security.** The real protection is a read-only database role with restricted permissions. The AST check catches mistakes early and cheaply, but it isn't your security boundary. Say this in an interview and it signals maturity.

### 4. Self-healing retry loop
On a parse or execution error, send the error message, the failed SQL, and the schema back to the model and ask for a fix, up to 2-3 attempts.
- **Metric:** recovery rate, meaning what fraction of first-attempt failures end up correct.
- **Limitation to state openly:** this only fixes errors that raise exceptions. A query that runs but returns the wrong answer sails through. That's the dominant failure mode, and the loop does nothing for it.

### 5. Confidence routing
Decide when the small model's answer isn't trustworthy and escalate to the frontier model. Candidate signals:
- Token logprobs (mean or minimum over the SQL)
- Number of retries needed
- **Self-consistency:** sample k queries, execute all, and check whether the results agree. This is usually the strongest signal but costs k times the generation.

The procedure: collect these signals on dev, plot accuracy against each signal, pick thresholds, and produce a **cost vs accuracy curve**. That curve is the centerpiece of your writeup. If logprobs turn out to be poorly calibrated, report that honestly and switch signals. A negative result you can explain is worth more than a claim you can't defend.

### 6. Fine-tuning (phase 2, optional)
- **SFT with QLoRA** on BIRD train (never dev). Measure whether it closes the gap to the frontier model.
- **DPO** only if SFT plateaus. Build preference pairs by sampling the model, executing the outputs, and pairing a correct result (preferred) against an incorrect one (rejected). Expect modest gains and significant effort.

### 7. Serving and observability
FastAPI service, per-stage latency logging, tracing (Langfuse or OpenTelemetry), caching of repeated questions, an audit log of every generated query, and a Docker deployment. This is what separates a project from a notebook.

## The deliverable: an ablation table

| Configuration | EX% | p95 latency | Cost/1k queries |
|---|---|---|---|
| Frontier, full schema (baseline) | ? | ? | ? |
| + schema retrieval | ? | ? | ? |
| + guardrails + retries | ? | ? | ? |
| Small model alone | ? | ? | ? |
| Small model + routing to frontier | ? | ? | ? |
| + SFT | ? | ? | ? |

Every question marks a number you haven't produced yet. Filling it in is the project.

## Build order for your hours

| Weeks | Work | Done means |
|---|---|---|
| 1-2 | Eval harness + frontier baseline | First table row |
| 3-4 | Schema retrieval, sqlglot, retry loop | Ablations, repo public, start applying |
| 5-7 | vLLM + open model + routing | Cost/accuracy curve |
| 8-10 | Optional: SFT | Gap closed or honest negative result |
| 10+ | Optional: MIMIC port, DPO | Domain story |

## Biggest risks

1. **Scope.** Four subsystems on 10-15 hours a week. Ship phases 1-3 before touching fine-tuning.
2. **Wrong-but-executes queries.** Most real failures live here, and your guardrails don't catch them. Your failure analysis should categorize them.
3. **Benchmark leakage.** Train on train, report on dev, and say so in the README.
4. **Weak small model.** It may well lose to the frontier model on accuracy. That's fine if your claim is "X% of frontier accuracy at Y% of the cost."

## How you'd explain it in an interview

"I built a Text-to-SQL system and treated it as a reliability and cost problem. Retrieval cut the prompt size by X and kept schema recall at Y. Guardrails and retries recovered Z% of failures. A small self-hosted model handles N% of queries and escalates the rest, which cut cost by W% at a V-point accuracy drop. Here's where it still fails."

You can't say that sentence yet because none of the letters have values. Start with the harness and the baseline run this weekend.
