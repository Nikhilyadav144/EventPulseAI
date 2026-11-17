"""EventPulse AI - train/test split evidence (read-only demo).

Run from the project folder so the project's own code is used:
    cd "EventPulseAI"
    python "../EventPulseAI_Master_Guide/Demo_Files/split_evidence.py"

Uses ONLY the synthetic dataset (data/synthetic/synthetic_registrations.csv).
Writes nothing to the project, database or models.

Part A shows the actual 80:20 split used by the hosted model.
Part B compares 90:10, 80:20 and 67:33 (about 70:30) on 10 different student-grouped splits each,
with the deployed Logistic Regression settings (C = 0.01, threshold 0.63).
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path.cwd()))

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

from config import settings
from core.model_training import grouped_holdout_split, model_specs
from core.preprocessing import prepare_frame
from core.schema import MODEL_INPUTS

TARGET, GROUP = "Will_Attend", "Student_ID"
df = prepare_frame(pd.read_csv(settings.SYNTHETIC_DATASET))
df[TARGET] = df[TARGET].astype(int)

print("=" * 72)
print("EventPulse AI - train/test split evidence (synthetic data only)")
print("=" * 72)
print(f"Dataset: {len(df)} registrations, {df[GROUP].nunique()} students, "
      f"attend rate {df[TARGET].mean():.1%} (target is SIMULATED)\n")

# ---------------- Part A: the real 80:20 split -----------------------------
train, test = grouped_holdout_split(df)        # project function, 20%, seed 42
overlap = set(train[GROUP]) & set(test[GROUP])
print("PART A - the split actually used (core.model_training.grouped_holdout_split)")
print(f"  Train: {len(train)} rows / {train[GROUP].nunique()} students "
      f"({len(train)/len(df):.1%}), attend rate {train[TARGET].mean():.1%}")
print(f"  Test : {len(test)} rows / {test[GROUP].nunique()} students "
      f"({len(test)/len(df):.1%}), attend rate {test[TARGET].mean():.1%}")
print(f"  Students in BOTH train and test: {len(overlap)}  -> "
      f"{'PASS (no leakage)' if not overlap else 'FAIL'}")
print(f"  Rows add up: {len(train)} + {len(test)} = {len(train)+len(test)} -> "
      f"{'PASS' if len(train)+len(test)==len(df) else 'FAIL'}\n")

# ---------------- Part B: why 80:20 ----------------------------------------
spec = next(s for s in model_specs(fast=True) if s.name == "Logistic Regression")
THRESHOLD = 0.63
print("PART B - 90:10 vs 80:20 vs 67:33 (~70:30) (10 student-grouped splits each, same model)")
print(f"  {'Split':<7}{'Train rows':>11}{'Test rows':>10}{'AUC mean':>10}{'AUC spread':>12}"
      f"{'Acc mean':>10}{'Acc spread':>12}")
rows = []
for name, k in (("90:10", 10), ("80:20", 5), ("67:33", 3)):
    aucs, accs, ntr, nte = [], [], [], []
    for seed in range(10):
        sg = StratifiedGroupKFold(n_splits=k, shuffle=True, random_state=seed)
        tr, te = next(sg.split(df, df[TARGET], df[GROUP]))
        a, b = df.iloc[tr], df.iloc[te]
        m = spec.build().set_params(model__C=0.01).fit(a[MODEL_INPUTS], a[TARGET])
        p = m.predict_proba(b[MODEL_INPUTS])[:, 1]
        aucs.append(roc_auc_score(b[TARGET], p))
        accs.append(accuracy_score(b[TARGET], (p >= THRESHOLD).astype(int)))
        ntr.append(len(a)); nte.append(len(b))
    rows.append((name, np.mean(ntr), np.mean(nte), np.mean(aucs), np.min(aucs), np.max(aucs),
                 np.mean(accs), np.min(accs), np.max(accs)))
    r = rows[-1]
    print(f"  {name:<7}{r[1]:>11.0f}{r[2]:>10.0f}{r[3]:>10.3f}   {r[4]:.3f}-{r[5]:.3f}"
          f"{r[6]:>10.1%}   {r[7]:.1%}-{r[8]:.1%}")
print("\nHow to read Part B:")
print("  * 'spread' = lowest-highest result over 10 different splits.")
print("  * 90:10 tests on only ~79 rows, so its result swings the most (least reliable).")
print("  * 80:20 and 67:33 give similar, stable results; neither is clearly better.")
print("  * 80:20 keeps more rows for training while still testing on ~157 rows,")
print("    so it is a reasonable, standard choice - not a tuned 'best' ratio.")
print("  * Model choice itself used 5-fold group cross-validation on the training part only.")
