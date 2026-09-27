"""Prediction Tool + feature extraction tests. No LLM involved (same rule as
Timeline), so these are fully deterministic against the real DB and the real
MLflow-served model - no OPENAI_API_KEY needed. Requires the local MLflow
server running (see README "MLflow" section) since the model is loaded via the
"champion" registry alias, not bundled into the repo.
"""

import pytest

from app.ml.readmission_features import FEATURE_COLUMNS, extract_features_for_patient, extract_training_data
from app.tools import prediction_tool
from app.tools.prediction_tool import predict_readmission_risk

PATIENT_WITH_INPATIENT_HISTORY = "02459150-c160-03b6-f1dd-dae8fd4b3e84"
PATIENT_WITHOUT_INPATIENT_HISTORY = "23d14605-b881-65ba-c09e-0ecf40ebfeff"  # verified: no inpatient encounters
NONEXISTENT_PATIENT_ID = "00000000-0000-0000-0000-000000000000"


class TestExtractFeatures:
    def test_training_data_matches_verified_label_balance(self):
        """Verified independently via direct SQL before this module existed:
        2,433 inpatient encounters, 502 (20.6%) readmitted within 30 days.
        """
        df = extract_training_data()
        assert len(df) == 2433
        assert df["readmitted_within_30d"].sum() == 502

    def test_training_data_has_no_nulls_in_feature_columns(self):
        df = extract_training_data()
        assert df[FEATURE_COLUMNS].isna().sum().sum() == 0

    def test_patient_without_inpatient_history_returns_none(self):
        assert extract_features_for_patient(PATIENT_WITHOUT_INPATIENT_HISTORY) is None

    def test_patient_with_inpatient_history_returns_features(self):
        features = extract_features_for_patient(PATIENT_WITH_INPATIENT_HISTORY)
        assert features is not None
        for col in FEATURE_COLUMNS:
            assert col in features
        assert features["prior_inpatient_count"] == 0  # verified: this was their first inpatient stay


class TestPredictReadmissionRisk:
    def test_patient_without_inpatient_history_is_not_applicable(self):
        result = predict_readmission_risk(PATIENT_WITHOUT_INPATIENT_HISTORY)
        assert result.applicable is False
        assert result.risk_score is None
        assert "no inpatient encounter" in result.reason.lower()

    def test_nonexistent_patient_is_not_applicable_not_an_error(self):
        result = predict_readmission_risk(NONEXISTENT_PATIENT_ID)
        assert result.applicable is False
        assert result.reason is not None

    def test_patient_with_inpatient_history_gets_a_real_score(self):
        result = predict_readmission_risk(PATIENT_WITH_INPATIENT_HISTORY)
        assert result.applicable is True
        assert 0.0 <= result.risk_score <= 1.0
        assert result.reference_encounter_id is not None
        assert result.features is not None

    def test_prediction_is_deterministic_across_calls(self):
        """Same patient, same underlying data, same model version -> same score.
        Guards against accidental nondeterminism (e.g. feature order drift).
        """
        r1 = predict_readmission_risk(PATIENT_WITH_INPATIENT_HISTORY)
        r2 = predict_readmission_risk(PATIENT_WITH_INPATIENT_HISTORY)
        assert r1.risk_score == r2.risk_score

    def test_unreachable_mlflow_server_fails_fast_not_hangs(self, monkeypatch):
        """Regression test for a real incident during development: a dead/
        unreachable MLflow server made mlflow.sklearn.load_model hang silently
        for 20+ minutes with no timeout. monkeypatch restores the real tracking
        URI and cached model after this test, so it can't affect any other test.
        """
        monkeypatch.setattr(prediction_tool, "MLFLOW_TRACKING_URI", "http://127.0.0.1:59999")
        monkeypatch.setattr(prediction_tool, "_model", None)
        with pytest.raises(prediction_tool.MLflowUnavailableError):
            prediction_tool._get_model()
