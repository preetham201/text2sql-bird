"""Question -> pruned schema. Composes ranking, selection and FK expansion.

    rank        every column of the database, best first (bm25 | dense | hybrid | hybrid_rerank)
    exact       move columns the question names, or whose values it mentions, to the top
    select      keep the top-K columns, then either
                  granularity="table":  the whole tables those columns belong to, or
                  granularity="column": just those columns
    fk_expand   add bridge tables + join keys + primary keys so the result can be joined
    render      CREATE TABLE text for what's kept

Ranking is the expensive part (embeddings, cross-encoder), so it's separate from selection:
eval/schema_recall.py ranks each question once and then tries many K values for free.
"""
from dataclasses import dataclass

import numpy as np

from eval.data import Example
from retrieval.bm25 import rank_bm25
from retrieval.dense import DEFAULT_MODEL, rank_dense
from retrieval.exact import boost, mention_hits, value_hits
from retrieval.fk_expand import connect_tables, join_columns, links
from retrieval.fusion import rerank, rrf
from retrieval.schema import DbSchema, load_schemas, original_ddl, render_ddl
from retrieval.text import query_text

METHODS = ("bm25", "dense", "hybrid", "hybrid_rerank")


@dataclass(frozen=True)  # frozen = immutable, so a config can't change halfway through a run
class RetrievalConfig:
    # Defaults = the setting chosen on BIRD train (notes/phase2-retrieval.md): 97.5% column recall,
    # schema text at 83% of full size. Reranking added +0.8 recall for ~110ms/question: left off.
    method: str = "hybrid"
    exact: bool = True           # boost exact column-name mentions and value matches
    top_k: int = 20              # columns taken from the ranking
    granularity: str = "table"   # "table" or "column"
    fk_expand: bool = True
    descriptions: bool = False   # add column descriptions + sample values to the DDL
    dense_model: str = DEFAULT_MODEL
    rerank_top_n: int = 30


@dataclass
class Retrieved:
    keep: set[str]       # kept column keys ("table.column", lower case)
    tables: list[str]    # kept tables (lower case), best first
    ddl: str             # the pruned CREATE TABLE text that goes into the prompt


def base_rank(schema: DbSchema, query: str, cfg: RetrievalConfig,
              query_vec: np.ndarray | None = None) -> list[tuple[str, float]]:
    """Rank all columns of the database with the chosen retriever (no exact-match boost)."""
    if cfg.method == "bm25":
        return rank_bm25(schema, query)
    dense = rank_dense(schema, query, cfg.dense_model, query_vec)
    if cfg.method == "dense":
        return dense
    fused = rrf([rank_bm25(schema, query), dense])
    if cfg.method == "hybrid":
        return fused
    if cfg.method == "hybrid_rerank":
        return rerank(schema, query, fused, top_n=cfg.rerank_top_n)
    raise ValueError(f"unknown method {cfg.method!r}; expected one of {METHODS}")


def exact_boost(schema: DbSchema, query: str, split: str, ranking: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Named columns first, then value matches, then the rest of the ranking."""
    return boost(ranking, mention_hits(schema, query) + value_hits(schema, query, split))


def rank(schema: DbSchema, query: str, split: str, cfg: RetrievalConfig,
         query_vec: np.ndarray | None = None) -> list[tuple[str, float]]:
    """Final ranking used for selection."""
    ranking = base_rank(schema, query, cfg, query_vec)
    return exact_boost(schema, query, split, ranking) if cfg.exact else ranking


def select(schema: DbSchema, ranking: list[tuple[str, float]], cfg: RetrievalConfig) -> tuple[set[str], list[str]]:
    """Turn a ranking into the set of kept columns and tables."""
    top = [key for key, _ in ranking[:cfg.top_k]]

    tables: list[str] = []  # in rank order: a table's position = its best column's position
    for key in top:
        t = key.split(".", 1)[0]
        if t not in tables:
            tables.append(t)
    if cfg.fk_expand:
        tables = connect_tables(schema, tables)

    if cfg.granularity == "table":
        keep = {c.key for c in schema.columns if c.table.lower() in tables}
    elif cfg.granularity == "column":
        keep = set(top)
        if cfg.fk_expand:
            keep |= join_columns(schema, set(tables))
        # every kept table needs at least one column to appear in the DDL
        for t in tables:
            if not any(k.startswith(t + ".") for k in keep):
                keep |= {c.key for c in schema.columns if c.table.lower() == t and c.is_pk} or \
                        {next(c.key for c in schema.columns if c.table.lower() == t)}
    else:
        raise ValueError(f"unknown granularity {cfg.granularity!r}")
    return keep, tables


def render(schema: DbSchema, db_path, keep: set[str], tables: list[str], cfg: RetrievalConfig) -> str:
    """Prompt text for the kept part of the schema.

    Table granularity (no descriptions): the database's original CREATE TABLE statements for the
    kept tables, i.e. the Phase 1 prompt with tables removed and nothing else changed.
    Column granularity or descriptions: rebuilt DDL (render_ddl), since the original text can't
    drop single columns or carry comments.
    """
    if cfg.granularity == "table" and not cfg.descriptions:
        return original_ddl(db_path, set(tables))
    return render_ddl(schema, keep, cfg.descriptions, links(schema))


def retrieve(ex: Example, cfg: RetrievalConfig) -> Retrieved:
    """The full pipeline for one question."""
    schema = load_schemas(ex.split)[ex.db_id]
    keep, tables = select(schema, rank(schema, query_text(ex), ex.split, cfg), cfg)
    return Retrieved(keep, tables, render(schema, ex.db_path, keep, tables, cfg))


if __name__ == "__main__":
    from eval.data import load_dev
    from eval.gold_columns import used_columns
    ex = load_dev(limit=1)[0]
    print("Q:", ex.question, "\nHINT:", ex.evidence)
    print("gold uses:", used_columns(ex.gold_sql, load_schemas("dev")[ex.db_id])[1])
    for cfg in (RetrievalConfig(granularity="column", top_k=6), RetrievalConfig(top_k=6)):
        r = retrieve(ex, cfg)
        print(f"\n{cfg.granularity}: {len(r.keep)} columns from {r.tables}\n{r.ddl[:600]}")
