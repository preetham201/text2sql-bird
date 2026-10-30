"""Build the baseline prompt: full schema DDL + evidence + question.

The prompt has two parts:
- system: instructions + the database schema. Identical for every question on the same
  database, which makes it the stable part (useful later for prompt caching).
- user:   the evidence hint + the question. Changes every call.
"""
import sqlite3
from functools import lru_cache
from pathlib import Path

from eval.data import Example

INSTRUCTIONS = """You are an expert SQLite developer. Write one SQLite query that answers the user's question about the database below.

Rules:
- Use only tables and columns that exist in the schema.
- Return only the columns the question asks for.
- Return the query inside a single ```sql code block, with no explanation."""


@lru_cache(maxsize=None)  # 11 databases, ~140 questions each: read each schema once, not 1,534 times
def schema_ddl(db_path: Path) -> str:
    """Return the CREATE TABLE statements of every table in the database.

    SQLite stores the original CREATE statement of each table in a built-in table called
    sqlite_master, so we can read the exact DDL instead of rebuilding it ourselves.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"  # skip SQLite's internal tables
        ).fetchall()
    finally:
        con.close()
    return "\n\n".join(r[0].strip() + ";" for r in rows)


def build_prompt(ex: Example) -> tuple[str, str]:
    """Return (system, user) prompt strings for one question."""
    system = f"{INSTRUCTIONS}\n\nDatabase schema:\n{schema_ddl(ex.db_path)}"

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
