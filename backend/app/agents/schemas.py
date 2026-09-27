"""Structured contracts for the Planner and Reviewer (design doc Section 3.3).
Both agents use OpenAI's structured-output parsing against these Pydantic models
(client.chat.completions.parse), not free-text JSON the app has to parse itself -
the API enforces the schema, so there's no "the LLM added markdown fences" class
of bug here the way there was for the SQL Tool's plain-text SQL output.
"""

from typing import Literal

from pydantic import BaseModel, Field

ToolName = Literal[
    "sql", "timeline", "medication", "literature", "prediction", "visualization", "drug_interactions"
]


class TaskSpec(BaseModel):
    tool: ToolName
    purpose: str = Field(description="What this task is meant to find out, in plain language")


class InvestigationPlan(BaseModel):
    goal: str = Field(description="A restatement of what the investigation is trying to establish")
    tasks: list[TaskSpec]


class ReviewDecision(BaseModel):
    sufficient: bool
    reasoning: str = Field(description="Why the evidence is or isn't sufficient to answer the goal")
    additional_tasks: list[TaskSpec] = Field(
        default_factory=list, description="Empty if sufficient=true"
    )
