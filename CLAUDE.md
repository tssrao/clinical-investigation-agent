# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Phase 0 essentially done (RxNorm pending UMLS license approval only), Phase 1 done, Phase 2 done. Repo layout is `backend/` (Python app, `pyproject.toml`/`uv.lock` live here) + `notebooks/` (EDA, untouched by the restructure — still run from `notebooks/`, paths there are relative to that folder) + `frontend/` (reserved, empty until Phase 7) + `data/` (gitignored — `data/synthea/`, `data/lookup/{loinc,rxnorm}/`; see README) + root-level docs/`docker-compose.yml`.

**Phase 0**: 18-table Synthea schema loaded into real Postgres (`backend/scripts/load_csv_data.py`, verified: row counts match source CSVs exactly, zero duplicate PKs). LOINC loaded (`backend/scripts/load_lookup_data.py`, resolves ~92% of `observations.CODE`). No ICD-10-CM table — verified it matches zero codes in this dataset (Synthea uses SNOMED-CT, out of scope per design doc §5). RxNorm pending UMLS license approval.

**Phase 1**: SQL Tool (`backend/app/tools/sql_tool.py`) — question -> LLM-generated SQL -> execute -> rows, SELECT-only safety guard, 2-retry auto-repair. Verified against real join-safety traps (e.g. correctly generates `COUNT(DISTINCT "Id")` for imaging_studies). Tests: `backend/tests/test_sql_tool.py` (13 deterministic) + `backend/tests/test_sql_tool_golden.py` (16 real-LLM golden questions against one real patient — caught and fixed two real bugs: CODE-vs-DESCRIPTION column confusion, and counting DISTINCT on the wrong column). All LLM tests skip cleanly without `OPENAI_API_KEY`.

**Phase 2**: real Planner + Reviewer agent loop (`backend/app/agents/{planner,reviewer,orchestrator}.py`) + persistent Investigation/Task/Artifact/Report domain model (`backend/app/db/models/investigation.py`, migration `dee776fb3a30`). Planner emits a structured plan (tasks scoped to the question, not a fixed pipeline — verified: a trivial question gets 1 task). Reviewer evaluates the Artifact set and can insert up to 2 additional rounds; verified the hard cap actually terminates the loop and sets `evidence_complete: false` honestly when still insufficient, rather than looping forever or claiming false success. Only `sql` is a real tool right now — Planner/Reviewer are explicitly told not to plan Timeline/Medication/Literature/Prediction/Visualization tasks, since those don't exist until Phase 3. Tests: `backend/tests/test_orchestrator.py`, including a DB-round-trip check (Investigation/Task/Artifact/Report actually persist and reload correctly from Postgres, not just in-memory).

**Known Phase 2 finding, not yet fixed**: the SQL Tool sometimes generates a `UNION ALL` across structurally different tables (e.g. `conditions`/`medications`, which have different column counts) when a task's purpose asks it to combine evidence from both — this fails even through the SQL Tool's own auto-repair loop. Surfaced during Phase 2 testing; deferred as a "bugs/guardrails" follow-up rather than fixed inline, per explicit direction to get the full phase loop working first.

**Phase 3 (in progress)**: Timeline Tool done (`backend/app/tools/timeline_tool.py`) — deterministic, no LLM (per design doc's own rule), pulls chronological events for a patient directly across 8 tables (encounters/conditions/medications-start+stop/procedures/immunizations/allergies/careplans/observations), excludes `TYPE == 'text'` observations (structured survey fields, not clinical data). Wired into the orchestrator (`planner.AVAILABLE_TOOLS` now `["sql", "timeline"]`); verified end-to-end (correct 1-task plan for a timeline-shaped question, Report's `investigation_timeline` section populated with real chronological data, no regression on trivial-question routing). Tests in `backend/tests/test_timeline_tool.py` (7, fully deterministic — no API key needed at all, since this tool calls no LLM). Medication (blocked on RxNorm/UMLS license), Prediction (needs MLflow infra, not set up), Literature (needs PubMed abstract sourcing decision), Visualization (needs artifact-format decision, no frontend yet), and the drug-interaction rules table are not yet started — each has a real setup/decision step, sequenced deliberately rather than rushed.

Before writing code, check the design doc's Phase table (Section 5) to see what phase is actually in progress — don't assume infrastructure (Redis, Celery, MLflow, etc.) is wired up just because it's listed in the tech stack.

## Essential reading before non-trivial work

- **`clinical-investigation-agent-design.md`** — the full architecture, domain models, RBAC design, data findings, capability matrix, known limitations, and phase-by-phase build plan. Read this first; it is the source of truth for design decisions, not this file.
- **`data_model.md`** — how the 18 Synthea CSV tables join together (key patterns, entity groups, ER diagram, join recipes). Read this before writing any query or loader touching Synthea data.
- **`join_reference.md`** — join *safety*: verified cardinality/fan-out/dedup behavior for every join in `data_model.md` (e.g. `claims` isn't 1:1 with `encounters`, `REASONCODE` causal-chain joins over-match, joining two clinical tables directly on `ENCOUNTER` cross-products them). Read this before writing the SQL Tool's join logic or any NL→SQL prompt — it's the difference between correct and silently-duplicated query results.
- **`README.md`** — quick orientation + how to regenerate `data/synthea/` and download the lookup vocabularies (none of `data/` is committed — regenerated/redownloaded on demand).

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

### Loading data

```bash
cd backend
uv run python scripts/load_csv_data.py      # Synthea CSVs -> 18 tables (data/synthea/output/csv/, must exist first)
uv run python scripts/load_lookup_data.py   # LOINC (+ RxNorm once its file exists) -> lookup tables (data/lookup/)
```

Both truncate-and-reload inside a single transaction — safe to re-run any time source data changes. See README for how to obtain `data/synthea/` and `data/lookup/`.

### Regenerating synthetic data

`data/` is gitignored — generated/downloaded fresh, not committed:

```bash
mkdir -p data/synthea && cd data/synthea
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

**Data source of truth is Synthea's schema, unmodified** — `Patient` is read directly from it, not duplicated into the application's own tables. LOINC and RxNorm are lookup tables (no ICD-10-CM — see Project status above); PubMed abstracts live in pgvector for the Literature Tool.

## Known dataset constraints (drive design decisions — don't design around data that isn't there)

- No free-text clinical notes anywhere in Synthea's output — every `TYPE == "text"` observation is a structured survey field, not a note.
- No claim denial/rejection state — `claims.STATUS*` is always `BILLED`/`CLOSED`. Claim questions must be framed as billing-lifecycle explanations, not denial reasoning.
- No drug-drug interaction data — RxNorm is a vocabulary, not an interactions DB. A hand-curated ~20-30 pattern interaction table is planned (Phase 3), not sourced externally.
- `medications.REASONCODE` links a med to the condition it treats on ~81% of rows (verified at the 2,338-patient dev scale) — this is the real causal-chain data backing the anchor "why did X change" scenarios; prefer it over LLM inference.
- `conditions.CODE` and `claims.DIAGNOSISn` are SNOMED-CT, not ICD-10-CM — verified against real loaded data (0 matches against a loaded ICD-10 table). Don't expect an ICD-10 lookup to resolve these; there isn't one in this schema (see above).
- `claims_transactions.PATIENTINSURANCEID` is **not** a `payers.Id` reference despite the name — it's `payer_transitions.MEMBERID` (a specific membership/plan instance). No FK on this column; see `app/db/models/billing.py` docstring.

See design doc §4.3.2 and §4.5 for the full findings list and rationale — don't rediscover these by re-exploring the CSVs.
