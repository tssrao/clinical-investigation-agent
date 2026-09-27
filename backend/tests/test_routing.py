"""Phase 5: routing discipline (design doc 3.6) - "A planner that over-invokes
tools on simple questions shows worse judgment than one that scopes its
investigation to the question asked. This is an explicit design requirement,
not an afterthought."

This is a dedicated, systematic pass across the question-complexity spectrum,
distinct from the per-tool routing checks scattered through
test_orchestrator.py (which verify "does the right tool get chosen", not "does
plan SIZE correctly track question complexity" as its own concern).

Two of the design doc's own example rows can't be replicated exactly right
now: "Simple structured list -> SQL + Medication" and the full pipeline
example's Literature step - Medication is blocked on the RxNorm/UMLS license,
Literature's real corpus is blocked on a PubMed outage (see CLAUDE.md). Tests
here verify the underlying PRINCIPLE (scope tools to the question) with the
tools actually available: sql, timeline, prediction, drug_interactions,
visualization.

All tests require OPENAI_API_KEY (real Planner calls).
"""

import pytest

from app.agents.orchestrator import run_investigation
from app.core.config import settings

requires_openai_key = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)

RICH_PATIENT = "23d14605-b881-65ba-c09e-0ecf40ebfeff"  # verified: conditions, meds, allergies, 10 creatinine readings
TRIPLE_WHAMMY_PATIENT = "1050cd48-cb09-1f0e-8441-4d587e8bcb2f"  # verified: real drug interaction match
INPATIENT_PATIENT = "02459150-c160-03b6-f1dd-dae8fd4b3e84"  # verified: has an inpatient encounter


@requires_openai_key
class TestFastPathQuestionsStayMinimal:
    """Trivial/terminology-lookup questions (design doc 3.6 row 1 & 3) must not
    balloon into a multi-tool investigation just because a patient id or a
    lookup table happens to be involved.
    """

    def test_trivial_count_question_is_one_task(self):
        inv = run_investigation("How many patients are there in total?")
        assert len(inv.tasks) == 1
        assert inv.tasks[0].tool == "sql"

    def test_trivial_single_value_question_about_a_patient_is_minimal(self):
        inv = run_investigation(
            "What is this patient's gender?", patient_id=RICH_PATIENT
        )
        assert len(inv.tasks) <= 2  # sql, maybe +1 reviewer-requested round at most
        assert all(t.tool == "sql" for t in inv.tasks)
        # a single categorical value has nothing to chart (design doc 3.6)
        assert inv.report.sections["visualizations"] == []

    def test_terminology_lookup_is_sql_only(self):
        """No separate "lookup tool" exists in this architecture - LOINC is a
        plain Postgres table, so a terminology question is still routed
        through sql, just a cheap, targeted one. The discipline being tested
        is that it doesn't ALSO plan a timeline/prediction/etc task.
        """
        inv = run_investigation("What does LOINC code 8480-6 mean?")
        assert all(t.tool == "sql" for t in inv.tasks)
        assert len(inv.tasks) <= 2

    def test_simple_list_question_does_not_trigger_unrelated_tools(self):
        inv = run_investigation(
            "List this patient's current medications.", patient_id=RICH_PATIENT
        )
        # sql is expected (and legitimate); prediction/drug_interactions/timeline
        # are NOT relevant to "just list them" - only sql (+ maybe visualization,
        # which also wouldn't make sense for a plain list) should appear
        assert all(t.tool == "sql" for t in inv.tasks)


@requires_openai_key
class TestFullInvestigationsUseMultipleTools:
    """The flip side of the discipline: a question that genuinely needs
    several distinct pieces of evidence should NOT get artificially
    restricted to one tool either - scoping means matching effort to the
    question in both directions.
    """

    def test_causal_investigation_uses_more_than_one_tool(self):
        inv = run_investigation(
            "Why has this patient's creatinine level been elevated over the years?",
            patient_id=RICH_PATIENT,
        )
        tools_used = {t.tool for t in inv.tasks}
        assert len(tools_used) >= 2

    def test_medication_safety_investigation_reaches_for_drug_interactions(self):
        inv = run_investigation(
            "Check this patient's current medications for any dangerous interactions.",
            patient_id=TRIPLE_WHAMMY_PATIENT,
        )
        assert any(t.tool == "drug_interactions" for t in inv.tasks)

    def test_readmission_investigation_reaches_for_prediction(self):
        inv = run_investigation(
            "Assess this patient's risk of being readmitted within 30 days.",
            patient_id=INPATIENT_PATIENT,
        )
        assert any(t.tool == "prediction" for t in inv.tasks)


@requires_openai_key
class TestProactiveVisualization:
    """Design doc 3.6: the Planner inserts a Visualization task whenever
    evidence is a multi-point trend a chart communicates faster than prose -
    EVEN IF the user never asked for a chart or graph. And the converse: no
    visualization task when there's nothing to trend.
    """

    def test_trend_question_gets_a_chart_without_being_asked(self):
        inv = run_investigation(
            "How has this patient's creatinine level changed over the years?",
            patient_id=RICH_PATIENT,
        )
        assert any(t.tool == "visualization" for t in inv.tasks)
        assert "chart" not in "how has this patient's creatinine level changed over the years?"
        assert len(inv.report.sections["visualizations"]) >= 1

    def test_single_categorical_value_question_gets_no_chart(self):
        inv = run_investigation(
            "What is this patient's blood type?", patient_id=RICH_PATIENT
        )
        assert not any(t.tool == "visualization" for t in inv.tasks)
        assert inv.report.sections["visualizations"] == []

    def test_trivial_count_question_gets_no_chart(self):
        inv = run_investigation("How many patients are there in total?")
        assert not any(t.tool == "visualization" for t in inv.tasks)
