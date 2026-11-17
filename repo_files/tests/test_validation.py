import pandas as pd

from core.data_validation import validate


def test_original_dataset_passes(raw_ml):
    rep = validate(raw_ml, "training")
    assert rep.ok, rep.summary()
    assert rep.class_balance == {0: 118, 1: 265}
    assert rep.duplicate_rows == 0 and rep.duplicate_registrations == 0
    assert rep.missing["Distance_From_Venue_KM"] == 19


def test_validation_does_not_modify_input(raw_ml):
    before = raw_ml.copy()
    validate(raw_ml, "training")
    pd.testing.assert_frame_equal(before, raw_ml)


def test_missing_columns(raw_ml):
    rep = validate(raw_ml.drop(columns=["Event_Type", "Will_Attend"]), "training")
    assert not rep.ok
    assert "Event_Type" in rep.missing_columns and "Will_Attend" in rep.missing_columns


def test_prediction_mode_does_not_need_target(raw_ml):
    rep = validate(raw_ml.drop(columns=["Will_Attend"]), "prediction")
    assert rep.ok, rep.summary()


def test_missing_values_reported_not_fatal(raw_ml):
    rep = validate(raw_ml, "training")
    assert any("Academic_Engagement_Score: 17 missing" in w for w in rep.warnings)
    assert any("Weather_Forecast" in w and "Unavailable" in w for w in rep.warnings)


def test_missing_value_in_required_complete_column(raw_ml):
    df = raw_ml.copy()
    df.loc[0, "Event_Type"] = None
    rep = validate(df, "training")
    assert any("Event_Type: 1 missing" in e for e in rep.errors)


def test_type_and_range_errors(raw_ml):
    df = raw_ml.copy().astype({"Year": object, "Event_Popularity": object})
    df.loc[0, "Year"] = "first"
    df.loc[1, "Event_Popularity"] = 150
    df.loc[2, "Has_Exam_Near_Event"] = 3
    rep = validate(df, "training")
    joined = " ".join(rep.errors)
    assert "Year: 1 value(s) are not numbers" in joined
    assert "Event_Popularity: 1 value(s) above" in joined
    assert "Has_Exam_Near_Event: 1 value(s) are not 0/1" in joined


def test_yes_no_binary_accepted(raw_ml):
    df = raw_ml.copy()
    df["Has_Exam_Near_Event"] = df["Has_Exam_Near_Event"].map({1: "Yes", 0: "No"})
    assert validate(df, "training").ok


def test_duplicates_detected(raw_ml):
    df = pd.concat([raw_ml, raw_ml.iloc[[0]]], ignore_index=True)
    rep = validate(df, "training")
    assert rep.duplicate_rows == 1 and rep.duplicate_registrations == 1
    assert not rep.ok                                   # repeated registration blocks training
    assert validate(df.drop(columns=["Will_Attend"]), "prediction").ok   # but only warns for prediction


def test_contradiction_attended_more_than_registered(raw_ml):
    df = raw_ml.copy()
    df.loc[0, "Previous_Event_Attendance"] = 30
    df.loc[0, "Previous_Events_Registered"] = 5
    assert any("attended more" in e for e in validate(df, "training").errors)


def test_single_class_target(raw_ml):
    df = raw_ml.copy()
    df["Will_Attend"] = 1
    assert any("only one class" in e for e in validate(df, "training").errors)


def test_unknown_category_is_warning_but_bad_ordinal_is_error(raw_ml):
    df = raw_ml.copy()
    df.loc[0, "Course"] = "B.Des"
    rep = validate(df, "training")
    assert rep.ok and any("Course: unrecognised" in w for w in rep.warnings)
    df.loc[1, "Experience_Level"] = "Expert"
    assert any("Experience_Level" in e for e in validate(df, "training").errors)


def test_empty_frame():
    assert not validate(pd.DataFrame(), "training").ok
