"""Phase 6 async infra tests (design doc 3.9). Requires Redis running
(app.worker.progress publishes to it; Celery uses it as broker/backend) -
requires_redis is checked with a real ping, same pattern as OPENAI_API_KEY
gating elsewhere.

Celery tasks run in EAGER mode here (task_always_eager=True) - executes
synchronously in-process, proving the task correctly wraps run_investigation
and returns the right investigation_id, WITHOUT needing a live worker process.
This deliberately does NOT test genuine live progress streaming (a real
worker publishing events while a WebSocket listens concurrently) - that needs
real async execution to observe, and was verified manually instead: submit ->
WebSocket receives investigation_started -> plan_ready -> task_started ->
task_finished -> investigation_complete, in order, with real content, while a
separate real Celery worker processed the job - see CLAUDE.md for the
captured transcript.
"""

import uuid

import pytest
import redis
from fastapi.testclient import TestClient

from app.core.config import settings

try:
    redis.Redis.from_url(settings.redis_url).ping()
    _redis_available = True
except redis.RedisError:
    _redis_available = False

requires_redis = pytest.mark.skipif(not _redis_available, reason="Redis not reachable")
requires_openai_key = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)


@requires_redis
class TestProgressPublishing:
    def test_publish_event_does_not_raise_when_redis_unreachable(self, monkeypatch):
        """Best-effort by design (module docstring) - a progress-streaming
        failure must never break an investigation.
        """
        from app.worker import progress

        monkeypatch.setattr(progress.settings, "redis_url", "redis://127.0.0.1:1/0")
        monkeypatch.setattr(progress, "_redis_client", None)
        progress.publish_event("some-id", "test_event", foo="bar")  # must not raise

    def test_publish_event_reaches_a_real_subscriber(self):
        import json
        from app.worker.progress import channel_for, publish_event

        investigation_id = str(uuid.uuid4())
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        pubsub = client.pubsub()
        pubsub.subscribe(channel_for(investigation_id))
        pubsub.get_message(timeout=1)  # subscribe confirmation

        publish_event(investigation_id, "test_event", foo="bar")
        message = pubsub.get_message(timeout=2)

        assert message is not None
        payload = json.loads(message["data"])
        assert payload["event"] == "test_event"
        assert payload["foo"] == "bar"
        assert payload["investigation_id"] == investigation_id


@requires_redis
@requires_openai_key
class TestCeleryTaskEager:
    @pytest.fixture(autouse=True)
    def eager_mode(self):
        from app.worker.celery_app import celery_app
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        yield
        celery_app.conf.task_always_eager = False

    def test_task_wraps_run_investigation_and_returns_the_pre_assigned_id(self):
        from app.worker.tasks import run_investigation_task

        investigation_id = str(uuid.uuid4())
        result = run_investigation_task.delay(
            "How many patients are there in total?", investigation_id=investigation_id
        )
        assert result.get(timeout=30) == investigation_id


@requires_redis
@requires_openai_key
class TestAPI:
    @pytest.fixture(autouse=True)
    def eager_mode(self):
        from app.worker.celery_app import celery_app
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        yield
        celery_app.conf.task_always_eager = False

    @pytest.fixture
    def client(self):
        from app.api.main import app
        return TestClient(app)

    def test_submit_returns_202_with_investigation_id(self, client):
        resp = client.post("/investigations", json={"question": "How many patients are there in total?"})
        assert resp.status_code == 202
        data = resp.json()
        assert "investigation_id" in data
        assert data["status"] == "submitted"

    def test_submit_then_get_returns_completed_investigation(self, client):
        # eager mode means the task (and thus the whole investigation) has
        # already finished synchronously by the time .post() returns
        resp = client.post("/investigations", json={"question": "How many patients are there in total?"})
        investigation_id = resp.json()["investigation_id"]

        get_resp = client.get(f"/investigations/{investigation_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["status"] == "complete"
        assert data["report"] is not None
        assert "2,338" in data["report"]["executive_summary"] or "2338" in data["report"]["executive_summary"]

    def test_get_unknown_id_returns_404(self, client):
        resp = client.get(f"/investigations/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_submit_rejects_unknown_role(self, client):
        resp = client.post(
            "/investigations", json={"question": "anything", "role": "nurse"}
        )
        assert resp.status_code == 422
