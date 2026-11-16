"""PDF, Excel and CSV report generation.

All content is computed from the database and model metadata at generation time.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import settings
from core import analytics as A
from core import charts as C
from core.db.connection import Database
from core.db.repositories import DatasetRepository

SECTIONS = ("project", "dataset", "models", "predictions", "events", "analytics", "findings")


@dataclass
class ReportData:
    generated: str
    kpis: A.OverviewKPIs
    datasets: pd.DataFrame
    models: pd.DataFrame
    active_details: dict | None
    events: pd.DataFrame
    departments: pd.DataFrame
    years: pd.DataFrame
    event_types: pd.DataFrame
    prob: dict
    pva: pd.DataFrame
    error_summary: dict
    recent: pd.DataFrame
    importance: pd.DataFrame
    findings: list[str]
    sources: dict = field(default_factory=dict)


_SRC = {"ORIGINAL_HYBRID": "Original dataset", "SYNTHETIC": "Synthetic demo", "MANUAL": "Entered in app",
        "UPLOAD": "Uploaded file"}


def collect(db: Database) -> ReportData:
    kpis = A.overview_kpis(db)
    models = A.model_comparison(db)
    active = models[models["active"]] if len(models) else models
    details = A.model_details(db, int(active["model_version_id"].iloc[0])) if len(active) else None
    rd = ReportData(
        generated=datetime.now().strftime("%d %b %Y, %H:%M"),
        kpis=kpis, datasets=pd.DataFrame(DatasetRepository(db).list()), models=models, active_details=details,
        events=A.event_trends(db), departments=A.participation_by(db, "Department"),
        years=A.participation_by(db, "Year"), event_types=A.participation_by(db, "Event_Type"),
        prob=A.probability_distribution(db), pva=A.predicted_vs_actual(db),
        error_summary=A.prediction_error_summary(db, include_simulated=True), recent=A.recent_predictions(db, 25),
        importance=A.feature_importance(db), findings=[], sources=A.data_source_summary(db))
    rd.findings = key_findings(rd)
    return rd


def key_findings(rd: ReportData) -> list[str]:
    """Plain-language findings derived from the data. Every sentence is computed."""
    out = []
    m = rd.models
    if len(m):
        cand = m[~m["baseline"]].dropna(subset=["cv_roc_auc"])
        if len(cand):
            best = cand.iloc[0]
            out.append(f"{best['model']} has the highest cross-validated ROC-AUC "
                       f"({best['cv_roc_auc']:.3f} ± {best['cv_roc_auc_std']:.3f}).")
            close = cand[(best["cv_roc_auc"] - cand["cv_roc_auc"] < (best["cv_roc_auc_std"] or 0)) &
                         (cand["model"] != best["model"])]
            if len(close):
                out.append("Within one standard deviation of it: " + ", ".join(close["model"]) +
                           " - the data cannot reliably separate these models.")
        base = m[m["baseline"]]
        if len(base) and base["cv_roc_auc"].notna().any():
            out.append(f"The original notebook pipeline (baseline) reaches CV ROC-AUC {base['cv_roc_auc'].iloc[0]:.3f}.")
        if rd.kpis.accuracy is not None and rd.kpis.baseline_accuracy is not None:
            diff = rd.kpis.accuracy - rd.kpis.baseline_accuracy
            out.append(f"Active-model holdout accuracy is {rd.kpis.accuracy:.1%} versus {rd.kpis.baseline_accuracy:.1%} "
                       f"for always predicting 'attend' ({diff:+.1%}).")
        if rd.active_details and rd.active_details.get("holdout_note"):
            out.append(rd.active_details["holdout_note"])
    if len(rd.importance):
        top = ", ".join(rd.importance["feature"].head(3))
        out.append(f"Most influential inputs for the active model (permutation importance): {top}. These mirror terms "
                   "of the documented simulation formula, so they show the model recovering the simulation rules.")
    d = rd.departments.dropna(subset=["attendance_rate"])
    d = d[d["outcomes"] >= 10]
    if len(d) >= 2:
        hi, lo = d.loc[d["attendance_rate"].idxmax()], d.loc[d["attendance_rate"].idxmin()]
        out.append(f"Recorded attendance rate is highest in {hi['Department']} ({hi['attendance_rate']:.0%}, "
                   f"n={int(hi['outcomes'])}) and lowest in {lo['Department']} ({lo['attendance_rate']:.0%}, "
                   f"n={int(lo['outcomes'])}).")
    if rd.kpis.expected_attendance is not None:
        out.append(f"Across {rd.kpis.predicted_registrations} predicted registrations the active model expects "
                   f"{rd.kpis.expected_attendance:.1f} attendees ({rd.kpis.predicted_attendees} above the threshold).")
    es = rd.error_summary
    if es.get("events"):
        out.append(f"Post-event comparisons for {es['events']} event(s): mean error {es['mean_error']:+.1f} attendees, "
                   f"mean absolute error {es['mean_absolute_error']:.1f}"
                   + (f" ({es['mean_percentage_error']:.1f}%)" if es.get("mean_percentage_error") is not None else "")
                   + (" - includes SIMULATED outcomes." if es.get("includes_simulated") else "."))
    if rd.kpis.recorded_attendance_simulated:
        out.append(settings.SIMULATED_TARGET_NOTICE)
    return out


# ======================================================================== PDF
def generate_pdf(db: Database, path: str | Path, sections: tuple[str, ...] = SECTIONS) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rd = collect(db)
    tmp = Path(tempfile.mkdtemp(prefix="ep_report_"))
    ss = getSampleStyleSheet()
    navy = colors.HexColor("#29263a")          # EventPulse ink
    accent = colors.HexColor("#6956a5")        # EventPulse purple
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], textColor=navy, fontSize=16, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=navy, fontSize=12.5, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13, alignment=TA_LEFT)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#6b6680"))
    notice = ParagraphStyle("n", parent=body, backColor=colors.HexColor("#fbf3e2"), borderPadding=6,
                            borderColor=colors.HexColor("#e9bc65"), borderWidth=0.6, fontSize=8.8,
                            textColor=colors.HexColor("#5c4210"))

    def table(df: pd.DataFrame, widths=None, fmt=None):
        fmt = fmt or {}
        cell = ParagraphStyle("cell", parent=body, fontSize=7.6, leading=9.2)
        data = [list(df.columns)]
        for _, r in df.iterrows():
            data.append([Paragraph(fmt[c](r[c]) if c in fmt and pd.notna(r[c]) else
                                   ("—" if pd.isna(r[c]) else str(r[c])), cell) for c in df.columns])
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), accent), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 7.8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f4fb")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2ddea")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        return t

    def chart(fig, name, width_cm=16):
        p = tmp / f"{name}.png"
        C.save_png(fig, p, dpi=170)
        w, h = fig.get_size_inches()
        return Image(str(p), width=width_cm * cm, height=width_cm * cm * h / w)

    f3 = lambda v: f"{v:.3f}"  # noqa: E731
    p1 = lambda v: f"{v:.1%}"  # noqa: E731
    story = []
    k = rd.kpis
    if "project" in sections:
        logo = settings.PROJECT_ROOT / "assets" / "logo_mark.png"
        title = [Paragraph(f"{settings.APP_NAME} — Attendance Analytics Report", h1),
                 Paragraph(f"<i>{settings.APP_TAGLINE}</i> &nbsp;·&nbsp; Version {settings.APP_VERSION} "
                           f"&nbsp;·&nbsp; Generated {rd.generated}", small)]
        if logo.exists():
            head = Table([[Image(str(logo), width=1.25 * cm, height=1.25 * cm), title]],
                         colWidths=[1.6 * cm, 15.4 * cm])
            head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
            story.append(head)
        else:
            story += title
        story += [
                  Spacer(1, 6),
                  Paragraph("AI-Based College Event Attendance Prediction System (CSL236, The NorthCap University). "
                            "The system predicts whether a registered student will attend a college event and "
                            "estimates expected attendance for planning food, seating and volunteers.", body),
                  Spacer(1, 6), Paragraph("<b>Important:</b> " + settings.SIMULATED_TARGET_NOTICE, notice),
                  Spacer(1, 8)]
        kp = pd.DataFrame([
            ["Students", f"{k.students:,}"], ["Events", f"{k.events:,} ({k.completed_events} completed, "
                                                      f"{k.upcoming_events} scheduled)"],
            ["Registrations", f"{k.registrations:,}"],
            ["Active model", f"{k.model_name} v{k.model_version}" if k.model_name else "—"],
            ["CV ROC-AUC (5 student-grouped folds)",
             f"{k.cv_roc_auc:.3f} ± {k.cv_roc_auc_std:.3f}" if k.cv_roc_auc is not None else "—"],
            ["Holdout accuracy / ROC-AUC",
             f"{k.accuracy:.1%} / {k.roc_auc:.3f}" if k.accuracy is not None else "—"],
            ["Majority-class baseline accuracy", f"{k.baseline_accuracy:.1%}" if k.baseline_accuracy else "—"],
            ["Predicted registrations / expected attendance",
             f"{k.predicted_registrations} / {k.expected_attendance}" if k.predicted_registrations else "none yet"],
            ["Attendance outcomes - SIMULATED (dataset)", f"{rd.sources['simulated_outcomes']:,}"],
            ["Attendance outcomes - recorded after real events", f"{rd.sources['observed_outcomes']:,}"],
            ["Data sources (registrations)", ", ".join(f"{_SRC.get(s, s)}: {n:,}"
                                                       for s, n in rd.sources["registrations"].items()) or "—"],
        ], columns=["Indicator", "Value"])
        story.append(table(kp, widths=[7.5 * cm, 9 * cm]))
    if "dataset" in sections and len(rd.datasets):
        ds = rd.datasets[["dataset_id", "name", "kind", "file_name", "row_count", "imported_utc"]].copy()
        ds.columns = ["ID", "Name", "Kind", "File", "Rows", "Imported (UTC)"]
        story += [Paragraph("Dataset summary", h2),
                  Paragraph("Datasets imported into the application database. Personal data (the Raw_Merged_Data "
                            "sheet) is never imported.", small), Spacer(1, 3),
                  table(ds, widths=[1.2 * cm, 5.2 * cm, 2.2 * cm, 4.2 * cm, 1.4 * cm, 3.2 * cm])]
    if "models" in sections and len(rd.models):
        mt = rd.models[["model", "version", "cv_roc_auc", "cv_roc_auc_std", "holdout_roc_auc", "accuracy",
                        "balanced_accuracy", "precision", "recall", "f1", "majority_baseline_accuracy"]].copy()
        mt.columns = ["Model", "v", "CV AUC", "± SD", "Holdout AUC", "Accuracy", "Bal. acc.", "Precision", "Recall",
                      "F1", "Baseline acc."]
        story += [PageBreak(), Paragraph("Model performance", h2),
                  Paragraph("Student-grouped holdout (no student in both training and test data); hyper-parameters "
                            "chosen by 5-fold student-grouped cross-validation on training data only. The holdout "
                            "is small, so the CV column is the more reliable estimate.", small), Spacer(1, 3),
                  table(mt, fmt={c: f3 for c in ["CV AUC", "± SD", "Holdout AUC", "Accuracy", "Bal. acc.",
                                                 "Precision", "Recall", "F1", "Baseline acc."]})]
        det = rd.active_details
        if det:
            roc_fig = C.roc({det["version"]["name"]: det["roc_curve"]}, theme=C.LIGHT, size=(4.6, 3.9))
            cm_fig = C.confusion(det["confusion_matrix"], theme=C.LIGHT, size=(4.2, 3.9))
            story.append(Spacer(1, 6))
            story.append(Table([[chart(roc_fig, "roc", 8.4), chart(cm_fig, "cm", 7.8)]]))
            if len(rd.importance):
                fi = C.bar(rd.importance["feature"], rd.importance["mean"], "Permutation importance (active model)",
                           C.LIGHT, horizontal=True, errors=rd.importance["std"], ylabel="ROC-AUC drop when shuffled",
                           value_labels=False, size=(6.4, 3.8))
                story.append(KeepTogether([chart(fi, "importance", 15)]))
    if "predictions" in sections:
        story += ([PageBreak()] if story else []) + [Paragraph("Prediction summary", h2)]
        if k.predicted_registrations:
            story.append(Paragraph(f"{k.predicted_registrations} registration(s) have a prediction from the active "
                                   f"model: {k.predicted_attendees} above the decision threshold, "
                                   f"{k.predicted_non_attendees} below; expected attendance "
                                   f"{k.expected_attendance} (sum of probabilities).", body))
            story.append(chart(C.histogram(rd.prob["counts"], rd.prob["edges"], "Predicted probability distribution",
                                           C.LIGHT, threshold=float(rd.models[rd.models["active"]]["threshold"].iloc[0])
                                           if rd.models["active"].any() else None, size=(6.4, 3.0)), "hist", 14))
            if len(rd.recent):
                story.append(table(rd.recent[["predicted_utc", "student_id", "event_id", "probability", "outcome",
                                              "certainty", "model"]].head(15)))
        else:
            story.append(Paragraph("No predictions have been stored yet.", body))
    if "events" in sections and len(rd.events):
        ev = rd.events
        story += [PageBreak(), Paragraph("Event-wise analysis", h2)]
        story.append(chart(C.bar(ev["event_id"], ev["attendance_rate"], "Recorded attendance rate by event",
                                 C.LIGHT, percent=True, value_labels=False,
                                 subtitle="Simulated outcomes" if k.recorded_attendance_simulated else None,
                                 size=(7.2, 3.0)), "events", 16.5))
        et = ev[["event_id", "event_type", "event_day", "time_slot", "registrations", "recorded_attended",
                 "attendance_rate", "expected_attendance_active_model"]].copy()
        et.columns = ["Event", "Type", "Day", "Time", "Regs", "Attended", "Rate", "Expected (model)"]
        story.append(table(et, fmt={"Rate": p1, "Expected (model)": lambda v: f"{v:.1f}"}))
        if len(rd.pva):
            pv = rd.pva[["event_id", "model", "method", "expected_attendance", "actual_attendees", "error",
                         "percentage_error", "target_is_simulated"]].copy()
            pv.columns = ["Event", "Model", "Method", "Expected", "Actual", "Error", "% error", "Simulated"]
            story += [Paragraph("Predicted vs actual attendance", h2),
                      table(pv, fmt={"Expected": lambda v: f"{v:.1f}", "Error": lambda v: f"{v:+.1f}",
                                     "% error": lambda v: f"{v:.1f}%", "Simulated": lambda v: "yes" if v else "no"})]
    if "analytics" in sections and len(rd.departments):
        dep = rd.departments
        story += [Paragraph("Participation analysis", h2),
                  Table([[chart(C.bar(dep["Department"], dep["registrations"], "Registrations by department", C.LIGHT,
                                      size=(4.6, 3.2)), "dept_regs", 8.2),
                          chart(C.bar(dep["Department"], dep["attendance_rate"], "Attendance rate by department",
                                      C.LIGHT, percent=True, slot=2, size=(4.6, 3.2)), "dept_rate", 8.2)]]),
                  Table([[chart(C.bar(rd.years["Year"], rd.years["attendance_rate"], "Attendance rate by year",
                                      C.LIGHT, percent=True, size=(4.6, 3.0)), "year", 8.2),
                          chart(C.bar(rd.event_types["Event_Type"], rd.event_types["attendance_rate"],
                                      "Attendance rate by event type", C.LIGHT, percent=True, slot=2,
                                      size=(4.6, 3.0)), "etype", 8.2)]])]
    if "findings" in sections:
        story += [Paragraph("Key findings", h2)]
        story += [Paragraph(f"• {f}", body) for f in rd.findings] or [Paragraph("Not enough data yet.", body)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawString(2 * cm, 1.2 * cm, f"{settings.APP_NAME} · {rd.generated} · simulated-target dataset")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.setStrokeColor(accent)
        canvas.line(2 * cm, A4[1] - 1.3 * cm, A4[0] - 2 * cm, A4[1] - 1.3 * cm)
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=1.8 * cm, title=f"{settings.APP_NAME} report", author=settings.APP_NAME)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path


# ======================================================================== Excel / CSV
def _write_sheet(ws, df: pd.DataFrame, pct_cols=(), dec_cols=()):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    ws.append(list(df.columns))
    for r in df.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and pd.isna(v)) else (v.item() if hasattr(v, "item") else v)
                   for v in r])
    head = PatternFill("solid", fgColor="6956A5")
    for c in ws[1]:
        c.fill, c.font, c.alignment = head, Font(bold=True, color="FFFFFF"), Alignment(vertical="center")
    for i, col in enumerate(df.columns, 1):
        letter = get_column_letter(i)
        width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) + 2
        ws.column_dimensions[letter].width = min(max(width, 9), 48)
        fmt = "0.0%" if col in pct_cols else ("0.000" if col in dec_cols else None)
        if fmt:
            for cell in ws[letter][1:]:
                cell.number_format = fmt
    ws.freeze_panes = "A2"
    if len(df):
        ws.auto_filter.ref = ws.dimensions


def generate_excel(db: Database, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rd = collect(db)
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    k = rd.kpis
    summary = pd.DataFrame([
        ["Report", f"{settings.APP_NAME} attendance analytics"], ["Generated", rd.generated],
        ["Notice", settings.SIMULATED_TARGET_NOTICE], ["Students", k.students], ["Events", k.events],
        ["Registrations", k.registrations], ["Active model", f"{k.model_name} v{k.model_version}"],
        ["CV ROC-AUC", k.cv_roc_auc], ["CV ROC-AUC SD", k.cv_roc_auc_std], ["Holdout accuracy", k.accuracy],
        ["Holdout ROC-AUC", k.roc_auc], ["Majority baseline accuracy", k.baseline_accuracy],
        ["Predicted registrations", k.predicted_registrations], ["Expected attendance", k.expected_attendance],
        ["Outcomes - SIMULATED (dataset)", rd.sources["simulated_outcomes"]],
        ["Outcomes - recorded after real events", rd.sources["observed_outcomes"]],
        ["Version", settings.APP_VERSION],
    ] + [[f"{_SRC.get(s, s)} registrations", n] for s, n in rd.sources["registrations"].items()]
      + [[f"Finding {i}", f] for i, f in enumerate(rd.findings, 1)], columns=["Item", "Value"])
    _write_sheet(ws, summary)
    ws.column_dimensions["B"].width = 110
    _write_sheet(wb.create_sheet("Model Comparison"), rd.models.drop(columns=["model_version_id"]),
                 dec_cols=[c for c in rd.models.columns if c not in ("model", "version", "active", "baseline",
                                                                      "trained_utc", "sklearn", "tn", "fp", "fn", "tp")])
    ev = rd.events.drop(columns=[c for c in ("name",) if c in rd.events.columns])
    _write_sheet(wb.create_sheet("Event Analysis"), ev, pct_cols=["attendance_rate"])
    dws = wb.create_sheet("Departments")
    _write_sheet(dws, rd.departments, pct_cols=["attendance_rate"])
    if len(rd.departments):
        ch = BarChart()
        ch.title, ch.y_axis.title = "Registrations by department", "Registrations"
        n = len(rd.departments) + 1
        ch.add_data(Reference(dws, min_col=2, min_row=1, max_row=n), titles_from_data=True)
        ch.set_categories(Reference(dws, min_col=1, min_row=2, max_row=n))
        ch.height, ch.width = 7.5, 16
        dws.add_chart(ch, "H2")
    _write_sheet(wb.create_sheet("Years"), rd.years, pct_cols=["attendance_rate"])
    _write_sheet(wb.create_sheet("Event Types"), rd.event_types, pct_cols=["attendance_rate"])
    _write_sheet(wb.create_sheet("Predicted vs Actual"), rd.pva)
    hist = pd.DataFrame(db.query(
        "SELECT p.prediction_id, p.predicted_utc, COALESCE(r.student_id, p.student_ref) student_id, "
        "COALESCE(r.event_id, p.event_ref) event_id, p.probability, p.predicted_attend, p.threshold, p.certainty, "
        "p.forecast_used, mv.name model, mv.version model_version, pr.run_type FROM predictions p "
        "JOIN model_versions mv USING(model_version_id) JOIN prediction_runs pr USING(run_id) "
        "LEFT JOIN registrations r USING(registration_id) ORDER BY p.prediction_id"))
    _write_sheet(wb.create_sheet("Prediction History"), hist, dec_cols=["probability", "threshold"])
    _write_sheet(wb.create_sheet("Feature Importance"), rd.importance, dec_cols=["mean", "std"])
    wb.save(path)
    return path


def export_frame(df: pd.DataFrame, path: str | Path, sheet: str = "Data") -> Path:
    """Export any table to .csv or .xlsx depending on the extension."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False)
    elif path.suffix.lower() == ".xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = sheet[:31]
        _write_sheet(ws, df, pct_cols=[c for c in df.columns if c.endswith("_rate")],
                     dec_cols=[c for c in df.columns if "probability" in c.lower()])
        wb.save(path)
    else:
        raise ValueError("Use a .csv or .xlsx file name.")
    return path
