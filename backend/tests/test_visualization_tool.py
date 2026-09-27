"""Visualization Tool tests. Metric selection uses one small LLM call (grounded
in the patient's real available metrics), so these require OPENAI_API_KEY -
gated the same way as the SQL Tool / orchestrator tests.
"""

import pytest

from app.core.config import settings
from app.tools.visualization_tool import _list_available_metrics, build_chart_for_patient

requires_openai_key = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)

# Verified against real data before writing anything: exactly 10 creatinine
# readings for this patient, code 38483-4.
PATIENT_ID = "23d14605-b881-65ba-c09e-0ecf40ebfeff"
NONEXISTENT_PATIENT_ID = "00000000-0000-0000-0000-000000000000"


class TestListAvailableMetrics:
    def test_known_patient_has_creatinine_with_enough_readings(self):
        metrics = _list_available_metrics(PATIENT_ID)
        creatinine = next((m for m in metrics if m["CODE"] == "38483-4"), None)
        assert creatinine is not None
        assert creatinine["n"] == 10

    def test_nonexistent_patient_has_no_metrics(self):
        assert _list_available_metrics(NONEXISTENT_PATIENT_ID) == []


@requires_openai_key
class TestBuildChartForPatient:
    def test_creatinine_request_selects_the_right_metric(self):
        result = build_chart_for_patient(PATIENT_ID, "Chart this patient's creatinine trend over time")
        assert result.applicable is True
        assert result.metric_code == "38483-4"
        assert result.units == "mg/dL"

    def test_data_points_match_verified_ground_truth(self):
        result = build_chart_for_patient(PATIENT_ID, "Chart this patient's creatinine trend over time")
        assert len(result.data_points) == 10
        values = [p["value"] for p in result.data_points]
        assert values == [3.0, 3.3, 2.8, 3.1, 3.3, 3.1, 2.9, 2.6, 3.4, 3.4]

    def test_returns_a_valid_plotly_figure(self):
        result = build_chart_for_patient(PATIENT_ID, "Chart this patient's creatinine trend over time")
        assert "data" in result.plotly_figure
        assert "layout" in result.plotly_figure
        assert result.plotly_figure["data"][0]["type"] == "scatter"

    def test_irrelevant_request_is_not_applicable(self):
        result = build_chart_for_patient(PATIENT_ID, "Chart this patient's recent vacation photos")
        assert result.applicable is False
        assert result.reason is not None

    def test_nonexistent_patient_is_not_applicable(self):
        result = build_chart_for_patient(NONEXISTENT_PATIENT_ID, "Chart anything for this patient")
        assert result.applicable is False
