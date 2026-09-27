"""Phase 4 RBAC tests. security.py/policy.py tests are fully deterministic (no
LLM) - the DB-level permission/RLS boundary doesn't need one. The orchestrator
integration tests do need OPENAI_API_KEY (real Planner/Reviewer calls), gated
the same way as every other orchestrator test.
"""

import time

import pytest
from jose import jwt as jose_jwt

from app.agents.orchestrator import run_investigation
from app.core.config import settings
from app.rbac.policy import TOOLS_FOR_ROLE, scoped_connection
from app.rbac.security import InvalidTokenError, create_access_token, decode_access_token

requires_openai_key = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)

DOCTOR_TEST_PATIENT = "23d14605-b881-65ba-c09e-0ecf40ebfeff"  # verified: 48 conditions, 25 medications
ADJUSTER_TEST_PATIENT = "1050cd48-cb09-1f0e-8441-4d587e8bcb2f"  # verified: real claims history


class TestSecurity:
    def test_round_trip(self):
        token = create_access_token("doctor", [DOCTOR_TEST_PATIENT])
        payload = decode_access_token(token)
        assert payload["role"] == "doctor"
        assert payload["patient_scope"] == [DOCTOR_TEST_PATIENT]

    def test_unknown_role_rejected_at_creation(self):
        with pytest.raises(ValueError):
            create_access_token("nurse", [DOCTOR_TEST_PATIENT])

    def test_tampered_token_rejected(self):
        token = create_access_token("doctor", [DOCTOR_TEST_PATIENT])
        tampered = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")
        with pytest.raises(InvalidTokenError):
            decode_access_token(tampered)

    def test_expired_token_rejected(self):
        # hand-craft an already-expired token rather than sleeping in a test
        payload = {"role": "doctor", "patient_scope": [DOCTOR_TEST_PATIENT], "exp": int(time.time()) - 60}
        expired_token = jose_jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(InvalidTokenError):
            decode_access_token(expired_token)

    def test_token_signed_with_wrong_secret_rejected(self):
        forged = jose_jwt.encode(
            {"role": "doctor", "patient_scope": [DOCTOR_TEST_PATIENT]},
            "wrong-secret", algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(forged)


class TestPolicy:
    def test_tools_for_role_covers_both_roles(self):
        assert set(TOOLS_FOR_ROLE.keys()) == {"doctor", "insurance_adjuster"}
        assert "timeline" in TOOLS_FOR_ROLE["doctor"]
        assert "timeline" not in TOOLS_FOR_ROLE["insurance_adjuster"]

    def test_doctor_scoped_connection_matches_known_ground_truth(self):
        from sqlalchemy import text
        with scoped_connection("doctor", [DOCTOR_TEST_PATIENT]) as conn:
            n = conn.execute(text('SELECT count(*) AS n FROM conditions')).scalar()
        assert n == 48

    def test_adjuster_denied_medications_table(self):
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError
        with pytest.raises(SQLAlchemyError):
            with scoped_connection("insurance_adjuster", [ADJUSTER_TEST_PATIENT]) as conn:
                conn.execute(text('SELECT * FROM medications'))

    def test_fails_closed_with_empty_scope(self):
        from sqlalchemy import text
        with scoped_connection("doctor", []) as conn:
            n = conn.execute(text('SELECT count(*) AS n FROM conditions')).scalar()
        assert n == 0

    def test_out_of_scope_patient_returns_zero_not_real_data(self):
        """The adversarial case: a doctor scoped to one patient explicitly
        querying a DIFFERENT real patient by id must get zero rows, not that
        patient's real (nonzero) data.
        """
        from sqlalchemy import text
        with scoped_connection("doctor", [DOCTOR_TEST_PATIENT]) as conn:
            n = conn.execute(
                text('SELECT count(*) AS n FROM conditions WHERE "PATIENT" = :other'),
                {"other": ADJUSTER_TEST_PATIENT},
            ).scalar()
        assert n == 0

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError):
            with scoped_connection("nurse", [DOCTOR_TEST_PATIENT]):
                pass


class TestOrchestratorRoleValidation:
    def test_unknown_role_rejected_before_any_llm_call(self):
        with pytest.raises(ValueError):
            run_investigation("anything", patient_id=DOCTOR_TEST_PATIENT, role="nurse")


@requires_openai_key
class TestOrchestratorRBACIntegration:
    def test_doctor_can_investigate_clinical_question(self):
        inv = run_investigation(
            "What conditions does this patient have?",
            patient_id=DOCTOR_TEST_PATIENT, role="doctor",
        )
        assert inv.status == "complete"
        assert any(t.status == "complete" for t in inv.tasks)

    def test_adjuster_asking_clinical_question_reports_restriction_not_false_negative(self):
        """Regression test for a real bug found during development: the
        executive summary once claimed 'the patient is not taking any
        medications' when the actual cause was an RBAC-denied query - a false
        negative, not a real finding. Must now report the restriction
        honestly instead of implying an absence.
        """
        inv = run_investigation(
            "What medications is this patient currently taking?",
            patient_id=DOCTOR_TEST_PATIENT, role="insurance_adjuster",
        )
        assert inv.status == "complete"
        summary_lower = inv.report.executive_summary.lower()
        # must not assert a false factual negative
        assert "not taking any medications" not in summary_lower
        assert "patient has none" not in summary_lower
        # should acknowledge the restriction/failure honestly
        assert any(
            phrase in summary_lower
            for phrase in ("restrict", "author", "could not retrieve", "denied", "unavailable", "unable to")
        )

    def test_adjuster_misrouted_clinical_tool_fails_safely_not_crashes(self):
        """The Planner is TOLD a role-scoped tool list, but that's a prompt
        instruction, not a guarantee - confirmed directly during development:
        it planned a timeline task for an insurance_adjuster investigation
        despite being told not to. The real safety property isn't "the
        Planner never misroutes" (it can), it's "even when it does, the DB-
        level boundary blocks it, the investigation completes without
        crashing, and no clinical data leaks into the Report."
        """
        inv = run_investigation(
            "Give me a full clinical history and timeline for this patient.",
            patient_id=DOCTOR_TEST_PATIENT, role="insurance_adjuster",
        )
        assert inv.status == "complete"  # must not crash even if misrouted
        for task in inv.tasks:
            if task.tool not in TOOLS_FOR_ROLE["insurance_adjuster"]:
                assert task.status == "failed"  # DB boundary blocked it
                assert task.artifact is not None
                assert "error" in task.artifact.content  # recorded, not silently swallowed
