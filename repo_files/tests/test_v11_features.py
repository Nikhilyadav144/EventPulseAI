"""v1.1.0: Demo Mode separation, data provenance, recent activity, filters and persistence."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from config import settings
from tests.conftest import make_event, make_student


def test_demo_database_is_separate_and_synthetic_only(tmp_path, trained_models):
    from core import analytics as A
    from core.db import services
    from core.db.connection import Database
    path = tmp_path / "demo.db"
    r = services.create_demo_database(path, models_dir=trained_models[0])
    assert r["created"] and r["import"]["registrations"] > 500
    assert services.create_demo_database(path, models_dir=trained_models[0])["created"] is False   # built once
    with Database(path) as db:
        assert services.is_demo_database(db)
        s = A.data_source_summary(db)
        assert set(s["students"]) == set(s["events"]) == set(s["registrations"]) == {"SYNTHETIC"}
        assert set(s["attendance"]) == {"SYNTHETIC"} and s["observed_outcomes"] == 0
        assert db.scalar("SELECT COUNT(*) FROM students WHERE student_id NOT LIKE 'SYN-%'") == 0
        assert db.query_one("SELECT * FROM model_versions WHERE is_active = 1") is not None


def test_demo_refuses_a_database_with_user_data(seeded_db, tmp_path):
    from core.db import services
    from core.db.errors import ConstraintViolationError
    seeded_db.close()
    with pytest.raises(ConstraintViolationError):
        services.create_demo_database(seeded_db.path)


def test_demo_refuses_unlabelled_data(tmp_path):
    from core.db import services
    from core.db.errors import ConstraintViolationError
    csv = tmp_path / "fake.csv"
    df = pd.read_csv(settings.SYNTHETIC_DATASET).head(20)
    df["Data_Source"] = "ORIGINAL_HYBRID"
    df.to_csv(csv, index=False)
    with pytest.raises(ConstraintViolationError):
        services.create_demo_database(tmp_path / "demo.db", synthetic_csv=csv)


def test_data_source_summary_separates_simulated_and_recorded(seeded_db):
    from core import analytics as A
    from core.db import services
    from core.db.repositories import RegistrationRepository
    s = A.data_source_summary(seeded_db)
    assert s["registrations"] == {"ORIGINAL_HYBRID": 383}
    assert s["simulated_outcomes"] == 383 and s["observed_outcomes"] == 0
    make_student(seeded_db, "STU901")
    make_event(seeded_db, "EVREAL", event_date=datetime.now(timezone.utc).date().isoformat())
    RegistrationRepository(seeded_db).register("STU901", "EVREAL", 3)
    services.predict_event(seeded_db, "EVREAL")
    services.record_actual_attendance(seeded_db, "EVREAL", attended={"STU901": 1})
    s = A.data_source_summary(seeded_db)
    assert s["registrations"]["MANUAL"] == 1 and s["observed_outcomes"] == 1 and s["simulated_outcomes"] == 383


def test_recent_activity_lists_real_actions_only(seeded_db):
    from core import analytics as A
    from core.db import services
    services.predict_event(seeded_db, "EV22")
    make_event(seeded_db, "EVNEW")
    act = A.recent_activity(seeded_db, 10)
    text = " | ".join(act["activity"])
    assert "Predicted 24 registrations for EV22" in text and "Created event EVNEW" in text
    assert "Created event EV01" not in text            # bulk-imported events are not listed one by one
    assert list(act["when_utc"]) == sorted(act["when_utc"], reverse=True)


def test_participation_filter_by_source(seeded_db):
    from core import analytics as A
    assert A.participation_by(seeded_db, "Department", "ORIGINAL_HYBRID")["registrations"].sum() == 383
    assert A.participation_by(seeded_db, "Department", "SYNTHETIC").empty


def test_records_persist_after_reopening(tmp_path, trained_models):
    """Events, registrations, predictions, model versions, actual attendance and comparisons survive a restart."""
    from core.data_loader import file_sha256
    from core.db import services
    from core.db.connection import Database
    from core.db.repositories import EventRepository, ModelVersionRepository
    path = tmp_path / "persist.db"
    if not settings.PROCESSED_DATASET.exists():
        pytest.skip("processed mid-term dataset not present (not published in the repository)")
    with Database(path) as db:
        p = settings.PROCESSED_DATASET
        services.import_dataset(db, pd.read_csv(p), "PROCESSED", "processed", p.name, file_sha256(p))
        services.sync_models(db, trained_models[0])
        make_student(db, "STU950")
        make_event(db, "EVKEEP", event_date=datetime.now(timezone.utc).date().isoformat())
        from core.db.repositories import RegistrationRepository
        RegistrationRepository(db).register("STU950", "EVKEEP", 4)
        run = services.predict_event(db, "EVKEEP")
        services.record_actual_attendance(db, "EVKEEP", attended={"STU950": 1})
        before = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in
                  ("events", "registrations", "predictions", "model_versions", "attendance_records",
                   "prediction_evaluations")}
    with Database(path) as db:
        after = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in before}
        assert after == before
        assert EventRepository(db).get("EVKEEP")["name"] == "Test workshop"
        assert db.scalar("SELECT COUNT(*) FROM predictions WHERE run_id = ?", (run["run_id"],)) == 1
        assert ModelVersionRepository(db).active() is not None
        ev = db.query_one("SELECT * FROM prediction_evaluations WHERE event_id = 'EVKEEP'")
        assert ev["actual_attendees"] == 1 and ev["target_is_simulated"] == 0


def test_reports_label_simulated_and_recorded_outcomes(seeded_db, tmp_path):
    from openpyxl import load_workbook

    from core import reports as R
    out = R.generate_excel(seeded_db, tmp_path / "r.xlsx")
    ws = load_workbook(out)["Summary"]
    items = {r[0].value: r[1].value for r in ws.iter_rows(min_row=2)}
    assert items["Outcomes - SIMULATED (dataset)"] == 383
    assert items["Outcomes - recorded after real events"] == 0
    assert items["Version"] == settings.APP_VERSION
    assert items["Students"] == 279 and items["Registrations"] == 383
    pdf = R.generate_pdf(seeded_db, tmp_path / "r.pdf", R.SECTIONS)
    assert pdf.read_bytes()[:5] == b"%PDF-"
    only = R.generate_pdf(seeded_db, tmp_path / "p.pdf", ("predictions",))     # no blank leading page
    assert only.stat().st_size > 1000


def test_brand_assets_exist():
    from PIL import Image
    for name in ("logo_mark.png", "icon.png", "AppIcon.icns"):
        assert (settings.PROJECT_ROOT / "assets" / name).exists(), name
    assert Image.open(settings.PROJECT_ROOT / "assets" / "icon.png").size == (1024, 1024)
