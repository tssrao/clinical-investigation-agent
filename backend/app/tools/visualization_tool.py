"""Visualization Tool (design doc 4.1, 3.6): builds an interactive Plotly chart
from a patient's observation history. Output is Plotly's own JSON figure spec
(fig.to_dict()) - directly consumable by react-plotly.js later with zero
translation, not a rendered image and not a bespoke schema.

General-purpose by design: works for ANY numeric observation metric with
multiple readings (creatinine, blood pressure, weight, HbA1c, ...), not
hardcoded to one. Since a task's "purpose" is plain language (e.g. "chart this
patient's creatinine trend"), one small LLM call maps that to a real observation
code - grounded in the patient's ACTUAL available metrics (queried first), so
it can never hallucinate a metric that doesn't exist for this patient. This is
the same "LLM + real schema" pattern the SQL Tool already uses, not a
freestanding guess.

*Which* tasks warrant a chart in the first place is the Planner's job (design
doc 3.6 routing discipline) - this tool doesn't decide whether to run, only
what to chart once asked.
"""

from dataclasses import dataclass, field

import plotly.graph_objects as go
from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine

MIN_READINGS_FOR_TREND = 3


class _MetricSelection(BaseModel):
    code: str | None  # None if nothing in the available list is actually relevant
    reasoning: str


@dataclass
class VisualizationResult:
    applicable: bool
    metric_code: str | None = None
    metric_description: str | None = None
    units: str | None = None
    data_points: list[dict] = field(default_factory=list)  # [{"date": iso, "value": float}, ...]
    plotly_figure: dict | None = None
    reason: str | None = None  # set when applicable=False


def _list_available_metrics(patient_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT "CODE", "DESCRIPTION", "UNITS", count(*) as n FROM observations '
                'WHERE "PATIENT" = :patient_id AND "TYPE" != \'text\' '
                'GROUP BY "CODE", "DESCRIPTION", "UNITS" HAVING count(*) >= :min_n '
                'ORDER BY n DESC'
            ),
            {"patient_id": patient_id, "min_n": MIN_READINGS_FOR_TREND},
        ).mappings().all()
    return [dict(r) for r in rows]


def _select_metric(purpose: str, available_metrics: list[dict]) -> _MetricSelection:
    client = OpenAI(api_key=settings.openai_api_key)
    metrics_text = "\n".join(
        f"- code={m['CODE']!r}, description={m['DESCRIPTION']!r}, units={m['UNITS']}, readings={m['n']}"
        for m in available_metrics
    )
    response = client.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Given a chart request and a list of this patient's ACTUALLY available "
                    "observation metrics, pick the single best-matching code. If nothing in "
                    "the list is genuinely relevant to the request, return code=null - never "
                    "invent a code that isn't in the list."
                ),
            },
            {"role": "user", "content": f"Chart request: {purpose}\n\nAvailable metrics:\n{metrics_text}"},
        ],
        response_format=_MetricSelection,
        temperature=0,
    )
    return response.choices[0].message.parsed


def _fetch_data_points(patient_id: str, code: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT "DATE", "VALUE" FROM observations '
                'WHERE "PATIENT" = :patient_id AND "CODE" = :code '
                'ORDER BY "DATE"'
            ),
            {"patient_id": patient_id, "code": code},
        ).all()
    return [{"date": r[0].isoformat(), "value": float(r[1])} for r in rows]


def build_chart_for_patient(patient_id: str, purpose: str) -> VisualizationResult:
    available_metrics = _list_available_metrics(patient_id)
    if not available_metrics:
        return VisualizationResult(
            applicable=False,
            reason="Patient has no observation metric with enough repeated readings to chart a trend.",
        )

    selection = _select_metric(purpose, available_metrics)
    if selection.code is None:
        return VisualizationResult(
            applicable=False,
            reason=f"No available metric matched the request. {selection.reasoning}",
        )

    metric = next(m for m in available_metrics if m["CODE"] == selection.code)
    data_points = _fetch_data_points(patient_id, selection.code)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[p["date"] for p in data_points],
        y=[p["value"] for p in data_points],
        mode="lines+markers",
        name=metric["DESCRIPTION"],
    ))
    fig.update_layout(
        title=f"{metric['DESCRIPTION']} over time",
        xaxis_title="Date",
        yaxis_title=f"{metric['DESCRIPTION']} ({metric['UNITS']})" if metric["UNITS"] else metric["DESCRIPTION"],
        template="plotly_white",
    )

    return VisualizationResult(
        applicable=True,
        metric_code=selection.code,
        metric_description=metric["DESCRIPTION"],
        units=metric["UNITS"],
        data_points=data_points,
        plotly_figure=fig.to_dict(),
    )
