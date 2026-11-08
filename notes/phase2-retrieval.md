# Phase 2 — Schema retrieval (end-to-end notes)

> Read this top to bottom once. After it you should be able to explain **what Phase 2 is, why it
> exists, how it works, what we found, and how it connects to Phase 1 and Phase 3**.
> Code blocks marked **⭐ MUST UNDERSTAND** are the ones to study line by line (and rewrite by hand).
> Everything else: understand the idea, skim the code.

---

## 0. One-paragraph summary (say this in an interview)

Phase 1 gave the model the **whole database schema** in every prompt. Phase 2 asks: *can we send
only the part of the schema the question needs*, so prompts get smaller, without losing the tables
and columns the model needs? We built a retrieval pipeline. It ranks every column of the
database against the question using keyword search (BM25), embeddings (BGE) and their fusion (RRF),
plus exact matches of column names and cell values. It keeps the top-ranked tables and adds
bridge tables via the foreign-key graph. We measured **schema recall** (did we keep every column
the gold SQL uses?) **offline, for $0**, by parsing the gold SQL with sqlglot. We tuned on BIRD
**train** (9,242 questions) and confirmed on **dev**. Result: **98.6% recall on dev with the schema
text at 88% of full size**. On small schemas pruning barely helps; on large ones (>150 columns) it
cuts the schema to ~42% at 93% recall. Because Phase 1 already caches the full schema, pruning
is expected to make the Sonnet run slightly *more* expensive (~$3.4 vs $2.64). The real payoff comes in Phase 4
(small local model, short context) and on large schemas.

---

## 1. Where Phase 2 sits in the project

```
Phase 1  harness + baseline          question ─► FULL schema prompt ─► Claude ─► SQL ─► execute ─► compare      = row 1 (68.38%)
Phase 2  schema retrieval            question ─► RETRIEVE schema ─► prompt ─► Claude ─► SQL ─► execute ─► compare = row 2
Phase 3  guardrails + retry                                                          SQL ─► VALIDATE ─► (error? retry once) ─► execute
Phase 4  small self-hosted model                              prompt ─► 7B model (needs SHORT prompts → Phase 2 matters most here)
Phase 5  routing                     retrieval scores become router FEATURES (how confident was retrieval?)
```

**What Phase 2 reuses from Phase 1 (unchanged):** the loader (`eval/data.py`), executor, comparator,
gold cache, metrics, the Claude client, the run loop. The harness is the "ruler"; Phase 2 only
changes **one thing in the prompt**: which part of the schema goes in.

**What Phase 2 hands to Phase 3:** a pipeline that produces a smaller schema per question, plus the
list of kept tables/columns. Phase 3's guardrail checks "does the SQL only use tables/columns that
exist?". With a pruned schema, it can also check "...that exist **in what we showed the model**".
If the model references a column we pruned away, that's a retrieval miss, and the retry can add it
back. Phase 3 also needs to know *why* queries fail; Phase 2's recall numbers tell us how many
failures could be retrieval's fault (at most ~1.4% of dev questions).

**What Phase 2 hands to Phase 4/5:** BIRD train is now downloaded and loadable, with a **train gold-results
cache** (needed to label which questions a small model gets right → router training data in Phase 5).

---

## 2. The question Phase 2 answers (H1) and the honest expectation

**H1:** *Retrieval can cut prompt tokens a lot while keeping schema recall high enough that EX% doesn't drop.*

What Phase 1 already told us before writing any code:
- BIRD dev schemas are small (11–199 columns, prompt ~1,670 tokens), so there isn't much to cut.
- 94% of the prompt was already read from cache at 10% price, so cutting tokens saves little money on Sonnet.
- Only ~8% of labelled failures were "wrong table", so retrieval can't add much accuracy.

So Phase 2's first job is **not to lose** anything. That's why **schema recall** is the key metric:

> If retrieval drops a column the query needs, the model *cannot* write the correct SQL.
> So **column recall is a ceiling on EX%** for the pruned prompt. 98.6% recall → at most 1.4% of
> questions become impossible.

---

## 3. The data we start from

BIRD gives three things per database (`data/bird/<split>/<split>_databases/<db>/`):
1. the `.sqlite` file (tables, columns, types, PK/FK constraints, the data itself)
2. `database_description/<table>.csv`: human-written column descriptions + value meanings
   (e.g. `Charter School (Y/N)`: "0: N; 1: Y")
3. the questions (`dev.json`, `train.json`) with gold SQL and a hint (`evidence`)

