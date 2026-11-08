"""Measure schema recall and compression of retrieval settings, offline (no API calls, $0).

For every question: parse the gold SQL to get the tables/columns it needs (eval/gold_columns.py),
run retrieval, and check whether everything needed was kept.

Metrics (per setting, averaged over questions):
  table_recall      % of questions where ALL gold tables were kept
  column_recall     % of questions where ALL gold columns were kept   <- the important one:
                    if a needed column is missing, the model cannot write the right query,
                    so this is a ceiling on EX% for the pruned prompt
  col_recall_mean   average share of gold columns kept (gentler, for intuition)
  kept_cols         average number of columns kept
  compression       average (pruned schema text length / full-schema prompt text length, i.e. what
                    the Phase 1 run sent); 0.3 = schema text is 70% smaller

Rankings are computed once per method; the exact-match boost and every K / granularity are cheap
cuts of the same ranking, so a full sweep costs one retrieval pass per method.

Run:
  uv run python -m eval.schema_recall --split train                       # tune here
  uv run python -m eval.schema_recall --split train --methods hybrid_rerank --sample 2000
  uv run python -m eval.schema_recall --split dev --methods bm25 --k 10   # confirm the pick here
Writes results/schema_recall_<split>.csv (+ per-question rows in results/raw/)
"""
import argparse
import random
import time
from pathlib import Path

import pandas as pd

from eval.data import load_split
from eval.gold_columns import used_columns
from retrieval.dense import embed_queries
from retrieval.pipeline import METHODS, RetrievalConfig, base_rank, exact_boost, render, select
from retrieval.schema import load_schemas, original_ddl
from retrieval.text import query_text

RESULTS = Path(__file__).resolve().parent.parent / "results"
K_VALUES = (5, 10, 15, 20, 30)


def size_bucket(n_columns: int) -> str:
    """Schema size groups, for the breakdown the plan asks for."""
    return "small (<50 cols)" if n_columns < 50 else "medium (50-150)" if n_columns <= 150 else "large (>150)"


COLUMNS = ["method", "exact", "k", "granularity", "question_id", "db_id", "size",
           "table_ok", "column_ok", "col_share", "kept_cols", "compression"]


def evaluate(split: str, methods: list[str], k_values: tuple[int, ...], sample: int | None) -> pd.DataFrame:
    """Memory notes: per question we keep only the top max(K) of each ranking (selection never
    looks deeper), results are compact tuples (not dicts), and questions are processed grouped by
    database so the per-DB caches (value index, embeddings) stay warm and few are loaded at once."""
    schemas = load_schemas(split)
    examples = load_split(split)
    if sample:
        random.seed(0)  # same sample every run, so settings are compared on the same questions
        examples = random.sample(examples, sample)
    examples.sort(key=lambda ex: (ex.db_id, ex.question_id))  # group by database

    # Gold columns once. Queries sqlglot can't analyse are excluded (and counted).
    gold = {}
    for ex in examples:
        g = used_columns(ex.gold_sql, schemas[ex.db_id])
        if g is not None:
            gold[ex.question_id] = g
    print(f"{split}: {len(gold)}/{len(examples)} questions with parseable gold SQL")
    examples = [ex for ex in examples if ex.question_id in gold]

    db_path = {ex.db_id: ex.db_path for ex in examples}
    full_len = {db: len(original_ddl(p)) for db, p in db_path.items()}
    max_k = max(k_values)
    rows: list[tuple] = []
    for method in methods:
        start = time.perf_counter()
        cfg0 = RetrievalConfig(method=method)
        # embeddings for all questions in one GPU batch: 384 floats each (~15 MB for all of train)
        vecs = embed_queries([query_text(ex) for ex in examples]) if method != "bm25" else [None] * len(examples)
        for ex, v in zip(examples, vecs):
            schema = schemas[ex.db_id]
            q = query_text(ex)
            base = base_rank(schema, q, cfg0, v)
            variants = {False: base[:max_k], True: exact_boost(schema, q, split, base)[:max_k]}
            g_tables, g_cols = gold[ex.question_id]
            size = size_bucket(len(schema.columns))
            for exact, ranking in variants.items():
                for k in k_values:
                    for gran in ("table", "column"):
                        cfg = RetrievalConfig(method=method, exact=exact, top_k=k, granularity=gran)
                        keep, tables = select(schema, ranking, cfg)
                        ddl_len = len(render(schema, db_path[ex.db_id], keep, tables, cfg))
                        rows.append((method, exact, k, gran, ex.question_id, ex.db_id, size,
                                     g_tables <= set(tables), g_cols <= keep,
                                     len(g_cols & keep) / len(g_cols) if g_cols else 1.0,
                                     len(keep), ddl_len / full_len[ex.db_id]))
        print(f"  {method}: done in {time.perf_counter() - start:.0f}s")
    return pd.DataFrame(rows, columns=COLUMNS)


def summarize(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    out = df.groupby(by, sort=False).agg(
        table_recall=("table_ok", "mean"), column_recall=("column_ok", "mean"),
        col_recall_mean=("col_share", "mean"), kept_cols=("kept_cols", "mean"),
        compression=("compression", "mean"), n=("question_id", "count"))
    for c in ("table_recall", "column_recall", "col_recall_mean"):
        out[c] = (100 * out[c]).round(1)
    return out.round({"kept_cols": 1, "compression": 3})


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="train", choices=["train", "dev"])
    p.add_argument("--methods", nargs="+", default=list(METHODS), choices=METHODS)
    p.add_argument("--k", nargs="+", type=int, default=list(K_VALUES))
    p.add_argument("--sample", type=int, default=None, help="random sample of N questions (seed 0)")
    p.add_argument("--tag", default="", help="suffix for the output file name")
    args = p.parse_args()

    df = evaluate(args.split, args.methods, tuple(args.k), args.sample)
    summary = summarize(df, ["method", "exact", "granularity", "k"])
    pd.set_option("display.width", 200)
    print(summary.to_string())

    name = f"schema_recall_{args.split}{'_' + args.tag if args.tag else ''}"
    (RESULTS / "raw").mkdir(parents=True, exist_ok=True)
    summary.to_csv(RESULTS / f"{name}.csv")
    # Breakdown by schema size: does pruning matter more on big databases?
    summarize(df, ["method", "exact", "granularity", "k", "size"]).to_csv(RESULTS / f"{name}_by_size.csv")
    df.to_csv(RESULTS / "raw" / f"{name}_per_question.csv.gz", index=False)  # for breakdowns
    print(f"saved results/{name}.csv")


if __name__ == "__main__":
    main()
