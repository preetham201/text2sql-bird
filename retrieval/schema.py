"""Extract a structured description of every BIRD database: the raw material for retrieval.

For each database we collect:
- tables and their columns, with SQL types and which columns are primary keys
- foreign keys (which column points to which table), used later to add join paths
- the human-written column descriptions BIRD ships in database_description/<table>.csv
- a few sample values per column (they help match "Fresno" in a question to County Name)

Phase 1 sent the raw CREATE TABLE text. Retrieval needs structure instead: something we
can score column by column and then rebuild a smaller CREATE TABLE from.

Run: `uv run python -m retrieval.schema` (self-check; builds the cache on first run)
"""
import csv
import io
import pickle
import sqlite3
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path

from eval.data import BIRD_ROOT

N_SAMPLES = 3          # sample values kept per column
MAX_VALUE_CHARS = 40   # long text values (addresses, comments) are cut to this length


@dataclass
class Column:
    table: str
    name: str
    type: str
    is_pk: bool
    description: str = ""        # from database_description/<table>.csv ("" if missing)
    value_description: str = ""  # e.g. "0: not charter; 1: charter"
    samples: list[str] = field(default_factory=list)

    @cached_property  # computed once per column, then stored (it's used thousands of times per question)
    def key(self) -> str:
        """Lower-cased "table.column": the id we use everywhere (recall, ranking, pruning)."""
        return f"{self.table}.{self.name}".lower()


@dataclass
class ForeignKey:
    table: str        # the table that holds the FK column
    column: str
    ref_table: str    # the table it points to
    ref_column: str


@dataclass
class DbSchema:
    db_id: str
    tables: dict[str, list[Column]]   # table name -> its columns, in CREATE TABLE order
    foreign_keys: list[ForeignKey]

    @cached_property  # built once, not on every call (retrieval asks for it constantly)
    def columns(self) -> list[Column]:
        return [c for cols in self.tables.values() for c in cols]


def quote(name: str) -> str:
    """Quote an identifier for SQL. BIRD has names like `Academic Year` or `Free Meal Count (K-12)`."""
    return '"' + name.replace('"', '""') + '"'


def _read_csv_any_encoding(path: Path) -> list[dict]:
    """BIRD's description CSVs come in mixed encodings (UTF-8, UTF-8 with BOM, Windows-1252).

    utf-8-sig handles plain UTF-8 and strips the BOM; cp1252 is the usual Windows fallback.
    latin-1 never fails (every byte is a valid character), so it's the last resort.
    """
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(text)))


def _load_descriptions(db_dir: Path) -> dict[str, dict[str, tuple[str, str]]]:
    """Return {table_lower: {column_lower: (description, value_description)}}."""
    out: dict[str, dict[str, tuple[str, str]]] = {}
    desc_dir = db_dir / "database_description"
    if not desc_dir.exists():
        return out
    for path in desc_dir.glob("*.csv"):
        cols = {}
        for row in _read_csv_any_encoding(path):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name = row.get("original_column_name", "")
            if not name:
                continue
            desc = row.get("column_description", "")
            # column_name is a friendlier name (e.g. "free meal count"); keep it if it adds info
            friendly = row.get("column_name", "")
            if friendly and friendly.lower() != name.lower() and friendly.lower() not in desc.lower():
                desc = f"{friendly}: {desc}" if desc else friendly
            cols[name.lower()] = (" ".join(desc.split()), " ".join(row.get("value_description", "").split()))
        out[path.stem.strip().lower()] = cols  # file names don't always match the table's case
    return out


def _sample_values(con: sqlite3.Connection, table: str, column: str) -> list[str]:
    """A few distinct non-null values. LIMIT lets SQLite stop early, so this stays fast on big tables."""
    try:
        rows = con.execute(
            f"SELECT DISTINCT {quote(column)} FROM {quote(table)} "
            f"WHERE {quote(column)} IS NOT NULL AND {quote(column)} != '' LIMIT {N_SAMPLES}"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0])[:MAX_VALUE_CHARS] for r in rows]


def extract_schema(db_path: Path) -> DbSchema:
    """Read one SQLite database (read-only) into a DbSchema."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    descriptions = _load_descriptions(db_path.parent)
    try:
        table_names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

        tables, fks = {}, []
        for t in table_names:
            t_desc = descriptions.get(t.lower(), {})
            cols = []
            # PRAGMA table_info: one row per column -> (cid, name, type, notnull, default, pk)
            for _, name, ctype, _, _, pk in con.execute(f"PRAGMA table_info({quote(t)})"):
                desc, val_desc = t_desc.get(name.lower(), ("", ""))
                if desc.lower() == name.lower():  # "CDSCode: CDSCode" adds nothing
                    desc = ""
                cols.append(Column(t, name, ctype or "", pk > 0, desc, val_desc, _sample_values(con, t, name)))
            tables[t] = cols

            # PRAGMA foreign_key_list: (id, seq, ref_table, from_col, to_col, ...)
            for row in con.execute(f"PRAGMA foreign_key_list({quote(t)})"):
                ref_table, from_col, to_col = row[2], row[3], row[4]
                if to_col is None:  # "REFERENCES other_table" with no column = its primary key
                    pks = [c[1] for c in con.execute(f"PRAGMA table_info({quote(ref_table)})") if c[5]]
                    to_col = pks[0] if pks else from_col
                fks.append(ForeignKey(t, from_col, ref_table, to_col))
    finally:
        con.close()
    return DbSchema(db_path.stem, tables, fks)


def _cache_path(split: str) -> Path:
    return BIRD_ROOT / split / "schemas.pkl"  # under data/, so gitignored


@lru_cache(maxsize=None)
def load_schemas(split: str) -> dict[str, DbSchema]:
    """Return {db_id: DbSchema} for a split. Built once (sampling values takes ~a minute on train), then cached."""
    path = _cache_path(split)
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)

    db_root = BIRD_ROOT / split / f"{split}_databases"
    schemas = {}
    for db_dir in sorted(p for p in db_root.iterdir() if p.is_dir()):
        db_file = db_dir / f"{db_dir.name}.sqlite"
        if db_file.exists():
            schemas[db_dir.name] = extract_schema(db_file)
            print(f"  schema: {db_dir.name} ({len(schemas[db_dir.name].columns)} columns)")
    with open(path, "wb") as f:
        pickle.dump(schemas, f)
    return schemas


@lru_cache(maxsize=None)
def _original_statements(db_path: Path) -> tuple[tuple[str, str], ...]:
    """(table name, original CREATE TABLE text) for every table, sorted by name."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    finally:
        con.close()
    return tuple((name, sql.strip() + ";") for name, sql in rows)


