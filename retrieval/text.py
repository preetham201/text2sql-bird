"""Turn columns and questions into text that the retrievers can search.

Every retriever ranks *columns*. Each column becomes one short "document" made of its table
name, column name, description and sample values. The question + BIRD hint is the "query".
"""
import re

from eval.data import Example
from retrieval.schema import Column

# Very common words that carry no information about which column is meant
STOPWORDS = set("""a an the of in on at to for by with from and or is are was were be been what which who whom
whose how many much list name names give show find all each per their its it this that these those
there please among than as do does did have has had not no""".split())


def split_identifier(name: str) -> str:
    """"CDSCode" -> "cds code", "free_meal_count" -> "free meal count", "NumTstTakr" -> "num tst takr".

    Database column names glue words together. Splitting them lets the word "code" in
    a question match the column CDSCode.
    """
    name = re.sub(r"[_\-()./]", " ", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)       # camelCase -> camel Case
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)    # CDSCode -> CDS Code
    return " ".join(name.split())


def column_text(c: Column) -> str:
    """The searchable document for one column."""
    parts = [split_identifier(c.table), split_identifier(c.name)]
    if c.name != split_identifier(c.name):
        parts.append(c.name)  # keep the raw name too, so an exact mention like "CDSCode" still matches
    parts += [c.description, c.value_description]
    if c.samples:
        parts.append("values: " + ", ".join(c.samples))
    return " | ".join(p for p in parts if p)


def query_text(ex: Example) -> str:
    """The question plus BIRD's hint. Hints often name columns outright ("refers to `NumTstTakr`")."""
    return f"{ex.question} {ex.evidence}".strip()


def tokenize(text: str) -> list[str]:
    """For BM25: split identifiers, lower-case, keep word tokens, drop stopwords."""
    words = re.findall(r"[a-z0-9]+", split_identifier(text).lower())
    return [w for w in words if w not in STOPWORDS]


if __name__ == "__main__":
    from retrieval.schema import load_schemas
    s = load_schemas("dev")["california_schools"]
    print(split_identifier("CDSCode"), "|", split_identifier("NumTstTakr"), "|", split_identifier("free_meal_count"))
    print(column_text(s.tables["satscores"][3]))
    print(tokenize("What is the highest eligible free rate for K-12 students in Alameda County?"))
