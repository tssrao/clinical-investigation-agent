# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phase 0 in progress (data foundation). Repo layout is now `backend/` (Python app, `pyproject.toml`/`uv.lock` live here) + `notebooks/` (EDA, untouched by the restructure — still run from `notebooks/`, paths there are relative to that folder) + `frontend/` (reserved, empty until Phase 7) + root-level docs/`docker-compose.yml`. The 18-table Synthea schema exists as SQLAlchemy models (`backend/app/db/models/`) and an initial Alembic migration (`backend/alembic/versions/0001_initial_synthea_schema.py`), but nothing has been loaded into a real Postgres yet — no loader script, no lookup tables (RxNorm/ICD-10/LOINC), no app code beyond the schema. Before writing code, check the design doc's Phase table (Section 5) to see what phase is actually in progress — don't assume infrastructure (Redis, Celery, MLflow, etc.) is wired up just because it's listed in the tech stack.

## Essential reading before non-trivial work

- **`clinical-investigation-agent-design.md`** — the full architecture, domain models, RBAC design, data findings, capability matrix, known limitations, and phase-by-phase build plan. Read this first; it is the source of truth for design decisions, not this file.
- **`data_model.md`** — how the 18 Synthea CSV tables join together (key patterns, entity groups, ER diagram, join recipes). Read this before writing any query or loader touching Synthea data.
- **`join_reference.md`** — join *safety*: verified cardinality/fan-out/dedup behavior for every join in `data_model.md` (e.g. `claims` isn't 1:1 with `encounters`, `REASONCODE` causal-chain joins over-match, joining two clinical tables directly on `ENCOUNTER` cross-products them). Read this before writing the SQL Tool's join logic or any NL→SQL prompt — it's the difference between correct and silently-duplicated query results.
- **`README.md`** — quick orientation + how to regenerate `synthea_data/` (not committed; synthetic data regenerated on demand via the Synthea jar, unpinned seed).

## Commands

Dependency management is via `uv`, run from **`backend/`** (see `backend/uv.lock`, `backend/pyproject.toml`, `requires-python = ">=3.12"`).

```bash
cd backend
uv sync                     # install/sync dependencies
uv run pytest               # run tests (pytest + pytest-asyncio configured; no tests written yet)
uv run ruff check .         # lint
uv run ruff format .        # format
```

`notebooks/` has its own kernel (registered against `backend/.venv`) but isn't part of the `backend/` uv project — run `uv run --directory backend jupyter lab` from repo root, or open `notebooks/eda.ipynb` directly if the kernel's already registered.

### Database (Postgres + pgvector, via Docker Compose)

```bash
docker compose up -d postgres         # from repo root — brings up Postgres+pgvector on localhost:5432
cd backend
uv run alembic upgrade head           # applies all migrations, including 0001 (full 18-table Synthea schema)
uv run alembic revision --autogenerate -m "..."   # after changing a model in app/db/models/
```

Connection settings come from `.env` at the repo root (copy `.env.example`) — `app/core/config.py` reads it, and `alembic/env.py` reads the same `Settings` object rather than `alembic.ini`'s placeholder URL. There is no build/run command for the application itself yet (Phase 1). **DB schema changes always go through Alembic migrations — never hand-edit the schema, never call `Base.metadata.create_all()` outside of migration 0001.**

### Regenerating synthetic data

`synthea_data/` is gitignored — generated fresh, not committed:

```bash
mkdir synthea_data && cd synthea_data
curl -sL -o synthea-with-dependencies.jar https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar
java -Xmx4g -jar synthea-with-dependencies.jar --exporter.csv.export=true --exporter.baseDirectory=./output -p 2000 Massachusetts
```

No pinned seed — patient records differ between runs by design; the agent is meant to answer questions against whatever data is currently loaded, not a fixed benchmark set. `-p 2000` is the current dev population size (2,000 living + deceased = 2,338 total patients); see design doc §7 for the 1,000–5,000 recommendation this is based on.

## Architecture (target — being built incrementally per the phase plan)

**Core philosophy:** only two components are "agents" — everything else is a deterministic-interface tool.
- **Planner** decides what evidence is required and emits a structured, inspectable **Investigation Plan** (task list) as its first action — it does not loop implicitly through tool calls.
- **Reviewer** checks whether gathered evidence is sufficient; can send the Planner back to insert more tasks, capped at 2 extra rounds, after which the Report generates anyway with `evidence_complete: false`.
- **Tools** (SQL, Timeline, Medication, Literature, Prediction, Visualization, Report) are plain deterministic code/LLM-calls with structured input/output — not agents. Timeline, Medication, and Prediction tools call no LLM at all.

**Four new persistent domain entities** sit on top of the untouched Synthea schema: `Investigation → Task[] → Artifact[]`, plus `Report`. Every tool call produces a typed **Artifact**; the Reviewer evaluates the Artifact set, not raw tool output; the Report is assembled entirely from Artifacts, keeping every claim traceable to its source.

**RBAC is enforced outside the LLM**: JWT → role resolution → row-level policy injected into the SQL Tool before any query executes. The LLM never makes an authorization decision. Two roles (`doctor`, `insurance_adjuster`) have deliberately *different investigation goals*, not just different column visibility — see design doc §2.2 before touching anything RBAC-related.

**Routing discipline matters**: the Planner should scope tools to the question (fast path: SQL-only or lookup-only for trivial questions) rather than always running the full pipeline. Over-invoking tools on simple questions is treated as a design defect, not a minor inefficiency — see design doc §3.6.

**Data source of truth is Synthea's schema, unmodified** — `Patient` is read directly from it, not duplicated into the application's own tables. RxNorm, ICD-10, and LOINC are lookup tables; PubMed abstracts live in pgvector for the Literature Tool.

## Known dataset constraints (drive design decisions — don't design around data that isn't there)

- No free-text clinical notes anywhere in Synthea's output — every `TYPE == "text"` observation is a structured survey field, not a note.
- No claim denial/rejection state — `claims.STATUS*` is always `BILLED`/`CLOSED`. Claim questions must be framed as billing-lifecycle explanations, not denial reasoning.
- No drug-drug interaction data — RxNorm is a vocabulary, not an interactions DB. A hand-curated ~20-30 pattern interaction table is planned (Phase 3), not sourced externally.
- `medications.REASONCODE` links a med to the condition it treats on ~81% of rows (verified at the 2,338-patient dev scale) — this is the real causal-chain data backing the anchor "why did X change" scenarios; prefer it over LLM inference.

See design doc §4.3.2 and §4.5 for the full findings list and rationale — don't rediscover these by re-exploring the CSVs.
