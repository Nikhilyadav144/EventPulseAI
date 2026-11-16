"""Evaluation metrics, baselines and curve data."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, brier_score_loss, confusion_matrix,
                             f1_score, precision_score, recall_score, roc_auc_score, roc_curve)


def classification_metrics(y_true, prob, threshold: float = 0.5) -> dict:
    y_true = np.asarray(y_true).astype(int)
    prob = np.asarray(prob, dtype=float)
    pred = (prob >= threshold).astype(int)
    cm = confusion_matrix(y_true, pred, labels=[0, 1])
    two_classes = len(np.unique(y_true)) == 2
    return {
        "n": int(len(y_true)),
        "threshold": float(threshold),
        "accuracy": float(accuracy_score(y_true, pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, pred)) if two_classes else None,
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "recall_non_attenders": float(recall_score(y_true, pred, pos_label=0, zero_division=0)),
        "precision_non_attenders": float(precision_score(y_true, pred, pos_label=0, zero_division=0)),
        "f1_macro": float(f1_score(y_true, pred, average="macro", zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, prob)) if two_classes else None,
        "brier": float(brier_score_loss(y_true, prob)),
        "confusion_matrix": cm.tolist(),           # [[TN, FP], [FN, TP]]
        "predicted_attendees": int(pred.sum()),
        "expected_attendees": float(prob.sum()),   # sum of probabilities
        "actual_attendees": int(y_true.sum()),
    }


def majority_baseline(y_train, y_test) -> dict:
    """Always predict the training majority class, with the training prevalence as probability."""
    y_train = np.asarray(y_train).astype(int)
    prevalence = float(y_train.mean())
    majority = int(prevalence >= 0.5)
    prob = np.full(len(y_test), prevalence)
    # threshold chosen so every prediction is the majority class
    thr = 0.0 if majority == 1 else 1.01
    m = classification_metrics(y_test, prob, threshold=thr)
    m["roc_auc"] = 0.5
    m["description"] = (f"Always predicts '{'attend' if majority else 'not attend'}' "
                        f"(training prevalence {prevalence:.1%}).")
    return m


def roc_points(y_true, prob, max_points: int = 200) -> dict:
    fpr, tpr, thr = roc_curve(np.asarray(y_true).astype(int), np.asarray(prob, float))
    if len(fpr) > max_points:
        idx = np.unique(np.linspace(0, len(fpr) - 1, max_points).astype(int))
        fpr, tpr, thr = fpr[idx], tpr[idx], thr[idx]
    thr = np.where(np.isfinite(thr), thr, 1.0)
    return {"fpr": fpr.round(4).tolist(), "tpr": tpr.round(4).tolist(), "thresholds": thr.round(4).tolist()}


def choose_threshold(y_true, prob) -> float:
    """Threshold maximising balanced accuracy. Call ONLY with out-of-fold
    predictions on training data, never with the holdout set."""
    y_true = np.asarray(y_true).astype(int)
    prob = np.asarray(prob, float)
    grid = np.round(np.linspace(0.20, 0.80, 61), 2)
    scores = [balanced_accuracy_score(y_true, (prob >= t).astype(int)) for t in grid]
    best = float(grid[int(np.argmax(scores))])
    return best


def attendance_interval(prob, z: float = 1.96) -> tuple[float, float, float]:
    """Expected head-count and an approximate 95% range, treating registrations as
    independent Bernoulli trials with the predicted probabilities."""
    p = np.asarray(prob, float)
    mean = float(p.sum())
    sd = float(np.sqrt((p * (1 - p)).sum()))
    return mean, max(0.0, mean - z * sd), min(float(len(p)), mean + z * sd)
