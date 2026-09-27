"""Row-level policy injection (design doc 2.2) - the enforcement half of RBAC.
security.py only decodes a token; this module is what actually restricts what
a query can see, at the Postgres engine level (SET LOCAL ROLE + the
app.patient_scope session variable the RLS policies from migration
7f3c9e2a1b4d key off), not in application code the LLM could talk its way
around.

DB_ROLE_FOR maps an app role to the Postgres role granted in that migration.
TOOLS_FOR_ROLE is the *other* half of "different investigation goals, not
just different column visibility" (design doc 2.2): the Planner is only ever
told about tools relevant to the requester's role, so an insurance_adjuster
investigation can't plan a Timeline/Prediction/DrugInteraction task in the
first place - not relying on DB permissions alone to catch a misrouted task.
"""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db.session import engine

DB_ROLE_FOR = {
    "doctor": "app_doctor",
    "insurance_adjuster": "app_insurance_adjuster",
}

# A real incident found during development: an LLM-generated query (a patient-
# similarity self-join with a correlated subquery, no LIMIT) ran for 18+
# minutes against real data volume before being manually killed. Nothing
# previously bounded how long a query could run - this is a hard backstop, not
# a performance tuning knob. 15s comfortably covers every legitimate query
# seen so far (worst case ~10s) while making a runaway query fail fast and
# recoverably instead of hanging indefinitely.
STATEMENT_TIMEOUT_MS = 15_000

TOOLS_FOR_ROLE = {
    "doctor": ["sql", "timeline", "prediction", "drug_interactions", "visualization"],
    "insurance_adjuster": ["sql", "visualization"],  # capability matrix: cost/comparative analysis only
}


@contextmanager
def scoped_connection(role: str, patient_scope: list[str]):
    """Yields a Connection with the DB role switched and app.patient_scope set
    for the lifetime of one transaction - both SET LOCAL, so neither leaks to
    any other connection or transaction from the pool.
    """
    db_role = DB_ROLE_FOR.get(role)
    if db_role is None:
        raise ValueError(f"unknown role: {role!r}")

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(f"SET LOCAL ROLE {db_role}"))
            conn.execute(
                text("SELECT set_config('app.patient_scope', :scope, true)"),
                {"scope": ",".join(patient_scope)},
            )
            conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            yield conn
