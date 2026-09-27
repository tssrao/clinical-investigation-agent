"""Drug Interaction Tool: checks a patient's CURRENT medications against the
hand-curated drug_interactions rules table (design doc §4.3.3). Calls no LLM -
deterministic substring matching against medication descriptions, same rule as
Timeline/Prediction.

"Current" medication = STOP IS NULL OR STOP > now() - same definition used
elsewhere in this codebase (app/ml/readmission_features.py's
active_medication_count).

Matching is substring-based on medications.DESCRIPTION (case-insensitive), not
RxNorm codes - deliberate, since RxNorm isn't loaded yet (blocked on UMLS
license) and every pattern here was verified present in this dataset's actual
drug names before being written (see seed_drug_interactions.py).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.models.drug_interactions import DrugInteraction
from app.db.session import SessionLocal


@dataclass
class MatchedInteraction:
    name: str
    severity: str
    mechanism: str
    reference: str
    matched_medications: list[str]  # one representative medication per group, in group order


def _get_current_medications(patient_id: str) -> list[str]:
    with SessionLocal() as session:
        rows = session.execute(
            text(
                'SELECT DISTINCT "DESCRIPTION" FROM medications '
                'WHERE "PATIENT" = :patient_id AND ("STOP" IS NULL OR "STOP" > :now)'
            ),
            {"patient_id": patient_id, "now": datetime.now(timezone.utc)},
        ).scalars().all()
    return list(rows)


def check_drug_interactions(patient_id: str) -> list[MatchedInteraction]:
    current_meds = _get_current_medications(patient_id)
    current_meds_lower = [m.lower() for m in current_meds]

    with SessionLocal() as session:
        rules = session.query(DrugInteraction).all()

    matches = []
    for rule in rules:
        matched_meds = []
        all_groups_matched = True
        for group in rule.drug_groups:
            match = next(
                (current_meds[i] for i, m in enumerate(current_meds_lower)
                 if any(pattern in m for pattern in group)),
                None,
            )
            if match is None:
                all_groups_matched = False
                break
            matched_meds.append(match)

        if all_groups_matched:
            matches.append(MatchedInteraction(
                name=rule.name,
                severity=rule.severity,
                mechanism=rule.mechanism,
                reference=rule.reference,
                matched_medications=matched_meds,
            ))

    return matches
