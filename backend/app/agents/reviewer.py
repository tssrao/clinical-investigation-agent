"""Reviewer (design doc Section 3.1, 3.3): the second of the two real agents.
Evaluates the Artifact set attached to an Investigation (3.5) - not raw tool
output - and decides whether it's sufficient to answer the investigation's goal.
If not, it proposes additional tasks; the orchestrator caps this at 2 extra
rounds (3.3) regardless of what the Reviewer keeps asking for.

Phase 4 RBAC: the Reviewer must be told the same role-scoped tool list as the
Planner (app.rbac.policy.TOOLS_FOR_ROLE) - otherwise it could propose an
additional-round task for a tool the requester's role doesn't have, even
though the Planner itself was correctly restricted. DB-level enforcement
(policy.scoped_connection) would still deny it if that happened, but this
stops the wrong task from being proposed at all.
"""

from openai import OpenAI

from app.agents.planner import ALL_TOOLS
from app.agents.schemas import ReviewDecision
from app.core.config import settings
from app.rbac.policy import TOOLS_FOR_ROLE


def _build_system_prompt(available_tools: list[str]) -> str:
    return (
        "You are the Reviewer for a clinical investigation system. Given the "
        "investigation's goal and the evidence (Artifacts) gathered so far, decide "
        "whether that evidence is sufficient to answer the goal. If not, propose "
        "specific additional tasks to fill the gap.\n\n"
        f"Tools available for additional tasks: {', '.join(available_tools)}. "
        "Do not propose a tool that isn't in this list.\n\n"
        "Be a genuine check, not a rubber stamp: if the evidence already answers the "
        "goal, say sufficient=true with an empty additional_tasks list. If something "
        "concrete is missing, say sufficient=false and propose the specific task(s) "
        "that would fill the gap - don't propose vague or redundant tasks."
    )


def review_evidence(goal: str, artifacts_summary: str, role: str | None = None) -> ReviewDecision:
    available_tools = TOOLS_FOR_ROLE[role] if role else ALL_TOOLS
    system_prompt = _build_system_prompt(available_tools)

    client = OpenAI(api_key=settings.openai_api_key)
    user_content = f"Investigation goal: {goal}\n\nEvidence gathered so far:\n{artifacts_summary}"

    response = client.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format=ReviewDecision,
        temperature=0,
    )
    return response.choices[0].message.parsed
