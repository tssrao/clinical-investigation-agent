"""Timeline Tool (design doc 4.1): transforms raw encounter/condition/medication/
procedure/immunization/allergy/careplan/observation rows into a chronological,
human-readable timeline for one patient. Calls no LLM - deterministic, direct
SQL against known tables, not NL->SQL (there's no natural-language step to get
wrong here, so going through the SQL Tool would just add risk for no benefit).

Observations are filtered to TYPE != 'text' - text-type observations are
structured survey fields (PRAPARE/PhenX screening, employment, education), not
clinical data (see CLAUDE.md "Known dataset constraints"), and including all
~764 observations a rich patient can have would swamp a "timeline" meant to be
human-readable.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import text

from app.db.session import engine

# (date column, extra columns to describe the event, category, description builder)
_QUERIES: list[tuple[str, str, str]] = [
    (
        "encounter",
        '"START"',
        """SELECT "START" AS event_date, "ENCOUNTERCLASS", "DESCRIPTION"
           FROM encounters WHERE "PATIENT" = :patient_id""",
    ),
    (
        "condition",
        '"START"',
        """SELECT "START" AS event_date, "DESCRIPTION"
           FROM conditions WHERE "PATIENT" = :patient_id""",
    ),
    (
        "medication_started",
        '"START"',
        """SELECT "START" AS event_date, "DESCRIPTION", "REASONDESCRIPTION"
           FROM medications WHERE "PATIENT" = :patient_id""",
    ),
    (
        "medication_stopped",
        '"STOP"',
        """SELECT "STOP" AS event_date, "DESCRIPTION"
           FROM medications WHERE "PATIENT" = :patient_id AND "STOP" IS NOT NULL""",
    ),
    (
        "procedure",
        '"START"',
        """SELECT "START" AS event_date, "DESCRIPTION"
           FROM procedures WHERE "PATIENT" = :patient_id""",
    ),
    (
        "immunization",
        '"DATE"',
        """SELECT "DATE" AS event_date, "DESCRIPTION"
           FROM immunizations WHERE "PATIENT" = :patient_id""",
    ),
    (
        "allergy",
        '"START"',
        """SELECT "START" AS event_date, "DESCRIPTION"
           FROM allergies WHERE "PATIENT" = :patient_id""",
    ),
    (
        "careplan_started",
        '"START"',
        """SELECT "START" AS event_date, "DESCRIPTION"
           FROM careplans WHERE "PATIENT" = :patient_id""",
    ),
    (
        "observation",
        '"DATE"',
        """SELECT "DATE" AS event_date, "DESCRIPTION", "VALUE", "UNITS"
           FROM observations WHERE "PATIENT" = :patient_id AND "TYPE" != 'text'""",
    ),
]


@dataclass
class TimelineEvent:
    event_date: str  # isoformat
    category: str
    description: str


@dataclass
class TimelineResult:
    patient_id: str
    event_count: int
    events: list[TimelineEvent] = field(default_factory=list)


def _describe(category: str, row: dict) -> str:
    if category == "encounter":
        return f"Encounter ({row['ENCOUNTERCLASS']}): {row['DESCRIPTION']}"
    if category == "condition":
        return f"Condition onset: {row['DESCRIPTION']}"
    if category == "medication_started":
        reason = f" (for {row['REASONDESCRIPTION']})" if row.get("REASONDESCRIPTION") else ""
        return f"Medication started: {row['DESCRIPTION']}{reason}"
    if category == "medication_stopped":
        return f"Medication stopped: {row['DESCRIPTION']}"
    if category == "procedure":
        return f"Procedure: {row['DESCRIPTION']}"
    if category == "immunization":
        return f"Immunization: {row['DESCRIPTION']}"
    if category == "allergy":
        return f"Allergy recorded: {row['DESCRIPTION']}"
    if category == "careplan_started":
        return f"Careplan started: {row['DESCRIPTION']}"
    if category == "observation":
        units = f" {row['UNITS']}" if row.get("UNITS") else ""
        return f"{row['DESCRIPTION']}: {row['VALUE']}{units}"
    raise ValueError(f"unknown category: {category}")


def build_timeline(
    patient_id: str, start_date: str | None = None, end_date: str | None = None
) -> TimelineResult:
    """start_date/end_date, if given, are ISO date strings that bound the window
    (inclusive) - useful once a question implies a specific period; omitted, the
    full patient history is returned.
    """
    events: list[TimelineEvent] = []

    with engine.connect() as conn:
        for category, _, query in _QUERIES:
            rows = conn.execute(text(query), {"patient_id": patient_id}).mappings().all()
            for row in rows:
                event_date = row["event_date"]
                if event_date is None:
                    continue
                if isinstance(event_date, (date, datetime)):
                    event_date_iso = event_date.isoformat()
                else:
                    event_date_iso = str(event_date)

                if start_date and event_date_iso < start_date:
                    continue
                if end_date and event_date_iso > end_date:
                    continue

                events.append(TimelineEvent(
                    event_date=event_date_iso,
                    category=category,
                    description=_describe(category, dict(row)),
                ))

    events.sort(key=lambda e: e.event_date)
    return TimelineResult(patient_id=patient_id, event_count=len(events), events=events)
