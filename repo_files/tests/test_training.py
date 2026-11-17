import json

import numpy as np
import pytest

from core.evaluation import attendance_interval, choose_threshold, classification_metrics, majority_baseline
from core.model_training import grouped_holdout_split, reproduce_original_result
from core.preprocessing import prepare_frame


def test_historical_result_reproduced_exactly(raw_ml):
    r = reproduce_original_result(raw_ml)
    assert r["n_test"] == 77
    assert round(r["accuracy"] * 100, 2) == 67.53
    assert round(r["roc_auc"], 4) == 0.7932
    assert r["confusion_matrix"] == [[9, 15], [10, 43]]
    assert round(r["majority_baseline_accuracy"] * 100, 2) == 68.83


def test_grouped_holdout_has_no_student_overlap(raw_ml):
    df = prepare_frame(raw_ml)
    train, test = grouped_holdout_split(df)
    assert not set(train.Student_ID) & set(test.Student_ID)
    assert len(train) + len(test) == len(df)
    assert 0.15 < len(test) / len(df) < 0.25
    assert abs(train.Will_Attend.mean() - test.Will_Attend.mean()) < 0.08


def test_training_run_outputs(trained_models):
    d, run = trained_models
    names = [r["name"] for r in run.results]
    assert names == ["Logistic Regression (original baseline)", "Logistic Regression", "Decision Tree",
                     "Random Forest", "Gradient Boosting"]
    for r in run.results:
        assert (d / r["model_id"] / "model.joblib").exists()
        meta = json.loads((d / r["model_id"] / "metadata.json").read_text())
        assert meta["evaluation"]["majority_baseline"]["accuracy"] == run.baseline["accuracy"]
        assert "SIMULATED" in meta["target_notice"]
        for k in ("accuracy", "precision", "recall", "f1", "roc_auc", "confusion_matrix"):
            assert k in r["holdout"]
        assert len(r["cv"]["roc_auc"]["folds"]) == 5
        assert 0.2 <= r["threshold"] <= 0.8
    comp = json.loads((d / "comparison.json").read_text())
    assert comp["split"]["students_in_both"] == 0
    assert json.loads((d / "active_model.json").read_text())["model_id"] == run.recommended


def test_recommendation_uses_cv_only(trained_models):
    _, run = trained_models
    cands = [r for r in run.results if not r["is_baseline"]]
    assert run.recommended == max(cands, key=lambda r: r["cv"]["roc_auc"]["mean"])["model_id"]
    assert run.recommended != "logistic_regression_original_baseline"


def test_training_rejects_invalid_data(small_synthetic):
    from core.model_training import train_and_compare
    with pytest.raises(ValueError, match="failed validation"):
        train_and_compare(small_synthetic.drop(columns=["Event_Type"]), "bad", fast=True, save=False)


def test_metrics_and_baseline():
    y = np.array([1, 1, 1, 0, 0])
    p = np.array([0.9, 0.8, 0.4, 0.3, 0.6])
    m = classification_metrics(y, p, 0.5)
    assert m["confusion_matrix"] == [[1, 1], [1, 2]]
    assert m["accuracy"] == 0.6 and abs(m["expected_attendees"] - 3.0) < 1e-9
    b = majority_baseline(np.array([1, 1, 0]), y)
    assert b["accuracy"] == 0.6 and b["roc_auc"] == 0.5 and b["predicted_attendees"] == 5


def test_threshold_and_interval():
    y = np.array([0] * 30 + [1] * 70)
    p = np.r_[np.linspace(0.1, 0.6, 30), np.linspace(0.5, 0.95, 70)]
    assert 0.45 <= choose_threshold(y, p) <= 0.62
    mean, lo, hi = attendance_interval(np.full(100, 0.5))
    assert mean == 50 and lo < 50 < hi
