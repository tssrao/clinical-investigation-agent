"""Prediction Tool (design doc 4.1): calls the MLflow-tracked readmission risk
model as a service. Calls no LLM (same rule as Timeline/Medication) - this is a
model inference call, not a language-generation step.

Loads the model via the registry alias "champion" (set by
scripts/train_readmission_model.py) rather than a bare version number, so a
retrained/promoted model takes effect without any code change here.

Caveat from the design doc (§4.5), worth repeating at the call site: this
demonstrates the ML pipeline (features -> train -> MLflow-track -> serve)
working end-to-end, not a clinically validated tool - it's trained entirely on
synthetic Synthea data.
"""

from dataclasses import dataclass

import mlflow
import pandas as pd
import requests

from app.ml.readmission_features import FEATURE_COLUMNS, extract_features_for_patient

MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
MODEL_URI = "models:/readmission_model@champion"

_model = None  # lazily loaded and cached - avoid a network round-trip to MLflow on every call


class MLflowUnavailableError(RuntimeError):
    pass


def _get_model():
    global _model
    if _model is None:
        # mlflow.sklearn.load_model has no reliable bound on how long it hangs
        # against a dead server (confirmed directly - MLFLOW_HTTP_REQUEST_TIMEOUT
        # does NOT bound the artifact-download path, only some REST metadata
        # calls). A cheap pre-flight health check with its own short timeout
        # fails fast instead of hanging indefinitely, which happened for real
        # during development (a 20-minute silent hang after the local tracking
        # server was killed).
        try:
            requests.get(f"{MLFLOW_TRACKING_URI}/health", timeout=3)
        except requests.exceptions.RequestException as e:
            raise MLflowUnavailableError(
                f"MLflow tracking server unreachable at {MLFLOW_TRACKING_URI} - is it running?"
            ) from e

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        _model = mlflow.sklearn.load_model(MODEL_URI)
    return _model


@dataclass
class PredictionResult:
    patient_id: str
    applicable: bool
    risk_score: float | None = None
    reference_encounter_id: str | None = None
    features: dict | None = None
    reason: str | None = None  # set when applicable=False


def predict_readmission_risk(patient_id: str) -> PredictionResult:
    features = extract_features_for_patient(patient_id)
    if features is None:
        return PredictionResult(
            patient_id=patient_id,
            applicable=False,
            reason="Patient has no inpatient encounter on record - readmission risk isn't applicable.",
        )

    model = _get_model()
    feature_row = pd.DataFrame([{col: features[col] for col in FEATURE_COLUMNS}])
    risk_score = float(model.predict_proba(feature_row)[0][1])

    return PredictionResult(
        patient_id=patient_id,
        applicable=True,
        risk_score=risk_score,
        reference_encounter_id=features["encounter_id"],
        features={col: features[col] for col in FEATURE_COLUMNS},
    )
