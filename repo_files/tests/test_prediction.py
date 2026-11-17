import json

import numpy as np
import pytest

from core.model_registry import ModelCompatibilityError, active_model_dir, list_models, load_model
from core.prediction import PredictionInputError, Predictor, certainty_label


@pytest.fixture(scope="module")
def predictor(trained_models):
    d, _ = trained_models
    return Predictor(active_model_dir(d))


def _record(df):
    return df.drop(columns=["Will_Attend", "Data_Source"]).iloc[0].to_dict()


def test_individual_prediction(predictor, small_synthetic):
    r = predictor.predict_one(_record(small_synthetic))
    assert 0.0 <= r["probability"] <= 1.0
    assert r["predicted_attend"] == int(r["probability"] >= r["threshold"])
    assert r["certainty"] in ("Uncertain", "Moderate", "Clear")
    assert all(abs(f.effect) >= 0.005 for f in r["factors"])
    assert "SIMULATED" in r["notice"]


def test_unavailable_forecast_is_supported(predictor, small_synthetic):
    rec = _record(small_synthetic)
    rec["Weather_Forecast"] = None
    r = predictor.predict_one(rec)
    assert r["forecast_unavailable"] and r["forecast_used"] == "Unavailable"
    rec.pop("Weather_Forecast")
    assert predictor.predict_one(rec)["forecast_unavailable"]


def test_bulk_prediction_and_summary(predictor, small_synthetic):
    out = predictor.predict(small_synthetic.drop(columns=["Will_Attend"]))
    assert len(out) == len(small_synthetic)
    assert out["Attendance_Probability"].between(0, 1).all()
    s = predictor.bulk_summary(out)
    assert s["predicted_attendees"] + s["predicted_non_attendees"] == len(out)
    lo, hi = s["expected_attendance_range_95"]
    assert lo <= s["expected_attendance"] <= hi
    assert sum(s["histogram"]) == len(out)
    assert {"Event_ID", "registrations", "expected_attendance"} <= set(s["by_event"][0])


def test_bulk_rejects_missing_columns(predictor, small_synthetic):
    with pytest.raises(PredictionInputError) as e:
        predictor.predict(small_synthetic.drop(columns=["Event_Type", "Year"]))
    assert "Event_Type" in e.value.report.missing_columns


def test_predictions_are_reproducible(predictor, trained_models, small_synthetic):
    X = small_synthetic.drop(columns=["Will_Attend"])
    a = predictor.predict(X)["Attendance_Probability"].to_numpy()
    b = Predictor(predictor.model_dir).predict(X)["Attendance_Probability"].to_numpy()
    np.testing.assert_array_equal(a, b)


def test_all_saved_models_load_and_predict(trained_models, small_synthetic):
    d, _ = trained_models
    models = list_models(d)
    assert len(models) == 5
    for m in models:
        p = Predictor(m["path"])
        assert p.predict(small_synthetic.head(5).drop(columns=["Will_Attend"])).shape[0] == 5


def test_incompatible_model_version_detected(trained_models, tmp_path):
    import shutil
    d, run = trained_models
    src = d / run.recommended
    dst = tmp_path / run.recommended
    shutil.copytree(src, dst)
    meta = json.loads((dst / "metadata.json").read_text())
    meta["environment"]["scikit_learn"] = "0.1.0"
    (dst / "metadata.json").write_text(json.dumps(meta))
    with pytest.raises(ModelCompatibilityError):
        load_model(dst)


def test_certainty_labels():
    assert certainty_label(0.55, 0.5) == "Uncertain"
    assert certainty_label(0.70, 0.5) == "Moderate"
    assert certainty_label(0.95, 0.5) == "Clear"
