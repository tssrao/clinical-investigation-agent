"""Phase 2 orchestrator: runs the real Planner -> execute -> Reviewer loop (design
doc 3.1/3.3) synchronously (async/Celery wrapping is Phase 6) and persists the
Investigation/Task/Artifact/Report domain model (3.4) as it goes.

Loop shape, exactly per 3.3: Planner emits the initial plan as its one action (no
implicit tool-call looping) -> orchestrator executes each task deterministically,
one Artifact per task -> Reviewer evaluates the Artifact set for sufficiency ->
if insufficient, Reviewer's proposed tasks are executed as another round, capped
at 2 extra rounds total -> Report Tool assembles the final Report from every
Artifact in the store, setting evidence_complete=false if the cap was hit while
still insufficient (never loops indefinitely).
"""

import json
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
from app.tools.interaction_tool import check_drug_interactions
from app.tools.prediction_tool import predict_readmission_risk
from app.tools.sql_tool import run_sql_tool
from app.tools.timeline_tool import build_timeline

MAX_REVIEW_ROUNDS = 2


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


def _execute_task(tool: str, purpose: str, patient_id: str | None) -> tuple[dict, str | None, str, bool]:
    """Returns (content, source, artifact_type, success). Only 'sql' is real
    right now (Phase 1) - see planner.AVAILABLE_TOOLS.
    """
    if tool == "sql":
        question = purpose + (f" (for patient id: {patient_id})" if patient_id else "")
        result = run_sql_tool(question)
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
        result = build_timeline(patient_id)
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

    raise NotImplementedError(f"tool '{tool}' is not implemented yet (Phase 3)")


def _summarize_artifacts(artifacts: list[Artifact]) -> str:
    lines = []
    for a in artifacts:
        content = a.content
        if a.type == "sql_result":
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
                    "support. No markdown, no bullet points, plain prose."
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

        try:
            content, source, artifact_type, success = _execute_task(spec.tool, spec.purpose, investigation.patient_id)
        except NotImplementedError as e:
            task.status = "skipped"
            content, source, artifact_type, success = {"error": str(e)}, None, "sql_result", False

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

    session.flush()
    return new_artifacts


def run_investigation(question: str, patient_id: str | None = None, role: str | None = None) -> Investigation:
    session = SessionLocal()
    try:
        investigation = Investigation(
            patient_id=patient_id,
            role=role,
            question=question,
            status="running",
            created_at=datetime.now(timezone.utc),
        )
        session.add(investigation)
        session.flush()

        plan = generate_plan(question, patient_id=patient_id, role=role)
        investigation.goal = plan.goal

        all_artifacts = _run_tasks(session, investigation, plan.tasks, round_num=0)

        evidence_complete = True
        for round_num in range(1, MAX_REVIEW_ROUNDS + 1):
            decision = review_evidence(plan.goal, _summarize_artifacts(all_artifacts))
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
            final_decision = review_evidence(plan.goal, _summarize_artifacts(all_artifacts))
            evidence_complete = final_decision.sufficient

        executive_summary = _generate_executive_summary(
            question, plan.goal, _summarize_artifacts(all_artifacts)
        )
        timeline_artifact = next((a for a in all_artifacts if a.type == "timeline"), None)
        prediction_artifact = next((a for a in all_artifacts if a.type == "prediction"), None)
        sections = {
            "investigation_timeline": timeline_artifact.content if timeline_artifact else None,
            "evidence": [
                {"artifact_id": a.id, "type": a.type, "source": a.source, "content": a.content}
                for a in all_artifacts
            ],
            "supporting_literature": [],  # Literature tool: not yet built
            "visualizations": [],  # Visualization tool: not yet built
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

        session.refresh(investigation)
        _ = investigation.tasks, investigation.artifacts, investigation.report  # load before session closes
        return investigation
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
