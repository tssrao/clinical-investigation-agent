"""Phase 2 orchestrator: runs the real Planner -> execute -> Reviewer loop (design
doc 3.1/3.3) and persists the Investigation/Task/Artifact/Report domain model
(3.4) as it goes. Stays synchronous and directly callable (every phase's tests
call run_investigation() as a plain function) - Phase 6's Celery task
(app/worker/tasks.py) wraps this unchanged rather than rewriting it, and
publish_event calls below are best-effort (never raise) so this still works
identically with no worker/Redis running at all.

Loop shape, exactly per 3.3: Planner emits the initial plan as its one action (no
implicit tool-call looping) -> orchestrator executes each task deterministically,
one Artifact per task -> Reviewer evaluates the Artifact set for sufficiency ->
if insufficient, Reviewer's proposed tasks are executed as another round, capped
at 2 extra rounds total -> Report Tool assembles the final Report from every
Artifact in the store, setting evidence_complete=false if the cap was hit while
still insufficient (never loops indefinitely).
"""

import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from openai import OpenAI
from sqlalchemy.orm import Session

from app.agents.planner import generate_plan
from app.agents.reviewer import review_evidence
from app.agents.schemas import TaskSpec
from app.core.config import settings
from app.db.models.investigation import Artifact, Investigation, Report, Task
from app.db.session import SessionLocal
from app.rbac.policy import TOOLS_FOR_ROLE
from app.tools.interaction_tool import check_drug_interactions
from app.tools.prediction_tool import predict_readmission_risk
from app.tools.sql_tool import run_sql_tool
from app.tools.timeline_tool import build_timeline
from app.tools.visualization_tool import build_chart_for_patient
from app.worker.progress import publish_event

MAX_REVIEW_ROUNDS = 2

# Used only when a tool raises instead of returning normally (e.g. a DB-level
# RBAC denial, or MLflow being transiently unreachable) - the Artifact still
# needs the RIGHT type so report-assembly's per-type lookups (prediction_
# artifact, timeline_artifact, etc.) actually find it instead of silently
# treating that section as never having run.
ARTIFACT_TYPE_FOR_TOOL = {
    "sql": "sql_result",
    "timeline": "timeline",
    "prediction": "prediction",
    "drug_interactions": "drug_interaction",
    "visualization": "visualization",
}