| | DBs | Tables per DB | Columns per DB | Questions |
|---|---|---|---|---|
| dev | 11 | 3–13 | 11–199 (798 total) | 1,534 |
| train | 69 | 2–65 | 6–455 (3,539 total) | 9,428 |

Train has much bigger schemas than dev, so retrieval matters more there. There's **no database
overlap** between train and dev, so tuning on train and testing on dev is a real generalisation test.

**Why tune on train, report on dev:** if we picked K by looking at dev recall, the dev number would
be optimistically biased (we'd have chosen the setting that happened to suit those 11 DBs). Train
is for choosing; dev is for reporting. Same rule as never training on the test set.

---

## 4. The pipeline, step by step

```
question + hint
   │
   ▼
[rank]   BM25 ranking ─┐
         dense ranking ┴─► RRF fusion ─► (optional cross-encoder rerank)
   │
   ▼
[exact]  columns named in the question/hint  ─┐ moved to the
         columns containing a mentioned value ┴ top of the ranking
   │
   ▼
[select] top-K columns ─► their tables ─► FK expansion (bridge tables)
   │
   ▼
[render] original CREATE TABLE text of the kept tables ─► into the Phase 1 prompt
```

Files (each depends only on files above it):

| # | File | Role | Study level |
|---|---|---|---|
| 1 | `eval/data.py` | `load_split("train"/"dev")` (extended at the start of Phase 2) | ⭐ (done in Phase 1) |
| 2 | `retrieval/schema.py` | Extract tables/columns/types/PK/FK/descriptions/samples; render DDL | ⭐ parts |
| 3 | `eval/gold_columns.py` | Parse gold SQL → which tables/columns it uses | ⭐ |
| 4 | `retrieval/text.py` | Column → searchable text; tokenizer | skim |
| 5 | `retrieval/bm25.py` | Keyword ranking | idea + 10 lines |
| 6 | `retrieval/dense.py` | Embedding ranking | idea + 10 lines |
| 7 | `retrieval/fusion.py` | RRF + cross-encoder rerank | ⭐ `rrf()` |
| 8 | `retrieval/exact.py` | Name mentions + value matches | idea |
| 9 | `retrieval/fk_expand.py` | FK graph, BFS shortest paths, inferred links | ⭐ `connect_tables` idea |
| 10 | `retrieval/pipeline.py` | Ties it together; `RetrievalConfig` | ⭐ |
| 11 | `eval/schema_recall.py` | Offline recall/compression sweep | ⭐ the metric part |
| 12 | `generation/prompt.py`, `generation/claude_client.py`, `eval/run.py`, `eval/metrics.py`, `eval/gold_cache.py` | Phase 1 files, extended | ⭐ the diffs |

### 4.1 `retrieval/schema.py`: structured schema

Phase 1 sent raw `CREATE TABLE` text. Retrieval needs **structure**: something we can score column by
column. For every database we build a `DbSchema` with `Column` objects:

```python
@dataclass
class Column:
    table: str
    name: str
    type: str
    is_pk: bool
    description: str = ""        # from database_description/<table>.csv
    value_description: str = ""  # e.g. "0: not charter; 1: charter"
    samples: list[str] = ...     # 3 distinct values, e.g. ['Alameda', 'Alpine', 'Amador']

    @property
    def key(self) -> str:
        return f"{self.table}.{self.name}".lower()   # "frpm.free meal count (k-12)"
```

**⭐ MUST UNDERSTAND: `Column.key`.** Every module talks about columns through this one id:
lower-case `"table.column"`. Lower case because SQLite names are case-insensitive (`CDSCode` = `cdscode`),
and the gold SQL and our schema don't always agree on case.

How it's filled (skim):
- `PRAGMA table_info(t)` → one row per column: name, type, is-PK. `PRAGMA foreign_key_list(t)` → FKs.
  PRAGMA = SQLite's built-in "tell me about the schema" commands.
- Descriptions CSVs come in **mixed encodings** (UTF-8, UTF-8 with BOM, Windows-1252), so
  `_read_csv_any_encoding` tries them in order. Real data is messy; this is normal.
- Sample values: `SELECT DISTINCT col ... LIMIT 3`. `LIMIT` lets SQLite stop early even on huge tables.
- Everything is cached to `data/bird/<split>/schemas.pkl` (train takes ~2 min to build once).

Two ways to turn a (pruned) schema back into prompt text:
- `original_ddl(db_path, tables)`: the database's **own** CREATE TABLE text, only for the kept tables.
- `render_ddl(schema, keep)`: rebuilds CREATE TABLE from our structure; can drop single columns and
  add `-- description` comments.

**Decision: table-level pruning uses `original_ddl`.** We found that rebuilt DDL is ~40% shorter than
the original *for the same tables* (formatting differences). If row 2 used rebuilt DDL, part of the
"compression" would really be formatting, and the ablation would change two things at once. With
`original_ddl` the row-2 prompt is the row-1 prompt with some tables deleted. We verified this:
the refactored Phase 1 prompt matches the saved prompts of the 200 questions we compared,
byte for byte.

### 4.2 `eval/gold_columns.py`: what does the gold SQL need? (the ground truth for recall)

To measure recall we need, per question, the set of columns the correct query uses. We get it by
**parsing** the gold SQL, not executing it.

**AST (abstract syntax tree):** SQL as a tree: `Select` → `From(Table)`, `Join(Table)`, `Where(...)`,
with `Column` leaves. sqlglot parses SQL into this tree, and we walk it.

The hard part is **aliases**: `SELECT T1.name FROM schools AS T1` → the column node says its table
is `T1`. sqlglot's `qualify()` uses the schema to rewrite every column as `alias.column` (even unqualified
ones like `name`), so we can map alias → real table:

