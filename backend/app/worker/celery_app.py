"""Phase 6 async infra (design doc 3.9): Celery + Redis run investigations as
background jobs so a request thread never blocks on an investigation's real
wall-clock time (multiple tool calls, an LLM call per task).

Interim setup like MLflow's: the worker process runs locally from
backend/.venv (`uv run celery -A app.worker.celery_app worker --loglevel=info
--pool=solo` on Windows - the default prefork pool needs os.fork, which
Windows doesn't have), not yet a docker-compose service - Redis itself is
containerized (docker-compose.yml), but the worker running arbitrary app code
isn't, the same tradeoff made for MLflow's tracking server.
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "clinical_investigation_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
)

# registers app.worker.tasks.run_investigation_task with this Celery app
import app.worker.tasks  # noqa: E402,F401
