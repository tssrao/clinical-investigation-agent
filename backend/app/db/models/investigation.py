"""Phase 2 domain model (design doc Section 3.4) - the only persistent entities the
application layer owns on top of the untouched Synthea schema. Every field maps
directly to what the design doc specifies; the one deliberate simplification is
dropping Task.artifact_id (design doc lists it as nullable-until-produced) in favor
of a one-directional Artifact.task_id FK only - the same relationship is queryable
either way (`artifacts WHERE task_id = X`), and a one-directional FK avoids a
nullable circular-FK pair between tasks and artifacts for no real benefit.

Status/type/role fields are plain indexed String columns with a Python-level
Literal for editor/type-checking, not a native Postgres ENUM - keeps adding a new
tool type (Phase 3) or status value a pure application-code change, no migration
needed just to add an enum value.
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

InvestigationStatus = Literal["pending", "running", "needs_more_evidence", "reviewed", "complete"]
TaskStatus = Literal["pending", "running", "complete", "skipped", "failed"]
# "report" is a real tool (assembles the Report); "reviewer" is orchestration, not a task.
ToolName = Literal[
    "sql", "timeline", "medication", "literature", "prediction", "visualization", "drug_interactions", "report"
]
ArtifactType = Literal[
    "sql_result", "timeline", "medication_list", "literature", "prediction", "visualization", "drug_interaction"
]
Role = Literal["doctor", "insurance_adjuster"]


def _uuid() -> str:
    return str(uuid.uuid4())


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # nullable: not every question is patient-scoped (e.g. a terminology lookup) -
    # see design doc 3.6's routing discipline table.
    patient_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("patients.Id"), index=True)
    role: Mapped[str | None] = mapped_column(String(32))  # Role - RBAC not enforced yet (Phase 4)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str | None] = mapped_column(Text)  # Planner's restated investigation goal
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # InvestigationStatus
    evidence_complete: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tasks: Mapped[list["Task"]] = relationship(back_populates="investigation", order_by="Task.id")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="investigation")
    report: Mapped["Report | None"] = relationship(back_populates="investigation")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("investigations.id"), nullable=False, index=True
    )
    tool: Mapped[str] = mapped_column(String(32), nullable=False)  # ToolName
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # TaskStatus
    round: Mapped[int] = mapped_column(default=0)  # 0 = initial plan, 1-2 = Reviewer-inserted rounds

    investigation: Mapped["Investigation"] = relationship(back_populates="tasks")
    artifact: Mapped["Artifact | None"] = relationship(back_populates="task", uselist=False)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("investigations.id"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=False, index=True, unique=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # ArtifactType
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)  # e.g. the SQL executed, a citation
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    investigation: Mapped["Investigation"] = relationship(back_populates="artifacts")
    task: Mapped["Task"] = relationship(back_populates="artifact")


class Report(Base):
    __tablename__ = "reports"

    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("investigations.id"), primary_key=True
    )
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    sections: Mapped[dict] = mapped_column(JSON, nullable=False)  # 3.7: timeline/evidence/literature/etc
    evidence_complete: Mapped[bool] = mapped_column(nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    investigation: Mapped["Investigation"] = relationship(back_populates="report")
