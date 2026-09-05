"""Core identity/visit spine: patients, encounters, organizations, providers.

Synthea IDs are plain (unencrypted) UUID-shaped strings, not integers - stored as
String(36) throughout rather than a native UUID/serial type, matching data_model.md
Section 1. All *CODE columns dataset-wide (SNOMED/RxNorm/LOINC) are stored as String
too, since LOINC codes contain hyphens (e.g. "8480-6") and would break an Integer
column - see join_reference.md for why these are never DB-level foreign keys.

Naming convention used across every model in this package: a FK column's Python
attribute is named `<thing>_id` (e.g. `patient_id`), leaving the bare name
(`patient`) free for the ORM relationship to the related object.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column("Id", String(36), primary_key=True)
    birthdate: Mapped[date] = mapped_column(Date, nullable=False)
    deathdate: Mapped[date | None] = mapped_column(Date)
    ssn: Mapped[str | None] = mapped_column(String(32))
    drivers: Mapped[str | None] = mapped_column(String(32))
    passport: Mapped[str | None] = mapped_column(String(32))
    prefix: Mapped[str | None] = mapped_column(String(16))
    first: Mapped[str | None] = mapped_column(String(128))
    middle: Mapped[str | None] = mapped_column(String(128))
    last: Mapped[str | None] = mapped_column(String(128))
    suffix: Mapped[str | None] = mapped_column(String(16))
    maiden: Mapped[str | None] = mapped_column(String(128))
    marital: Mapped[str | None] = mapped_column(String(8))
    race: Mapped[str | None] = mapped_column(String(64))
    ethnicity: Mapped[str | None] = mapped_column(String(64))
    gender: Mapped[str | None] = mapped_column(String(8))
    birthplace: Mapped[str | None] = mapped_column(String(256))
    address: Mapped[str | None] = mapped_column(String(256))
    city: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(64))
    county: Mapped[str | None] = mapped_column(String(128))
    fips: Mapped[str | None] = mapped_column(String(16))
    zip: Mapped[str | None] = mapped_column(String(16))
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    healthcare_expenses: Mapped[float | None] = mapped_column(Numeric(14, 2))
    healthcare_coverage: Mapped[float | None] = mapped_column(Numeric(14, 2))
    income: Mapped[float | None] = mapped_column(Numeric(14, 2))

    encounters: Mapped[list["Encounter"]] = relationship(back_populates="patient")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column("Id", String(36), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256))
    address: Mapped[str | None] = mapped_column(String(256))
    city: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(64))
    zip: Mapped[str | None] = mapped_column(String(16))
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    phone: Mapped[str | None] = mapped_column(String(64))
    revenue: Mapped[float | None] = mapped_column(Numeric(14, 2))
    utilization: Mapped[int | None] = mapped_column()


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column("Id", String(36), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(
        "ORGANIZATION", String(36), ForeignKey("organizations.Id"), index=True
    )
    name: Mapped[str | None] = mapped_column(String(256))
    gender: Mapped[str | None] = mapped_column(String(8))
    speciality: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(String(256))
    city: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(64))
    zip: Mapped[str | None] = mapped_column(String(16))
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    encounters_count: Mapped[int | None] = mapped_column("ENCOUNTERS")
    procedures_count: Mapped[int | None] = mapped_column("PROCEDURES")

    organization: Mapped["Organization | None"] = relationship()


class Encounter(Base):
    __tablename__ = "encounters"

    id: Mapped[str] = mapped_column("Id", String(36), primary_key=True)
    start: Mapped[datetime] = mapped_column("START", DateTime(timezone=True), nullable=False, index=True)
    stop: Mapped[datetime | None] = mapped_column("STOP", DateTime(timezone=True))
    patient_id: Mapped[str] = mapped_column(
        "PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True
    )
    organization_id: Mapped[str | None] = mapped_column(
        "ORGANIZATION", String(36), ForeignKey("organizations.Id"), index=True
    )
    provider_id: Mapped[str | None] = mapped_column(
        "PROVIDER", String(36), ForeignKey("providers.Id"), index=True
    )
    payer_id: Mapped[str | None] = mapped_column(
        "PAYER", String(36), ForeignKey("payers.Id"), index=True
    )
    encounterclass: Mapped[str | None] = mapped_column("ENCOUNTERCLASS", String(32))
    code: Mapped[str | None] = mapped_column("CODE", String(32))
    description: Mapped[str | None] = mapped_column("DESCRIPTION", String(512))
    base_encounter_cost: Mapped[float | None] = mapped_column("BASE_ENCOUNTER_COST", Numeric(12, 2))
    total_claim_cost: Mapped[float | None] = mapped_column("TOTAL_CLAIM_COST", Numeric(12, 2))
    payer_coverage: Mapped[float | None] = mapped_column("PAYER_COVERAGE", Numeric(12, 2))
    reasoncode: Mapped[str | None] = mapped_column("REASONCODE", String(32))
    reasondescription: Mapped[str | None] = mapped_column("REASONDESCRIPTION", String(512))

    patient: Mapped["Patient"] = relationship(back_populates="encounters")
    organization: Mapped["Organization | None"] = relationship()
    provider: Mapped["Provider | None"] = relationship()
    payer: Mapped["Payer | None"] = relationship()
