"""Load the LOINC vocabulary lookup table from data/lookup/.

Same COPY-based approach as load_csv_data.py (see that file's docstring for the
general reasoning). Much simpler here: no FK dependencies between this table
or to/from the Synthea tables (see app/db/models/lookups.py docstring), and no
weird dtype issues since it's just code + description-shaped columns.

No ICD-10-CM loader here - removed after verifying it matches zero codes in this
dataset (Synthea uses SNOMED-CT, not ICD-10-CM; see lookups.py docstring).

RxNorm is intentionally skipped - data/lookup/rxnorm/ is empty until the UMLS
license is approved (see README.md "Lookup vocabularies"). This script prints a
message and moves on rather than failing when that file is missing.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app.db.session import engine

LOOKUP_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "lookup"


def load_loinc(conn) -> int:
    csv_path = LOOKUP_DATA_PATH / "loinc" / "LoincTableCore.csv"

    usecols = ["LOINC_NUM", "COMPONENT", "SYSTEM", "CLASS", "LONG_COMMON_NAME", "SHORTNAME", "STATUS"]
    db_columns = ['"LOINC_NUM"', '"COMPONENT"', '"SYSTEM"', '"CLASS"', '"LONG_COMMON_NAME"', '"SHORTNAME"', '"STATUS"']

    df = pd.read_csv(csv_path, usecols=usecols, dtype="string")
    df = df[usecols]

    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="")
    buffer.seek(0)
    with conn.cursor() as cur:
        cur.copy_expert(
            f"COPY loinc_codes ({', '.join(db_columns)}) FROM STDIN WITH (FORMAT csv, NULL '')",
            buffer,
        )
    return len(df)


def main() -> None:
    conn = engine.raw_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE loinc_codes")

        loinc_path = LOOKUP_DATA_PATH / "loinc" / "LoincTableCore.csv"
        if loinc_path.exists():
            row_count = load_loinc(conn)
            print(f"loinc_codes: loaded {row_count} rows")
        else:
            print(f"loinc_codes: SKIPPED - {loinc_path} not found")

        rxnorm_path = LOOKUP_DATA_PATH / "rxnorm"
        if not any(rxnorm_path.glob("*.RRF")):
            print("rxnorm_codes: SKIPPED - RXNCONSO.RRF not found (pending UMLS license)")

        conn.commit()
        print("Lookup data load complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
