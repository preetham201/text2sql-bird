"""Combine rankings (RRF) and re-score the top candidates with a cross-encoder.

Reciprocal Rank Fusion: each retriever votes 1 / (k + rank) for every column; votes are summed.
It uses ranks only, not scores, which matters because BM25 scores (0 to ~30) and cosine
similarities (0.3 to 0.9) live on different scales: averaging them would let BM25 dominate.
k = 60 is the standard value from the original paper; it keeps a #1 from one retriever from
outweighing consistent top-5 placements in both.

Cross-encoder reranking: the retrievers above are "bi-encoders" or keyword matchers: the question
and each column are processed separately and only compared at the end. A cross-encoder reads
the question and one column *together* in a single pass, so it can model their interaction
("highest eligible free rate" vs a column described as "eligible free rate = ..."). That's more
accurate but ~100x slower, so we only apply it to the top-N fused candidates.
"""
from sentence_transformers import CrossEncoder

from retrieval.schema import DbSchema
from retrieval.text import column_text

RRF_K = 60
DEFAULT_RERANKER = "BAAI/bge-reranker-base"

_RERANKERS: dict[str, CrossEncoder] = {}


def rrf(rankings: list[list[tuple[str, float]]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse several rankings of the same items. Returns (key, fused score), best first."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, (key, _) in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda kv: -kv[1])


def _reranker(name: str) -> CrossEncoder:
    if name not in _RERANKERS:
        _RERANKERS[name] = CrossEncoder(name)
    return _RERANKERS[name]


def rerank(schema: DbSchema, query: str, ranking: list[tuple[str, float]], top_n: int = 30,
           model: str = DEFAULT_RERANKER) -> list[tuple[str, float]]:
    """Re-score the first `top_n` items with a cross-encoder; the rest keep their order after them.

    Keeping the tail (instead of dropping it) means a later step can still go deeper than top_n.
    """
    by_key = {c.key: c for c in schema.columns}
    head, tail = ranking[:top_n], ranking[top_n:]
    scores = _reranker(model).predict([(query, column_text(by_key[k])) for k, _ in head], batch_size=64)
    reranked = sorted(zip([k for k, _ in head], scores.tolist()), key=lambda kv: -kv[1])
    low = min(s for _, s in reranked) if reranked else 0.0
    return reranked + [(k, low - 1 - i) for i, (k, _) in enumerate(tail)]  # tail stays below the head


if __name__ == "__main__":
    a = [("x", 9.0), ("y", 5.0), ("z", 1.0)]
    b = [("y", 0.9), ("z", 0.8), ("x", 0.1)]
    print(rrf([a, b]))  # y is 2nd and 1st -> wins over x (1st and 3rd)
