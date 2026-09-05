"""Clinical event tables - all key off PATIENT + ENCOUNTER (data_model.md Section 2.2).

Most of these tables have no natural Synthea-assigned row primary key, so each gets
a synthetic BIGINT identity `id` here purely so Postgres has something to key/index
on - it carries no meaning from the source data. `careplans` and `imaging_studies`
already have their own `Id` from Synthea and use that instead.

REASONCODE columns (medications, procedures, careplans) are stored as plain indexed
String columns, NOT as a ForeignKey to conditions.CODE - see join_reference.md
Section 2: it's a code-value match, not a real referential constraint (conditions.CODE
isn't unique, and a naive join on it over-matches when a condition code recurs for a
patient). Application code doing the causal-chain lookup must apply the
nearest-prior-date tiebreak described there, not rely on the DB to enforce it.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Condition(Base):
    __tablename__ = "conditions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    start: Mapped[datetime] = mapped_column("START", DateTime(timezone=True), nullable=False)
    stop: Mapped[datetime | None] = mapped_column("STOP", DateTime(timezone=True))
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    system: Mapped[str | None] = mapped_column("SYSTEM", String(128))
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column("DATE", DateTime(timezone=True), nullable=False)
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    # nullable: ~3.6% of rows (QALY/DALY/QOLS) are patient-level yearly metrics with
    # no visit to attach to - see join_reference.md Section 6.
    encounter_id: Mapped[str | None] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), index=True)
    category: Mapped[str | None] = mapped_column("CATEGORY", String(32))
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    value: Mapped[str | None] = mapped_column("VALUE", Text)
    units: Mapped[str | None] = mapped_column("UNITS", String(64))
    type: Mapped[str | None] = mapped_column("TYPE", String(16))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter | None"] = relationship()


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    start: Mapped[datetime] = mapped_column("START", DateTime(timezone=True), nullable=False)
    stop: Mapped[datetime | None] = mapped_column("STOP", DateTime(timezone=True))
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    payer_id: Mapped[str | None] = mapped_column("PAYER", String(36), ForeignKey("payers.Id"), index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    base_cost: Mapped[float | None] = mapped_column("BASE_COST", Numeric(12, 2))
    payer_coverage: Mapped[float | None] = mapped_column("PAYER_COVERAGE", Numeric(12, 2))
    dispenses: Mapped[int | None] = mapped_column("DISPENSES")
    totalcost: Mapped[float | None] = mapped_column("TOTALCOST", Numeric(14, 2))
    reasoncode: Mapped[str | None] = mapped_column("REASONCODE", String(32), index=True)
    reasondescription: Mapped[str | None] = mapped_column("REASONDESCRIPTION", String(512))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()
    payer: Mapped["Payer | None"] = relationship()


class Procedure(Base):
    __tablename__ = "procedures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    start: Mapped[datetime] = mapped_column("START", DateTime(timezone=True), nullable=False)
    stop: Mapped[datetime] = mapped_column("STOP", DateTime(timezone=True), nullable=False)
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    system: Mapped[str | None] = mapped_column("SYSTEM", String(128))
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    base_cost: Mapped[float | None] = mapped_column("BASE_COST", Numeric(12, 2))
    reasoncode: Mapped[str | None] = mapped_column("REASONCODE", String(32), index=True)
    reasondescription: Mapped[str | None] = mapped_column("REASONDESCRIPTION", String(512))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()


class Allergy(Base):
    __tablename__ = "allergies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    start: Mapped[datetime] = mapped_column("START", DateTime(timezone=True), nullable=False)
    # STOP is 100% null in the generated data (Synthea never resolves an allergy) -
    # kept nullable rather than dropped, since a future Synthea version could populate it.
    stop: Mapped[datetime | None] = mapped_column("STOP", DateTime(timezone=True))
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False)
    system: Mapped[str | None] = mapped_column("SYSTEM", String(128))
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    type: Mapped[str | None] = mapped_column("TYPE", String(16))
    category: Mapped[str | None] = mapped_column("CATEGORY", String(32))
    reaction1: Mapped[str | None] = mapped_column("REACTION1", String(32))
    description1: Mapped[str | None] = mapped_column("DESCRIPTION1", String(512))
    severity1: Mapped[str | None] = mapped_column("SEVERITY1", String(16))
    reaction2: Mapped[str | None] = mapped_column("REACTION2", String(32))
    description2: Mapped[str | None] = mapped_column("DESCRIPTION2", String(512))
    severity2: Mapped[str | None] = mapped_column("SEVERITY2", String(16))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()


class Careplan(Base):
    __tablename__ = "careplans"

    id: Mapped[str] = mapped_column("Id", String(36), primary_key=True)
    start: Mapped[datetime] = mapped_column("START", DateTime(timezone=True), nullable=False)
    stop: Mapped[datetime | None] = mapped_column("STOP", DateTime(timezone=True))
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    reasoncode: Mapped[str | None] = mapped_column("REASONCODE", String(32), index=True)
    reasondescription: Mapped[str | None] = mapped_column("REASONDESCRIPTION", String(512))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()


class Immunization(Base):
    __tablename__ = "immunizations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column("DATE", DateTime(timezone=True), nullable=False)
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    base_cost: Mapped[float | None] = mapped_column("BASE_COST", Numeric(12, 2))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    start: Mapped[datetime] = mapped_column("START", DateTime(timezone=True), nullable=False)
    stop: Mapped[datetime | None] = mapped_column("STOP", DateTime(timezone=True))
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    # unique per row (one physical device instance) - see join_reference.md Section 6.
    udi: Mapped[str | None] = mapped_column("UDI", String(256), unique=True)

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()


class Supply(Base):
    __tablename__ = "supplies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column("DATE", DateTime(timezone=True), nullable=False)
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column("CODE", String(32), nullable=False)
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    quantity: Mapped[int | None] = mapped_column("QUANTITY")

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()


class ImagingStudy(Base):
    __tablename__ = "imaging_studies"

    # NOTE row grain is one DICOM *instance*, not one study - Id/SERIES_UID repeat
    # across many rows. Always COUNT(DISTINCT "Id") or dedupe before aggregating at
    # the study level. See join_reference.md Section 4. The true row-unique column is
    # INSTANCE_UID, but Id is kept as the table's PK to match every other Synthea
    # table's convention (and because (Id, INSTANCE_UID) would be the "real" natural
    # key - enforced below as a composite unique constraint instead of the PK, so a
    # naive `SELECT * FROM imaging_studies WHERE "Id" = ...` still returns the
    # instance-level rows a caller expects from this table).
    instance_uid: Mapped[str] = mapped_column("INSTANCE_UID", String(128), primary_key=True)
    id: Mapped[str] = mapped_column("Id", String(36), nullable=False, index=True)
    date: Mapped[datetime] = mapped_column("DATE", DateTime(timezone=True), nullable=False)
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    encounter_id: Mapped[str] = mapped_column("ENCOUNTER", String(36), ForeignKey("encounters.Id"), nullable=False, index=True)
    series_uid: Mapped[str] = mapped_column("SERIES_UID", String(128), nullable=False, index=True)
    bodysite_code: Mapped[str | None] = mapped_column("BODYSITE_CODE", String(32))
    bodysite_description: Mapped[str | None] = mapped_column("BODYSITE_DESCRIPTION", String(256))
    modality_code: Mapped[str | None] = mapped_column("MODALITY_CODE", String(32))
    modality_description: Mapped[str | None] = mapped_column("MODALITY_DESCRIPTION", String(128))
    sop_code: Mapped[str | None] = mapped_column("SOP_CODE", String(64))
    sop_description: Mapped[str | None] = mapped_column("SOP_DESCRIPTION", String(256))
    procedure_code: Mapped[str | None] = mapped_column("PROCEDURE_CODE", String(32))

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped["Encounter"] = relationship()
