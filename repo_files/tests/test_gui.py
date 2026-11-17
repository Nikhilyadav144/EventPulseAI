"""GUI tests: run the real PySide6 window off-screen and exercise every page.

Skipped where PySide6 is not installed. All files are written to temporary folders.
"""
import os
from datetime import datetime, timezone

import pandas as pd
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from config import settings  # noqa: E402

PAGE_MODULES = ["overview", "dataset_explorer", "events", "prediction", "model_lab", "analytics", "reports",
                "settings_page", "calendar_page", "registrations", "attendance", "insights", "monitoring",
                "notifications", "whatif"]


@pytest.fixture(scope="session")
def qapp():
    import matplotlib
    matplotlib.use("QtAgg", force=True)
    return QApplication.instance() or QApplication([])


def wait(app, ms=120_000):
    for _ in range(3):
        QThreadPool.globalInstance().waitForDone(ms)
        for _ in range(20):
            app.processEvents()


@pytest.fixture
def ui(qapp, tmp_path, monkeypatch, trained_models):
    import importlib

    import core.db.backup as backup_mod
    from core.data_loader import file_sha256
    from core.db import services
    from core.db.connection import Database
    from gui import theme
    from gui.context import AppContext
    from gui.main_window import MainWindow

    for name in ("MODELS_DIR", "REPORTS_DIR", "EXPORTS_DIR"):
        monkeypatch.setattr(settings, name, tmp_path / name.lower())
    monkeypatch.setattr(settings, "DEMO_DATABASE", tmp_path / "demo.db")
    monkeypatch.setattr(backup_mod, "BACKUP_DIR", tmp_path / "backups")
    errors, confirms = [], []
    for m in PAGE_MODULES:
        mod = importlib.import_module(f"gui.pages.{m}")
        if hasattr(mod, "show_error"):
            monkeypatch.setattr(mod, "show_error", lambda parent, t, msg, d=None: errors.append((t, msg)))
        if hasattr(mod, "confirm"):
            monkeypatch.setattr(mod, "confirm", lambda *a, **k: confirms.append(a[1]) or True)
    path = tmp_path / "gui.db"
    db = Database(path)
    p = settings.PROCESSED_DATASET
    services.import_dataset(db, pd.read_csv(p), "PROCESSED", "processed", p.name, file_sha256(p))
    services.sync_models(db, trained_models[0])
    db.close()
    ctx = AppContext(path)
    qapp.setStyleSheet(theme.stylesheet(theme.set_current("cream")))
    win = MainWindow(ctx)
    win.resize(1440, 900)
    win.show()
    qapp.processEvents()
    win.errors, win.confirms, win.app = errors, confirms, qapp
    yield win
    wait(qapp)
    win.close()


def page(win, cls_name):
    return next(p for p in win.pages if type(p).__name__ == cls_name)


def goto(ui, name):
    """Open a page by class name (indices changed when pages were added)."""
    pg = page(ui, name)
    ui.go(ui.pages.index(pg))
    ui.app.processEvents()
    return pg



def test_window_opens_and_every_page_loads(ui):
    assert ui.windowTitle().startswith("EventPulse AI")
    assert len(ui.pages) == len(ui.nav_buttons) == 14
    for i, p in enumerate(ui.pages):
        ui.go(i)
        ui.app.processEvents()
        assert ui.stack.currentIndex() == i and ui.nav_buttons[i].isChecked()
        assert not p._dirty, type(p).__name__
        assert p.notice.objectName() != "ErrorNotice", (type(p).__name__, p.notice.lbl.text())
    assert ui.errors == []


def test_overview_matches_database(ui):
    from core import analytics as A
    ui.go(0)
    ui.app.processEvents()
    ov = page(ui, "OverviewPage")
    k = A.overview_kpis(ui.ctx.db)
    assert ov.k_students.v.text() == f"{k.students:,}" == "279"
    assert ov.k_events.v.text() == "36"
    assert ov.k_attend.v.text() == "—"                      # nothing predicted yet
    assert ov.recent.model.rowCount() == 0


