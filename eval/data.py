"""Load BIRD questions (dev or train) into typed Example objects."""
import json
from dataclasses import dataclass
from pathlib import Path

# Project root = two folders up from this file (eval/data.py -> eval/ -> project root).
# Building paths from __file__ means the code works no matter where you run it from.
BIRD_ROOT = Path(__file__).resolve().parent.parent / "data" / "bird"
BIRD_DEV = BIRD_ROOT / "dev"


@dataclass
class Example:
    """One BIRD question with everything needed to evaluate it."""
    question_id: int
    db_id: str          # which database the question is about, e.g. "california_schools"
    question: str       # the natural-language question
    evidence: str       # BIRD's domain-knowledge hint (goes into the prompt)
    gold_sql: str       # the reference SQL query
    difficulty: str     # "simple" | "moderate" | "challenging" ("unknown" on train: BIRD doesn't label it)
    split: str = "dev"  # "dev" (where we report) or "train" (where we tune). Default keeps old code working.

    @property
    def db_path(self) -> Path:
        # Every database lives at <split>/<split>_databases/<db_id>/<db_id>.sqlite
        return BIRD_ROOT / self.split / f"{self.split}_databases" / self.db_id / f"{self.db_id}.sqlite"


def load_split(split: str, limit: int | None = None) -> list[Example]:
    """Read <split>.json ("dev" or "train"). Pass `limit` to get only the first N questions."""
    with open(BIRD_ROOT / split / f"{split}.json") as f:
        records = json.load(f)  # list of dicts, one per question

    examples = []
    for i, r in enumerate(records):
        examples.append(Example(
            # train.json has no question_id or difficulty; its position in the file is a stable id
            question_id=r.get("question_id", i),
            db_id=r["db_id"],
            question=r["question"],
            evidence=r["evidence"],
            gold_sql=r["SQL"],  # BIRD calls it "SQL"; we use a clearer name
            difficulty=r.get("difficulty", "unknown"),
            split=split,
        ))

    if limit is not None:
        examples = examples[:limit]
    return examples


def load_dev(limit: int | None = None) -> list[Example]:
    """Shortcut used by all Phase 1 code."""
    return load_split("dev", limit)


if __name__ == "__main__":
    # Quick self-check: `uv run python -m eval.data`
    for ex in load_dev(limit=3):
        print(ex.question_id, ex.db_id, ex.difficulty, "|", ex.question)
        print("   db exists:", ex.db_path.exists())
    print("total dev:", len(load_dev()))
    ex = load_split("train", limit=1)[0]
    print("train example:", ex.question_id, ex.db_id, "| db exists:", ex.db_path.exists())
    print("total train:", len(load_split("train")))
