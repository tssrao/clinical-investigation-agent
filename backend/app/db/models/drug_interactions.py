"""Hand-curated drug-drug interaction rules table (design doc §4.3.3, §5 Phase 3,
Known Limitations "No real drug-drug interaction data" - RxNorm is a vocabulary,
not an interactions database, so this is built as a small, defensible, scoped
item rather than sourced externally). Every row must be independently explained
and defended, not just plausible - see scripts/seed_drug_interactions.py for the
citation/mechanism behind each one.

drug_groups is a JSON list of groups, e.g.
    [["ibuprofen", "naproxen"], ["lisinopril", "losartan"], ["hydrochlorothiazide", "furosemide"]]
means: this rule fires only if the patient's CURRENT medications include at
least one drug matching each group (OR within a group, AND across groups) -
grounded in medications.DESCRIPTION substrings actually present in this
dataset, verified before writing any rule (see interaction_tool.py docstring).
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DrugInteraction(Base):
    __tablename__ = "drug_interactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    drug_groups: Mapped[list] = mapped_column(JSON, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # "high" | "moderate"
    mechanism: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
