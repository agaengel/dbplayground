"""Helpers for the SQL playground: DuckDB <-> ER diagrams.

    con = connect()
    er_diagram(con)                  # draw the current schema (Graphviz)
    schema_to_mermaid(con)           # current schema as Mermaid erDiagram text
    mermaid_to_sql(text)             # Mermaid erDiagram text -> CREATE TABLE statements
    apply_mermaid(con, text)         # ... and run them
"""
import re
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).parent / "work" / "playground.duckdb"


def connect(path=DB_PATH):
    """Open (or create) the persistent playground database."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def reset(con):
    """Drop every table (careful!). Tables with foreign keys go first."""
    remaining = [t for (t,) in con.sql("select table_name from duckdb_tables() where not internal").fetchall()]
    while remaining:
        failed = []
        for t in remaining:
            try:
                con.execute(f'drop table "{t}"')
            except duckdb.Error:
                failed.append(t)  # still referenced by another table; retry next round
        if len(failed) == len(remaining):
            raise RuntimeError(f"Cannot drop tables: {failed}")
        remaining = failed


# ---------------------------------------------------------------- reading --
def _introspect(con):
    tables = {}
    for t, c, typ, nullable in con.sql(
        "select table_name, column_name, data_type, is_nullable "
        "from duckdb_columns() where schema_name = 'main' and not internal order by table_name, column_index"
    ).fetchall():
        tables.setdefault(t, {"cols": [], "pk": [], "fk": {}, "uq": set()})["cols"].append(
            {"name": c, "type": typ, "nullable": nullable}
        )
    fks = []  # (child, child_cols, parent, parent_cols)
    # SQL identifiers are case-insensitive, but DuckDB stores a FK's referenced table
    # as it was typed (`REFERENCES airport` for table `Airport`): map to the real name.
    real = {t.lower(): t for t in tables}
    for t, ctype, cols, ref, refcols in con.sql(
        "select table_name, constraint_type, constraint_column_names, "
        "referenced_table, referenced_column_names "
        "from duckdb_constraints() where schema_name = 'main'"
    ).fetchall():
        if t not in tables:
            continue
        if ctype == "PRIMARY KEY":
            tables[t]["pk"] = list(cols)
        elif ctype == "UNIQUE":
            tables[t]["uq"].update(cols)
        elif ctype == "FOREIGN KEY":
            ref = real.get(ref.lower(), ref)
            fks.append((t, list(cols), ref, list(refcols)))
            for c in cols:
                tables[t]["fk"][c] = ref
    return tables, fks


class Mermaid:
    """Mermaid text that JupyterLab (>= 4.1) renders as a diagram (mime type text/vnd.mermaid)."""

    def __init__(self, text):
        self.text = text

    def _repr_mimebundle_(self, include=None, exclude=None):
        return {"text/vnd.mermaid": self.text, "text/plain": self.text}

    def __str__(self):
        return self.text


def er_diagram(con, engine="mermaid"):
    """ER diagram of the current database, rendered inline in Jupyter.

    engine="mermaid" (default) needs JupyterLab >= 4.1; engine="graphviz" uses the
    Graphviz binary instead.
    """
    if engine == "graphviz":
        return er_diagram_graphviz(con)
    return Mermaid(schema_to_mermaid(con))


def er_diagram_graphviz(con):
    """Return a Graphviz object (renders inline in Jupyter)."""
    import graphviz

    tables, fks = _introspect(con)
    g = graphviz.Digraph("er", graph_attr={"rankdir": "LR", "nodesep": "0.6"})
    g.attr("node", shape="plain", fontname="Helvetica")
    g.attr("edge", fontname="Helvetica", arrowhead="crow", arrowtail="tee", dir="both")
    for t, info in tables.items():
        rows = "".join(
            '<TR><TD ALIGN="LEFT">{}{}</TD><TD ALIGN="LEFT">{}</TD><TD ALIGN="LEFT">{}</TD></TR>'.format(
                "<B>PK</B>" if c["name"] in info["pk"] else "",
                " FK" if c["name"] in info["fk"] else "",
                c["name"],
                f'<FONT COLOR="gray40">{c["type"]}</FONT>',
            )
            for c in info["cols"]
        )
        g.node(
            t,
            f'<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="4">'
            f'<TR><TD COLSPAN="3" BGCOLOR="#dbe8fc"><B>{t}</B></TD></TR>{rows}</TABLE>>',
        )
    for child, ccols, parent, _ in fks:
        nullable = any(
            c["nullable"] for c in tables[child]["cols"] if c["name"] in ccols
        )
        g.edge(parent, child, taillabel="", label=", ".join(ccols),
               arrowtail="teeodot" if nullable else "teetee")
    return g


def schema_to_mermaid(con):
    """Current schema as Mermaid `erDiagram` text (can be edited and fed back in)."""
    tables, fks = _introspect(con)
    out = ["erDiagram"]
    for child, ccols, parent, _ in fks:
        nullable = any(c["nullable"] for c in tables[child]["cols"] if c["name"] in ccols)
        left = "|o" if nullable else "||"
        out.append(f"    {parent} {left}--o{{ {child} : {ccols[0]}")
    for t, info in tables.items():
        out.append(f"    {t} {{")
        for c in info["cols"]:
            keys = [k for k, on in (("PK", c["name"] in info["pk"]),
                                    ("FK", c["name"] in info["fk"])) if on]
            typ = re.sub(r"\W+", "_", c["type"]).strip("_")
            out.append(f"        {typ} {c['name']} {', '.join(keys)}".rstrip())
        out.append("    }")
    return "\n".join(out)


# ---------------------------------------------------------------- writing --
_TYPES = {"string": "VARCHAR", "text": "VARCHAR", "int": "INTEGER", "integer": "INTEGER",
          "float": "DOUBLE", "double": "DOUBLE", "bool": "BOOLEAN", "boolean": "BOOLEAN",
          "datetime": "TIMESTAMP"}
_REL = re.compile(r"^(\w+)\s+(\|\||\|o|\}o|\}\|)(--|\.\.)(\|\||o\||o\{|\|\{)\s+(\w+)\s*(?::.*)?$")
_ATTR = re.compile(r'^(\w+(?:\(\d+(?:,\s*\d+)?\))?)\s+(\w+)\s*([A-Z, ]*?)\s*(?:"[^"]*")?$')


def _parse_mermaid(text):
    entities, rels, cur = {}, [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%") or line == "erDiagram":
            continue
        if cur is not None:
            if line == "}":
                cur = None
                continue
            m = _ATTR.match(line)
            if not m:
                raise ValueError(f"Cannot parse attribute line: {raw!r}")
            typ, name, keys = m.groups()
            keys = {k.strip() for k in keys.split(",") if k.strip()}
            entities[cur].append({"type": typ, "name": name, "keys": keys})
        elif line.endswith("{"):
            cur = line[:-1].strip()
            entities.setdefault(cur, [])
        elif (m := _REL.match(line)):
            rels.append(m.groups())
        elif re.fullmatch(r"\w+", line):
            entities.setdefault(line, [])
        else:
            raise ValueError(f"Cannot parse line: {raw!r}")
    return entities, rels


def mermaid_to_sql(text):
    """Translate a Mermaid erDiagram into CREATE TABLE statements.

    Rules: the "one" side of a relationship is the parent, the "many" side the child
    (for 1:1 the right side is the child). The child gets a foreign key column
    `<parent>_<pk>` unless it already has an attribute marked FK for it. A parent
    without PK gets `id INTEGER PRIMARY KEY`. Many-to-many creates a junction table.
    """
    entities, rels = _parse_mermaid(text)
    ents = {n.lower(): {"name": n, "attrs": a} for n, a in entities.items()}
    for e in ents.values():
        e["fks"] = []  # (column, parent, parent_col, nullable)

    def ent(n):
        return ents.setdefault(n.lower(), {"name": n, "attrs": [], "fks": []})

    def pk_of(e):
        pks = [a for a in e["attrs"] if "PK" in a["keys"]]
        if not pks:
            e["attrs"].insert(0, {"type": "int", "name": "id", "keys": {"PK"}})
            pks = [e["attrs"][0]]
        if len(pks) > 1:
            raise ValueError(f"{e['name']}: foreign keys to composite primary keys are not supported")
        return pks[0]

    def link(parent, child, optional):
        p, c = ent(parent), ent(child)
        pk = pk_of(p)
        col = f"{p['name'].lower()}_{pk['name']}"
        free = [a for a in c["attrs"] if "FK" in a["keys"]
                and a["name"] not in {f[0] for f in c["fks"]}]
        match = next((a for a in free if a["name"].lower() == col.lower()), None) or \
            (free[0] if free else None)
        if match:
            col = match["name"]
        else:
            c["attrs"].append({"type": pk["type"], "name": col, "keys": {"FK"}})
        c["fks"].append((col, p["name"], pk["name"], optional))

    for left, ltok, _, rtok, right in rels:
        lmany, rmany = ltok[0] == "}", rtok[1] == "{"
        if lmany and rmany:  # many-to-many -> junction table
            j = ent(f"{left}_{right}")
            j["attrs"] = []
            link(left, j["name"], False)
            link(right, j["name"], False)
            for a in j["attrs"]:
                a["keys"].add("PK")
        elif lmany:
            link(right, left, rtok[0] == "o")
        else:
            link(left, right, ltok[1] == "o")

    def ddl(e):
        lines, pks = [], [a["name"] for a in e["attrs"] if "PK" in a["keys"]]
        fkc = {f[0]: f for f in e["fks"]}
        for a in e["attrs"]:
            t = a["type"]
            t = _TYPES.get(t.lower(), t.upper() if "(" not in t else t)
            parts = [f'"{a["name"]}"', t]
            if "PK" in a["keys"] and len(pks) == 1:
                parts.append("PRIMARY KEY")
            elif "PK" not in a["keys"] and a["name"] in fkc and not fkc[a["name"]][3]:
                parts.append("NOT NULL")
            if "UK" in a["keys"]:
                parts.append("UNIQUE")
            lines.append("  " + " ".join(parts))
        if len(pks) > 1:
            lines.append("  PRIMARY KEY (" + ", ".join(f'"{p}"' for p in pks) + ")")
        for col, parent, pcol, _ in e["fks"]:
            lines.append(f'  FOREIGN KEY ("{col}") REFERENCES "{parent}" ("{pcol}")')
        return f'CREATE TABLE "{e["name"]}" (\n' + ",\n".join(lines) + "\n);"

    # parents before children
    order, done = [], set()
    pending = list(ents.values())
    while pending:
        ready = [e for e in pending
                 if all(f[1].lower() in done or f[1].lower() == e["name"].lower() for f in e["fks"])]
        if not ready:
            raise ValueError("Circular relationships cannot be created in this order")
        for e in ready:
            order.append(ddl(e))
            done.add(e["name"].lower())
            pending.remove(e)
    return "\n\n".join(order)


def apply_mermaid(con, text, show=True):
    """Create the tables described by a Mermaid erDiagram."""
    sql = mermaid_to_sql(text)
    if show:
        print(sql)
    con.execute(sql)
    return er_diagram(con)
