"""Model Laboratory: compare model versions, inspect evaluation, activate, retrain."""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
from PySide6.QtWidgets import QCheckBox, QComboBox, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from config import settings
from core import analytics as A
from core import charts as C
from core.db import services
from core.db.repositories import ModelVersionRepository
from gui import theme
from gui.pages.base import Page
from gui.widgets import (ChartView, DataTable, KpiCard, ResponsiveGrid, button, card, confirm, fmt_num, fmt_pct,
                         hbox, label, run_task, show_error)

METRICS = {
    "CV ROC-AUC": ("curve", "purple",
                   "How well the model ranks attenders above non-attenders for students it has not seen, averaged "
                   "over 5 student-grouped folds. 0.5 = chance, 1.0 = perfect. This is the primary estimate."),
    "Holdout ROC-AUC": ("curve", "blue",
                        "The same measure on one 20% student holdout. It is a single split, so it can be luckier or "
                        "harder than average - the range over 10 other splits shows how much it moves."),
    "Accuracy": ("check", "blue",
                 "Share of holdout registrations predicted correctly. Always compare it with the majority baseline "
                 "(predicting 'attend' for everyone)."),
    "F1-score": ("spark", "purple",
                 "Balance of precision (predicted attendees who did attend) and recall (attendees the model found)."),
    "No-show recall": ("cross", "gold",
                               "Share of actual no-shows the model flags - what matters when planning for empty "
                               "seats."),
    "Threshold": ("prediction", "neutral",
                  "Probability at which a registration counts as 'will attend'. Chosen on training data (best "
                  "balanced accuracy), never on the holdout."),
}


