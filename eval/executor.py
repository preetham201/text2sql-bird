"""Run SQL against a BIRD SQLite database safely: read-only, with a timeout."""
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_S = 30.0  # BIRD's official evaluation also uses a 30s limit


@dataclass
class ExecResult:
    ok: bool                       # True if the query ran without error
    rows: list[tuple] | None       # result rows (None if it failed)
    error: str | None              # error message (None if it succeeded)
    seconds: float                 # how long execution took


def execute(db_path: Path, sql: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> ExecResult:
    """Execute `sql` on the database at `db_path` and return rows or an error. Never raises."""
    start = time.perf_counter()

    # mode=ro opens the file read-only: even a generated DROP TABLE cannot modify it.
    # This permission is the real safety boundary, not any check on the SQL text.
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    # SQLite has no built-in query timeout. A progress handler is a callback SQLite
    # calls every N internal steps; returning a non-zero value aborts the query.
    deadline = start + timeout_s
    con.set_progress_handler(lambda: int(time.perf_counter() > deadline), 10_000)

    try:
        rows = con.execute(sql).fetchall()
        return ExecResult(True, rows, None, time.perf_counter() - start)
    except sqlite3.OperationalError as e:
        elapsed = time.perf_counter() - start
        # An aborted query shows up as "interrupted"; label it clearly as a timeout.
        msg = f"timeout after {timeout_s}s" if "interrupted" in str(e) else str(e)
        return ExecResult(False, None, msg, elapsed)
    except Exception as e:  # any other failure (bad SQL type errors, etc.) is still just a failed prediction
        return ExecResult(False, None, f"{type(e).__name__}: {e}", time.perf_counter() - start)
    finally:
        con.close()


def execute_fingerprint(db_path: Path, sql: str, timeout_s: float = DEFAULT_TIMEOUT_S):
    """Like execute(), but streams rows into a Fingerprint instead of keeping them.

    Returns (Fingerprint or None, error or None, seconds). Peak memory stays small even for a
    query returning millions of rows, because rows are read 10,000 at a time and then dropped.
    """
    from eval.compare import fingerprint

    start = time.perf_counter()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    deadline = start + timeout_s
    con.set_progress_handler(lambda: int(time.perf_counter() > deadline), 10_000)

    def stream(cursor):
        while batch := cursor.fetchmany(10_000):
            yield from batch

    try:
        fp = fingerprint(stream(con.execute(sql)))
        return fp, None, time.perf_counter() - start
    except sqlite3.OperationalError as e:
        msg = f"timeout after {timeout_s}s" if "interrupted" in str(e) else str(e)
        return None, msg, time.perf_counter() - start
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", time.perf_counter() - start
    finally:
        con.close()


if __name__ == "__main__":
    # Self-check: `uv run python -m eval.executor`
    from eval.data import load_dev
    ex = load_dev(limit=1)[0]
    print("gold:   ", execute(ex.db_path, ex.gold_sql))
    print("bad col:", execute(ex.db_path, "SELECT no_such_column FROM frpm"))
    print("write:  ", execute(ex.db_path, "DROP TABLE frpm"))
