"""Insurance/claims tables (data_model.md Section 2.3).

Two fan-out gotchas baked into these joins, both documented in join_reference.md:
claims.APPOINTMENTID is NOT 1:1 with encounters.Id (1.85 claims/encounter on
average - aggregate claims per APPOINTMENTID before joining to encounters if the
question is encounter-scoped), and claims_transactions' AMOUNT/PAYMENTS/TRANSFERS
columns are only meaningful conditional on TYPE (Section 5) - that's an application-
level rule, not something a DB constraint can enforce cleanly here.
"""

from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Payer(Base):
    __tablename__ = "payers"

    id: Mapped[str] = mapped_column("Id", String(36), primary_key=True)
    name: Mapped[str | None] = mapped_column("NAME", String(256))
    ownership: Mapped[str | None] = mapped_column("OWNERSHIP", String(32))
    address: Mapped[str | None] = mapped_column("ADDRESS", String(256))
    city: Mapped[str | None] = mapped_column("CITY", String(128))
    state_headquartered: Mapped[str | None] = mapped_column("STATE_HEADQUARTERED", String(64))
    zip: Mapped[str | None] = mapped_column("ZIP", String(16))
    phone: Mapped[str | None] = mapped_column("PHONE", String(64))
    amount_covered: Mapped[float | None] = mapped_column("AMOUNT_COVERED", Numeric(16, 2))
    amount_uncovered: Mapped[float | None] = mapped_column("AMOUNT_UNCOVERED", Numeric(16, 2))
    revenue: Mapped[float | None] = mapped_column("REVENUE", Numeric(16, 2))
    covered_encounters: Mapped[int | None] = mapped_column("COVERED_ENCOUNTERS")
    uncovered_encounters: Mapped[int | None] = mapped_column("UNCOVERED_ENCOUNTERS")
    covered_medications: Mapped[int | None] = mapped_column("COVERED_MEDICATIONS")
    uncovered_medications: Mapped[int | None] = mapped_column("UNCOVERED_MEDICATIONS")
    covered_procedures: Mapped[int | None] = mapped_column("COVERED_PROCEDURES")
    uncovered_procedures: Mapped[int | None] = mapped_column("UNCOVERED_PROCEDURES")
    covered_immunizations: Mapped[int | None] = mapped_column("COVERED_IMMUNIZATIONS")
    uncovered_immunizations: Mapped[int | None] = mapped_column("UNCOVERED_IMMUNIZATIONS")
    unique_customers: Mapped[int | None] = mapped_column("UNIQUE_CUSTOMERS")
    qols_avg: Mapped[float | None] = mapped_column("QOLS_AVG", Numeric(8, 6))
    member_months: Mapped[int | None] = mapped_column("MEMBER_MONTHS")


