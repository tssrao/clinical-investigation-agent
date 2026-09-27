"""Phase 1 SQL Tool: question -> SQL -> execute -> rows. A single deterministic-
interface function, per the project's core philosophy that tools aren't agents.

Schema context and join-safety rules are both read from source-of-truth files at
call time (Base.metadata for schema, join_reference.md for join safety), not
duplicated into a hand-maintained prompt string that would drift from the real
schema/findings over time.

Phase 4 RBAC: when role/patient_scope are given, execute_sql runs the query
through app.rbac.policy.scoped_connection instead of a plain connection - a
real Postgres permission/RLS boundary underneath the generated SQL, not a
prompt convention the LLM could be talked around. Optional (defaults to no
scoping) so existing callers/tests that don't care about RBAC are unaffected.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.rbac.policy import STATEMENT_TIMEOUT_MS, scoped_connection
import app.db.models  # noqa: F401  (registers every table on Base.metadata)

JOIN_REFERENCE_PATH = Path(__file__).resolve().parents[3] / "join_reference.md"

MAX_REPAIR_ATTEMPTS = 2


@dataclass
class SqlToolResult:
    question: str
    sql: str
    rows: list[dict]
    row_count: int
    success: bool
    error: str | None = None


def build_schema_context() -> str:
    """Render every table's actual DB columns and types from Base.metadata -
    the same source of truth the loader scripts and Alembic use, so this never
    drifts out of sync with the real schema the way a hand-written copy would.
    """
    lines = []
    for table in sorted(Base.metadata.sorted_tables, key=lambda t: t.name):
        columns = ", ".join(f'"{c.name}" {c.type}' for c in table.columns)
        lines.append(f"{table.name}({columns})")
    return "\n".join(lines)


def load_join_safety_notes() -> str:
    return JOIN_REFERENCE_PATH.read_text(encoding="utf-8")


def _build_messages(question: str, schema_context: str, join_notes: str,
                     previous_sql: str | None = None, previous_error: str | None = None) -> list[dict]:
    system_prompt = (
        "You are a SQL generator for a PostgreSQL database of Synthea synthetic "
        "patient data. Given a question, output ONLY a single valid PostgreSQL "
        "SELECT statement that answers it - no markdown fences, no explanation, "
        "no other statement types (no INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE).\n\n"
        "Quote every column name exactly as given below (many are uppercase, e.g. "
        '"PATIENT", "CODE") - Postgres is case-sensitive once a name is quoted.\n\n'
        "IMPORTANT: on every clinical/billing table, \"CODE\" holds an external "
        "vocabulary code (SNOMED-CT, RxNorm, LOINC - a number or short alphanumeric "
        "id, never a human-readable name), while \"DESCRIPTION\" holds the "
        "human-readable text. A drug/condition/procedure NAME mentioned in a "
        "question (e.g. \"Simvastatin\", \"hypertension\") almost always belongs in "
        "a DESCRIPTION filter (use ILIKE '%name%' for partial matches), not a CODE "
        "filter - CODE should only be used when the question gives you an actual "
        "code value to match against.\n\n"
        "When asked to count DISTINCT related entities referenced by a table's rows "
        "(e.g. \"how many distinct encounters do these claims correspond to\"), count "
        "distinct on the FOREIGN KEY column that points to that entity (e.g. "
        '"APPOINTMENTID" for encounters), never on the row\'s own primary key - '
        "counting DISTINCT on a column that's already unique per row (like a table's "
        "own Id) just returns the row count and answers a different question than "
        "the one asked.\n\n"
        f"Schema:\n{schema_context}\n\n"
        f"Join safety rules - read carefully, several joins in this schema silently "
        f"produce wrong/duplicated results if done naively:\n{join_notes}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    if previous_sql is not None:
        messages.append({"role": "assistant", "content": previous_sql})
        repair_hint = ""
        if previous_error and "does not exist" in previous_error.lower() and '"' not in previous_error:
            # near-certainly an unquoted-identifier bug: Postgres lowercases an
            # unquoted name, so the error message names a lowercase column that
            # "doesn't exist" only because the real (uppercase) one was never
            # quoted - a recurring mistake worth calling out explicitly rather
            # than trusting the raw Postgres error to be self-explanatory.
            repair_hint = (
                " This usually means a column name was not wrapped in double "
                "quotes - re-check EVERY column reference against the schema "
                "above and quote each one exactly (e.g. m.\"PATIENT\", not m.PATIENT)."
            )
        messages.append({
            "role": "user",
            "content": (
                f"That query failed with this error:\n{previous_error}\n{repair_hint}\n\n"
                "Fix it and output only the corrected SQL."
            ),
        })
    return messages


def _extract_sql(raw: str) -> str:
    # strip markdown code fences if the model added them despite instructions not to
    match = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.DOTALL)
    return (match.group(1) if match else raw).strip()


def is_select_only(sql: str) -> bool:
    """Reject anything that isn't a single SELECT statement - a NL->SQL tool that
    can generate a DELETE/DROP/UPDATE is a real risk even before RBAC (Phase 4)
    exists to scope what a query is allowed to touch.
    """
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        return False  # no statement stacking
    return bool(re.match(r"^\s*SELECT\b", stripped, re.IGNORECASE))


def execute_sql(
    sql: str, role: str | None = None, patient_scope: list[str] | None = None
) -> tuple[list[dict], str | None]:
    try:
        if role is not None:
            with scoped_connection(role, patient_scope or []) as conn:
                result = conn.execute(text(sql))
                rows = [dict(row._mapping) for row in result]
        else:
            with engine.connect() as conn:
                with conn.begin():
                    conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
                    result = conn.execute(text(sql))
                    rows = [dict(row._mapping) for row in result]
        return rows, None
    except SQLAlchemyError as e:
        return [], str(e.__cause__ or e)


def run_sql_tool(
    question: str,
    max_retries: int = MAX_REPAIR_ATTEMPTS,
    role: str | None = None,
    patient_scope: list[str] | None = None,
) -> SqlToolResult:
    client = OpenAI(api_key=settings.openai_api_key)
    schema_context = build_schema_context()
    join_notes = load_join_safety_notes()

    sql = ""
    error: str | None = None

    for attempt in range(max_retries + 1):
        messages = _build_messages(
            question, schema_context, join_notes,
            previous_sql=sql if attempt > 0 else None,
            previous_error=error if attempt > 0 else None,
        )
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=0,
        )
        sql = _extract_sql(response.choices[0].message.content)

        if not is_select_only(sql):
            error = "Generated SQL was rejected: only a single SELECT statement is allowed."
            continue

        rows, error = execute_sql(sql, role=role, patient_scope=patient_scope)
        if error is None:
            return SqlToolResult(
                question=question, sql=sql, rows=rows,
                row_count=len(rows), success=True,
            )

    return SqlToolResult(
        question=question, sql=sql, rows=[], row_count=0,
        success=False, error=error,
    )


def format_report(result: SqlToolResult) -> str:
    """Trivial Report per Phase 1's scope - a real Report Tool (design doc 3.7)
    with Artifact traceability comes in Phase 2.
    """
    if not result.success:
        return f"Could not answer '{result.question}': {result.error}"
    if result.row_count == 0:
        return f"'{result.question}' - no matching rows."

    lines = [f"'{result.question}' - {result.row_count} row(s):"]
    for row in result.rows[:20]:
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in row.items()))
    if result.row_count > 20:
        lines.append(f"  ... and {result.row_count - 20} more")
    return "\n".join(lines)
