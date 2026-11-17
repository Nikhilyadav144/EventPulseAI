import numpy as np
import pandas as pd

from core.preprocessing import (FeatureEngineer, build_eventpulse_preprocessor, consolidate_entities,
                                prepare_frame)
from core.schema import FORECAST_UNAVAILABLE, MODEL_INPUTS


def test_prepare_frame_forecast_unavailable_and_inputs(raw_ml):
    df = raw_ml.drop(columns=["Weather_Forecast"])
    out = prepare_frame(df)
    assert (out["Weather_Forecast"] == FORECAST_UNAVAILABLE).all()
    assert set(MODEL_INPUTS) <= set(out.columns)
    assert prepare_frame(raw_ml)["Weather_Forecast"].isna().sum() == 0


def test_prepare_frame_recomputes_rate():
    df = pd.DataFrame({"Previous_Event_Attendance": [3], "Previous_Events_Registered": [4],
                       "Previous_Attendance_Rate": [np.nan]})
    assert prepare_frame(df)["Previous_Attendance_Rate"].iloc[0] == 75.0


def test_consolidation_uses_only_same_entity():
    df = pd.DataFrame({
        "Student_ID": ["A", "A", "B", "C"], "Event_ID": ["E1", "E2", "E1", "E3"],
        "Distance_From_Venue_KM": [3.0, np.nan, 9.0, np.nan],
        "Event_Popularity": [50.0, 70.0, np.nan, 60.0],
        "Weather_Forecast": ["Rainy", "Clear", FORECAST_UNAVAILABLE, FORECAST_UNAVAILABLE],
        "Will_Attend": [1, 0, 1, 0],
    })
    out, log = consolidate_entities(df)
    assert out.loc[1, "Distance_From_Venue_KM"] == 3.0       # from student A's other row
    assert np.isnan(out.loc[3, "Distance_From_Venue_KM"])    # student C has no other row
    assert out.loc[2, "Event_Popularity"] == 50.0            # from event E1
    assert out.loc[2, "Weather_Forecast"] == "Rainy"         # event E1 forecast
    assert out.loc[3, "Weather_Forecast"] == FORECAST_UNAVAILABLE   # never invented
    assert log.filled == {"Distance_From_Venue_KM": 1, "Event_Popularity": 1, "Weather_Forecast": 1}


def test_consolidation_ignores_target(raw_ml):
    a, _ = consolidate_entities(prepare_frame(raw_ml))
    flipped = raw_ml.copy()
    flipped["Will_Attend"] = 1 - flipped["Will_Attend"]
    b, _ = consolidate_entities(prepare_frame(flipped))
    pd.testing.assert_frame_equal(a.drop(columns="Will_Attend"), b.drop(columns="Will_Attend"))


def test_feature_engineer_matches_documented_rules():
    df = pd.DataFrame({"Course": ["B.Tech CSE", "Unknown"], "Area_Of_Interest": ["Problem Solving & Decision Making",
                       "Not Sure Yet"], "Event_Type": ["Workshop", "Cultural"], "Role": ["Graphics/Creativity",
                       "Did not apply"], "Registration_Days_Before": [1, 20], "Previous_Event_Attendance": [0, 3],
                       "Previous_Events_Registered": [1, 4], "Weather_Forecast": [None, "Clear"]})
    out = FeatureEngineer().fit_transform(df)
    assert out["Department"].tolist() == ["Engineering", "Other"]
    assert out["Interest_Match"].tolist() == [1, 0]
    assert out["Role_Match"].tolist() == [1, 0]
    assert out["Registration_Window"].tolist() == ["0-1 days", "15+ days"]
    assert out["Weather_Forecast"].iloc[0] == FORECAST_UNAVAILABLE


def test_preprocessor_fits_on_train_and_handles_unseen(raw_ml):
    df = prepare_frame(raw_ml)
    train, test = df.iloc[:300], df.iloc[300:].copy()
    pre = build_eventpulse_preprocessor().fit(train[MODEL_INPUTS])
    test.loc[test.index[0], "Course"] = "Brand New Course"
    test.loc[test.index[1], "Weather_Forecast"] = FORECAST_UNAVAILABLE
    Xt = pre.transform(test[MODEL_INPUTS])
    assert Xt.shape[0] == len(test) and not np.isnan(Xt).any()
    # scaler statistics come from training rows only
    scaler = pre.named_steps["columns"].named_transformers_["num"].named_steps["scale"]
    assert scaler.n_samples_seen_ == 300
