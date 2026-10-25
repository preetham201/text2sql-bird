# Phase 0 — Setup (revision notes)

**Goal:** be able to load a BIRD database and run a gold query from code. ✅ Done.

---

## What we set up

| Thing | Choice | Why |
|---|---|---|
| Version control | git + private GitHub repo `preetham201/text2sql-bird` | History from day 1; go public at end of Phase 3 |
| Commit email | GitHub noreply address | Keeps real email out of public commit history |
| Python env | `uv`, Python 3.12, project-local `.venv/` | Isolation from other projects; reproducible via `uv.lock`; 3.12 is safest for the ML stack (vLLM/torch lag new Python versions) |
| Frontier model | Claude (Anthropic API) | User has credit ($9.66) |
| Secrets | `.env` file, gitignored | Never commit API keys |
| Data | `data/bird/dev/`, gitignored | 1.4 GB — too big for git, and it's public data anyone can download |

**Daily commands**
- Run code: `uv run python script.py` (no activation needed)
- Add a package: `uv add <pkg>`
- Rebuild env on another machine: `uv sync`

---

## The BIRD benchmark

**BIRD** = "BIg Bench for LaRge-scale Database Grounded Text-to-SQL". Harder than older benchmarks (e.g. Spider) because databases are real, messy, and large, and many questions need domain knowledge.

### Two splits
| Split | Size | Role |
|---|---|---|
| **train** | ~9.4k questions, ~70 DBs | Few-shot examples, fine-tuning data |
| **dev** | 1,534 questions, 11 DBs | **Evaluation only** — every reported number |

**Leakage rule:** never put dev questions in prompts as examples or in training data. Dev is the exam. (BIRD's real test set is hidden; dev is what everyone reports on.)

### Dev files
- `dev.json` — each item: `question_id`, `db_id`, `question`, `evidence`, `SQL` (gold), `difficulty`
- Difficulty split: 925 simple / 464 moderate / 145 challenging
- `dev_databases/<db_id>/<db_id>.sqlite` — the actual databases
- `dev_databases/<db_id>/database_description/*.csv` — column descriptions per table (used in Phase 2)
- `dev_tables.json` — schema metadata incl. foreign keys

### The `evidence` field
BIRD gives a hint per question with domain knowledge the model couldn't guess, e.g.
> Eligible free rate for K-12 = `Free Meal Count (K-12)` / `Enrollment (K-12)`

It goes into the prompt. Results "with evidence" and "without evidence" are reported separately in the literature — we use evidence.

---

## Key concept: Execution Accuracy (EX)

We don't compare SQL text — we **run** the predicted SQL and the gold SQL and compare **results**. Many different queries are correct:

```sql
SELECT COUNT(*) FROM patients;
SELECT COUNT(id) FROM patients;   -- same answer → both correct
```

So the harness must execute both and compare result sets.

---

## Read-only connections

```python
sqlite3.connect("file:path/to/db.sqlite?mode=ro", uri=True)
```
`mode=ro` opens the file read-only — a generated `DROP TABLE` can't damage it. This is the SQLite version of the "read-only database role" idea: the real safety comes from permissions, not from checking the SQL text.

---

## Budget notes (Claude API, per 1M tokens, Oct 2026)

| Model | Input / Output |
|---|---|
| Opus 5.5 | $4 / $20 |
| Sonnet 5.5 | $2 / $10 |
| Haiku 5.5 | $0.10 / $0.50 |

- A naive full-schema dev run is ~9M input tokens (estimate — to be measured).
- Cost levers: **prompt caching** (schema repeats across questions in the same DB; cached reads much cheaper) and **Batch API** (50% off, async).
- Plan: debug on Haiku with small subsets → measure real tokens → choose baseline model.

---

## Interview questions you should be able to answer
1. *Why execution accuracy instead of comparing SQL strings?* — Many SQL queries are semantically equivalent; string match would mark correct answers wrong.
2. *Why never use dev for few-shot examples?* — Leakage: the score would measure memorization, not generalization.
3. *Why a project-specific env?* — Reproducibility (lock file) and isolation of version-sensitive ML dependencies.
