"""Phase 4 RBAC: DB roles, table-level GRANTs, row-level security policies

Design doc Section 2.2 - "RBAC is enforced outside the LLM: JWT -> role
resolution -> row-level policy injection into the SQL Tool, before any query
touches the database." Two enforcement layers, both at the Postgres engine
level so no LLM-generated query can bypass them by omission or persuasion:

1. Table-level: two DB roles (app_doctor, app_insurance_adjuster) with GRANTs
   matching the design doc's Data Access table exactly. A query touching a
   table the role has no GRANT on fails with a Postgres permission error -
   not a prompt-level convention.
2. Row-level: RLS policies on every patient-scoped table, gated on session
   variable app.patient_scope (a comma-separated list of authorized patient
   ids, set via SET LOCAL before a query runs). Fails CLOSED: if the setting
   is unset, current_setting(..., true) returns NULL and the policy matches
   zero rows - never "everything," even if the app forgets to set it.

Doctor tables: the design doc's literal list (patients, encounters,
conditions, procedures, observations, medications, careplans, allergies) plus
immunizations - Timeline Tool (already built, doctor-facing) reads
immunizations for every investigation, so excluding it would break a working
tool over a table the design doc's list just didn't happen to enumerate.
devices/supplies/imaging_studies are granted to neither role: no current tool
touches them, so principle of least privilege wins until one does.

Insurance adjuster tables: claims, claims_transactions, payer_transitions,
payers, plus encounters and procedures (both shared with doctor) - the
capability matrix's "Claim-vs-documentation consistency check" needs
procedures to compare billed-vs-documented care, so it's not blocked despite
being "clinical."

Reference tables (organizations, providers, loinc_codes, rxnorm_codes,
drug_interactions, literature_abstracts) and the app's own operational tables
(investigations, tasks, artifacts, reports) are granted to both roles - not
patient-sensitive, or needed by both roles' tools regardless of scope.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "7f3c9e2a1b4d"
down_revision: Union[str, Sequence[str], None] = "4a554bc159d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOCTOR_ROLE = "app_doctor"
ADJUSTER_ROLE = "app_insurance_adjuster"

# (table, patient-scope column) - every table RLS-gated on app.patient_scope
PATIENT_SCOPED_TABLES = [
    ("patients", '"Id"'),
    ("encounters", '"PATIENT"'),
    ("conditions", '"PATIENT"'),
    ("observations", '"PATIENT"'),
    ("medications", '"PATIENT"'),
    ("procedures", '"PATIENT"'),
    ("allergies", '"PATIENT"'),
    ("careplans", '"PATIENT"'),
    ("immunizations", '"PATIENT"'),
    ("claims", '"PATIENTID"'),
    ("claims_transactions", '"PATIENTID"'),
    ("payer_transitions", '"PATIENT"'),
]

DOCTOR_TABLES = [
    "patients", "encounters", "conditions", "observations", "medications",
    "procedures", "allergies", "careplans", "immunizations",
]
ADJUSTER_TABLES = [
    "claims", "claims_transactions", "payer_transitions", "payers",
    "encounters", "procedures", "patients",
]
SHARED_REFERENCE_TABLES = [
    "organizations", "providers", "loinc_codes", "rxnorm_codes",
    "drug_interactions", "literature_abstracts",
]
SHARED_APP_TABLES = ["investigations", "tasks", "artifacts", "reports"]


def upgrade() -> None:
    op.execute(f"CREATE ROLE {DOCTOR_ROLE}")
    op.execute(f"CREATE ROLE {ADJUSTER_ROLE}")
    # lets the app's own connection role (cia) switch into either via SET LOCAL ROLE,
    # without managing a second set of credentials
    op.execute(f"GRANT {DOCTOR_ROLE} TO cia")
    op.execute(f"GRANT {ADJUSTER_ROLE} TO cia")

    for table in DOCTOR_TABLES:
        op.execute(f'GRANT SELECT ON "{table}" TO {DOCTOR_ROLE}')
    for table in ADJUSTER_TABLES:
        op.execute(f'GRANT SELECT ON "{table}" TO {ADJUSTER_ROLE}')
    for table in SHARED_REFERENCE_TABLES + SHARED_APP_TABLES:
        op.execute(f'GRANT SELECT ON "{table}" TO {DOCTOR_ROLE}, {ADJUSTER_ROLE}')
    for table in SHARED_APP_TABLES:
        op.execute(f'GRANT INSERT, UPDATE ON "{table}" TO {DOCTOR_ROLE}, {ADJUSTER_ROLE}')

    for table, column in PATIENT_SCOPED_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f"""
            CREATE POLICY patient_scope ON "{table}"
            USING ({column} = ANY(string_to_array(current_setting('app.patient_scope', true), ',')))
        """)


def downgrade() -> None:
    for table, _ in PATIENT_SCOPED_TABLES:
        op.execute(f'DROP POLICY IF EXISTS patient_scope ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    for table in DOCTOR_TABLES:
        op.execute(f'REVOKE ALL ON "{table}" FROM {DOCTOR_ROLE}')
    for table in ADJUSTER_TABLES:
        op.execute(f'REVOKE ALL ON "{table}" FROM {ADJUSTER_ROLE}')
    for table in SHARED_REFERENCE_TABLES + SHARED_APP_TABLES:
        op.execute(f'REVOKE ALL ON "{table}" FROM {DOCTOR_ROLE}, {ADJUSTER_ROLE}')

    op.execute(f"REVOKE {DOCTOR_ROLE} FROM cia")
    op.execute(f"REVOKE {ADJUSTER_ROLE} FROM cia")
    op.execute(f"DROP ROLE {DOCTOR_ROLE}")
    op.execute(f"DROP ROLE {ADJUSTER_ROLE}")
