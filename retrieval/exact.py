"""Exact matches: columns the question names outright, and columns containing a value it mentions.

Error analysis on train showed two kinds of misses that ranking can't fix well:
1. The hint names the column ("lowest rating refers to MIN(StarRating)"), but a generic name like
   `title` or `name` exists in many tables, so BM25 gives it little weight and it lands at rank 15-25.
2. The question mentions a *cell value*: "customer Lillie Kim" -> customer.first_name / last_name,
   "Hispanic" -> race. Nothing in the schema text says "Lillie", so no ranker can find it.

Fix: two exact-match signals whose hits are moved to the top of the ranking before the top-K cut.
- mentions: a column name (raw "StarRating", or split "star rating") appears as a whole phrase
  in the question or hint.
- values:   an n-gram (1-4 words) of the question/hint, or a 'quoted' string in the hint, equals
  a stored text value of a column. Values come from a one-time scan of every text column
  (distinct values up to 40 characters, at most 50k per column), cached to disk.

These signals are precise but narrow, so they *add to* the ranking, they don't replace it.
"""
import pickle
import re
import sqlite3
import time
from functools import lru_cache

from eval.data import BIRD_ROOT
from retrieval.schema import DbSchema, load_schemas, quote
from retrieval.text import STOPWORDS, split_identifier

MAX_VALUE_LEN = 40
MAX_VALUES_PER_COLUMN = 50_000
MAX_COLUMNS_PER_VALUE = 3   # "Male" may be in 10 columns; an ambiguous value only boosts a few

# Column names too generic to count as a mention on their own ("name" matches every question)
GENERIC = {"id", "name", "type", "date", "year", "code", "description", "title", "status", "value", "number"}


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


# ---------- value index ----------
# Memory design (the first version kept one dict for all 69 train DBs, with a separate
# "table.column" string stored for each of 4.9M values: a 254 MB pickle and a few GB of RAM):
#   - one small file per database, loaded only when a question about that DB comes in
#   - each value maps to a column INDEX (an int into schema.columns), not a repeated string;
#     a value found in one column (the usual case) is a bare int, not a set
#   - an LRU cache keeps only the last few databases in memory

ColumnRefs = int | tuple[int, ...]


def _values_dir(split: str):
    return BIRD_ROOT / split / "values"


def _scan_db(db_path, schema: DbSchema) -> dict[str, ColumnRefs]:
    """{normalized value: column index or indices} for one database's text columns."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    index: dict[str, ColumnRefs] = {}
    try:
        for i, c in enumerate(schema.columns):
            if c.type and "char" not in c.type.lower() and "text" not in c.type.lower():
                continue  # numbers/dates are matched by the model from the hint, not by lookup
            try:
                cursor = con.execute(
                    f"SELECT DISTINCT {quote(c.name)} FROM {quote(c.table)} "
                    f"WHERE typeof({quote(c.name)}) = 'text' AND length({quote(c.name)}) BETWEEN 2 AND {MAX_VALUE_LEN} "
                    f"LIMIT {MAX_VALUES_PER_COLUMN}")
                for (v,) in cursor:  # iterate the cursor: rows stream in, no full list in memory
                    key = _norm(v)
                    old = index.get(key)
                    if old is None:
                        index[key] = i
                    elif isinstance(old, int):
                        if old != i:
                            index[key] = (old, i)
                    elif i not in old:
                        index[key] = old + (i,)
            except sqlite3.Error:
                continue
    finally:
        con.close()
    return index


def build_value_index(split: str) -> None:
    """Scan every database of a split once (a few minutes on train); one file per DB."""
    out_dir = _values_dir(split)
    out_dir.mkdir(exist_ok=True)
    for db_id, schema in load_schemas(split).items():
        path = out_dir / f"{db_id}.pkl"
        if path.exists():
            continue  # resumable: already-built databases are skipped
        start = time.perf_counter()
        index = _scan_db(BIRD_ROOT / split / f"{split}_databases" / db_id / f"{db_id}.sqlite", schema)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(index, f)
        tmp.replace(path)  # atomic: a crash never leaves a half-written file
        print(f"  values: {db_id}: {len(index)} distinct values in {time.perf_counter() - start:.1f}s", flush=True)
        del index  # free it before the next database


@lru_cache(maxsize=4)  # at most 4 databases' values in memory at once
def db_values(split: str, db_id: str) -> dict[str, ColumnRefs]:
    path = _values_dir(split) / f"{db_id}.pkl"
    if not path.exists():
        build_value_index(split)
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------- matching ----------

def _candidates(query: str) -> list[str]:
    """Strings from the question that could be cell values, longest first."""
    quoted = re.findall(r"'([^']{2,40})'|\"([^\"]{2,40})\"", query)
    out = [_norm(a or b) for a, b in quoted]
    words = re.findall(r"[\w.\-&]+", query)
    for n in (4, 3, 2, 1):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if n == 1 and (gram[0].lower() in STOPWORDS or len(gram[0]) < 3 or gram[0].isdigit()):
                continue
            out.append(_norm(" ".join(gram)))
    return list(dict.fromkeys(out))  # dedupe, keep order


def value_hits(schema: DbSchema, query: str, split: str) -> list[str]:
    """Column keys holding a value mentioned in the query; longer matches first."""
    index = db_values(split, schema.db_id)
    columns = schema.columns
    hits: list[str] = []
    for cand in _candidates(query):
        refs = index.get(cand)
        if refs is None:
            continue
        for i in ((refs,) if isinstance(refs, int) else refs)[:MAX_COLUMNS_PER_VALUE]:
            if columns[i].key not in hits:
                hits.append(columns[i].key)
    return hits


def mention_hits(schema: DbSchema, query: str) -> list[str]:
    """Column keys whose name appears as a whole phrase in the query. Longer names first."""
    q = " " + _norm(re.sub(r"[`\"'()\[\],;:=<>]", " ", query)) + " "
    q_split = " " + _norm(split_identifier(re.sub(r"[`\"'()\[\],;:=<>]", " ", query))) + " "
    hits = []
    for c in sorted(schema.columns, key=lambda c: -len(c.name)):
        raw, split = _norm(c.name), _norm(split_identifier(c.name))
        if raw in GENERIC or len(raw) < 3:
            continue
        if f" {raw} " in q or f" {split} " in q_split:
            hits.append(c.key)
    return hits


def boost(ranking: list[tuple[str, float]], first: list[str]) -> list[tuple[str, float]]:
    """Move `first` (in its order) to the top of the ranking; the rest keep their order."""
    pos = {k: i for i, (k, _) in enumerate(ranking)}
    first = [k for k in first if k in pos]
    head = set(first)
    top = ranking[0][1] if ranking else 0.0
    return [(k, top + len(first) - i) for i, k in enumerate(first)] + [kv for kv in ranking if kv[0] not in head]


if __name__ == "__main__":
    import retrieval.exact as me  # import by module name so pickled objects load elsewhere (see schema.py)
    for split in ("dev", "train"):
        me.build_value_index(split)
    s = load_schemas("dev")["california_schools"]
    q = "What is the highest eligible free rate for K-12 students in the schools in Alameda County? `Free Meal Count (K-12)`"
    print("mentions:", me.mention_hits(s, q))
    print("values:  ", me.value_hits(s, q, "dev"))
