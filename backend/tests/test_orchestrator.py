"""Phase 2 orchestrator tests. Deterministic helpers run unconditionally; the
full Planner->Reviewer loop needs a real OPENAI_API_KEY, so those are skipped
without one (same pattern as test_sql_tool.py).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.agents.orchestrator import _json_safe, _summarize_artifacts, run_investigation
from app.core.config import settings
from app.db.models.investigation import Artifact
from app.db.session import SessionLocal

requires_openai_key = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)


class TestJsonSafe:
    def test_decimal_becomes_float(self):
        assert _json_safe(Decimal("132458.23")) == 132458.23

    def test_date_becomes_isoformat_string(self):
        assert _json_safe(date(1965, 8, 29)) == "1965-08-29"

    def test_datetime_becomes_isoformat_string(self):
        d = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert _json_safe(d) == d.isoformat()

    def test_recurses_into_nested_dicts_and_lists(self):
        value = {"rows": [{"amount": Decimal("10.50"), "when": date(2020, 1, 1)}]}
        result = _json_safe(value)
        assert result == {"rows": [{"amount": 10.50, "when": "2020-01-01"}]}

    def test_plain_values_pass_through(self):
        assert _json_safe({"a": 1, "b": "text", "c": None}) == {"a": 1, "b": "text", "c": None}


class TestSummarizeArtifacts:
    def test_empty_list(self):
        assert "no evidence" in _summarize_artifacts([])

    def test_sql_result_summary_includes_sql_and_row_count(self):
        artifact = Artifact(
            id="a1", investigation_id="i1", task_id="t1", type="sql_result",
            content={"sql": "SELECT 1", "row_count": 1, "rows": [{"n": 1}]},
            source="SELECT 1", created_at=datetime.now(timezone.utc),
        )
        summary = _summarize_artifacts([artifact])
        assert "SELECT 1" in summary
        assert "1 row(s)" in summary


@requires_openai_key
class TestRunInvestigationIntegration:
    def test_trivial_question_gets_scoped_plan(self):
        """Routing discipline (design doc 3.6): a trivial fact lookup should get
        a small plan, not an elaborate multi-task investigation.
        """
        inv = run_investigation("How many patients are there in total?")
        assert inv.status == "complete"
        assert 1 <= len(inv.tasks) <= 2
        assert inv.report is not None
        assert inv.report.executive_summary

    def test_investigation_persists_and_is_queryable_from_a_fresh_session(self):
        """Prove persistence actually round-trips through Postgres, not just
        that the in-memory object looks right.
        """
        inv = run_investigation("How many patients are there in total?")
        investigation_id = inv.id

        session = SessionLocal()
        try:
            from app.db.models.investigation import Investigation
            reloaded = session.get(Investigation, investigation_id)
            assert reloaded is not None
            assert reloaded.status == "complete"
            assert len(reloaded.tasks) >= 1
            assert reloaded.report is not None
            # every task has exactly one artifact, and every artifact traces
            # back to the task that produced it (design doc 3.5)
            for task in reloaded.tasks:
                if task.status == "complete":
                    assert task.artifact is not None
                    assert task.artifact.task_id == task.id
        finally:
            session.close()

    def test_loop_terminates_within_the_round_cap(self):
        """A question likely to need multiple pieces of evidence must still
        terminate (complete status, a Report exists) rather than loop forever -
        regardless of whether the Reviewer ever reaches sufficient=true.
        """
        inv = run_investigation(
            "What conditions does this patient have, and what medications are "
            "they taking to treat those conditions?",
            patient_id="23d14605-b881-65ba-c09e-0ecf40ebfeff",
        )
        assert inv.status == "complete"
        assert inv.report is not None
        # round is never allowed past the hard cap (design doc 3.3: 2 extra rounds)
        assert all(t.round <= 2 for t in inv.tasks)

    def test_timeline_question_plans_a_timeline_task_and_populates_report(self):
        inv = run_investigation(
            "Give me a chronological timeline of this patient's care history.",
            patient_id="23d14605-b881-65ba-c09e-0ecf40ebfeff",
        )
        assert inv.status == "complete"
        assert any(t.tool == "timeline" for t in inv.tasks)
        timeline_section = inv.report.sections["investigation_timeline"]
        assert timeline_section is not None
        assert timeline_section["event_count"] > 0
        # chronological, and the very first known event for this patient
        assert timeline_section["events"][0]["event_date"] == "1958-05-31T22:17:10+00:00"
