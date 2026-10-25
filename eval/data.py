"""Load BIRD dev questions into typed Example objects."""
import json
from dataclasses import dataclass
from pathlib import Path

# Project root = two folders up from this file (eval/data.py -> eval/ -> project root).
# Building paths from __file__ means the code works no matter where you run it from.
BIRD_DEV = Path(__file__).resolve().parent.parent / "data" / "bird" / "dev"


@dataclass
class Example:
    """One BIRD question with everything needed to evaluate it."""
    question_id: int
    db_id: str          # which database the question is about, e.g. "california_schools"
    question: str       # the natural-language question
    evidence: str       # BIRD's domain-knowledge hint (goes into the prompt)
    gold_sql: str       # the reference SQL query
    difficulty: str     # "simple" | "moderate" | "challenging"

    @property
    def db_path(self) -> Path:
        # Every database lives at dev_databases/<db_id>/<db_id>.sqlite
        return BIRD_DEV / "dev_databases" / self.db_id / f"{self.db_id}.sqlite"


def load_dev(limit: int | None = None) -> list[Example]:
    """Read dev.json. Pass `limit` to get only the first N questions (cheap debugging runs)."""
    with open(BIRD_DEV / "dev.json") as f:
        records = json.load(f)  # list of dicts, one per question

    examples = []
    for r in records:
        examples.append(Example(
            question_id=r["question_id"],
            db_id=r["db_id"],
            question=r["question"],
            evidence=r["evidence"],
            gold_sql=r["SQL"],  # BIRD calls it "SQL"; we use a clearer name
            difficulty=r["difficulty"],
        ))

    if limit is not None:
        examples = examples[:limit]
    return examples


if __name__ == "__main__":
    # Quick self-check: `uv run python -m eval.data`
    for ex in load_dev(limit=3):
        print(ex.question_id, ex.db_id, ex.difficulty, "|", ex.question)
        print("   db exists:", ex.db_path.exists())
    print("total:", len(load_dev()))
