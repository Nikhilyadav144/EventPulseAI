"""Train, compare and save all models.

Usage:
    python scripts/train_models.py                    # processed ORIGINAL dataset (default)
    python scripts/train_models.py --dataset synthetic --models-dir models/synthetic
    python scripts/train_models.py --fast             # reduced grids (for quick checks)
"""
import argparse
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=UserWarning)

import pandas as pd  # noqa: E402

from config import settings  # noqa: E402
from core.data_loader import load_original_ml_dataset  # noqa: E402
from core.model_training import reproduce_original_result, train_and_compare  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["original", "synthetic"], default="original")
    ap.add_argument("--models-dir", default=None)
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()

    if a.dataset == "original":
        path = settings.PROCESSED_DATASET
        label = "ORIGINAL_HYBRID (processed ML_Dataset)"
        print("Historical check - original notebook pipeline on the unrepaired ML_Dataset:")
        h = reproduce_original_result(load_original_ml_dataset().df)
        print(f"  accuracy {h['accuracy']:.4f}, ROC-AUC {h['roc_auc']:.4f}, n_test {h['n_test']}, "
              f"majority baseline {h['majority_baseline_accuracy']:.4f}\n")
    else:
        path = settings.SYNTHETIC_DATASET
        label = "SYNTHETIC (generated, not real student data)"
    if not path.exists():
        sys.exit(f"{path} not found. Run scripts/prepare_data.py or scripts/generate_synthetic.py first.")
    df = pd.read_csv(path)
    models_dir = Path(a.models_dir) if a.models_dir else settings.MODELS_DIR
    print(settings.SIMULATED_TARGET_NOTICE, "\n")
    run = train_and_compare(df, label, dataset_path=path, fast=a.fast, models_dir=models_dir, progress=print)

    b = run.baseline
    print(f"\nHoldout: {run.split['test_rows']} rows / {run.split['test_students']} students "
          f"(no student in both parts)")
    print(f"{'Model':42s} {'CV AUC':>13s} {'AUC':>6s} {'Acc':>6s} {'BalAcc':>6s} {'F1':>6s} {'Thr':>5s}")
    print(f"{'Majority-class baseline':42s} {'':>13s} {0.5:6.3f} {b['accuracy']:6.3f} {0.5:6.3f} "
          f"{b['f1']:6.3f} {'':>5s}")
    for r in run.results:
        cv = r["cv"]["roc_auc"]
        h = r["holdout"]
        flag = "  <- recommended" if r["model_id"] == run.recommended else ""
        print(f"{r['name']:42s} {cv['mean']:.3f} ± {cv['std']:.3f} {h['roc_auc']:6.3f} {h['accuracy']:6.3f} "
              f"{h['balanced_accuracy']:6.3f} {h['f1']:6.3f} {r['threshold']:5.2f}{flag}")
    print(f"\nSaved to {models_dir}; active model = {run.recommended}")


if __name__ == "__main__":
    main()
