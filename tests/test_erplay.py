import duckdb
import pytest

import erplay

MERMAID = """
erDiagram
    STUDENT ||--o{ ENROLLMENT : has
    COURSE  ||--o{ ENROLLMENT : has
    TEACHER |o--o{ COURSE : teaches
    STUDENT {
        int id PK
        string name
    }
    COURSE {
        int id PK
        string title
    }
    TEACHER {
        int id PK
        string name
    }
"""


@pytest.fixture
def con():
    return duckdb.connect()


def test_mermaid_creates_schema(con):
    erplay.apply_mermaid(con, MERMAID, show=False)
    tables, fks = erplay._introspect(con)
    assert set(tables) == {"STUDENT", "COURSE", "TEACHER", "ENROLLMENT"}
    assert {(c, p) for c, _, p, _ in fks} == {
        ("ENROLLMENT", "STUDENT"), ("ENROLLMENT", "COURSE"), ("COURSE", "TEACHER")}
    cols = {c["name"]: c["nullable"] for c in tables["COURSE"]["cols"]}
    assert cols["teacher_id"] is True  # optional parent -> nullable FK


def test_foreign_keys_are_enforced(con):
    erplay.apply_mermaid(con, MERMAID, show=False)
    with pytest.raises(duckdb.ConstraintException):
        con.execute("insert into ENROLLMENT values (1, 1)")


def test_many_to_many_makes_junction_table(con):
    erplay.apply_mermaid(con, "erDiagram\n A }o--o{ B : x", show=False)
    tables, _ = erplay._introspect(con)
    assert set(tables) == {"A", "B", "A_B"}


def test_round_trip(con):
    erplay.apply_mermaid(con, MERMAID, show=False)
    text = erplay.schema_to_mermaid(con)
    other = duckdb.connect()
    erplay.apply_mermaid(other, text, show=False)
    assert erplay._introspect(other)[1] == erplay._introspect(con)[1]


def test_diagram_has_all_tables(con):
    erplay.apply_mermaid(con, MERMAID, show=False)
    src = erplay.er_diagram(con).source
    for t in ("STUDENT", "COURSE", "TEACHER", "ENROLLMENT"):
        assert t in src


def test_bad_input():
    with pytest.raises(ValueError):
        erplay.mermaid_to_sql("erDiagram\n this is nonsense !!")


def test_reset_drops_tables_with_foreign_keys(con):
    erplay.apply_mermaid(con, MERMAID, show=False)
    erplay.reset(con)
    assert erplay._introspect(con)[0] == {}


def test_fk_reference_with_different_case(con):
    con.execute("create table Airport (id integer primary key, name varchar)")
    con.execute("create table Flight (id integer primary key, "
                "airport_id integer references airport (id))")
    tables, fks = erplay._introspect(con)
    assert set(tables) == {"Airport", "Flight"}
    assert [(c, p) for c, _, p, _ in fks] == [("Flight", "Airport")]
    assert "\tairport [" not in erplay.er_diagram(con).source  # no phantom lower-case node