def test_dataset_explorer_workflow(ui, tmp_path):
    dx = goto(ui, "DatasetExplorerPage")
    dx.open_file(str(settings.PROCESSED_DATASET))
    assert dx.preview.model.rowCount() == 383
    assert "PASSED" in dx.validation_text.toPlainText()
    dx.preview.search.setText("EV22")
    assert dx.preview.proxy.rowCount() == 24
    dx.do_clean()
    assert dx.cleaned is not None and "•" in dx.clean_log.toPlainText()
    out = tmp_path / "clean.csv"
    dx.export_cleaned(str(out))
    assert len(pd.read_csv(out)) == 383
    pii = tmp_path / "pii.csv"
    pd.DataFrame({"Student_ID": ["S1"], "Name": ["X"]}).to_csv(pii, index=False)
    dx.open_file(str(pii))
    assert ui.errors and "personal" in ui.errors[-1][1].lower()
    for i in range(dx.dist_col.count()):
        dx.dist_col.setCurrentIndex(i)                     # every distribution renders
    dx.load_reference("synthetic")
    assert dx.preview.model.rowCount() > 500


def test_event_lifecycle_prediction_and_actuals(ui):
    from core.db.repositories import RegistrationRepository, StudentRepository
    ev = goto(ui, "EventsPage")
    ev.new_event()
    ev.f_id.setText("EVGUI")
    ev.f_name.setText("GUI test workshop")
    ev.f_type.setCurrentText("Workshop")
    today = datetime.now(timezone.utc).date()
    from PySide6.QtCore import QDate
    ev.f_date.setDate(QDate(today.year, today.month, today.day))
    ev.save_event()
    ui.app.processEvents()
    assert ui.errors == [] and ev.current == "EVGUI"
    for i in range(5):
        StudentRepository(ui.ctx.db).create(student_id=f"STUG{i}", course="BCA", year=1 + i % 3,
                                            currently_uba_volunteer=i % 2, participated_uba_event_before=1,
                                            experience_level="Some experience", area_of_interest="Not Sure Yet",
                                            role="Content", previous_event_attendance=2, previous_events_registered=4)
        RegistrationRepository(ui.ctx.db).register(f"STUG{i}", "EVGUI", 3 + i)
    ev.fc_value.setCurrentText("Rainy")
    ev.add_forecast()
    ui.app.processEvents()
    assert "Rainy" in ev.fc_current.text()
    pr = goto(ui, "PredictionPage")
    pr.reload()
    idx = next(i for i in range(pr.e_event.count()) if pr.e_event.itemText(i).startswith("EVGUI"))
    pr.e_event.setCurrentIndex(idx)
    pr.predict_event()
    wait(ui.app)
    assert ui.errors == [] and pr.e_k[0].v.text() == "5" and pr.e_table.model.rowCount() == 5
    ui.go(2)
    ev.load_event("EVGUI")
    assert ev.att_table.rowCount() == 5
    for i in range(5):
        ev.att_table.item(i, 1).setCheckState(Qt.Checked if i < 3 else Qt.Unchecked)
    ev.save_attendance()
    assert ui.errors == [] and "expected vs 3 actual" in ev.eval_text.text()
    ev.reload()
    assert ev.eval_table.model.rowCount() == 1
    an = goto(ui, "AnalyticsPage")
    an.include_sim.setChecked(False)
    assert an.e_k[0].v.text() == "1"
    ui.go(0)
    assert page(ui, "OverviewPage").k_attend.v.text() != "—"


def test_individual_and_bulk_prediction(ui, tmp_path):
    pr = goto(ui, "PredictionPage")
    pr.i_student.setCurrentText("STU001")
    pr.i_event.setCurrentText("EV09")
    pr.predict_individual()
    assert ui.errors == [] and pr.r_prob.v.text().endswith("%")
    assert pr.r_out.v.text() in ("Will attend", "Will not attend")
    pr.f["Weather_Forecast"].setCurrentText("Unavailable")
    pr.predict_individual()
    assert "No weather forecast" in pr.r_note.lbl.text()
    src = tmp_path / "bulk.xlsx"
    pd.read_csv(settings.SYNTHETIC_DATASET).head(60).drop(columns=["Will_Attend"]).to_excel(src, index=False)
    pr.run_bulk(str(src))
    wait(ui.app)
    assert ui.errors == [] and pr.b_k[0].v.text() == "60" and pr.b_table.model.rowCount() == 60
    out = tmp_path / "bulk_out.csv"
    pr.export_bulk("csv", str(out))
    assert len(pd.read_csv(out)) == 60
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"Year": [1]}).to_csv(bad, index=False)
    pr.run_bulk(str(bad))
    assert pr.b_valid.objectName() == "ErrorNotice"


