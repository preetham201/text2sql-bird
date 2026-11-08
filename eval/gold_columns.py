"""Find which tables and columns a SQL query uses, by parsing it (no execution).

Why: schema recall = "did retrieval keep every table/column the gold SQL needs?". To know
what the gold SQL needs we parse it into an AST (abstract syntax tree: the query as a tree of
nodes like Select, Join, Column) with sqlglot and walk the tree.

The hard part is aliases. In `SELECT T1.name FROM schools AS T1`, the column node says its
table is "T1". sqlglot's `qualify` step resolves that: given the schema, it rewrites every
column as table_alias.column (even unqualified ones like `name`), and each table node knows
its alias. So we map alias -> real table name and read off (table, column) pairs.

Run: `uv run python -m eval.gold_columns` (self-check + parse success rate on dev and train)
"""
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from retrieval.schema import DbSchema


_SQLGLOT_SCHEMAS: dict[str, dict] = {}  # db_id -> sqlglot schema, built once per database


def _sqlglot_schema(schema: DbSchema) -> dict:
    """{table: {column: type}} in lower case: the shape sqlglot's qualify() expects."""
    if schema.db_id not in _SQLGLOT_SCHEMAS:
        _SQLGLOT_SCHEMAS[schema.db_id] = {
            t.lower(): {c.name.lower(): "TEXT" for c in cols} for t, cols in schema.tables.items()}
    return _SQLGLOT_SCHEMAS[schema.db_id]


def used_columns(sql: str, schema: DbSchema) -> tuple[set[str], set[str]] | None:
    """Return (tables, columns) used by `sql`, as lower-case "table" and "table.column".

    Returns None if sqlglot can't parse or qualify the query (we count those separately
    instead of guessing).
    """
    try:
        # SQLite accepts `name` and "name" and [name] quoting; BIRD gold SQL uses backticks a lot.
        tree = sqlglot.parse_one(sql, read="sqlite")
        # Lower-case everything first: SQLite identifiers are case-insensitive, sqlglot's aren't.
        tree = tree.transform(
            lambda n: exp.to_identifier(n.name.lower(), quoted=n.quoted) if isinstance(n, exp.Identifier) else n)
        tree = qualify(tree, schema=_sqlglot_schema(schema), dialect="sqlite",
                       validate_qualify_columns=False, quote_identifiers=False)
    except Exception:  # sqlglot raises several error types; any of them means "couldn't analyse"
        return None

    real_tables = {t.lower() for t in schema.tables}
    known_columns = {c.key for c in schema.columns}

    # alias -> real table, for every base table in the query (including inside subqueries)
    alias_to_table = {}
    tables = set()
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if name in real_tables:
            tables.add(name)
            alias_to_table[t.alias_or_name.lower()] = name

    columns = set()
    for c in tree.find_all(exp.Column):
        table = alias_to_table.get(c.table.lower())
        key = f"{table}.{c.name.lower()}"
        # Columns of a subquery/CTE alias (derived columns) aren't schema columns: skip them.
        # Their underlying base columns are found inside the subquery itself.
        if table and key in known_columns:
            columns.add(key)
    return tables, columns


if __name__ == "__main__":
    from eval.data import load_split
    from retrieval.schema import load_schemas

    s = load_schemas("dev")["california_schools"]
    sql = ("SELECT T2.County FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode "
           "WHERE `Free Meal Count (K-12)` > 500")
    print(used_columns(sql, s))

    for split in ("dev", "train"):
        schemas = load_schemas(split)
        examples = load_split(split)
        failed = [ex for ex in examples if used_columns(ex.gold_sql, schemas[ex.db_id]) is None]
        print(f"{split}: parsed {len(examples) - len(failed)}/{len(examples)} gold queries")
        for ex in failed[:3]:
            print("   could not parse:", ex.question_id, ex.gold_sql[:120])