def original_ddl(db_path: Path, tables: set[str] | None = None) -> str:
    """The database's own CREATE TABLE statements (exactly what Phase 1 sent), optionally only
    for some tables (lower-case names). Used for table-level pruning so the only difference from
    the full-schema prompt is *which* tables appear, not how they're written."""
    return "\n\n".join(sql for name, sql in _original_statements(db_path)
                        if tables is None or name.lower() in tables)


def render_ddl(schema: DbSchema, keep: set[str] | None = None, with_descriptions: bool = False,
               foreign_keys: list[ForeignKey] | None = None) -> str:
    """Rebuild CREATE TABLE statements, optionally only for some columns.

    keep: set of column keys ("table.column", lower case) to include. A table appears only if
          at least one of its columns is kept. None = the whole schema.
    with_descriptions: add descriptions + sample values as SQL comments (`-- ...`).
    foreign_keys: which links to write (default: the declared ones; the pipeline passes
          declared + inferred, see retrieval/fk_expand.py).
    FK clauses are written only when both ends are kept, so the DDL never mentions
    a column the model can't see.
    """
    kept = (lambda c: True) if keep is None else (lambda c: c.key in keep)
    kept_keys = {c.key for c in schema.columns if kept(c)}
    parts = []
    for table, cols in schema.tables.items():
        cols = [c for c in cols if kept(c)]
        if not cols:
            continue
        lines = []
        for c in cols:
            line = f"  {quote(c.name)} {c.type}".rstrip()
            if with_descriptions:
                notes = [n for n in (c.description, c.value_description) if n]
                if c.samples:
                    notes.append("e.g. " + ", ".join(c.samples))
                if notes:
                    line += "  -- " + " | ".join(notes)
            lines.append(line)
        pks = [c.name for c in cols if c.is_pk]
        extra = []
        if pks:
            extra.append(f"  PRIMARY KEY ({', '.join(quote(p) for p in pks)})")
        for fk in (schema.foreign_keys if foreign_keys is None else foreign_keys):
            if fk.table == table and f"{table}.{fk.column}".lower() in kept_keys \
                    and f"{fk.ref_table}.{fk.ref_column}".lower() in kept_keys:
                extra.append(f"  FOREIGN KEY ({quote(fk.column)}) REFERENCES {quote(fk.ref_table)}({quote(fk.ref_column)})")
        # comments must end the line, so the comma goes before the "--"
        body = []
        all_lines = lines + extra
        for i, line in enumerate(all_lines):
            comma = "," if i < len(all_lines) - 1 else ""
            if "  -- " in line:
                code, comment = line.split("  -- ", 1)
                body.append(f"{code}{comma}  -- {comment}")
            else:
                body.append(line + comma)
        parts.append(f"CREATE TABLE {quote(table)} (\n" + "\n".join(body) + "\n);")
    return "\n\n".join(parts)


if __name__ == "__main__":
    # Import ourselves by module name: pickle records the class path, and running this file as
    # __main__ would store "__main__.Column", which other modules can't load.
    from retrieval.schema import load_schemas, render_ddl  # noqa: F811
    for split in ("dev", "train"):
        schemas = load_schemas(split)
        n_cols = [len(s.columns) for s in schemas.values()]
        n_tabs = [len(s.tables) for s in schemas.values()]
        n_fk = sum(len(s.foreign_keys) for s in schemas.values())
        n_desc = sum(bool(c.description or c.value_description) for s in schemas.values() for c in s.columns)
        print(f"{split}: {len(schemas)} DBs, tables {min(n_tabs)}-{max(n_tabs)}, columns {min(n_cols)}-{max(n_cols)} "
              f"(total {sum(n_cols)}), {n_fk} FKs, {100 * n_desc / sum(n_cols):.0f}% of columns described")

    s = load_schemas("dev")["california_schools"]
    print("\nexample column:", s.tables["schools"][4])
    print("\nexample FK:", s.foreign_keys[0])
    keep = {"frpm.cdscode", "frpm.free meal count (k-12)", "schools.cdscode", "schools.county"}
    print("\npruned DDL with descriptions:\n" + render_ddl(s, keep, with_descriptions=True))
