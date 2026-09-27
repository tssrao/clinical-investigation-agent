"""Train the 30-day readmission risk model, tracked in MLflow (design doc
Phase 3 / 4.1 "Prediction Tool"). See app/ml/readmission_features.py for the
feature/label definitions this trains against.

Assumes a local MLflow tracking server is running (see README "MLflow" section
for the start command) - this is a pragmatic interim setup (a local process
from backend/.venv, not yet a docker-compose service like the other
infrastructure) documented as such, not silently different from what the docs
claim.

Model choice: RandomForestClassifier, class_weight="balanced" (20.6% positive
rate - not degenerate, but balanced weighting is a defensible default without
tuning). This is intentionally simple - proving the pipeline (extract -> train
-> track -> register -> serve) end-to-end matters more here than model
sophistication, per the design doc's own caveat that this demonstrates the
pipeline, not clinical validity.
"""

import io
import sys
from pathlib import Path

# MLflow prints an emoji in its post-run summary; Windows' default console
# codepage (cp1252) can't encode it and raises UnicodeEncodeError after the
# run has already completed successfully - force UTF-8 stdout to avoid that.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mlflow
import mlflow.sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

from app.ml.readmission_features import FEATURE_COLUMNS, LABEL_COLUMN, extract_training_data

MLFLOW_TRACKING_URI = "http://127.0.0.1:5000"
EXPERIMENT_NAME = "readmission_risk"
REGISTERED_MODEL_NAME = "readmission_model"


def main() -> None:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    df = extract_training_data()
    X = df[FEATURE_COLUMNS]
    y = df[LABEL_COLUMN]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    params = {"n_estimators": 200, "max_depth": 8, "class_weight": "balanced", "random_state": 42}

    with mlflow.start_run() as run:
        model = RandomForestClassifier(**params)
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]
        metrics = {
            "accuracy": accuracy_score(y_test, y_pred),
            "precision": precision_score(y_test, y_pred, zero_division=0),
            "recall": recall_score(y_test, y_pred, zero_division=0),
            "roc_auc": roc_auc_score(y_test, y_proba),
        }

        mlflow.log_params(params)
        mlflow.log_param("train_rows", len(X_train))
        mlflow.log_param("test_rows", len(X_test))
        mlflow.log_param("positive_rate", float(y.mean()))
        mlflow.log_metrics(metrics)
        model_info = mlflow.sklearn.log_model(
            model,
            name="model",
            registered_model_name=REGISTERED_MODEL_NAME,
            input_example=X_train.head(3),
        )

        # MLflow 3.x model registry uses aliases, not the old stage="latest"
        # syntax - "champion" is what the Prediction Tool loads at serve time,
        # so promoting a version to this alias is the actual "deploy" step.
        client = mlflow.MlflowClient()
        version = model_info.registered_model_version
        client.set_registered_model_alias(REGISTERED_MODEL_NAME, "champion", version)

        print(f"run_id: {run.info.run_id}")
        print(f"metrics: {metrics}")
        print(f"registered as {REGISTERED_MODEL_NAME} v{version}, alias 'champion'")


if __name__ == "__main__":
    main()