```python
# ⭐ MUST UNDERSTAND (eval/gold_columns.py, core of used_columns)
tree = sqlglot.parse_one(sql, read="sqlite")
tree = qualify(tree, schema=_sqlglot_schema(schema), dialect="sqlite", ...)

alias_to_table = {}
for t in tree.find_all(exp.Table):            # every table node, also inside subqueries
    name = t.name.lower()
    if name in real_tables:
        tables.add(name)
        alias_to_table[t.alias_or_name.lower()] = name   # "t1" -> "schools"

for c in tree.find_all(exp.Column):           # every column reference
    table = alias_to_table.get(c.table.lower())
    key = f"{table}.{c.name.lower()}"
    if table and key in known_columns:       # skip derived columns of subqueries/CTEs
        columns.add(key)
```

Parse success: **dev 1,534/1,534, train 9,242/9,428 (98%)**. The 186 train failures are mostly
broken gold SQL (e.g. the same alias `T2` used for two tables), so they're excluded and counted, not guessed.

### 4.3 Turning columns and questions into searchable text (`retrieval/text.py`)

Each column becomes one short "document":
```
"frpm | Free Meal Count K 12 | Free Meal Count (K-12) | <description> | values: 565.0, 186.0, 134.0"
```
- `split_identifier`: `CDSCode` → `CDS Code`, `NumTstTakr` → `Num Tst Takr`, `free_meal_count` → `free meal count`.
  Database names glue words together; splitting lets the question word "code" match `CDSCode`.
- The **query** is `question + hint`. The hint matters a lot: BIRD hints often name columns outright
  ("number of test takers refers to `NumTstTakr`").

### 4.4 Ranker 1: BM25 (`retrieval/bm25.py`)

**Idea:** score a document higher when it contains the query's words, weighted by **rarity**
(IDF: a word in every column, like "id", is worth little; "enrollment" is worth a lot), with
diminishing returns for repeats and a penalty for long documents. It's exact-word matching:
great when the question/hint uses the column's own words, blind to synonyms.

