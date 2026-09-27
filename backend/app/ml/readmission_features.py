"""Shared feature extraction for the 30-day readmission risk model - used by
BOTH the training script and the Prediction Tool, so there is exactly one
definition of what a feature means and no train/serve skew.

Label definition (verified against real data before building this): an
inpatient encounter is positive if the same patient has another inpatient
encounter starting within 30 days of this one's discharge. At the current dev
scale: 2,433 inpatient encounters, 502 positive (20.6%) - a workable class
balance, not degenerate.

Design doc's own caveat applies here (§4.5): this demonstrates the ML pipeline
(feature engineering -> train -> MLflow-track -> serve) working end-to-end, not
a clinically validated readmission model - it's trained entirely on synthetic
Synthea data.
"""

import pandas as pd
from sqlalchemy import text

from app.db.session import engine

FEATURE_COLUMNS = [
    "length_of_stay_days",
    "age_at_encounter",
    "gender_male",
    "prior_encounter_count",
    "prior_inpatient_count",
    "condition_count",
    "active_medication_count",
    "procedure_count",
]
LABEL_COLUMN = "readmitted_within_30d"

_FEATURES_CTE = """
WITH inpatient AS (
    SELECT e."Id" AS encounter_id, e."PATIENT" AS patient_id, e."START" AS start_ts, e."STOP" AS stop_ts,
           LEAD(e."START") OVER (PARTITION BY e."PATIENT" ORDER BY e."START") AS next_start
    FROM encounters e
    WHERE e."ENCOUNTERCLASS" = 'inpatient'
)
SELECT
    i.encounter_id,
    i.patient_id,
    i.start_ts,
    i.stop_ts,
    EXTRACT(EPOCH FROM (i.stop_ts - i.start_ts)) / 86400.0 AS length_of_stay_days,
    EXTRACT(YEAR FROM AGE(i.start_ts, p.birthdate)) AS age_at_encounter,
    CASE WHEN p.gender = 'M' THEN 1 ELSE 0 END AS gender_male,
    (SELECT COUNT(*) FROM encounters e2
        WHERE e2."PATIENT" = i.patient_id AND e2."START" < i.start_ts) AS prior_encounter_count,
    (SELECT COUNT(*) FROM encounters e3
        WHERE e3."PATIENT" = i.patient_id AND e3."ENCOUNTERCLASS" = 'inpatient' AND e3."START" < i.start_ts
    ) AS prior_inpatient_count,
    (SELECT COUNT(DISTINCT c."CODE") FROM conditions c
        WHERE c."PATIENT" = i.patient_id AND c."START" <= i.stop_ts) AS condition_count,
    (SELECT COUNT(DISTINCT m."CODE") FROM medications m
        WHERE m."PATIENT" = i.patient_id AND m."START" <= i.stop_ts
          AND (m."STOP" IS NULL OR m."STOP" >= i.start_ts)
    ) AS active_medication_count,
    (SELECT COUNT(*) FROM procedures pr WHERE pr."ENCOUNTER" = i.encounter_id) AS procedure_count,
    CASE WHEN i.next_start IS NOT NULL AND i.next_start - i.stop_ts <= INTERVAL '30 days'
         THEN 1 ELSE 0 END AS readmitted_within_30d
FROM inpatient i
JOIN patients p ON p."Id" = i.patient_id
"""


def extract_training_data() -> pd.DataFrame:
    """Every inpatient encounter in the dataset, with features + label."""
    with engine.connect() as conn:
        return pd.read_sql_query(text(_FEATURES_CTE), conn)


def extract_features_for_patient(patient_id: str) -> dict | None:
    """Features for a patient's most recent inpatient encounter. Returns None
    if the patient has no inpatient encounter on record - readmission risk
    isn't a meaningful question without one.
    """
    query = _FEATURES_CTE + ' WHERE i.patient_id = :patient_id ORDER BY i.start_ts DESC LIMIT 1'
    with engine.connect() as conn:
        df = pd.read_sql_query(text(query), conn, params={"patient_id": patient_id})
    if df.empty:
        return None
    return df.iloc[0].to_dict()
