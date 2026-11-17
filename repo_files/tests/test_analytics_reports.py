"""Phase 4: analytics, charts and report generation."""
import zipfile

import matplotlib
import numpy as np
import pandas as pd
import pytest

from core import analytics as A
from core import charts as C
from core import reports as R
from core.db import services
from core.db.repositories import AttendanceRepository


@pytest.fixture
def predicted_db(seeded_db):
    services.predict_event(seeded_db, "EV22")
    services.predict_event(seeded_db, "EV06")
    att = {r["student_id"]: r["attended"] for r in AttendanceRepository(seeded_db).for_event("EV06")}
    services.record_actual_attendance(seeded_db, "EV06", attended=att)
    return seeded_db


def test_overview_kpis_come_from_database(predicted_db):
    k = A.overview_kpis(predicted_db)
    db = predicted_db
    assert k.students == db.scalar("SELECT COUNT(*) FROM students") == 279
    assert k.events == 36 and k.registrations == 383
    assert k.predicted_registrations == 24 + 20
    assert k.predicted_attendees + k.predicted_non_attendees == 44
    s = db.scalar("SELECT SUM(probability) FROM v_latest_predictions")
    assert k.expected_attendance == pytest.approx(s, abs=0.06)
    assert k.cv_roc_auc is not None and k.baseline_accuracy is not None
    assert k.recorded_attendance_simulated and any("SIMULATED" in n for n in k.notes)


def test_overview_on_empty_database(db):
    k = A.overview_kpis(db)
    assert k.students == 0 and k.model_name is None and k.predicted_attendees is None
    assert any("No active model" in n for n in k.notes)
    assert A.recent_predictions(db).empty and A.event_trends(db).empty
    assert A.probability_distribution(db)["n"] == 0


@pytest.mark.parametrize("dim", ["Department", "Year", "Event_Type", "Event_Day", "Event_Time", "Weather_Forecast"])
def test_participation_totals_match(seeded_db, dim):
    df = A.participation_by(seeded_db, dim)
    assert df["registrations"].sum() == 383
    assert df["attended"].sum() == 265
    assert ((df["attendance_rate"] >= 0) & (df["attendance_rate"] <= 1)).all()


def test_participation_rejects_unknown_dimension(seeded_db):
    with pytest.raises(ValueError):
        A.participation_by(seeded_db, "Name")


def test_event_trends_and_predicted_vs_actual(predicted_db):
    ev = A.event_trends(predicted_db)
    assert len(ev) == 36
    ev22 = ev.set_index("event_id").loc["EV22"]
    assert ev22["registrations"] == 24 and ev22["expected_attendance_active_model"] > 0
    pva = A.predicted_vs_actual(predicted_db)
    assert list(pva["event_id"]) == ["EV06"]
    row = pva.iloc[0]
    assert row["error"] == pytest.approx(row["expected_attendance"] - row["actual_attendees"], abs=1e-3)
    assert A.prediction_error_summary(predicted_db)["events"] == 0          # simulated excluded by default
    assert A.prediction_error_summary(predicted_db, include_simulated=True)["events"] == 1


def test_probability_distribution(predicted_db):
    d = A.probability_distribution(predicted_db)
    assert d["n"] == 44 and sum(d["counts"]) == 44 and len(d["edges"]) == 11


def test_model_comparison_and_details(seeded_db):
    mc = A.model_comparison(seeded_db)
    assert len(mc) == 5 and mc["active"].sum() == 1
    assert mc["baseline"].iloc[-1]                                       # baseline listed last
    assert (mc["majority_baseline_accuracy"].notna()).all()
    det = A.model_details(seeded_db, int(mc["model_version_id"].iloc[0]))
    assert det["roc_curve"] and len(det["roc_curve"]["fpr"]) == len(det["roc_curve"]["tpr"])
    cm = np.array(det["confusion_matrix"])
    assert cm.shape == (2, 2) and cm.sum() == det["holdout"]["n"]
    assert len(A.feature_importance(seeded_db)) > 0


def test_profile_and_clean_dataset(raw_ml):
    dup = pd.concat([raw_ml, raw_ml.head(2)], ignore_index=True)
    p = A.profile_dataset(dup)
    assert p.rows == 385 and p.duplicate_rows == 2 and p.duplicate_registrations == 2
    assert p.class_balance == {0: 118 + int((raw_ml.head(2).Will_Attend == 0).sum()),
                               1: 265 + int((raw_ml.head(2).Will_Attend == 1).sum())}
    assert "Distance_From_Venue_KM" in set(p.missing["column"])
    before = dup.copy()
    cleaned, log = A.clean_dataset(dup)
    pd.testing.assert_frame_equal(before, dup)                           # input untouched
    assert len(cleaned) == 383 and any("duplicate" in l for l in log)
    assert cleaned["Weather_Forecast"].isna().sum() == 0


def test_charts_build_for_both_themes(predicted_db):
    det = A.model_details(predicted_db, int(A.model_comparison(predicted_db)["model_version_id"].iloc[0]))
    d = A.participation_by(predicted_db, "Department")
    for th in (C.DARK, C.LIGHT):
        figs = [C.bar(d.Department, d.attendance_rate, "x", th, percent=True),
                C.histogram([1, 2, 3], [0, 0.3, 0.6, 1], "h", th, threshold=0.5),
                C.roc({"m": det["roc_curve"]}, theme=th), C.confusion(det["confusion_matrix"], theme=th),
                C.line(["a", "b"], [0.5, 0.7], "l", th, percent=True), C.attendance_split(3, 1, "s", th),
                C.grouped_bar(["E1"], {"Expected": [2.0], "Actual": [3]}, "g", th),
                C.bar([], [], "empty", th), C.histogram([0, 0], [0, .5, 1], "empty", th)]
        assert all(isinstance(f, matplotlib.figure.Figure) for f in figs)
    assert len(figs[0]._ep_hover) == len(d)


def test_pdf_report(predicted_db, tmp_path):
    p = R.generate_pdf(predicted_db, tmp_path / "r.pdf")
    data = p.read_bytes()
    assert data[:5] == b"%PDF-" and len(data) > 50_000
    assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") >= 4


def test_pdf_on_empty_database(db, tmp_path):
    p = R.generate_pdf(db, tmp_path / "empty.pdf")
    assert p.read_bytes()[:5] == b"%PDF-"


def test_excel_report(predicted_db, tmp_path):
    from openpyxl import load_workbook
    p = R.generate_excel(predicted_db, tmp_path / "r.xlsx")
    assert zipfile.is_zipfile(p)
    wb = load_workbook(p)
    assert {"Summary", "Model Comparison", "Event Analysis", "Departments", "Predicted vs Actual",
            "Prediction History", "Feature Importance"} <= set(wb.sheetnames)
    assert wb["Prediction History"].max_row == 44 + 1
    assert wb["Event Analysis"].max_row == 36 + 1
    assert wb["Departments"]._charts


def test_key_findings_are_data_driven(predicted_db):
    f = R.collect(predicted_db).findings
    assert any("cross-validated ROC-AUC" in s for s in f)
    assert any("SIMULATED" in s for s in f)


def test_export_frame(tmp_path):
    df = pd.DataFrame({"a": [1, 2], "attendance_rate": [0.5, 0.25]})
    assert pd.read_csv(R.export_frame(df, tmp_path / "x.csv")).shape == (2, 2)
    assert pd.read_excel(R.export_frame(df, tmp_path / "x.xlsx")).shape == (2, 2)
    with pytest.raises(ValueError):
        R.export_frame(df, tmp_path / "x.txt")
