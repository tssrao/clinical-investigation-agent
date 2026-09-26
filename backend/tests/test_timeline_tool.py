"""Timeline Tool tests. No LLM involved (design doc: Timeline/Medication/
Prediction call no LLM at all), so every test here is fully deterministic
against the real DB - no OPENAI_API_KEY gating needed.
"""

from app.tools.timeline_tool import build_timeline

PATIENT_ID = "23d14605-b881-65ba-c09e-0ecf40ebfeff"  # same rich test patient as the SQL Tool golden set
NONEXISTENT_PATIENT_ID = "00000000-0000-0000-0000-000000000000"

KNOWN_CATEGORIES = {
    "encounter", "condition", "medication_started", "medication_stopped",
    "procedure", "immunization", "allergy", "careplan_started", "observation",
}


class TestBuildTimeline:
    def test_nonexistent_patient_returns_empty_timeline(self):
        result = build_timeline(NONEXISTENT_PATIENT_ID)
        assert result.event_count == 0
        assert result.events == []

    def test_real_patient_returns_events(self):
        result = build_timeline(PATIENT_ID)
        assert result.event_count > 0
        assert len(result.events) == result.event_count

    def test_events_are_chronologically_sorted(self):
        result = build_timeline(PATIENT_ID)
        dates = [e.event_date for e in result.events]
        assert dates == sorted(dates)

    def test_every_event_has_a_known_category_and_nonempty_description(self):
        result = build_timeline(PATIENT_ID)
        for e in result.events:
            assert e.category in KNOWN_CATEGORIES
            assert e.description

    def test_observations_exclude_text_type_survey_fields(self):
        """The known dataset constraint: TYPE == 'text' observations are
        structured survey fields, not clinical data - confirm the timeline's
        observation count matches a direct non-text-only count, not the raw
        observations table total (which includes text-type rows).
        """
        result = build_timeline(PATIENT_ID)
        observation_events = [e for e in result.events if e.category == "observation"]
        assert len(observation_events) == 445  # verified independently via direct SQL

    def test_date_window_filters_events(self):
        full = build_timeline(PATIENT_ID)
        windowed = build_timeline(PATIENT_ID, start_date="2020-01-01", end_date="2020-12-31")
        assert windowed.event_count < full.event_count
        assert windowed.event_count > 0
        for e in windowed.events:
            assert "2020-01-01" <= e.event_date <= "2020-12-31T23:59:59"

    def test_medication_started_events_include_reason_when_present(self):
        result = build_timeline(PATIENT_ID)
        simvastatin_events = [
            e for e in result.events
            if e.category == "medication_started" and "Simvastatin" in e.description
        ]
        assert simvastatin_events
        assert any("Hyperlipidemia" in e.description for e in simvastatin_events)