One index per database (only that DB's columns compete):
```python
index = BM25Okapi([tokenize(column_text(c)) for c in schema.columns])
scores = index.get_scores(tokenize(query))      # one score per column
```

### 4.5 Ranker 2: dense embeddings (`retrieval/dense.py`)

**Idea:** an embedding model maps text to a vector (384 numbers for `bge-small-en-v1.5`) so that
similar meanings point in similar directions. Embed each column once, embed the question, rank by
**cosine similarity** = dot product of length-1 vectors. Catches synonyms ("pupils" → Enrollment).

```python
vecs = model.encode(column_texts, normalize_embeddings=True)   # once per DB
q    = model.encode([QUERY_PREFIX + query], normalize_embeddings=True)[0]
scores = vecs @ q                                               # cosine similarity per column
```
- BGE wants an instruction prefix on **queries only** ("Represent this sentence for searching relevant passages: ").
- **Decision: no FAISS.** The plan listed FAISS, but a database has at most a few hundred columns, so
  brute-force comparison is one tiny matrix multiply. FAISS (approximate nearest-neighbour search) pays off at
  millions of vectors. Adding it here would be complexity with zero gain. (Good interview answer:
  "I chose not to use X because at this scale Y is exact and faster.")

### 4.6 Fusion: RRF + optional cross-encoder rerank (`retrieval/fusion.py`)

**⭐ MUST UNDERSTAND: RRF (Reciprocal Rank Fusion)**
```python
def rrf(rankings, k=60):
    fused = {}
    for ranking in rankings:
        for rank, (key, _) in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda kv: -kv[1])
```
- Each ranker "votes" `1/(60 + rank)` for every column; votes are summed.
- **Why ranks, not scores?** BM25 scores range 0–40, cosine 0.3–0.9. Averaging raw scores would let BM25
  dominate. Ranks put both on the same scale with no calibration needed.
- **Why k=60?** Standard value from the original paper. It flattens the curve, so one ranker's #1 doesn't
  outweigh a column both rankers put in their top 5.

**Cross-encoder rerank (optional, off by default):**
- BM25 and dense are "bi-encoder"-style: question and column are processed **separately**, compared at the end. Fast.
- A cross-encoder (`BAAI/bge-reranker-base`) reads question **and** column **together** in one pass,
  so it can model their interaction. More accurate, ~100× slower, so only applied to the top 30.

### 4.7 Exact matches (`retrieval/exact.py`): added after error analysis

After the first sweep, we **looked at the misses** (always do this before tuning). BM25 at K=10 missed
743 gold columns on 1,500 train questions:
- ~1/3 were **named in the question/hint** but ranked 13–26, because generic names like `title`, `name`
  exist in many tables, so BM25's rarity weighting pushes them down.
- many were **cell values**: "customer **Lillie Kim**" → `customer.first_name/last_name`;
  "**Hispanic**" → `race`. Nothing in the schema text says "Lillie", so no ranker can find it.
- the rest were mostly join keys (fixed by FK expansion, §4.8).

Fix: two exact-match signals, whose hits are **moved to the top of the ranking** before the top-K cut:
- **mentions:** a column's raw name (`StarRating`) or split name (`star rating`) appears as a whole phrase in the
  question/hint. Generic names (`id`, `name`, `year`, ...) don't count on their own.
- **values:** a 1–4 word n-gram of the question, or a `'quoted'` string in the hint, equals a stored value of a text
  column. Values come from a one-time scan of every text column (distinct values ≤40 chars, ≤50k per column;
  4.9M values on train). An ambiguous value boosts at most 3 columns.
  Memory design (v2, after the OOM incident, see `failures-log.md` F17): **one file per database**
  (`data/bird/<split>/values/<db>.pkl`), each value maps to an **int column index** instead of a repeated
  `"table.column"` string, files are **loaded lazily** with an LRU cache of 4 DBs. Loading went from 2.09 GB
  (all 69 DBs at once) to ≤93 MB (the biggest single DB); the build peaks at 165 MB.

This is a cheap version of the "value retriever" used in strong BIRD systems (CHESS, CodeS).
**It was the single biggest improvement:** BM25 table K=10 recall 90.7% → 93.5%; column K=10 71.6% → 78.7%.

### 4.8 FK expansion (`retrieval/fk_expand.py`)

**Problem:** retrieval may keep `customers` and `products`, but they only connect through
`transactions_1k`, which nothing in the question mentions. Without it, the model can't write the JOIN.

**Idea:** schema = graph (tables = nodes, FKs = edges). Connect kept tables with **shortest paths**
found by **BFS** (breadth-first search: explore neighbours level by level, so the first time you reach a
node it's via a shortest path). Add every table on those paths.

```python
# ⭐ MUST UNDERSTAND (idea): greedy connection, best-ranked table first
kept = [tables[0]]
for t in tables[1:]:
    path = _shortest_path(graph, set(kept), t)   # BFS from ALL kept tables at once
    for node in (path or [t]):                   # no path? keep t anyway
        if node not in kept:
            kept.append(node)
```

**Surprise in the data:** BIRD's declared FKs are **incomplete**. `debit_card_specializing.transactions_1k`
has `CustomerID`, `GasStationID`, `ProductID` but declares *no* FKs, and 3 train DBs declare none at all.
BIRD's own `*_tables.json` is no better (checked). So `links()` adds **inferred** links: same column name in two
tables, where it's the primary key of one (`transactions_1k.CustomerID` → `customers.CustomerID`). Generic names
(`id`, `name`) are skipped. On that DB: 2 declared FKs → 7 links.

(For column-level pruning, `join_columns()` also adds the FK columns on both ends + each table's PK.)

### 4.9 Selection + the config (`retrieval/pipeline.py`)

**⭐ MUST UNDERSTAND: the whole file is short; this is the heart of Phase 2.**

```python
@dataclass(frozen=True)        # immutable: a config can't change mid-run
class RetrievalConfig:
    method: str = "hybrid"     # bm25 | dense | hybrid | hybrid_rerank
    exact: bool = True         # name/value boost
    top_k: int = 20            # columns taken from the ranking
    granularity: str = "table" # keep whole tables, or only the columns
    fk_expand: bool = True
    descriptions: bool = False

def select(schema, ranking, cfg):
    top = [key for key, _ in ranking[:cfg.top_k]]       # top-K columns
    tables = []                                          # their tables, in rank order
    for key in top:
        t = key.split(".", 1)[0]
        if t not in tables:
            tables.append(t)
    if cfg.fk_expand:
        tables = connect_tables(schema, tables)          # + bridge tables
    if cfg.granularity == "table":
        keep = {c.key for c in schema.columns if c.table.lower() in tables}
    ...

def retrieve(ex, cfg):
    schema = load_schemas(ex.split)[ex.db_id]
    keep, tables = select(schema, rank(schema, query_text(ex), ex.split, cfg), cfg)
    return Retrieved(keep, tables, render(schema, ex.db_path, keep, tables, cfg))
```

Key design choice: **ranking is separated from selection.** Ranking is expensive (embeddings, cross-encoder);
selection is a cheap cut. So the recall sweep ranks each question **once** and then tries every K and
granularity for free.

**Why K counts columns, but we keep whole tables:** columns are the unit we can score precisely; tables are
the unit that keeps the prompt safe (all columns of a needed table present) and keeps the ablation clean (original DDL).

---

## 5. Measuring: `eval/schema_recall.py` (offline, $0)

For each question: gold columns (§4.2) vs kept columns.

| Metric | Meaning |
|---|---|
| `table_recall` | % questions where **all** gold tables were kept |
| **`column_recall`** | % questions where **all** gold columns were kept → **ceiling on EX%** |
| `col_recall_mean` | average share of gold columns kept (gentler, for intuition) |
| `kept_cols` | average columns kept |
| `compression` | pruned schema text length ÷ what the Phase 1 prompt sent (0.83 = 17% smaller) |

**⭐ MUST UNDERSTAND (the metric itself):**
```python
g_tables, g_cols = gold[ex.question_id]
"table_ok":  g_tables <= set(tables),       # <= on sets = "is a subset of"
"column_ok": g_cols <= keep,
"col_share": len(g_cols & keep) / len(g_cols),
```
"All gold columns kept" is strict on purpose: one missing column is enough to make the question unanswerable.

**Note:** compression is measured in characters, not tokens (no API call needed). The real token counts come
from the paid run. On dev the ratio is ~0.40 tokens per character for these DDL prompts.

---

## 6. Results

### 6.1 Train sweep (9,242 questions, table granularity, chosen pieces in bold)

| Method | exact boost | K=5 | K=10 | K=15 | **K=20** | K=30 | size at K=20 |
|---|---|---|---|---|---|---|---|
| bm25 | no | 79.3 | 90.7 | 94.3 | 95.9 | 97.8 | 0.82 |
| bm25 | yes | 85.5 | 93.5 | 95.9 | 97.0 | 98.6 | 0.83 |
| dense | no | 71.7 | 85.8 | 91.7 | 94.9 | 97.8 | 0.82 |
| dense | yes | 85.8 | 93.8 | 96.1 | 97.5 | 99.1 | 0.83 |
| hybrid | no | 76.1 | 89.7 | 94.0 | 96.3 | 98.4 | 0.82 |
| **hybrid** | **yes** | 85.5 | 93.8 | 96.2 | **97.5** | 99.2 | **0.83** |

(column recall %, file `results/schema_recall_train_full.csv`)

Reranker, on a fixed random sample of 1,959 train questions (exact on, table):

| Method | K=10 | K=20 | extra latency |
|---|---|---|---|
| bm25 | 93.6 | 96.6 | ~0 |
| hybrid | 93.8 | 97.0 | ~3 ms/question |
| hybrid_rerank | 94.4 | 97.8 | ~110 ms/question (GPU) |

**What we learned:**
1. **BM25 beats dense on its own** (90.7 vs 85.8 at K=10). BIRD hints name columns in their exact words, and
   column "documents" are short and full of identifiers: keyword territory.
2. **Fusion alone doesn't beat BM25** (89.7 vs 90.7): RRF gives the weaker ranker equal weight.
3. **The exact-match boost is the big win** (+3 to +8 points), and with it all three rankers converge (93.5–93.8 at K=10).
   Fixing the *kind* of error found in error analysis beat adding fancier models.
4. **The reranker helps a little** (+0.8) at ~110 ms and 278M parameters on the GPU. Left **off**: Phase 4 will need that
   GPU memory for the 7B model, and Phase 5 cares about latency. It's one flag (`--method hybrid_rerank`) if needed.
5. **Table vs column granularity:** at equal size they're about equally good (table K=10: 93.8% at 0.69; column K=30:
   94.2% at 0.71), and column mode's size advantage partly comes from the compact rebuilt format. Table mode wins on safety and clean ablation.

### 6.2 By schema size (train, hybrid + exact, table)

| Schema size | questions | K=10 recall / size | K=20 recall / size | K=30 recall / size |
|---|---|---|---|---|
| small (<50 cols) | 4,674 | 97.5% / 0.82 | 99.6% / 0.96 | 99.9% / 0.99 |
| medium (50–150) | 3,732 | 92.1% / 0.61 | 95.8% / 0.76 | 98.9% / 0.85 |
| large (>150) | 836 | 81.2% / 0.29 | 93.3% / 0.42 | 96.7% / 0.50 |

**The story:** on small schemas pruning does nothing useful (K=20 keeps 96% of the text). On large schemas
it cuts the schema by more than half. A fixed K is a compromise across sizes; a per-size K is an obvious next step.

### 6.3 Chosen setting, confirmed on dev (1,534 questions, picked on train first)

**hybrid + exact boost, table granularity, K=20, FK expansion on.**

| Split | column recall | table recall | schema size vs full |
|---|---|---|---|
| train | 97.5% | 97.5% | 0.83 |
| **dev** | **98.6%** | 98.6% | **0.88** |

Other methods on dev at K=20 (for reference only, **not** used to choose): bm25 + exact 98.9%, hybrid_rerank + exact
99.0%. Plain BM25 is slightly ahead on dev while hybrid was ahead on train (97.5 vs 97.0). We keep the train choice:
switching now would be choosing on dev. The gap (0.3 points ≈ 5 questions) is noise-sized anyway.

Recall holds on dev (different databases), so the choice generalises. Compression is weaker on dev because
dev schemas are smaller. Retrieval adds only a few milliseconds per question (BM25 + embedding + lookups).

### 6.4 Cost: the counter-intuitive finding

Estimated with row 1's real token counts (0.40 tokens/char) and Sonnet prices:

| | prompt tokens/question | distinct system prompts | est. full-dev cost |
|---|---|---|---|
| Row 1: full schema (actual) | 1,669 | 11 (one per DB) | **$2.64** |
| Row 2: pruned, caching off | ~1,462 | 253 | ~$6.34 |
| Row 2: pruned, caching on | ~1,462 | 253 | **~$3.36** |

**Smaller prompts, higher cost.** Row 1 sends 11 different system prompts, each reused ~140 times from cache at
10% price. Pruning makes the prompt depend on the question → 253 different prompts → more cache writes
(125% price) and fewer cheap reads. Prompts are only 12% shorter on dev, so the lost cache reuse costs more than
the shorter prompt saves. Output tokens (~70% of the bill) don't change at all.

We first assumed pruned prompts would *never* repeat and planned to switch caching off. Measuring showed 1,534
questions produce only 253 distinct prompts (many questions keep the same tables), so caching stays **on**:
it halves row 2's cost. Lesson: **measure the assumption before building on it.**

**When pruning *does* pay:** (a) large schemas (train's >150-column DBs: 58% smaller); (b) no prompt cache
(self-hosted models, Phase 4); (c) short-context or small models, where long noisy prompts hurt accuracy;
(d) latency: fewer input tokens = faster time-to-first-token.

### 6.5 Row 2 (paid): status

Not run yet: needs your approval (~$3.4 for full dev on Sonnet, caching on). Command:
```
uv run python -m eval.run --model claude-sonnet-5-5 --effort low --schema pruned --max-cost 4.5 --name pruned_sonnet55_low_k20
```
Expected: EX within about ±1–2 points of 68.38% (recall ceiling 98.6%, and fewer distracting tables can
help a little), cost ~27% higher. Either outcome is a fine result to report.

---

## 7. Phase 1 files changed in Phase 2 (study these diffs)

| File | Change | Why |
|---|---|---|
| `generation/prompt.py` | `build_prompt(ex, schema_text=None)`; `schema_ddl` now calls `retrieval.schema.original_ddl` | one code path for full and pruned DDL; default = Phase 1 behaviour, verified byte-identical |
| `generation/claude_client.py` | `cache: bool = True` parameter | switch for prompts that never repeat (not used in Phase 2 after the measurement above) |
| `eval/run.py` | `--split dev|train`, `--schema full|pruned`, `--method`, `--k`, `--granularity`, `--no-exact`, `--descriptions`; `retrieve_schema()` step; per-question `retrieval_seconds`, `kept_tables`, `kept_columns`, `schema_chars`, `compression`, `schema_recall_ok` | runs the ablation; latency now includes retrieval |
| `eval/metrics.py` | reports schema recall, compression, retrieval p50 when present; difficulty "unknown" (train) | |
| `eval/gold_cache.py` | per split; train stores **fingerprints** (streamed, parallel, checkpointed); failures stored as `None` | the first version (rows in RAM) was OOM-killed at 26 GB |
| `eval/compare.py` | `Fingerprint`, `fingerprint()`, `matches()` | compare a prediction with gold without keeping gold rows |
| `eval/executor.py` | `execute_fingerprint()` streams rows with `fetchmany` | constant memory for huge results |

**⭐ MUST UNDERSTAND: the new `run_one` (how Phase 2 plugs into the Phase 1 loop):**
```python
def run_one(ex, gold_rows, model, effort, cfg=None):
    schema_text, stats = retrieve_schema(ex, cfg) if cfg else (None, {})   # Phase 2 step (skipped for row 1)
    system, user = build_prompt(ex, schema_text)                            # None -> full schema
    g = generate_sql(system, user, model=model, effort=effort)              # caching stays on
    scored = score_sql(ex, g.sql, gold_rows)                                # Phase 1, unchanged
    ...
    "total_seconds": retrieval_s + g.seconds + scored["exec_seconds"],      # latency includes retrieval
```
Compare with Phase 1: the only new line is the first one. That's what "the ablation changes one thing" looks like in code.

### Train gold cache (and the out-of-memory crash that reshaped it)
Needed in Phase 4/5: to label "did the small model get this train question right?" without re-running 9,428 gold queries.

**v1 crashed.** It stored every gold result as rows in one dict, like the dev cache. Some train gold queries return
millions of rows; the process reached **26 GB** and the kernel killed it (and the session with it). Full story:
`notes/failures-log.md` F16.

**v2 stores a fingerprint, not rows.** The official rule is `set(pred) == set(gold)`. Two sets are equal exactly when
they have the same members, so we keep: number of distinct rows + an **order-independent hash** (sum of 64-bit hashes of
the distinct rows). Order-independent because sets have no order; sum of distinct hashes = same total whatever order
rows arrive in.

```python
# ⭐ MUST UNDERSTAND (eval/compare.py)
def fingerprint(rows):                       # rows can be a streaming cursor
    hashes = set()
    for row in rows:                         # one row at a time: never the whole result in RAM
        hashes.add(row_hash(row))            # 64-bit blake2b of the (normalised) row
    return Fingerprint(len(hashes), sum(hashes) % (1 << 64))

def matches(pred_rows, gold):                # gold = rows (dev) or Fingerprint (train)
    if isinstance(gold, Fingerprint):
        fp = fingerprint(pred_rows)
        return (fp.n_distinct, fp.digest) == (gold.n_distinct, gold.digest)
    return results_match(pred_rows, gold)
```
- `row_hash` normalises `1.0` → `1` because Python's set rule treats `1 == 1.0`; the fingerprint must agree.
- `execute_fingerprint` streams rows with `cursor.fetchmany(10_000)`: memory = one query's row *hashes*, never its rows.
- 4 worker processes; checkpoint every 500 questions (resume after a crash); atomic writes (tmp file + rename).
- **Validated:** re-scoring all 1,534 row-1 predictions with fingerprints gives 68.38%, agreeing with row
  comparison on 1,534/1,534 questions.
- Collision risk: two different results with the same count *and* same 64-bit sum: about 1 in 10^19. Negligible.
- **Measured:** 8 min 5 s, peak 383 MB per process (v1: 26 GB, killed), 0.6 MB file. 352 gold queries fail, 330 of them in
  `retail_world`, whose gold SQL references tables missing from the shipped database. Stored as `None` and skipped;
  9,076 train questions are usable.

---

## 8. Decisions log (what we chose, and what we rejected)

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Retrieval unit | columns (scored), tables (kept) | tables only; columns only | columns score precisely; whole tables are safe + clean ablation |
| Prompt text for kept tables | original CREATE TABLE | rebuilt DDL | rebuilt is 40% shorter for the same tables → would mix format and pruning effects |
| Vector search | numpy dot product | FAISS | ≤455 vectors per DB; brute force is exact and instant |
| Embedding model | bge-small-en-v1.5 | bigger models | dense isn't the bottleneck (exact boost closes the gap); small is fast |
| Fusion | RRF (k=60) | score averaging | different score scales; ranks need no calibration |
| Reranker | off by default | on | +0.8 recall for 110 ms + GPU memory that Phase 4 needs |
| Extra signal | exact name/value matching | more model capacity | error analysis showed *what* was missed; targeted fix gave +3–8 points |
| FKs | declared + inferred | declared only | BIRD declares incomplete FKs |
| K | 20 (picked on train) | picking on dev | tuning on the report set inflates the result |
| Recall target | ~97–98% | max compression | every lost point of recall is a question the model can't answer |
| Caching for pruned | on | off | 253 distinct prompts for 1,534 questions → caching halves the cost |
| Train gold cache | fingerprints, streamed | rows in RAM | v1 hit 26 GB and was OOM-killed; fingerprints are ~100 bytes/question and score identically |
| Value index storage | per-DB files, int ids, lazy LRU | one big pickle of strings | 2.09 GB → ≤93 MB loaded |

---

## 9. Limitations (say them before an interviewer does)
- **Dev schemas are small**, so compression on dev is modest (12%). The large-schema story comes from train.
- **Recall ≠ accuracy.** Recall says the needed columns are *present*, not that the model uses them right.
- **Gold SQL is one valid answer.** A different correct query might use other columns; recall against gold is a proxy.
- **Value index is exact-match only**: misspellings, plurals or partial names ("Wesley Lemke" split across first/last
  works; "Wes" doesn't). Fuzzy matching (LSH, edit distance) is a known next step.
- **Fixed K across schema sizes**; per-size or score-threshold K would compress small schemas less pointlessly.
- **Inferred FKs can be wrong** (same name, different meaning). They only add tables, never remove them.
- 186 train gold queries (2%) couldn't be parsed and are excluded from train recall.

---

## 10. How to run everything (all free unless marked)
```bash
uv run python -m retrieval.schema          # build/check schema cache (dev + train)
uv run python -m retrieval.exact           # build/check value index
uv run python -m eval.gold_columns         # parse rate of gold SQL
uv run python -m eval.schema_recall --split train --methods bm25 dense hybrid --tag full
uv run python -m eval.schema_recall --split train --methods bm25 hybrid hybrid_rerank --sample 2000 --tag rerank2000
uv run python -m eval.schema_recall --split dev --methods bm25 hybrid hybrid_rerank
uv run python -m eval.gold_cache train     # train gold results (once)
uv run python -m eval.run --model claude-haiku-5-5 --limit 20 --schema pruned          # smoke test, <1 cent
uv run python -m eval.run --model claude-sonnet-5-5 --effort low --schema pruned ...   # PAID: row 2 (~$3.4)
```

---

## 11. Interview questions (answer them out loud)
1. **Why is schema recall a ceiling on EX%?** A missing needed column makes the correct query impossible to write.
2. **How do you measure recall without running anything?** Parse the gold SQL into an AST with sqlglot, resolve aliases
   with `qualify`, collect (table, column) pairs, check subset of what was kept.
3. **Why did BM25 beat embeddings here?** BIRD hints use the exact column words; column docs are short identifier-heavy text.
   Dense helps with synonyms, which are rarer here.
4. **What does RRF do that averaging scores doesn't?** Works on ranks, so different score scales don't matter; no calibration.
5. **Why can pruning break a query even when every needed column is retrieved?** Joins: the bridge table and join keys
   may not be retrieved. FK expansion with BFS shortest paths fixes it. And BIRD's FKs are incomplete, so we infer links.
6. **What was your biggest improvement and how did you find it?** Error analysis of misses → named columns ranked low and cell
   values invisible to rankers → exact name/value matching, +3–8 points recall.
7. **Why tune K on train and report on dev?** Choosing on dev overfits the choice to those DBs and inflates the reported number.
8. **Your prompt got smaller but the cost went up. Why?** Prompt caching: full-schema prompts repeat per DB and are read
   at 10% price; pruned prompts vary per question, so fewer cache hits. Output dominates cost anyway.
9. **So why do retrieval at all?** Large schemas (58% smaller on >150-column DBs), self-hosted models without a cache,
   small models that degrade on long noisy prompts (Phase 4), and lower latency.
10. **Why not use the cross-encoder?** +0.8 recall vs 110 ms/question and GPU memory needed by the local model; measured, then decided.
11. **Why reuse the original CREATE TABLE text instead of rebuilding it?** To change only one thing in the ablation:
    rebuilt DDL is 40% shorter for the same tables, which would inflate "compression".
12. **Your job ran out of memory. What did you do?** Found the OOM kill in the kernel log (26 GB). Root cause: caching all
    gold rows. Replaced rows with streamed set fingerprints, added checkpoint/resume + atomic writes, validated the new scoring
    agrees on 1,534/1,534 questions. Same pattern applied to the value index (2.09 GB → 93 MB).
