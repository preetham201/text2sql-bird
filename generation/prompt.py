"""Build the prompt: schema DDL + evidence + question.

Phase 1 (default): the full schema, read from sqlite_master.
Phase 2: pass `schema_text` = the pruned DDL from retrieval/pipeline.py. Everything else
(instructions, hint, question, format) stays identical, so the ablation changes one thing.

The prompt has two parts:
- system: instructions + the database schema. Identical for every question on the same
  database, which makes it the stable part (useful later for prompt caching).
- user:   the evidence hint + the question. Changes every call.
"""
from pathlib import Path

from eval.data import Example
from retrieval.schema import original_ddl

INSTRUCTIONS = """You are an expert SQLite developer. Write one SQLite query that answers the user's question about the database below.

Rules:
- Use only tables and columns that exist in the schema.
- Return only the columns the question asks for.
- Return the query inside a single ```sql code block, with no explanation."""


def schema_ddl(db_path: Path) -> str:
    """Return the CREATE TABLE statements of every table in the database.

    SQLite stores the original CREATE statement of each table in a built-in table called
    sqlite_master, so we can read the exact DDL instead of rebuilding it ourselves.
    (Phase 2 moved the reading code to retrieval/schema.py:original_ddl so table-level pruning
    can reuse the exact same text; it's read once per database and cached there.)
    """
    return original_ddl(db_path)


def build_prompt(ex: Example, schema_text: str | None = None) -> tuple[str, str]:
    """Return (system, user) prompt strings for one question. schema_text=None -> full schema."""
    schema = schema_ddl(ex.db_path) if schema_text is None else schema_text
    system = f"{INSTRUCTIONS}\n\nDatabase schema:\n{schema}"

    # Some BIRD questions have empty evidence; leave the line out instead of sending "Hint: ".
    user = f"Hint: {ex.evidence}\n\nQuestion: {ex.question}" if ex.evidence else f"Question: {ex.question}"
    return system, user


if __name__ == "__main__":
    # Self-check: `uv run python -m generation.prompt`
    from eval.data import load_dev
    ex = load_dev(limit=1)[0]
    system, user = build_prompt(ex)
    print(system[:800], "\n...\n")
    print(user)
    print(f"\nsystem prompt: {len(system)} characters (~{len(system) // 4} tokens)")
