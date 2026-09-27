"""Progress streaming (design doc 3.9): "WebSocket streams task-status updates
to the frontend as the Planner's task list progresses." Redis pub/sub is the
transport - reusing the same Redis already standing up as the Celery
broker/backend rather than introducing a second message system. The
orchestrator publishes; the WebSocket endpoint (app/api/main.py) subscribes
and relays to the connected client. Nothing here is Celery-specific, so
run_investigation stays testable synchronously (as every prior phase's tests
already do) with or without a worker/API running.
"""

import json
from datetime import datetime, timezone

import redis

from app.core.config import settings

_redis_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def channel_for(investigation_id: str) -> str:
    return f"investigation:{investigation_id}:progress"


def publish_event(investigation_id: str, event: str, **fields) -> None:
    """Best-effort - a progress-streaming failure must never break an
    investigation. If Redis is unreachable, this silently no-ops rather than
    raising, since progress events are a UI nicety, not part of the
    Investigation/Task/Artifact/Report data an investigation actually needs to
    complete correctly (which is why call sites don't need to catch anything
    here themselves).
    """
    payload = {
        "investigation_id": investigation_id,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    try:
        _get_client().publish(channel_for(investigation_id), json.dumps(payload))
    except redis.RedisError:
        pass