def test_model_lab_inspect_activate_and_retrain(ui):
    ml = goto(ui, "ModelLabPage")
    assert ml.table.model.rowCount() == 5
    for r in range(5):
        ml._select(r)
        assert ml.k["CV ROC-AUC"].v.text() != "—"
    other = int(ml.mc[~ml.mc["active"]]["model_version_id"].iloc[0])
    ml.show_model(other)
    ml.activate()
    ui.app.processEvents()
    from core.db.repositories import ModelVersionRepository
    assert ModelVersionRepository(ui.ctx.db).active()["model_version_id"] == other
    ml.t_data.setCurrentIndex(1)
    ml.t_fast.setChecked(True)
    ml.train()
    wait(ui.app, 600_000)
    assert ui.errors == [], ui.errors
    assert "Registered 5 model version" in ml.t_log.toPlainText()
    ml.reload()
    assert ml.table.model.rowCount() == 10


def test_reports_and_exports(ui):
    rp = goto(ui, "ReportsPage")
    rp.generate("pdf", wait=True)
    rp.generate("xlsx", wait=True)
    assert ui.errors == []
    files = list(settings.REPORTS_DIR.iterdir())
    assert {f.suffix for f in files} == {".pdf", ".xlsx"}
    assert next(f for f in files if f.suffix == ".pdf").read_bytes()[:5] == b"%PDF-"
    for i in range(rp.table_choice.count()):
        rp.table_choice.setCurrentIndex(i)
        rp.export_table("csv", str(settings.EXPORTS_DIR / f"t{i}.csv"))
    assert len(list(settings.EXPORTS_DIR.glob("*.csv"))) == rp.table_choice.count()
    rp.refresh()
    assert rp.files.model.rowCount() >= 2 + rp.table_choice.count()


def test_settings_theme_backup_restore(ui):
    st = goto(ui, "SettingsPage")
    assert st.b["Schema version"].text() == "3"
    st.backup()
    assert st.backups.model.rowCount() == 1
    st.theme_box.setCurrentIndex(1)
    ui.app.processEvents()
    from gui import theme
    assert theme.current().name == "evening" and ui.ctx.settings.get("theme") == "evening"
    for i in range(len(ui.pages)):
        ui.go(i)
        ui.app.processEvents()
    st.theme_box.setCurrentIndex(0)
    assert theme.current().name == "cream"
    from core.db.repositories import StudentRepository
    StudentRepository(ui.ctx.db).create(student_id="STUAFTER", course="BCA", year=1, currently_uba_volunteer=0,
                                        participated_uba_event_before=0, experience_level="No experience",
                                        area_of_interest="Not Sure Yet", role="Content", previous_event_attendance=0)
    st.backups.view.selectRow(0)
    st.restore_selected()
    assert ui.errors == []
    assert not StudentRepository(ui.ctx.db).exists("STUAFTER")
    st.integrity()
    assert st.b["Integrity"].text() == "ok"


def test_window_resizing(ui):
    from PySide6.QtWidgets import QScrollArea
    overflow = []
    for w, h in ((1100, 700), (1920, 1080), (1280, 800)):
        ui.resize(w, h)
        for i in range(len(ui.pages)):
            ui.go(i)
            for _ in range(5):
                ui.app.processEvents()
            bar = ui.pages[i].findChild(QScrollArea).horizontalScrollBar()
            if bar.maximum() > 0:
                overflow.append((w, type(ui.pages[i]).__name__, bar.maximum()))
    assert overflow == [], f"pages need horizontal scrolling: {overflow}"
    assert ui.width() >= 1100 and ui.errors == []


