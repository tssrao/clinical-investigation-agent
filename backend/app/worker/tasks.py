"""Celery task wrapping run_investigation (design doc 3.9). Returns just the
investigation id, not the Investigation ORM object - Celery results are
JSON-serialized and a SQLAlchemy object isn't; callers fetch the full
Investigation from Postgres by id once the task completes (or read progress
events via Redis pub/sub while it's running - app/worker/progress.py).
"""

from app.agents.orchestrator import run_investigation
from app.worker.celery_app import celery_app


@celery_app.task(name="run_investigation_task", bind=True)
def run_investigation_task(
    self,
    question: str,
    patient_id: str | None = None,
    role: str | None = None,
    investigation_id: str | None = None,
) -> str:
    investigation = run_investigation(
        question, patient_id=patient_id, role=role, investigation_id=investigation_id
    )
    return investigation.id
