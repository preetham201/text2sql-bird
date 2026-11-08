"""Make a pruned schema joinable: add bridge tables and join-key columns.

Problem: retrieval may keep `schools.County` and `satscores.AvgScrMath` but not the columns
that join the two tables (`schools.CDSCode`, `satscores.cds`). Or the question needs tables
A and C that are only connected through table B, which mentions nothing in the question.
Without those, the model can see the right data but can't write the JOIN.

Fix: treat the schema as a graph (tables = nodes, foreign keys = edges). Connect the kept
tables with shortest paths (BFS, breadth-first search: explore neighbours level by level, so
the first time you reach a node is via a shortest path), add any tables on those paths, then
add the FK columns on both ends of every edge between kept tables, plus each kept table's
primary key.
"""
from collections import deque

from retrieval.schema import DbSchema, ForeignKey

_LINKS: dict[str, list[ForeignKey]] = {}  # db_id -> declared + inferred links


def links(schema: DbSchema) -> list[ForeignKey]:
    """Declared foreign keys plus inferred ones.

    BIRD's declared FKs are incomplete: e.g. debit_card_specializing.transactions_1k has
    CustomerID, GasStationID and ProductID but declares no FK at all, and 3 train databases
    declare none. Inference rule: a column with the same name in two tables, where it is the
    primary key of one of them, is treated as a join link (transactions_1k.CustomerID ->
    customers.CustomerID). Generic names like "id" are skipped: every table has one.
    """
    if schema.db_id not in _LINKS:
        out = list(schema.foreign_keys)
        # remember both directions, so an inferred link never just reverses a declared FK
        seen = {(fk.table.lower(), fk.column.lower(), fk.ref_table.lower()) for fk in out} | \
               {(fk.ref_table.lower(), fk.ref_column.lower(), fk.table.lower()) for fk in out}
        pks = [c for c in schema.columns if c.is_pk and c.name.lower() not in {"id", "name"}]
        for pk in pks:
            for c in schema.columns:
                if c.name.lower() == pk.name.lower() and c.table != pk.table \
                        and (c.table.lower(), c.name.lower(), pk.table.lower()) not in seen:
                    out.append(ForeignKey(c.table, c.name, pk.table, pk.name))
                    seen.add((c.table.lower(), c.name.lower(), pk.table.lower()))
                    seen.add((pk.table.lower(), pk.name.lower(), c.table.lower()))
        _LINKS[schema.db_id] = out
    return _LINKS[schema.db_id]


def _graph(schema: DbSchema) -> dict[str, set[str]]:
    """Undirected adjacency list over lower-case table names."""
    g: dict[str, set[str]] = {t.lower(): set() for t in schema.tables}
    for fk in links(schema):
        a, b = fk.table.lower(), fk.ref_table.lower()
        if a in g and b in g and a != b:
            g[a].add(b)
            g[b].add(a)
    return g


def _shortest_path(g: dict[str, set[str]], sources: set[str], target: str) -> list[str] | None:
    """BFS from all `sources` at once to `target`. Returns the path's nodes, or None if unreachable."""
    prev: dict[str, str | None] = {s: None for s in sources}
    queue = deque(sorted(sources))
    while queue:
        node = queue.popleft()
        if node == target:
            path = []
            while node is not None:
                path.append(node)
                node = prev[node]
            return path
        # sorted(): sets of strings iterate in a different order in every Python process (hash
        # randomisation), so ties between equally short paths would be broken differently run to run
        for nxt in sorted(g[node]):
            if nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    return None


def connect_tables(schema: DbSchema, tables: list[str]) -> list[str]:
    """Add bridge tables so `tables` (best-ranked first) form one connected piece where possible.

    Greedy: start from the best table, then link each next table to everything kept so far by
    the shortest FK path. Tables with no FK path are kept anyway (some BIRD DBs lack FKs).
    """
    if not tables:
        return []
    g = _graph(schema)
    kept = [tables[0]]
    for t in tables[1:]:
        if t in kept:
            continue
        path = _shortest_path(g, set(kept), t)
        for node in (path or [t]):
            if node not in kept:
                kept.append(node)
    return kept


def join_columns(schema: DbSchema, tables: set[str]) -> set[str]:
    """Column keys needed to join and identify rows of the kept tables: FK pairs between them + PKs."""
    out = set()
    for fk in links(schema):
        if fk.table.lower() in tables and fk.ref_table.lower() in tables:
            out.add(f"{fk.table}.{fk.column}".lower())
            out.add(f"{fk.ref_table}.{fk.ref_column}".lower())
    for c in schema.columns:
        if c.is_pk and c.table.lower() in tables:
            out.add(c.key)
    return out


if __name__ == "__main__":
    from retrieval.schema import load_schemas
    s = load_schemas("dev")["debit_card_specializing"]
    print(f"{len(s.foreign_keys)} declared FKs, {len(links(s))} with inferred ones")
    print("FK graph:", _graph(s))
    print("connect customers + products:", connect_tables(s, ["customers", "products"]))
    print("join columns:", sorted(join_columns(s, {"customers", "transactions_1k", "products"})))
