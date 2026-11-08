"""Keyword retrieval with BM25: rank a database's columns by word overlap with the question.

BM25 scores a document higher when it contains the query's words, especially rare words
(a word that appears in every column, like "id", counts for little; "enrollment" counts a lot),
with diminishing returns for repeats and a correction for long documents.
It's exact-word matching: great when the question or hint uses the column's own words
("NumTstTakr", "Enrollment (K-12)"), blind to synonyms ("pupils" vs "students").
"""
from rank_bm25 import BM25Okapi

from retrieval.schema import DbSchema
from retrieval.text import column_text, tokenize

_INDEXES: dict[str, tuple[BM25Okapi, list[str]]] = {}  # db_id -> (index, column keys)


def _index(schema: DbSchema) -> tuple[BM25Okapi, list[str]]:
    """One BM25 index per database (only that database's columns compete with each other)."""
    if schema.db_id not in _INDEXES:
        cols = schema.columns
        _INDEXES[schema.db_id] = (BM25Okapi([tokenize(column_text(c)) for c in cols]), [c.key for c in cols])
    return _INDEXES[schema.db_id]


def rank_bm25(schema: DbSchema, query: str) -> list[tuple[str, float]]:
    """All columns of the database, best match first, as (column key, score)."""
    index, keys = _index(schema)
    scores = index.get_scores(tokenize(query))
    return sorted(zip(keys, scores.tolist()), key=lambda kv: -kv[1])


if __name__ == "__main__":
    from retrieval.schema import load_schemas
    s = load_schemas("dev")["california_schools"]
    for key, score in rank_bm25(s, "How many test takers in Fresno county? NumTstTakr = number of test takers")[:5]:
        print(f"{score:6.2f}  {key}")