def test_settings_values_are_not_clipped(ui):
    """Every value on the Settings page must be shown in full (no one-line clipping)."""
    page = next(p for p in ui.pages if type(p).__name__ == "SettingsPage")
    ui.go(ui.pages.index(page))
    clipped = []
    for w, h in ((1100, 700), (1440, 900), (1280, 800)):      # one- and two-column layouts
        ui.resize(w, h)
        for _ in range(8):
            ui.app.processEvents()
        for group in (page.i, page.d, page.m, page.b):
            labels = list(group.items())
            for key, lb in labels:
                if lb.text() and lb.height() < lb.heightForWidth(lb.width()):
                    clipped.append((w, key, lb.width(), lb.height(), lb.heightForWidth(lb.width())))
            for (k1, a), (k2, b) in zip(labels, labels[1:]):             # rows must not overlap
                if a.mapTo(page, a.rect().bottomLeft()).y() > b.mapTo(page, b.rect().topLeft()).y():
                    clipped.append((w, k1, "overlaps", k2))
    assert clipped == [], f"clipped settings values: {clipped}"
    assert page.i["Version"].text() == settings.APP_VERSION == "1.2.0"


def test_demo_mode_is_separate_from_user_data(ui, monkeypatch, trained_models):
    monkeypatch.setattr(settings, "MODELS_DIR", trained_models[0])
    ov = page(ui, "OverviewPage")
    main_students = ui.ctx.db.scalar("SELECT COUNT(*) FROM students")
    ui.set_demo(True, ask=False, wait=True)
    ui.go(0)
    ui.app.processEvents()
    assert ui.errors == [] and ui.ctx.demo and ui.ctx.db_path == settings.DEMO_DATABASE
    assert not ui.demo_banner.isHidden() and "Demo Mode" in ui.windowTitle()
    assert ov.k_students.v.text() == "600"
    assert ui.ctx.db.scalar("SELECT COUNT(*) FROM students WHERE data_source <> 'SYNTHETIC'") == 0
    assert ui.ctx.main_db.scalar("SELECT COUNT(*) FROM students") == main_students     # user data untouched
    for i in range(len(ui.pages)):                                                         # every page works
        ui.go(i)
        ui.app.processEvents()
        assert ui.pages[i].notice.objectName() != "ErrorNotice", type(ui.pages[i]).__name__
    st = page(ui, "SettingsPage")
    assert "Demo" in st.d["Working database"].text()
    ui.set_demo(False)
    ui.go(0)
    ui.app.processEvents()
    assert not ui.ctx.demo and ui.demo_banner.isHidden() and ov.k_students.v.text() == "279"


def test_prediction_uses_the_selected_model(ui):
    pr = goto(ui, "PredictionPage")
    pr.reload()
    assert pr.model_box.count() == 5 and pr.selected_model_id() is None          # active model by default
    pr.model_box.setCurrentIndex(2)
    chosen = pr.selected_model_id()
    pr.i_student.setCurrentText("STU001")
    pr.i_event.setCurrentText("EV09")
    pr.predict_individual()
    assert ui.errors == []
    last = ui.ctx.db.query_one("SELECT model_version_id FROM predictions ORDER BY prediction_id DESC LIMIT 1")
    assert last["model_version_id"] == chosen
    assert pr.r_up.text() or pr.r_down.text()


def test_reports_presets_filters_and_preview(ui):
    rp = goto(ui, "ReportsPage")
    rp.preset.setCurrentText("Model performance")
    assert [k for k, cb in rp.checks.items() if cb.isChecked()] == ["project", "models", "findings"]
    rp.checks["dataset"].setChecked(True)
    assert rp.preset.currentText() == "Custom"
    rp.table_choice.setCurrentText("Event analysis")
    rp.event_filter.setCurrentText("EV22")
    assert rp.preview.model.rowCount() == 1 and "1 row" in rp.preview_note.text()
    out = settings.EXPORTS_DIR / "ev22.csv"
    rp.export_table("csv", str(out))
    assert pd.read_csv(out)["event_id"].tolist() == ["EV22"]
    rp.event_filter.setCurrentText("All events")
    rp.use_dates.setChecked(True)                      # imported events are undated
    assert rp.preview.model.rowCount() == 0
    assert rp.k_outcomes.v.text() == "383"


