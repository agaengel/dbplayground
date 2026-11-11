# SQL Playground

A throw-away-able place to learn SQL: **DuckDB + JupyterLab in one container**.

* define schemas with SQL and run queries (`%%sql` cells)
* generate an **ER diagram** from the current database (rendered as Mermaid in JupyterLab; `engine="graphviz"` as fallback)
* go the other way: write an ER diagram (Mermaid `erDiagram` text) and get the **tables + foreign keys generated**
* the database is a single file, `work/playground.duckdb`, that survives container restarts

DuckDB was chosen over Postgres because there is no server, user or password to set up, and its SQL is
very close to Postgres. (Want a real server later? Add a `postgres` service to `compose.yaml`.)

## Run it (Windows + WSL)

1. Install Docker (Docker Desktop with WSL integration) **or** Podman inside WSL.
2. Clone this repo **inside the WSL filesystem** (e.g. `~/dbplayground`, not `/mnt/c/...` – much faster).
3. In the WSL terminal:

   ```sh
   docker compose up --build        # or: podman compose up --build
   ```
4. Open <http://localhost:8888> and start `notebooks/playground.ipynb`.

Stop with `Ctrl+C`; reset everything by deleting `work/playground.duckdb`.

Rootless Podman note: if the notebook can't write to `work/`, run
`podman compose up` with `userns_mode: keep-id` added to the service, or `chmod -R a+rwX work notebooks`.

## ER diagram as input

```
erDiagram
    AUTHOR ||--o{ BOOK : writes
    AUTHOR { int id PK
             string name }
```

* `A ||--o{ B` – one A, many B → B gets a foreign key to A (`|o` / `o|`: optional → nullable)
* `}o--o{` – many-to-many → junction table
* mark columns with `PK`, `FK`, `UK`; a parent without PK gets `id INTEGER PRIMARY KEY`

`erplay.schema_to_mermaid(con)` produces the same format from an existing database, so you can round-trip.

## Without a container

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest   # plus the `graphviz` system package for diagrams
jupyter lab
pytest
```

## CI

`.github/workflows/ci.yml` builds the image, runs the tests and executes the whole notebook inside it,
and checks that JupyterLab starts.
