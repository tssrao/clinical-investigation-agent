"""Vocabulary lookup tables (Phase 0 exit criteria) - resolve a code stored on a
Synthea table (medications.CODE, observations.CODE) to a human-readable
description. These are NOT foreign-keyed to the Synthea tables, for the same
reason *CODE columns dataset-wide aren't FKs (see join_reference.md /
app/db/models/core.py docstring): a code-value match is not a real referential
constraint we want the DB enforcing (a code can legitimately appear in Synthea data
that isn't in a given vocabulary release/version, and vice versa).

No ICD-10-CM table here: verified against real loaded data that Synthea uses
SNOMED-CT (not ICD-10-CM) for both conditions.CODE and claims.DIAGNOSISn - 0/285
and 0/221 distinct codes respectively matched a loaded ICD-10-CM lookup table, so
it was removed rather than kept as dead weight. SNOMED-CT is explicitly out of
scope for this project (design doc Section 5, "Explicitly deferred, not planned").
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LoincCode(Base):
    """LOINC lab/observation codes (LoincTableCore, release 2.83)."""

    __tablename__ = "loinc_codes"

    code: Mapped[str] = mapped_column("LOINC_NUM", String(16), primary_key=True)
    component: Mapped[str | None] = mapped_column("COMPONENT", String(256))
    system: Mapped[str | None] = mapped_column("SYSTEM", String(128))
    class_: Mapped[str | None] = mapped_column("CLASS", String(64))
    long_common_name: Mapped[str | None] = mapped_column("LONG_COMMON_NAME", String(512))
    shortname: Mapped[str | None] = mapped_column("SHORTNAME", String(128))
    status: Mapped[str | None] = mapped_column("STATUS", String(16))


class RxNormCode(Base):
    """RxNorm drug/ingredient codes (RXNCONSO.RRF, restricted to English + current).

    Empty until the UMLS license is approved and RXNCONSO.RRF is loaded - see
    README.md "Lookup vocabularies" for the download steps once access is granted.
    """

    __tablename__ = "rxnorm_codes"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(512))
    term_type: Mapped[str | None] = mapped_column(String(20))
