"""Import every model module here so Alembic's autogenerate (env.py -> Base.metadata)
sees the full schema. A model defined but not imported through this file is invisible
to `alembic revision --autogenerate`.
"""

from app.db.models.core import Encounter, Organization, Patient, Provider
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
from app.db.models.billing import Claim, ClaimTransaction, Payer, PayerTransition
from app.db.models.lookups import LoincCode, RxNormCode
from app.db.models.investigation import Investigation, Task, Artifact, Report
from app.db.models.literature import LiteratureAbstract
from app.db.models.drug_interactions import DrugInteraction

__all__ = [
    "Patient",
    "Encounter",
    "Organization",
    "Provider",
    "Condition",
    "Observation",
    "Medication",
    "Procedure",
    "Allergy",
    "Careplan",
    "Immunization",
    "Device",
    "Supply",
    "ImagingStudy",
    "Payer",
    "PayerTransition",
    "Claim",
    "ClaimTransaction",
    "LoincCode",
    "RxNormCode",
    "Investigation",
    "Task",
    "Artifact",
    "Report",
    "LiteratureAbstract",
    "DrugInteraction",
]
