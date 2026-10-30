# Phase 1 — Why the baseline model is Claude Sonnet 5.5

Decision (2026-10-30): the frontier baseline is **`claude-sonnet-5-5`, effort `low`, full-schema prompt, prompt caching on**.
This file records why, with the numbers we measured, so the choice can be defended in an interview.

---

## What the baseline has to do

The baseline is **row 1 of the ablation table**. Every later phase (retrieval, guardrails, small model, routing)
is measured against it. So it must be:

1. **Strong.** It stands for "what a frontier model gets". A weak baseline makes every later
   improvement look bigger than it is, which is the easiest way to fool yourself and the reader.
2. **Affordable for a full dev run (1,534 questions), more than once.** Budget: $9.66 total credit,
   and Phases 2–3 need re-runs too.
3. **Reproducible.** Same settings, saved predictions, re-scorable.

---

## The candidates

Prices are $ per 1 million tokens (input / output), from `generation/cost.py`.

| Model | Price in / out | Role in the Claude lineup |
|---|---|---|
| Claude Haiku 5.5 | $0.10 / $0.50 | smallest, fastest, cheapest |
| **Claude Sonnet 5.5** | **$2 / $10** | mid tier: strong and fast |
| Claude Opus 5.5 | $4 / $20 | top general model |
| Claude Fable 5.1 | $10 / $50 | most capable, built for long agentic work |

## What we measured (first 20 dev questions, same prompt)

| Run | EX | Output tokens / question | Cost per 1k questions |
|---|---|---|---|
| Haiku 5.5, effort medium | 75% | 313 | $0.33 |
| Sonnet 5.5, effort medium | 90% | 140 | $4.89 |
| Sonnet 5.5, effort low | 95% | 131 | $4.80 |
| Sonnet 5.5, effort low + caching | 90% | 128 | $2.51 |

Caveat: 20 questions, all from one database, mostly "simple". 1 question = 5 points here, so these
numbers say "Sonnet is clearly better than Haiku", not "Sonnet low beats Sonnet medium".

---

## Full-dev result with this choice
68.38% EX on 1,534 questions for **$2.64 total ($1.72 per 1k)**, below the $4–6 estimate because 94% of
prompt tokens were read from cache over the full run (vs 77% on the 20-question test).

---

## Why each other model was rejected

### Haiku 5.5: too weak to be "the frontier"
- 75% vs Sonnet's 90–95% on the same questions, a 15–20 point gap even on easy ones.
- Using it as row 1 would make the project's main question ("how close can a cheap model get to the
  frontier?") meaningless: we'd be comparing cheap against cheap.
- It's still useful: it's our **debugging model** (a 20-question test costs under 1 cent).

### Opus 5.5: about 2× the cost for a gain we can't afford to measure
- Twice Sonnet's price per token. Sonnet's run cost $2.64, and ~70% of that was output tokens.
  Opus output is $20/1M and its thinking can't be switched off, so a full run would be about **$5–8 even
  with caching**: most of the remaining budget on one run, with nothing left for Phases 2–3.
- On BIRD, the gap between top models is a few points. Paying 2× for a possible few points is
  not worth losing the ability to run the ablations, which are the actual point of the project.
- Also: Opus 5.5 can't turn thinking off, and its default effort is `medium`, so output (the
  expensive part) tends to be longer.

### Fable 5.1: wrong tool for the job
- 5× Sonnet's price (~$13+ per full run even with caching): more than the whole budget.
- It's built for long, multi-step agentic tasks. Writing one SQL query is a single short answer,
  so most of what it's good at doesn't matter here.

---

## Why Sonnet 5.5 at effort `low`

- **Effort** sets how much the model thinks before answering. Thinking tokens are billed as output
  ($10 / 1M on Sonnet, 5× the input price).
- On our sample, `low` was as accurate as `medium` and slightly cheaper. One SQL query from a
  given schema doesn't need long reasoning.
- We set effort **explicitly** in every call, so the run doesn't silently change if the API default changes.

## Why prompt caching

- The system prompt (instructions + full schema, about 1,600–2,000 tokens) is **identical for every
  question on the same database**. BIRD dev has 11 databases and ~140 questions each.
- With caching, the API stores that prefix for 5 minutes. Re-reading it costs ~10% of the normal input
  price (writing it once costs 125%).
- Measured: 77% of input tokens came from cache, and **cost per 1k questions fell from $4.80 to $2.51**.
- Caching changes **cost only, never the answer**: the model sees exactly the same text.

## Rejected cost tricks (for now)
- **Batch API (50% off):** results come back asynchronously (minutes to hours), so per-question
  latency, one of our metrics, becomes meaningless. Worth it later for big sweeps that don't measure latency.
- **Pruning the schema:** that's Phase 2's experiment. Doing it now would remove the baseline it's compared against.

---

## Honest caveats to state in the README
- **Run-to-run randomness:** Sonnet scored 95% then 90% on the same 20 questions with the same
  settings (one question changed). Sonnet 5.5 doesn't allow setting `temperature`, so we can't force
  identical outputs. Reproducibility therefore means: saved predictions + `--rescore` gives exactly
  the same EX%.
- **Possible contamination:** BIRD dev is public, so the model may have seen some of it in pretraining.
- **The choice is about this budget:** with unlimited credit, the right move would be to also run Opus
  as a reference ceiling.

## Interview questions
1. *Why not use the strongest model as the baseline?* — The baseline must be strong **and** affordable to
   re-run; Opus would eat the whole budget in one run and leave no room for the ablations.
2. *Why not the cheapest?* — A weak baseline inflates every later improvement.
3. *How did prompt caching cut cost without changing accuracy?* — Same prefix per database; cached reads
   cost ~10% of input price; the model sees identical text.
4. *Why not the Batch API?* — Async results break the latency metric; good later for big runs that don't measure latency.
5. *Your model is non-deterministic, so how is the result reproducible?* — Predictions are saved; re-scoring them is exact.