def _json_safe(value):
    """Recursively convert values the JSON column can't hold natively (Decimal
    from NUMERIC columns, date/datetime from DATE/TIMESTAMP columns - both show
    up constantly in real query results, e.g. claims_transactions.PAYMENTS or
    patients.birthdate) into plain JSON-serializable types.
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    return value


def _execute_task(
    tool: str, purpose: str, patient_id: str | None, role: str | None = None
) -> tuple[dict, str | None, str, bool]:
    """Returns (content, source, artifact_type, success). role, when given
    (Phase 4 RBAC), is threaded into every tool that touches Postgres so the
    same DB-level permission/RLS boundary applies regardless of which tool the
    Planner routed to - see app.rbac.policy.
    """
    patient_scope = [patient_id] if patient_id else []

    if tool == "sql":
        question = purpose + (f" (for patient id: {patient_id})" if patient_id else "")
        result = run_sql_tool(question, role=role, patient_scope=patient_scope)
        content = _json_safe({
            "question": result.question,
            "sql": result.sql,
            "rows": result.rows,
            "row_count": result.row_count,
            "success": result.success,
            "error": result.error,
        })
        return content, result.sql, "sql_result", result.success

    if tool == "timeline":
        if not patient_id:
            return {"error": "timeline requires a patient_id"}, None, "timeline", False
        result = build_timeline(patient_id, role=role)
        content = _json_safe({
            "patient_id": result.patient_id,
            "event_count": result.event_count,
            "events": [{"event_date": e.event_date, "category": e.category, "description": e.description}
                       for e in result.events],
        })
        return content, f"timeline for patient {patient_id}", "timeline", True

    if tool == "prediction":
        if not patient_id:
            return {"error": "prediction requires a patient_id"}, None, "prediction", False
        result = predict_readmission_risk(patient_id)
        content = _json_safe({
            "patient_id": result.patient_id,
            "applicable": result.applicable,
            "risk_score": result.risk_score,
            "reference_encounter_id": result.reference_encounter_id,
            "features": result.features,
            "reason": result.reason,
        })
        source = "readmission_model (MLflow, alias: champion)"
        return content, source, "prediction", True  # ran successfully either way; "applicable" carries the outcome

    if tool == "drug_interactions":
        if not patient_id:
            return {"error": "drug_interactions requires a patient_id"}, None, "drug_interaction", False
        matches = check_drug_interactions(patient_id)
        content = _json_safe({
            "patient_id": patient_id,
            "match_count": len(matches),
            "matches": [
                {
                    "name": m.name, "severity": m.severity, "mechanism": m.mechanism,
                    "reference": m.reference, "matched_medications": m.matched_medications,
                }
                for m in matches
            ],
        })
        return content, "drug_interactions rules table (hand-curated)", "drug_interaction", True

    if tool == "visualization":
        if not patient_id:
            return {"error": "visualization requires a patient_id"}, None, "visualization", False
        result = build_chart_for_patient(patient_id, purpose)
        content = _json_safe({
            "patient_id": patient_id,
            "applicable": result.applicable,
            "metric_code": result.metric_code,
            "metric_description": result.metric_description,
            "units": result.units,
            "data_points": result.data_points,
            "plotly_figure": result.plotly_figure,
            "reason": result.reason,
        })
        source = f"observations (code: {result.metric_code})" if result.applicable else None
        return content, source, "visualization", True  # ran successfully either way; "applicable" carries the outcome

    raise NotImplementedError(f"tool '{tool}' is not implemented yet (Phase 3)")


def _summarize_artifacts(artifacts: list[Artifact]) -> str:
    lines = []
    for a in artifacts:
        content = a.content
        if content.get("success") is False and "error" in content and "sql" not in content:
            # A tool raised instead of returning normally (e.g. a DB-level RBAC
            # denial, or MLflow being transiently unreachable) - this applies
            # across every tool type, not just sql_result, and must be as
            # clearly flagged as a failure as the sql_result branch below.
            lines.append(
                f"- [{a.type}] TASK FAILED (not evidence of an empty/negative "
                f"result - this is an execution/authorization failure): "
                f"error: {content.get('error')}"
            )
        elif a.type == "sql_result":
            if not content.get("success"):
                # Critical: a failed/denied query must never be summarized the
                # same way as a genuinely empty result - a permission-denied
                # RBAC failure is not evidence of "no data," and reporting it
                # as one would be a false negative finding, not a traceable
                # claim (design doc 3.5's whole point).
                lines.append(
                    f"- [{a.type}] QUERY FAILED (not evidence of an empty result - this "
                    f"is an execution/authorization failure): SQL: {content.get('sql')} | "
                    f"error: {content.get('error')}"
                )
            else:
                sample = content.get("rows", [])[:3]
                lines.append(
                    f"- [{a.type}] SQL: {content.get('sql')} | "
                    f"{content.get('row_count')} row(s) | sample: {sample}"
                )
        elif a.type == "timeline":
            events = content.get("events", [])
            first_last = f"{events[0]['event_date']} .. {events[-1]['event_date']}" if events else "n/a"
            # a compact sample, not all events - a rich patient can have 800+,
            # which would blow up the Reviewer prompt for no benefit
            sample = events[:5]
            lines.append(
                f"- [{a.type}] {content.get('event_count')} event(s) spanning {first_last} | "
                f"earliest events: {sample}"
            )
        elif a.type == "prediction":
            if content.get("applicable"):
                lines.append(
                    f"- [{a.type}] 30-day readmission risk score: {content.get('risk_score'):.3f} "
                    f"(0-1 scale, from a statistical model trained on synthetic data - not "
                    f"clinically validated) based on {content.get('features')}"
                )
            else:
                lines.append(f"- [{a.type}] not applicable: {content.get('reason')}")
        elif a.type == "drug_interaction":
            if content.get("match_count"):
                summaries = [f"{m['name']} ({m['severity']})" for m in content.get("matches", [])]
                lines.append(f"- [{a.type}] {content.get('match_count')} known interaction(s) found: {summaries}")
            else:
                lines.append(f"- [{a.type}] no known interactions found among current medications")
        elif a.type == "visualization":
            if content.get("applicable"):
                points = content.get("data_points", [])
                lines.append(
                    f"- [{a.type}] chart built: {content.get('metric_description')} "
                    f"({len(points)} data points, {points[0]['date']} to {points[-1]['date']})"
                )
            else:
                lines.append(f"- [{a.type}] not applicable: {content.get('reason')}")
        else:
            lines.append(f"- [{a.type}] {content}")
    return "\n".join(lines) if lines else "(no evidence gathered yet)"


def _generate_executive_summary(question: str, goal: str, artifacts_summary: str) -> str:
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Write a 2-4 sentence plain-language executive summary answering "
                    "the investigation's original question, grounded ONLY in the "
                    "evidence given - do not state anything the evidence doesn't "
                    "support. No markdown, no bullet points, plain prose.\n\n"
                    "CRITICAL: some evidence entries are marked QUERY FAILED or TASK "
                    "FAILED (an execution or authorization failure, e.g. access was "
                    "restricted by the requester's role) - this is NOT the same as a "
                    "tool that ran successfully and found nothing. NEVER report a "
                    "QUERY FAILED or TASK FAILED entry as 'no data exists', 'the "
                    "patient has none', or similar - a failed or denied task means "
                    "the evidence is simply unavailable to this "
                    "investigation, not that the answer is negative. State plainly "
                    "that this information could not be retrieved (and why, if an "
                    "authorization restriction is evident), rather than implying an "
                    "absence."
                ),
            },
            {
                "role": "user",
                "content": f"Question: {question}\nGoal: {goal}\n\nEvidence:\n{artifacts_summary}",
            },
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def _run_tasks(
    session: Session, investigation: Investigation, task_specs: list[TaskSpec], round_num: int
) -> list[Artifact]:
    new_artifacts = []
    for spec in task_specs:
        task = Task(
            investigation_id=investigation.id,
            tool=spec.tool,
            purpose=spec.purpose,
            status="running",
            round=round_num,
        )
        session.add(task)
        session.flush()  # assign task.id before the Artifact FK references it
        publish_event(investigation.id, "task_started", task_id=task.id, tool=spec.tool, purpose=spec.purpose)

        try:
            content, source, artifact_type, success = _execute_task(
                spec.tool, spec.purpose, investigation.patient_id, role=investigation.role
            )
        except NotImplementedError as e:
            task.status = "skipped"
            content, source, artifact_type, success = {"error": str(e)}, None, "sql_result", False
        except Exception as e:
            # A tool that exists can still fail at execution time - most
            # importantly a DB-level RBAC denial (Section 2.2): the Planner is
            # told a role-scoped tool list, but that's a prompt instruction,
            # not a guarantee (confirmed directly: the Planner planned a
            # timeline task for an insurance_adjuster investigation despite
            # being told not to). The real enforcement is the DB permission/RLS
            # boundary underneath, which raises here rather than silently
            # filtering - must fail this one task gracefully, not crash the
            # whole investigation.
            task.status = "failed"
            content, source, artifact_type, success = (
                {"error": f"{type(e).__name__}: {e}", "success": False},
                None,
                ARTIFACT_TYPE_FOR_TOOL.get(spec.tool, "sql_result"),
                False
            )

        if content is not None:
            artifact = Artifact(
                investigation_id=investigation.id,
                task_id=task.id,
                type=artifact_type,
                content=content,
                source=source,
                created_at=datetime.now(timezone.utc),
            )
            session.add(artifact)
            new_artifacts.append(artifact)

        if task.status != "skipped":
            task.status = "complete" if success else "failed"

        publish_event(investigation.id, "task_finished", task_id=task.id, tool=spec.tool, status=task.status)

    session.flush()
    return new_artifacts


def run_investigation(
    question: str, patient_id: str | None = None, role: str | None = None, investigation_id: str | None = None
) -> Investigation:
    """investigation_id: normally left to auto-generate, but the Phase 6 API
    (app/api/main.py) pre-assigns one before submitting to Celery, so it can
    return the id to the client immediately - otherwise the id doesn't exist
    until this function actually starts running inside the worker, and the
    client would have nothing to subscribe a progress WebSocket to yet.
    """
    if role is not None and role not in TOOLS_FOR_ROLE:
        raise ValueError(f"unknown role: {role!r}, must be one of {list(TOOLS_FOR_ROLE)} or None")

    session = SessionLocal()
    investigation = None
    try:
        investigation = Investigation(
            id=investigation_id or str(uuid.uuid4()),
            patient_id=patient_id,
            role=role,
            question=question,
            status="running",
            created_at=datetime.now(timezone.utc),
        )
        session.add(investigation)
        session.flush()
        publish_event(investigation.id, "investigation_started", question=question)

        plan = generate_plan(question, patient_id=patient_id, role=role)
        investigation.goal = plan.goal
        publish_event(
            investigation.id, "plan_ready", goal=plan.goal,
            tasks=[{"tool": t.tool, "purpose": t.purpose} for t in plan.tasks],
        )

        all_artifacts = _run_tasks(session, investigation, plan.tasks, round_num=0)

        evidence_complete = True
        for round_num in range(1, MAX_REVIEW_ROUNDS + 1):
            decision = review_evidence(plan.goal, _summarize_artifacts(all_artifacts), role=investigation.role)
            if decision.sufficient:
                evidence_complete = True
                break
            if not decision.additional_tasks:
                # marked insufficient but nothing actionable proposed - stop here,
                # honestly reflecting that the gap was never actually filled
                evidence_complete = False
                break
            investigation.status = "needs_more_evidence"
            new_artifacts = _run_tasks(session, investigation, decision.additional_tasks, round_num=round_num)
            all_artifacts.extend(new_artifacts)
            evidence_complete = False  # only true again if a later review says sufficient
        else:
            # cap hit (loop completed without an early sufficient=True break)
            final_decision = review_evidence(plan.goal, _summarize_artifacts(all_artifacts), role=investigation.role)
            evidence_complete = final_decision.sufficient

        executive_summary = _generate_executive_summary(
            question, plan.goal, _summarize_artifacts(all_artifacts)
        )
        timeline_artifact = next((a for a in all_artifacts if a.type == "timeline"), None)
        prediction_artifact = next((a for a in all_artifacts if a.type == "prediction"), None)
        visualization_artifacts = [
            a.content for a in all_artifacts if a.type == "visualization" and a.content.get("applicable")
        ]
        sections = {
            "investigation_timeline": timeline_artifact.content if timeline_artifact else None,
            "evidence": [
                {"artifact_id": a.id, "type": a.type, "source": a.source, "content": a.content}
                for a in all_artifacts
            ],
            "supporting_literature": [],  # Literature tool: real corpus blocked on a PubMed outage
            "visualizations": visualization_artifacts,
            "prediction": prediction_artifact.content if prediction_artifact else None,
            "references": [],
        }
        report = Report(
            investigation_id=investigation.id,
            executive_summary=executive_summary,
            sections=sections,
            evidence_complete=evidence_complete,
            generated_at=datetime.now(timezone.utc),
        )
        session.add(report)

        investigation.status = "complete"
        investigation.evidence_complete = evidence_complete
        session.commit()
        publish_event(
            investigation.id, "investigation_complete",
            evidence_complete=evidence_complete, executive_summary=executive_summary,
        )

        session.refresh(investigation)
        # load before session closes - task.artifact is its own lazy relationship,
        # not implied by touching investigation.artifacts (found via a test that
        # accessed it on a failed task after the session had already closed)
        _ = investigation.tasks, investigation.artifacts, investigation.report
        for task in investigation.tasks:
            _ = task.artifact
        return investigation
    except Exception as e:
        session.rollback()
        if investigation is not None and investigation.id is not None:
            publish_event(investigation.id, "investigation_failed", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        session.close()