class ModelLabPage(Page):
    title = "Model Laboratory"
    subtitle = ("Logistic Regression (original baseline and tuned), Decision Tree, Random Forest and Gradient "
                "Boosting, evaluated on a student-grouped holdout with 5-fold student-grouped cross-validation.")
    areas = ("models",)

    def build(self):
        self.selected: int | None = None
        self.table = DataTable()
        self.table.setMinimumHeight(240)
        self.table.row_selected.connect(self._select)
        self.btn_activate = button("Set as active model", "Primary", self.activate)
        self.sel_label = label("", "Secondary")
        self.body.addWidget(card(self.table, hbox(self.btn_activate, (self.sel_label, 1)), title="Model versions",
                                 subtitle="Select a row to inspect it. The active model (highlighted) makes new "
                                          "predictions. The recommendation is the highest mean cross-validated "
                                          "ROC-AUC on training data; the holdout is never used to choose."))
        self.k = {n: KpiCard(n, tone=t, icon=i, tooltip=tip) for n, (i, t, tip) in METRICS.items()}
        self.note = label("", "Muted")
        self.body.addWidget(card(ResponsiveGrid(list(self.k.values()), min_width=150, allowed=(6, 3, 2), spacing=12),
                                 self.note, title="Selected model", subtitle="Hover a metric for its meaning."))
        cells = []
        for name, (_ic, _t, tip) in METRICS.items():
            cell = QWidget()
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(2)
            cl.addWidget(label(name, "SubTitle"))
            cl.addWidget(label(tip, "Muted"))
            cells.append(cell)
        gw0 = ResponsiveGrid(cells, min_width=300, allowed=(2, 1), spacing=14)
        self.body.addWidget(card(gw0, label("Results describe how well a model recovers the dataset's SIMULATED "
                                            "attendance rules. Treat the cross-validated mean and the split-sensitivity "
                                            "range as the realistic estimate; a single holdout number is not more "
                                            "reliable than cross-validation.", "Secondary"),
                                 title="How to read these metrics", eyebrow="Model explanation"))
        self.roc = ChartView(min_height=330)
        self.cm = ChartView(min_height=330)
        self.body.addWidget(ResponsiveGrid(
            [card(self.roc, title="ROC curve", subtitle="Selected model vs the original baseline (holdout)."),
             card(self.cm, title="Confusion matrix", subtitle="Holdout predictions at the decision threshold.")],
            min_width=380, allowed=(2, 1)))
        self.cv_table = DataTable(searchable=False, filterable=False)
        self.cv_table.setMinimumHeight(230)
        self.imp = ChartView(min_height=330)
        self.body.addWidget(ResponsiveGrid(
            [card(self.cv_table, title="Cross-validation (5 student-grouped folds)",
                  subtitle="Per-fold results on training students; the mean is the primary estimate."),
             card(self.imp, title="Feature importance", subtitle="Permutation importance on the holdout "
                                                                 "(drop in ROC-AUC when shuffled).")],
            min_width=380, allowed=(2, 1)))

        # ---- training
        self.t_data = QComboBox()
        self.t_data.addItems(["Processed original dataset (simulated target)", "Synthetic dataset"])
        self.t_fast = QCheckBox("Quick run")
        self.t_activate = QCheckBox("Activate recommended model")
        self.t_activate.setChecked(True)
        self.t_btn = button("Train and compare models", "Primary", self.train)
        self.t_bar = QProgressBar()
        self.t_bar.setRange(0, 0)
        self.t_bar.setVisible(False)
        self.t_log = QPlainTextEdit()
        self.t_log.setReadOnly(True)
        self.t_log.setMaximumHeight(200)
        self.body.addWidget(card(hbox(label("Training data", wrap=False), (self.t_data, 1), self.t_btn),
                                 hbox(self.t_fast, self.t_activate, "stretch"), self.t_bar, self.t_log,
                                 title="Retrain",
                                 subtitle="Each run is saved to its own folder and registered as new model versions; "
                                          "earlier versions and their prediction history are kept."))

    def _build_mlflow(self):
        self.mlf = DataTable(filterable=False)
        self.mlf.setMinimumHeight(260)
        self.mlf_note = label("", "Muted")
        self.body.addWidget(card(self.mlf, self.mlf_note, title="MLflow experiment runs (end-term pipeline)",
                                 subtitle="Every train.py run logged to mlflow.db (SQLite): parameters, cross-validated "
                                          "and holdout metrics. The best mean CV ROC-AUC becomes model/model.joblib, "
                                          "which this app registers as 'end-term' and the web API serves."))

    def refresh(self):
        if not hasattr(self, "mlf"):
            self._build_mlflow()
        runs = A.mlflow_runs()
        best = None
        mf = settings.PROJECT_ROOT / "model" / "metadata.json"
        if mf.exists():
            best = json.loads(mf.read_text()).get("mlflow_run_id")
        if len(runs):
            runs["saved_model"] = runs["run_id"].map(lambda r: "● model.joblib" if r == best else "")
        f3 = lambda v: "—" if pd.isna(v) else f"{float(v):.3f}"  # noqa: E731
        self.mlf.set_frame(runs, formats={c: f3 for c in runs.columns if c.startswith(("cv_", "holdout_", "midterm"))},
                           row_tone=lambda r: "purple" if r.get("saved_model") else None)
        self.mlf_note.setText(f"{len(runs)} run(s), {runs['algorithm'].nunique()} algorithm(s). Open the full MLflow "
                              "UI with: mlflow ui --backend-store-uri sqlite:///mlflow.db" if len(runs) else
                              "No MLflow runs yet: run python train.py --sweep (or EventPulse_EndTerm_Run.command).")
        mc = A.model_comparison(self.ctx.db)
        self.mc = mc
        cols = ["model", "version", "active", "cv_roc_auc", "cv_roc_auc_std", "holdout_roc_auc", "accuracy",
                "majority_baseline_accuracy", "balanced_accuracy", "precision", "recall", "f1", "threshold",
                "trained_utc"]
        f3 = lambda v: f"{v:.3f}"  # noqa: E731
        self.table.set_frame(mc[cols] if len(mc) else pd.DataFrame(columns=cols),
                             formats={c: f3 for c in cols if c not in ("model", "version", "active", "trained_utc")}
                             | {"active": lambda v: "● active" if v else ""},
                             tones={"active": lambda v: "purple" if v else None},
                             row_tone=lambda r: "purple" if bool(r.get("active")) else None)
        if len(mc):
            sel = self.selected if self.selected in set(mc["model_version_id"]) else \
                int(mc[mc["active"]]["model_version_id"].iloc[0]) if mc["active"].any() else int(mc["model_version_id"].iloc[0])
            self.show_model(sel)
        else:
            self.notice.set("No models registered. Train models below.", "info")

    def _select(self, row: int):
        self.show_model(int(self.mc["model_version_id"].iloc[row]))     # table rows follow self.mc order

    def show_model(self, mv_id: int):
        self.selected = mv_id
        th = theme.current().chart
        d = A.model_details(self.ctx.db, mv_id)
        cv, hold, base = d["cv"], d["holdout"], d["baseline"]
        self.k["CV ROC-AUC"].set(f"{cv['roc_auc']['mean']:.3f}" if cv.get("roc_auc") else "—",
                                 f"± {cv['roc_auc']['std']:.3f} across folds" if cv.get("roc_auc") else "")
        sens = d.get("split_sensitivity") or {}
        self.k["Holdout ROC-AUC"].set(fmt_num(hold.get("roc_auc")),
                                      f"10 other splits: {sens['min']:.2f}–{sens['max']:.2f}" if sens else "")
        self.k["Accuracy"].set(fmt_pct(hold.get("accuracy")), f"baseline {fmt_pct(base.get('accuracy'))}")
        self.k["F1-score"].set(fmt_num(hold.get("f1")), f"precision {fmt_num(hold.get('precision'))} · recall "
                                                        f"{fmt_num(hold.get('recall'))}")
        self.k["No-show recall"].set(fmt_pct(hold.get("recall_non_attenders")),
                                             "share of no-shows correctly flagged")
        mv = d["version"]
        self.k["Threshold"].set(f"{mv['threshold']:.2f}", "chosen on training data only")
        params = json.loads(mv["hyperparameters"] or "{}")
        self.note.setText(f"{mv['name']} v{mv['version']} · trained {mv['trained_utc']} · scikit-learn "
                          f"{mv['sklearn_version']} · parameters: "
                          f"{', '.join(f'{k.split(chr(95) * 2)[-1]}={v}' for k, v in params.items()) or 'none'}"
                          + (f"\n{d['holdout_note']}" if d.get("holdout_note") else ""))
        curves = {f"{mv['name']}": d["roc_curve"]}
        base_row = self.mc[self.mc["baseline"]]
        if len(base_row) and int(base_row["model_version_id"].iloc[0]) != mv_id:
            curves["Original baseline"] = A.model_details(self.ctx.db, int(base_row["model_version_id"].iloc[0]))["roc_curve"]
        self.roc.set_figure(C.roc(curves, theme=th, size=(5.2, 4.0)))
        self.cm.set_figure(C.confusion(d["confusion_matrix"], theme=th, size=(5.0, 4.0)))
        rows = []
        for metric, v in cv.items():
            rows.append({"metric": metric, "mean": v["mean"], "std": v["std"],
                         **{f"fold {i + 1}": x for i, x in enumerate(v.get("folds", []))}})
        self.cv_table.set_frame(pd.DataFrame(rows), formats={c: (lambda x: f"{x:.3f}") for c in
                                                             ["mean", "std"] + [f"fold {i}" for i in range(1, 6)]})
        imp = pd.DataFrame(d["importance"][:12])
        if len(imp):
            self.imp.set_figure(C.bar(imp["feature"], imp["mean"], "Permutation importance", th, horizontal=True,
                                      errors=imp["std"], value_labels=False, ylabel="ROC-AUC drop",
                                      size=(6.0, 4.2)))
        self.btn_activate.setEnabled(not bool(mv["is_active"]))
        self.sel_label.setText(f"Showing {mv['name']} v{mv['version']}" +
                               (" - the active model." if mv["is_active"] else " - not active."))

    def activate(self):
        if self.selected is None:
            return
        mv = ModelVersionRepository(self.ctx.db).get(self.selected)
        problems = A.model_support_problems(self.ctx.db, self.selected)
        if problems:
            show_error(self, "This model cannot be activated", "\n".join(problems))
            return
        if confirm(self, "Activate model", f"Use {mv['name']} v{mv['version']} for all new predictions?", "Activate"):
            ModelVersionRepository(self.ctx.db).set_active(self.selected)
            self.ctx.status.emit(f"Active model: {mv['name']} v{mv['version']}")
            self.ctx.notify("models")

    def train(self):
        synthetic = self.t_data.currentIndex() == 1
        fast, activate = self.t_fast.isChecked(), self.t_activate.isChecked()
        run_dir = settings.MODELS_DIR / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.t_btn.setEnabled(False)
        self.t_bar.setVisible(True)
        self.t_log.setPlainText(f"Training into {run_dir} …")

        def work(progress):
            from core.model_training import train_and_compare
            path = settings.SYNTHETIC_DATASET if synthetic else settings.PROCESSED_DATASET
            label_ = "SYNTHETIC (generated, not real student data)" if synthetic else \
                "ORIGINAL_HYBRID (processed ML_Dataset)"
            run = train_and_compare(pd.read_csv(path), label_, dataset_path=path, fast=fast, models_dir=run_dir,
                                    progress=progress)
            with self.ctx.open_db() as db:
                ids = services.sync_models(db, run_dir, activate=activate)
            return run, ids

        def done(res):
            run, ids = res
            self.t_btn.setEnabled(True)
            self.t_bar.setVisible(False)
            self.t_log.appendPlainText(f"Done. Registered {len(ids)} model version(s); recommended: {run.recommended}"
                                       + (" (activated)." if activate else "."))
            self.ctx.notify("models")

        def failed(m, d):
            self.t_btn.setEnabled(True)
            self.t_bar.setVisible(False)
            self.t_log.appendPlainText("FAILED: " + m)
            show_error(self, "Training failed", m, d)

        run_task(work, on_done=done, on_error=failed, on_progress=self.t_log.appendPlainText)
