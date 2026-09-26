"""Bulk-load Synthea CSVs into the Postgres schema created by migration 0001.

Design (see CLAUDE.md / join_reference.md for the underlying data facts):

- Column mapping is derived from the SQLAlchemy models themselves (Model.__table__.columns),
  not hand-written per table: for each DB column we look for a CSV header that matches its
  actual DB column name case-insensitively. A DB column with no CSV match (the synthetic
  BigInteger autoincrement `id` PKs on conditions/observations/medications/etc.) is left out
  of the COPY column list entirely, so Postgres assigns it automatically.
- Every String-typed model column is read from its CSV as pandas' nullable "string" dtype,
  so ID-shaped and zip-code-shaped values never get silently coerced into int/float and lose
  leading zeros or gain a spurious ".0".
- Every Date/DateTime-typed model column is parsed with pd.to_datetime after reading.
- Loading uses COPY via psycopg2.copy_expert (not row-by-row INSERT), which is the only
  approach that's fast enough for claims_transactions (~2.2M rows).
- Tables are truncated (in reverse dependency order) and reloaded inside a single transaction,
  so re-running this script after regenerating synthea_data/ is always safe.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import BigInteger, Date, DateTime, Integer, String

from app.db.models.billing import Claim, ClaimTransaction, Payer, PayerTransition
from app.db.models.clinical import (
    Allergy,
    Careplan,
    Condition,
    Device,
    ImagingStudy,
    Immunization,
    Medication,
    Observation,
    Procedure,
    Supply,
)
from app.db.models.core import Encounter, Organization, Patient, Provider
from app.db.session import engine

SYNTHEA_DATA_PATH = Path(__file__).resolve().parents[2] / "synthea_data" / "output" / "csv"

# (csv filename, model class) in FK-dependency order: every table only depends on
# tables that already appear earlier in this list.
TABLES = [
    ("organizations.csv", Organization),
    ("payers.csv", Payer),
    ("patients.csv", Patient),
    ("providers.csv", Provider),
    ("encounters.csv", Encounter),
    ("allergies.csv", Allergy),
    ("careplans.csv", Careplan),
    ("conditions.csv", Condition),
    ("devices.csv", Device),
    ("immunizations.csv", Immunization),
    ("imaging_studies.csv", ImagingStudy),
    ("medications.csv", Medication),
    ("observations.csv", Observation),
    ("procedures.csv", Procedure),
    ("supplies.csv", Supply),
    ("payer_transitions.csv", PayerTransition),
    ("claims.csv", Claim),
    ("claims_transactions.csv", ClaimTransaction),
]


def build_column_map(model, csv_header: list[str]) -> dict[str, str]:
    """Map CSV header name -> DB column name for every DB column that has a CSV source.

    A DB column with no matching CSV header (synthetic autoincrement PKs) is left out,
    so it's simply absent from the COPY column list and Postgres fills it in.
    """
    csv_header_by_lower = {name.lower(): name for name in csv_header}
    mapping: dict[str, str] = {}
    for column in model.__table__.columns:
        csv_name = csv_header_by_lower.get(column.name.lower())
        if csv_name is not None:
            mapping[csv_name] = column.name
    return mapping


def load_table(conn, csv_filename: str, model) -> int:
    table_name = model.__tablename__
    csv_path = SYNTHEA_DATA_PATH / csv_filename

    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    column_map = build_column_map(model, header)

    string_csv_columns = [
        csv_name
        for csv_name, db_name in column_map.items()
        if isinstance(model.__table__.columns[db_name].type, String)
    ]
    date_csv_columns = [
        csv_name
        for csv_name, db_name in column_map.items()
        if isinstance(model.__table__.columns[db_name].type, (Date, DateTime))
    ]
    integer_csv_columns = [
        csv_name
        for csv_name, db_name in column_map.items()
        if isinstance(model.__table__.columns[db_name].type, (Integer, BigInteger))
    ]

    # nullable "Int64"/"string" dtypes (capital I / pandas extension types) hold NaN
    # without upgrading the whole column to float64 - a plain "int64" can't hold NaN
    # at all, so any nullable integer CSV column would otherwise silently become
    # "2.0" instead of "2", which Postgres then rejects for an integer column.
    dtype_overrides = {col: "string" for col in string_csv_columns}
    dtype_overrides.update({col: "Int64" for col in integer_csv_columns})

    df = pd.read_csv(
        csv_path,
        usecols=list(column_map.keys()),
        dtype=dtype_overrides,
    )
    for col in date_csv_columns:
        df[col] = pd.to_datetime(df[col])

    df = df.rename(columns=column_map)
    db_columns = list(column_map.values())
    df = df[db_columns]

    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="")
    buffer.seek(0)

    with conn.cursor() as cur:
        quoted_columns = ", ".join(f'"{col}"' for col in db_columns)
        cur.copy_expert(
            f'COPY "{table_name}" ({quoted_columns}) FROM STDIN WITH (FORMAT csv, NULL \'\')',
            buffer,
        )

    return len(df)


def main() -> None:
    conn = engine.raw_connection()
    try:
        with conn.cursor() as cur:
            # reverse order so FK-referenced tables aren't truncated before their dependents
            for _, model in reversed(TABLES):
                cur.execute(f'TRUNCATE TABLE "{model.__tablename__}" CASCADE')

        for csv_filename, model in TABLES:
            row_count = load_table(conn, csv_filename, model)
            print(f"{model.__tablename__}: loaded {row_count} rows")

        conn.commit()
        print("All tables loaded successfully.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