class PayerTransition(Base):
    __tablename__ = "payer_transitions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[str] = mapped_column("PATIENT", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    memberid: Mapped[str | None] = mapped_column("MEMBERID", String(64))
    start_date: Mapped[date] = mapped_column("START_DATE", DateTime(timezone=True), nullable=False)
    end_date: Mapped[date | None] = mapped_column("END_DATE", DateTime(timezone=True))
    payer_id: Mapped[str] = mapped_column("PAYER", String(36), ForeignKey("payers.Id"), nullable=False, index=True)
    # only 9.3% of rows have a secondary payer - see join_reference.md Section 3 (Payer Transitions).
    secondary_payer_id: Mapped[str | None] = mapped_column("SECONDARY_PAYER", String(36), ForeignKey("payers.Id"), index=True)
    plan_ownership: Mapped[str | None] = mapped_column("PLAN_OWNERSHIP", String(32))
    owner_name: Mapped[str | None] = mapped_column("OWNER_NAME", String(256))

    patient: Mapped["Patient"] = relationship()
    payer: Mapped["Payer"] = relationship(foreign_keys=[payer_id])
    secondary_payer: Mapped["Payer | None"] = relationship(foreign_keys=[secondary_payer_id])


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column("Id", String(36), primary_key=True)
    patient_id: Mapped[str] = mapped_column("PATIENTID", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    provider_id: Mapped[str | None] = mapped_column("PROVIDERID", String(36), ForeignKey("providers.Id"), index=True)
    primary_insurance_id: Mapped[str | None] = mapped_column(
        "PRIMARYPATIENTINSURANCEID", String(36), ForeignKey("payers.Id"), index=True
    )
    secondary_insurance_id: Mapped[str | None] = mapped_column(
        "SECONDARYPATIENTINSURANCEID", String(36), ForeignKey("payers.Id"), index=True
    )
    # no lookup table for these in the Synthea export - opaque internal ids, kept as-is.
    departmentid: Mapped[str | None] = mapped_column("DEPARTMENTID", String(32))
    patientdepartmentid: Mapped[str | None] = mapped_column("PATIENTDEPARTMENTID", String(32))
    diagnosis1: Mapped[str | None] = mapped_column("DIAGNOSIS1", String(32))
    diagnosis2: Mapped[str | None] = mapped_column("DIAGNOSIS2", String(32))
    diagnosis3: Mapped[str | None] = mapped_column("DIAGNOSIS3", String(32))
    diagnosis4: Mapped[str | None] = mapped_column("DIAGNOSIS4", String(32))
    diagnosis5: Mapped[str | None] = mapped_column("DIAGNOSIS5", String(32))
    diagnosis6: Mapped[str | None] = mapped_column("DIAGNOSIS6", String(32))
    diagnosis7: Mapped[str | None] = mapped_column("DIAGNOSIS7", String(32))
    diagnosis8: Mapped[str | None] = mapped_column("DIAGNOSIS8", String(32))
    # 100% null in the generated data - kept nullable, not dropped (see join_reference.md).
    referring_provider_id: Mapped[str | None] = mapped_column(
        "REFERRINGPROVIDERID", String(36), ForeignKey("providers.Id")
    )
    # NOT 1:1 with encounters - see module docstring / join_reference.md Section 3.
    appointment_id: Mapped[str | None] = mapped_column(
        "APPOINTMENTID", String(36), ForeignKey("encounters.Id"), index=True
    )
    currentillnessdate: Mapped[datetime | None] = mapped_column("CURRENTILLNESSDATE", DateTime(timezone=True))
    servicedate: Mapped[datetime | None] = mapped_column("SERVICEDATE", DateTime(timezone=True))
    supervising_provider_id: Mapped[str | None] = mapped_column(
        "SUPERVISINGPROVIDERID", String(36), ForeignKey("providers.Id")
    )
    # always BILLED/CLOSED in the generated data - no DENIED state exists (design doc §4.3.2).
    status1: Mapped[str | None] = mapped_column("STATUS1", String(16))
    status2: Mapped[str | None] = mapped_column("STATUS2", String(16))
    statusp: Mapped[str | None] = mapped_column("STATUSP", String(16))
    outstanding1: Mapped[float | None] = mapped_column("OUTSTANDING1", Numeric(12, 2))
    outstanding2: Mapped[float | None] = mapped_column("OUTSTANDING2", Numeric(12, 2))
    outstandingp: Mapped[float | None] = mapped_column("OUTSTANDINGP", Numeric(12, 2))
    lastbilleddate1: Mapped[datetime | None] = mapped_column("LASTBILLEDDATE1", DateTime(timezone=True))
    lastbilleddate2: Mapped[datetime | None] = mapped_column("LASTBILLEDDATE2", DateTime(timezone=True))
    lastbilleddatep: Mapped[datetime | None] = mapped_column("LASTBILLEDDATEP", DateTime(timezone=True))
    healthcareclaimtypeid1: Mapped[str | None] = mapped_column("HEALTHCARECLAIMTYPEID1", String(8))
    healthcareclaimtypeid2: Mapped[str | None] = mapped_column("HEALTHCARECLAIMTYPEID2", String(8))

    patient: Mapped["Patient"] = relationship()
    provider: Mapped["Provider | None"] = relationship(foreign_keys=[provider_id])
    primary_insurance: Mapped["Payer | None"] = relationship(foreign_keys=[primary_insurance_id])
    secondary_insurance: Mapped["Payer | None"] = relationship(foreign_keys=[secondary_insurance_id])
    referring_provider: Mapped["Provider | None"] = relationship(foreign_keys=[referring_provider_id])
    supervising_provider: Mapped["Provider | None"] = relationship(foreign_keys=[supervising_provider_id])
    appointment: Mapped["Encounter | None"] = relationship()


class ClaimTransaction(Base):
    __tablename__ = "claims_transactions"

    id: Mapped[str] = mapped_column("ID", String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column("CLAIMID", String(36), ForeignKey("claims.Id"), nullable=False, index=True)
    chargeid: Mapped[str | None] = mapped_column("CHARGEID", String(32))
    patient_id: Mapped[str] = mapped_column("PATIENTID", String(36), ForeignKey("patients.Id"), nullable=False, index=True)
    # TYPE determines which of amount/payments/transfers is populated - see module docstring.
    type: Mapped[str] = mapped_column("TYPE", String(16), nullable=False, index=True)
    amount: Mapped[float | None] = mapped_column("AMOUNT", Numeric(14, 2))
    method: Mapped[str | None] = mapped_column("METHOD", String(16))
    fromdate: Mapped[datetime | None] = mapped_column("FROMDATE", DateTime(timezone=True))
    todate: Mapped[datetime | None] = mapped_column("TODATE", DateTime(timezone=True))
    placeofservice: Mapped[str | None] = mapped_column("PLACEOFSERVICE", String(36))
    procedurecode: Mapped[str | None] = mapped_column("PROCEDURECODE", String(32), index=True)
    modifier1: Mapped[str | None] = mapped_column("MODIFIER1", String(16))
    modifier2: Mapped[str | None] = mapped_column("MODIFIER2", String(16))
    # 1-4: which DIAGNOSIS<n> slot on the parent claim this line item bills against.
    diagnosisref1: Mapped[int | None] = mapped_column("DIAGNOSISREF1")
    diagnosisref2: Mapped[int | None] = mapped_column("DIAGNOSISREF2")
    diagnosisref3: Mapped[int | None] = mapped_column("DIAGNOSISREF3")
    diagnosisref4: Mapped[int | None] = mapped_column("DIAGNOSISREF4")
    units: Mapped[int | None] = mapped_column("UNITS")
    departmentid: Mapped[str | None] = mapped_column("DEPARTMENTID", String(32))
    notes: Mapped[str | None] = mapped_column("NOTES", Text)
    unitamount: Mapped[float | None] = mapped_column("UNITAMOUNT", Numeric(14, 2))
    transferoutid: Mapped[str | None] = mapped_column("TRANSFEROUTID", String(36))
    transfertype: Mapped[str | None] = mapped_column("TRANSFERTYPE", String(16))
    payments: Mapped[float | None] = mapped_column("PAYMENTS", Numeric(14, 2))
    # always exactly 0.0 in the generated data - a structurally dead column, not sparse.
    adjustments: Mapped[float | None] = mapped_column("ADJUSTMENTS", Numeric(14, 2))
    transfers: Mapped[float | None] = mapped_column("TRANSFERS", Numeric(14, 2))
    outstanding: Mapped[float | None] = mapped_column("OUTSTANDING", Numeric(14, 2))
    appointment_id: Mapped[str | None] = mapped_column(
        "APPOINTMENTID", String(36), ForeignKey("encounters.Id"), index=True
    )
    linenote: Mapped[str | None] = mapped_column("LINENOTE", Text)
    patientinsurance_id: Mapped[str | None] = mapped_column(
        "PATIENTINSURANCEID", String(36), ForeignKey("payers.Id")
    )
    feescheduleid: Mapped[str | None] = mapped_column("FEESCHEDULEID", String(32))
    provider_id: Mapped[str | None] = mapped_column("PROVIDERID", String(36), ForeignKey("providers.Id"), index=True)
    supervising_provider_id: Mapped[str | None] = mapped_column(
        "SUPERVISINGPROVIDERID", String(36), ForeignKey("providers.Id")
    )

    claim: Mapped["Claim"] = relationship()
    patient: Mapped["Patient"] = relationship()
    appointment: Mapped["Encounter | None"] = relationship()
    patient_insurance: Mapped["Payer | None"] = relationship(foreign_keys=[patientinsurance_id])
    provider: Mapped["Provider | None"] = relationship(foreign_keys=[provider_id])
    supervising_provider: Mapped["Provider | None"] = relationship(foreign_keys=[supervising_provider_id])
