"""Model training, student-grouped evaluation and model comparison.

Protocol
1. One student-grouped, stratified holdout (20%) is set aside first. No student
   appears in both parts. It is used exactly once per model, for final reporting.
2. Hyper-parameters are chosen by GridSearchCV with StratifiedGroupKFold (groups =
   Student_ID) on the training part only.
3. The classification threshold is chosen from out-of-fold predictions on the
   training part only (maximum balanced accuracy).
4. The model with the best mean cross-validated ROC-AUC is recommended. The
   holdout plays no part in that choice.
5. Every result is reported next to the majority-class baseline.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_selection import SelectKBest, f_classif
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import (GridSearchCV, StratifiedGroupKFold, cross_val_predict,
                                     train_test_split)
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier

from config import settings
from core.data_loader import file_sha256
from core.data_validation import validate
from core.evaluation import (choose_threshold, classification_metrics, majority_baseline, roc_points)
from core.model_registry import save_model, set_active_model, slugify
from core.preprocessing import build_eventpulse_preprocessor, build_notebook_preprocessor, prepare_frame
from core.schema import MODEL_INPUTS, STUDENT_ID, TARGET


@dataclass
class ModelSpec:
    name: str
    build: Callable[[], Pipeline]
    grid: dict
    description: str
    is_baseline: bool = False


def _notebook_pipeline() -> Pipeline:
    return Pipeline([("prep", build_notebook_preprocessor()),
                     ("select", SelectKBest(f_classif, k=15)),
                     ("model", LogisticRegression(max_iter=1000, random_state=settings.RANDOM_SEED))])


def model_specs(fast: bool = False) -> list[ModelSpec]:
    seed = settings.RANDOM_SEED
    rf_trees = 100 if fast else 300
    specs = [
        ModelSpec("Logistic Regression (original baseline)", _notebook_pipeline, {},
                  "Exact replica of the CSL236 notebook: training-median distance binning, one-hot encoding, "
                  "scaling, SelectKBest(k=15), LogisticRegression(max_iter=1000). Not tuned.",
                  is_baseline=True),
        ModelSpec("Logistic Regression",
                  lambda: Pipeline([("prep", build_eventpulse_preprocessor()),
                                    ("model", LogisticRegression(max_iter=3000, random_state=seed))]),
                  {"model__C": [0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0]},
                  "Engineered pre-event features, ordinal Experience_Level, log counts, L2-regularised."),
        ModelSpec("Decision Tree",
                  lambda: Pipeline([("prep", build_eventpulse_preprocessor(scale=False)),
                                    ("model", DecisionTreeClassifier(random_state=seed))]),
                  {"model__max_depth": [2, 3, 4, 5, 6], "model__min_samples_leaf": [5, 10, 20]},
                  "Single CART tree; depth and leaf size tuned to limit overfitting."),
        ModelSpec("Random Forest",
                  lambda: Pipeline([("prep", build_eventpulse_preprocessor(scale=False)),
                                    ("model", RandomForestClassifier(n_estimators=rf_trees, random_state=seed,
                                                                     n_jobs=1))]),
                  {"model__max_depth": [4, 8, None], "model__min_samples_leaf": [3, 5, 10],
                   "model__max_features": ["sqrt", 0.5]},
                  f"{rf_trees} trees; depth, leaf size and features per split tuned."),
        ModelSpec("Gradient Boosting",
                  lambda: Pipeline([("prep", build_eventpulse_preprocessor(scale=False)),
                                    ("model", GradientBoostingClassifier(random_state=seed))]),
                  {"model__n_estimators": [100, 200], "model__learning_rate": [0.03, 0.1],
                   "model__max_depth": [2, 3]},
                  "Shallow boosted trees; number of trees, learning rate and depth tuned."),
    ]
    if fast:
        for s in specs:
            s.grid = {k: v[:2] for k, v in s.grid.items()}
    return specs


# ---------------------------------------------------------------------------
def grouped_holdout_split(df: pd.DataFrame, test_fraction: float = settings.HOLDOUT_FRACTION,
                          seed: int = settings.RANDOM_SEED):
    """Stratified split with no Student_ID in both parts."""
    n_splits = max(2, round(1 / test_fraction))
    sgkf = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    train_idx, test_idx = next(sgkf.split(df, df[TARGET], groups=df[STUDENT_ID]))
    return df.iloc[train_idx].reset_index(drop=True), df.iloc[test_idx].reset_index(drop=True)


def reproduce_original_result(raw_ml_dataset: pd.DataFrame) -> dict:
    """Re-run the notebook exactly (random 80:20 stratified split, seed 42) on the
    *unrepaired* ML_Dataset. Used to verify the historical 67.53% / 0.7932."""
    df = prepare_frame(raw_ml_dataset)
    X, y = df[MODEL_INPUTS], df[TARGET].astype(int)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.20, random_state=42, stratify=y)
    pipe = _notebook_pipeline().fit(Xtr, ytr)
    prob = pipe.predict_proba(Xte)[:, 1]
    m = classification_metrics(yte, prob, 0.5)
    return {"accuracy": m["accuracy"], "roc_auc": m["roc_auc"], "n_test": m["n"],
            "confusion_matrix": m["confusion_matrix"],
            "majority_baseline_accuracy": majority_baseline(ytr, yte)["accuracy"]}


@dataclass
class TrainingRun:
    dataset_label: str
    results: list[dict] = field(default_factory=list)
    recommended: str | None = None
    baseline: dict | None = None
    split: dict | None = None
    saved_dirs: dict = field(default_factory=dict)


def train_and_compare(df: pd.DataFrame, dataset_label: str, dataset_path: Path | None = None,
                      fast: bool = False, save: bool = True, models_dir: Path | None = None,
                      progress: Callable[[str], None] | None = None,
                      sensitivity_splits: int | None = None) -> TrainingRun:
    if sensitivity_splits is None:
        sensitivity_splits = 0 if fast else 10
    log = progress or (lambda m: None)
    rep = validate(df, mode="training")
    if not rep.ok:
        raise ValueError("Training data failed validation:\n" + rep.summary())
    if STUDENT_ID not in df.columns:
        raise ValueError("Student_ID is required for student-grouped evaluation.")

    data = prepare_frame(df)
    data[TARGET] = data[TARGET].astype(int)
    train, test = grouped_holdout_split(data)
    overlap = set(train[STUDENT_ID]) & set(test[STUDENT_ID])
    assert not overlap, "student leakage between train and holdout"
    Xtr, ytr, gtr = train[MODEL_INPUTS], train[TARGET], train[STUDENT_ID]
    Xte, yte = test[MODEL_INPUTS], test[TARGET]

    run = TrainingRun(dataset_label=dataset_label)
    run.split = {"method": "StratifiedGroupKFold holdout (groups = Student_ID)", "seed": settings.RANDOM_SEED,
                 "train_rows": len(train), "test_rows": len(test),
                 "train_students": int(gtr.nunique()), "test_students": int(test[STUDENT_ID].nunique()),
                 "students_in_both": 0,
                 "train_class_balance": {str(k): int(v) for k, v in ytr.value_counts().sort_index().items()},
                 "test_class_balance": {str(k): int(v) for k, v in yte.value_counts().sort_index().items()},
                 "holdout_student_ids": sorted(test[STUDENT_ID].unique().tolist())}
    run.baseline = majority_baseline(ytr, yte)

    cv = StratifiedGroupKFold(n_splits=settings.CV_FOLDS, shuffle=True, random_state=settings.RANDOM_SEED)
    scoring = {"roc_auc": "roc_auc", "accuracy": "accuracy", "balanced_accuracy": "balanced_accuracy",
               "f1": "f1", "precision": "precision", "recall": "recall", "f1_macro": "f1_macro"}
    reference = _reference_values(Xtr)
    fitted = {}

    for spec in model_specs(fast):
        t0 = time.time()
        log(f"Training {spec.name} ...")
        search = GridSearchCV(spec.build(), spec.grid or {}, scoring=scoring, refit="roc_auc", cv=cv,
                              n_jobs=1, error_score="raise")
        search.fit(Xtr, ytr, groups=gtr)
        best = search.best_index_
        cvres = {k: {"mean": float(search.cv_results_[f"mean_test_{k}"][best]),
                     "std": float(search.cv_results_[f"std_test_{k}"][best]),
                     "folds": [float(search.cv_results_[f"split{i}_test_{k}"][best])
                               for i in range(settings.CV_FOLDS)]}
                 for k in scoring}
        oof = cross_val_predict(search.best_estimator_, Xtr, ytr, groups=gtr, cv=cv, method="predict_proba")[:, 1]
        threshold = 0.5 if spec.is_baseline else choose_threshold(ytr, oof)
        model = search.best_estimator_  # refit on the full training part
        prob = model.predict_proba(Xte)[:, 1]
        hold = classification_metrics(yte, prob, threshold)
        hold_05 = classification_metrics(yte, prob, 0.5)
        importance = _permutation_importance(model, Xte, yte)
        res = {
            "model_id": slugify(spec.name), "name": spec.name, "description": spec.description,
            "is_baseline": spec.is_baseline, "best_params": _clean(search.best_params_),
            "cv": cvres, "cv_folds": settings.CV_FOLDS, "threshold": threshold,
            "threshold_rule": "fixed 0.5 (as in the original notebook)" if spec.is_baseline else
                              "maximises balanced accuracy on out-of-fold training predictions",
            "holdout": hold, "holdout_at_0_5": hold_05, "roc_curve": roc_points(yte, prob),
            "permutation_importance": importance, "train_seconds": round(time.time() - t0, 1),
            "probability_histogram": np.histogram(prob, bins=10, range=(0, 1))[0].tolist(),
        }
        run.results.append(res)
        log(f"  CV AUC {cvres['roc_auc']['mean']:.3f} ± {cvres['roc_auc']['std']:.3f}; "
            f"holdout AUC {hold['roc_auc']:.3f}, accuracy {hold['accuracy']:.3f}")
        fitted[res["model_id"]] = model

    if sensitivity_splits:
        log(f"Split-sensitivity check over {sensitivity_splits} student-grouped splits ...")
        sens = holdout_sensitivity(data, run.results, sensitivity_splits)
        for r in run.results:
            r["split_sensitivity"] = sens[r["model_id"]]
            cv_m, cv_s = r["cv"]["roc_auc"]["mean"], r["cv"]["roc_auc"]["std"]
            h = r["holdout"]["roc_auc"]
            r["holdout_note"] = (
                "The single holdout AUC is more than 1 CV standard deviation "
                f"{'above' if h > cv_m else 'below'} the cross-validated mean; with "
                f"{run.split['test_rows']} holdout rows this split is "
                f"{'easier' if h > cv_m else 'harder'} than average. Treat the CV mean and the "
                "split-sensitivity range as the realistic estimate."
                if abs(h - cv_m) > cv_s else
                "The holdout AUC is consistent with the cross-validated estimate.")

    if save:
        for r in run.results:
            meta = _metadata(r, run, dataset_label, dataset_path, reference, data)
            run.saved_dirs[r["model_id"]] = str(save_model(fitted[r["model_id"]], meta, models_dir))

    # Recommendation uses TRAINING cross-validation only.
    candidates = [r for r in run.results if not r["is_baseline"]]
    top = max(candidates, key=lambda r: r["cv"]["roc_auc"]["mean"])
    run.recommended = top["model_id"]
    close = [r["name"] for r in candidates if r is not top and
             top["cv"]["roc_auc"]["mean"] - r["cv"]["roc_auc"]["mean"] < top["cv"]["roc_auc"]["std"]]
    if save:
        md = Path(models_dir or settings.MODELS_DIR)
        set_active_model(run.recommended, md)
        summary = {
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset_label": dataset_label, "target_notice": settings.SIMULATED_TARGET_NOTICE,
            "split": run.split, "majority_baseline": run.baseline, "recommended": run.recommended,
            "recommendation_rule": "highest mean cross-validated ROC-AUC on the training part",
            "within_one_sd_of_recommended": close,
            "models": [{k: v for k, v in r.items() if k not in ("roc_curve",)} for r in run.results],
        }
        (md / "comparison.json").write_text(json.dumps(summary, indent=2))
    return run


def holdout_sensitivity(data: pd.DataFrame, results: list[dict], n_splits: int = 10) -> dict:
    """Supplementary analysis: how much does holdout ROC-AUC move between different
    student-grouped splits? Each model keeps the hyper-parameters chosen above.
    Not used for model selection."""
    specs = {slugify(s.name): s for s in model_specs()}
    out = {r["model_id"]: [] for r in results}
    for seed in range(n_splits):
        sg = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
        tr, te = next(sg.split(data, data[TARGET], data[STUDENT_ID]))
        a, b = data.iloc[tr], data.iloc[te]
        for r in results:
            p = specs[r["model_id"]].build().set_params(**r["best_params"])
            p.fit(a[MODEL_INPUTS], a[TARGET])
            out[r["model_id"]].append(float(roc_auc_score_safe(b[TARGET], p.predict_proba(b[MODEL_INPUTS])[:, 1])))
    return {k: {"splits": n_splits, "auc_values": [round(x, 4) for x in v], "mean": float(np.mean(v)),
                "min": float(np.min(v)), "max": float(np.max(v)),
                "note": "Hyper-parameters fixed from the main run; supplementary, not used for selection."}
            for k, v in out.items()}


def roc_auc_score_safe(y, p):
    from sklearn.metrics import roc_auc_score
    return roc_auc_score(y, p) if len(np.unique(y)) == 2 else float("nan")


# ---------------------------------------------------------------------------
def _reference_values(X: pd.DataFrame) -> dict:
    """Typical training values used for 'what-if' explanations."""
    ref = {}
    for c in MODEL_INPUTS:
        s = X[c]
        if pd.api.types.is_numeric_dtype(s):
            ref[c] = float(s.median())
        else:
            ref[c] = s.mode(dropna=True).iloc[0] if s.notna().any() else None
    return ref


def _permutation_importance(model, X, y) -> list[dict]:
    r = permutation_importance(model, X, y, scoring="roc_auc", n_repeats=20,
                               random_state=settings.RANDOM_SEED)
    items = [{"feature": c, "mean": float(m), "std": float(s)}
             for c, m, s in zip(X.columns, r.importances_mean, r.importances_std)]
    return sorted(items, key=lambda d: d["mean"], reverse=True)


def _metadata(res, run, label, path, reference, data) -> dict:
    return {
        "model_id": res["model_id"], "name": res["name"], "description": res["description"],
        "is_baseline": res["is_baseline"], "best_params": res["best_params"],
        "threshold": res["threshold"], "threshold_rule": res["threshold_rule"],
        "inputs": MODEL_INPUTS, "reference_values": reference,
        "trained_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "training_data": {"label": label, "file": Path(path).name if path else None,
                          "sha256": file_sha256(Path(path)) if path and Path(path).exists() else None,
                          "rows_total": int(len(data)), "rows_train": run.split["train_rows"]},
        "evaluation": {"split": {k: v for k, v in run.split.items() if k != "holdout_student_ids"},
                       "cv": res["cv"], "holdout": res["holdout"], "majority_baseline": run.baseline,
                       "split_sensitivity": res.get("split_sensitivity"), "holdout_note": res.get("holdout_note")},
        "permutation_importance": res["permutation_importance"],
        "roc_curve": res["roc_curve"], "probability_histogram": res["probability_histogram"],
        "holdout_student_ids": run.split["holdout_student_ids"],
        "target_notice": settings.SIMULATED_TARGET_NOTICE,
        "deployed_model_note": "This saved model is the one evaluated: fitted on the training part only.",
    }


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, (np.integer,)):
            v = int(v)
        elif isinstance(v, (np.floating,)):
            v = float(v)
        out[k] = v
    return out
