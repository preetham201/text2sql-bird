"""Dense (embedding) retrieval: rank columns by meaning, not exact words.

An embedding model maps a text to a vector (here 384 or 768 numbers) so that texts with similar
meaning get vectors pointing in similar directions. We embed every column document once, embed
the question at query time, and rank columns by cosine similarity (the dot product of
length-1 vectors). This catches synonyms BM25 misses ("pupils" -> Enrollment).

Model: BGE (BAAI General Embedding), a strong small open retrieval model. BGE wants a short
instruction in front of *queries* (not documents) for retrieval tasks.

Why no FAISS: a database has at most a few hundred columns, so comparing the question with
every column is one tiny matrix multiply (~microseconds). FAISS (approximate nearest-neighbour
search) pays off at millions of vectors; here it would add a dependency for no gain.
"""
import numpy as np
from sentence_transformers import SentenceTransformer

from retrieval.schema import DbSchema
from retrieval.text import column_text

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_MODELS: dict[str, SentenceTransformer] = {}
_INDEXES: dict[tuple[str, str], tuple[np.ndarray, list[str]]] = {}  # (model, db_id) -> (vectors, keys)


def _model(name: str) -> SentenceTransformer:
    if name not in _MODELS:
        _MODELS[name] = SentenceTransformer(name)  # uses the GPU automatically if there is one
    return _MODELS[name]


def _index(schema: DbSchema, model: str) -> tuple[np.ndarray, list[str]]:
    key = (model, schema.db_id)
    if key not in _INDEXES:
        cols = schema.columns
        # normalize_embeddings=True makes every vector length 1, so dot product = cosine similarity
        vecs = _model(model).encode([column_text(c) for c in cols], normalize_embeddings=True, batch_size=64)
        _INDEXES[key] = (vecs, [c.key for c in cols])
    return _INDEXES[key]


def embed_queries(queries: list[str], model: str = DEFAULT_MODEL) -> np.ndarray:
    """Embed many questions in one batch (much faster on a GPU than one at a time)."""
    return _model(model).encode([QUERY_PREFIX + q for q in queries], normalize_embeddings=True, batch_size=128)


def rank_dense(schema: DbSchema, query: str, model: str = DEFAULT_MODEL,
               query_vec: np.ndarray | None = None) -> list[tuple[str, float]]:
    """All columns, most similar first, as (column key, cosine similarity).

    Pass `query_vec` (from embed_queries) to skip embedding the question again.
    """
    vecs, keys = _index(schema, model)
    q = query_vec if query_vec is not None else embed_queries([query], model)[0]
    scores = vecs @ q  # one dot product per column
    order = np.argsort(-scores)
    return [(keys[i], float(scores[i])) for i in order]


if __name__ == "__main__":
    from retrieval.schema import load_schemas
    s = load_schemas("dev")["california_schools"]
    for key, score in rank_dense(s, "How many pupils attend schools in Fresno?")[:5]:
        print(f"{score:.3f}  {key}")
