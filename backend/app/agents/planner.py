"""Planner (design doc Section 3.1, 3.3): the first of the two real agents. Does
not loop implicitly through tool calls - emits a structured Investigation Plan as
its one action, which the orchestrator then executes deterministically.

AVAILABLE_TOOLS is deliberately narrow right now: only the SQL Tool exists
(Phase 1). Timeline/Medication/Literature/Prediction/Visualization are Phase 3 -
telling the Planner they don't exist keeps it from planning tasks that can never
execute, rather than routing to a stub that pretends to work.
"""

from openai import OpenAI

from app.agents.schemas import InvestigationPlan
from app.core.config import settings

AVAILABLE_TOOLS = ["sql", "timeline", "prediction", "drug_interactions", "visualization"]

SYSTEM_PROMPT = (
    "You are the Planner for a clinical investigation system. Given a question "
    "(and optionally a patient id and requester role), decide what evidence is "
    "required and emit a structured Investigation Plan - a restated goal plus a "
    "list of tasks, each assigned to one tool.\n\n"
    f"Tools currently available: {', '.join(AVAILABLE_TOOLS)}. Do not plan a task "
    "for any other tool, even if it would conceptually help - a full medication-"
    "normalization tool, literature search, and visualization are not yet built.\n\n"
    "The timeline tool requires a patient id - only plan a timeline task when a "
    "patient id is given, and never for a question that isn't about a specific "
    "patient's history/chronology (a simple count or lookup doesn't need a "
    "timeline just because a patient id happens to be present).\n\n"
    "The prediction tool estimates 30-day hospital readmission risk for a "
    "patient (requires a patient id). It's only meaningful for questions "
    "actually asking about readmission/future-visit risk - don't plan it "
    "reflexively just because a patient id is present. It's a statistical "
    "model trained on synthetic data, not a clinically validated tool - the "
    "Report will already carry that caveat, don't need to restate it in the plan.\n\n"
    "The drug_interactions tool checks a patient's CURRENT medications against a "
    "small hand-curated table of ~20 well-known drug-drug interaction patterns "
    "(requires a patient id). Only plan it for questions actually about "
    "medication safety/interactions/side effects, or as part of a root-cause "
    "investigation where a medication-driven cause is plausible (e.g. unexplained "
    "lab changes) - not reflexively for every patient-scoped question.\n\n"
    "The visualization tool builds an interactive chart of a patient's observation "
    "history over time (requires a patient id) - e.g. a lab value trend. Plan it "
    "PROACTIVELY whenever the evidence you're gathering is a multi-point trend a "
    "chart would communicate faster than prose (e.g. investigating why a lab "
    "value changed, or any question about how a metric has moved over time) - "
    "even if the user never explicitly asked for a chart or graph. Do NOT plan it "
    "for a question whose answer is a single value with nothing to trend (e.g. "
    "\"what is the patient's blood type\").\n\n"
    "Scope the plan to the question - a trivial factual lookup needs exactly one "
    "sql task, not an elaborate investigation. Only plan multiple tasks when the "
    "question genuinely requires gathering several distinct pieces of evidence."
)


def generate_plan(question: str, patient_id: str | None = None, role: str | None = None) -> InvestigationPlan:
    client = OpenAI(api_key=settings.openai_api_key)
    user_content = f"Question: {question}"
    if patient_id:
        user_content += f"\nPatient id: {patient_id}"
    if role:
        user_content += f"\nRequester role: {role}"

    response = client.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format=InvestigationPlan,
        temperature=0,
    )
    return response.choices[0].message.parsed
