"""Reviewer (design doc Section 3.1, 3.3): the second of the two real agents.
Evaluates the Artifact set attached to an Investigation (3.5) - not raw tool
output - and decides whether it's sufficient to answer the investigation's goal.
If not, it proposes additional tasks; the orchestrator caps this at 2 extra
rounds (3.3) regardless of what the Reviewer keeps asking for.
"""

from openai import OpenAI

from app.agents.planner import AVAILABLE_TOOLS
from app.agents.schemas import ReviewDecision
from app.core.config import settings

SYSTEM_PROMPT = (
    "You are the Reviewer for a clinical investigation system. Given the "
    "investigation's goal and the evidence (Artifacts) gathered so far, decide "
    "whether that evidence is sufficient to answer the goal. If not, propose "
    "specific additional tasks to fill the gap.\n\n"
    f"Tools currently available for additional tasks: {', '.join(AVAILABLE_TOOLS)}. "
    "Do not propose a tool that isn't in this list.\n\n"
    "Be a genuine check, not a rubber stamp: if the evidence already answers the "
    "goal, say sufficient=true with an empty additional_tasks list. If something "
    "concrete is missing, say sufficient=false and propose the specific task(s) "
    "that would fill the gap - don't propose vague or redundant tasks."
)


def review_evidence(goal: str, artifacts_summary: str) -> ReviewDecision:
    client = OpenAI(api_key=settings.openai_api_key)
    user_content = f"Investigation goal: {goal}\n\nEvidence gathered so far:\n{artifacts_summary}"

    response = client.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format=ReviewDecision,
        temperature=0,
    )
    return response.choices[0].message.parsed