def test_brand_assets_icons_and_about(ui):
    from PySide6.QtWidgets import QWidget

    from gui import icons
    from gui.branding import AboutDialog, logo_pixmap, splash_pixmap
    assert not splash_pixmap().isNull() and not logo_pixmap(36).isNull()
    for name in icons.ICONS:
        assert not icons.pixmap(name, "#6956a5", 18).isNull()
    assert all(not b.icon().isNull() for b in ui.nav_buttons)
    dlg = AboutDialog(ui, "Logistic Regression v1")
    assert settings.APP_TAGLINE in " ".join(w.text() for w in dlg.findChildren(QWidget) if hasattr(w, "text"))
    dlg.close()


def test_kpi_rows_reflow_with_window_width(ui):
    from gui.widgets import ResponsiveGrid
    grid = goto(ui, "OverviewPage").findChildren(ResponsiveGrid)[0]
    ui.resize(1100, 720)
    for _ in range(5):
        ui.app.processEvents()
    narrow = grid.columns
    ui.resize(1920, 1080)
    for _ in range(5):
        ui.app.processEvents()
    assert narrow in (2, 3) and grid.columns == 6


def test_navigation_follows_accessibility_toggle(ui):
    """VoiceOver / accessibility 'press' on a sidebar item toggles it without a click."""
    for i in (3, 6, 0):
        ui.nav_buttons[i].toggle()
        ui.app.processEvents()
        assert ui.stack.currentIndex() == i and ui.nav_buttons[i].isChecked()
    assert sum(b.isChecked() for b in ui.nav_buttons) == 1


def test_v12_event_workflow_pages(ui):
    """Calendar, registrations, desk check-in, finalise, notifications, insights, monitoring, what-if."""
    from datetime import date

    from core.db.repositories import EventRepository, RegistrationRepository
    db = ui.ctx.db
    EventRepository(db).create(event_id="EVGUI", name="GUI day", event_type="Workshop",
                               event_date=date.today().isoformat(), time_slot="Morning", duration_hours=2,
                               capacity=2, registration_deadline=date.today().isoformat())
    for sid in ("STU001", "STU002"):
        RegistrationRepository(db).register(sid, "EVGUI", 2, 0)
    ui.ctx.notify("events")
    cal = page(ui, "EventCalendarPage")
    ui.go(ui.pages.index(cal))
    ui.app.processEvents()
    assert "EVGUI" in set(cal.frame["event_id"]) and cal.k[3].v.text() == "36"
    reg = page(ui, "RegistrationsPage")
    ui.go(ui.pages.index(reg))
    ui.app.processEvents()
    assert reg.k[0].v.text() == str(db.scalar("SELECT COUNT(*) FROM registrations"))
    att = page(ui, "AttendanceRecordsPage")
    ui.go(ui.pages.index(att))
    ui.app.processEvents()
    assert att.current() == "EVGUI"
    att.desk_sid.setText("STU001")
    att.desk_checkin()
    assert att.k[1].v.text() == "1" and att.k[3].v.text() == "1"
    att.finalise()
    assert att.k[2].v.text() == "1" and EventRepository(db).get("EVGUI")["status"] == "Completed"
    nt = page(ui, "NotificationsPage")
    ui.go(ui.pages.index(nt))
    ui.app.processEvents()
    assert nt.k[0].v.text().isdigit()
    for name in ("InsightsPage", "MonitoringPage", "ModelLabPage", "AnalyticsPage"):
        pg = page(ui, name)
        ui.go(ui.pages.index(pg))
        ui.app.processEvents()
        assert pg.notice.objectName() != "ErrorNotice", (name, pg.notice.lbl.text())
    pr = page(ui, "PredictionPage")
    ui.go(ui.pages.index(pr))
    pr.tabs.setCurrentIndex(3)
    ui.app.processEvents()
    i = next(k for k in range(pr.whatif.event.count()) if pr.whatif.event.itemText(k).startswith("EV22"))
    pr.whatif.event.setCurrentIndex(i)
    pr.whatif.fields["Has_Exam_Near_Event"][0].setChecked(True)
    pr.whatif.fields["Has_Exam_Near_Event"][1].setCurrentText("Yes")
    pr.whatif.run()
    assert pr.whatif.k[2].v.text().startswith("-")
    ui.ctx.open_event.emit("EVGUI")
    ui.app.processEvents()
    assert ui.stack.currentWidget() is page(ui, "EventsPage")
    assert ui.errors == []
