"""Phase 6 minimal API surface (design doc 3.9/Phase 7's "REST/WebSocket API"
that the eventual Next.js frontend builds against) - just enough to prove
Celery submission + Redis progress streaming work end-to-end for a real
client, not the full API surface (auth middleware, pagination, etc. are
later/Phase 7 concerns).

POST /investigations   - submits a Celery job, returns immediately with a
                          pre-assigned investigation_id (see orchestrator.
                          run_investigation's investigation_id parameter) so
                          the client can open the WebSocket before or as soon
                          as the job starts, not after.
GET  /investigations/{id} - fetch the current Investigation (may 404 briefly
                          right after submission, before the worker has
                          created the row - expected for an async job, not a
                          bug; the WebSocket is the real-time source of truth).
WS   /ws/investigations/{id} - relays every Redis pub/sub progress event for
                          that investigation as JSON, closing after
                          investigation_complete/investigation_failed.
"""

import json
import uuid

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.db.models.investigation import Investigation
from app.db.session import SessionLocal
from app.rbac.policy import TOOLS_FOR_ROLE
from app.worker.progress import channel_for
from app.worker.tasks import run_investigation_task

app = FastAPI(title="Clinical Investigation Agent API")


class InvestigationRequest(BaseModel):
    question: str
    patient_id: str | None = None
    role: str | None = None


class InvestigationSubmitted(BaseModel):
    investigation_id: str
    task_id: str
    status: str = "submitted"


@app.post("/investigations", response_model=InvestigationSubmitted, status_code=202)
def submit_investigation(req: InvestigationRequest):
    if req.role is not None and req.role not in TOOLS_FOR_ROLE:
        return JSONResponse(
            status_code=422,
            content={"detail": f"unknown role: {req.role!r}, must be one of {list(TOOLS_FOR_ROLE)} or null"},
        )

    investigation_id = str(uuid.uuid4())
    async_result = run_investigation_task.delay(
        req.question, patient_id=req.patient_id, role=req.role, investigation_id=investigation_id
    )
    return InvestigationSubmitted(investigation_id=investigation_id, task_id=async_result.id)


@app.get("/investigations/{investigation_id}")
def get_investigation(investigation_id: str):
    session = SessionLocal()
    try:
        investigation = session.get(Investigation, investigation_id)
        if investigation is None:
            return JSONResponse(
                status_code=404,
                content={"detail": "not found yet - job may still be starting; use the WebSocket for live status"},
            )
        return {
            "id": investigation.id,
            "question": investigation.question,
            "goal": investigation.goal,
            "status": investigation.status,
            "evidence_complete": investigation.evidence_complete,
            "tasks": [{"tool": t.tool, "status": t.status} for t in investigation.tasks],
            "report": {
                "executive_summary": investigation.report.executive_summary,
                "sections": investigation.report.sections,
            } if investigation.report else None,
        }
    finally:
        session.close()


@app.websocket("/ws/investigations/{investigation_id}")
async def investigation_progress(websocket: WebSocket, investigation_id: str):
    await websocket.accept()
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel_for(investigation_id))
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await websocket.send_text(message["data"])
            event = json.loads(message["data"])
            if event["event"] in ("investigation_complete", "investigation_failed"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_for(investigation_id))
        await client.aclose()
        try:
            await websocket.close()
        except RuntimeError:
            pass  # already closed (e.g. client disconnected)
