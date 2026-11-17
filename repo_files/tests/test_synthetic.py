import pandas as pd

from config import settings
from core import synthetic
from core.data_validation import validate


def test_reproducible_and_seed_dependent():
    a = synthetic.generate(120, 12, seed=3).df
    b = synthetic.generate(120, 12, seed=3).df
    c = synthetic.generate(120, 12, seed=4).df
    pd.testing.assert_frame_equal(a, b)
    assert not a.equals(c)


def test_clearly_labelled(small_synthetic):
    df = small_synthetic
    assert (df["Data_Source"] == "SYNTHETIC").all()
    assert df["Student_ID"].str.startswith("SYN-STU-").all()
    assert df["Event_ID"].str.startswith("SYN-EV-").all()
    r = synthetic.generate(60, 8, seed=1)
    assert "SYNTHETIC" in r.metadata["label"] and r.metadata["choices_not_in_documentation"]


def test_passes_validation_without_contradictions(small_synthetic):
    rep = validate(small_synthetic, "training")
    assert rep.ok, rep.summary()
    df = small_synthetic
    assert not df.duplicated(["Student_ID", "Event_ID"]).any()
    assert not df.duplicated().any()
    assert not (df["Previous_Event_Attendance"] > df["Previous_Events_Registered"]).any()
    assert not (df["Event_Day"] == "Sunday").any()


def test_realistic_target_rate_and_missingness():
    r = synthetic.generate(600, 48, seed=settings.SYNTHETIC_SEED)
    rate = r.df["Will_Attend"].mean()
    assert 0.60 < rate < 0.76
    assert abs(r.metadata["mean_true_probability"] - 0.68) < 0.005
    miss = r.df.isna().mean()
    assert 0.02 < miss["Distance_From_Venue_KM"] < 0.08
    assert (r.df["Weather_Forecast"] == "Unavailable").any()


def test_target_is_not_a_trivial_rule(small_synthetic):
    """No single feature value determines the outcome."""
    df = small_synthetic
    for col in ["Has_Exam_Near_Event", "Event_Type", "Weather_Forecast", "Experience_Level"]:
        rates = df.groupby(col)["Will_Attend"].mean()
        assert ((rates > 0.0) & (rates < 1.0)).all(), col


def test_kept_separate_from_original():
    assert settings.SYNTHETIC_DIR != settings.ORIGINAL_DIR
    assert settings.SYNTHETIC_DATASET.parent == settings.SYNTHETIC_DIR
