"""Central configuration for EventPulse AI.

All paths are resolved relative to the project root so the application can be
moved or copied without editing code.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "EventPulse AI"
APP_TAGLINE = "Predict. Plan. Participate."
APP_VERSION = "1.2.0"


def _project_root() -> Path:
    """Folder holding data/, models/, database/ ...

    From source: the repository folder. From the packaged .app: EVENTPULSE_HOME, else
    the path recorded at build time (Contents/Resources/project_path.txt), else the
    folder that contains the .app.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent
    exe = Path(sys.executable).resolve()
    candidates = [os.environ.get("EVENTPULSE_HOME")]
    marker = exe.parent.parent / "Resources" / "project_path.txt"
    if marker.exists():
        candidates.append(marker.read_text().strip())
    candidates.append(str(exe.parents[3]) if len(exe.parents) > 3 else None)
    for c in candidates:
        if c and (Path(c) / "data").is_dir() and (Path(c) / "models").is_dir():
            return Path(c)
    raise SystemExit("EventPulse AI: project folder not found. Keep the app inside the EventPulseAI folder.")


PROJECT_ROOT = _project_root()

DATA_DIR = PROJECT_ROOT / "data"
ORIGINAL_DIR = DATA_DIR / "original"      # untouched copies of reference data (read-only)
PROCESSED_DIR = DATA_DIR / "processed"    # derived, documented datasets
SYNTHETIC_DIR = DATA_DIR / "synthetic"    # clearly-labelled synthetic data only
MODELS_DIR = PROJECT_ROOT / "models"
DATABASE_DIR = PROJECT_ROOT / "database"
EXPORTS_DIR = PROJECT_ROOT / "exports"
REPORTS_DIR = PROJECT_ROOT / "reports"
DEMO_DATABASE = DATABASE_DIR / "eventpulse_demo.db"   # Demo Mode: synthetic data only, never mixed

ORIGINAL_WORKBOOK_NAME = "College_Event_Attendance_ML_Final.xlsx"
ORIGINAL_WORKBOOK = ORIGINAL_DIR / ORIGINAL_WORKBOOK_NAME
ML_SHEET = "ML_Dataset"

PROCESSED_DATASET = PROCESSED_DIR / "ml_dataset_processed.csv"
PROCESSED_METADATA = PROCESSED_DIR / "ml_dataset_processed.metadata.json"
SYNTHETIC_DATASET = SYNTHETIC_DIR / "synthetic_registrations.csv"
SYNTHETIC_METADATA = SYNTHETIC_DIR / "synthetic_registrations.metadata.json"

RANDOM_SEED = 42
SYNTHETIC_SEED = 2026          # deliberately different from the original dataset's seed
HOLDOUT_FRACTION = 0.20
CV_FOLDS = 5

SIMULATED_TARGET_NOTICE = (
    "Will_Attend in this dataset is SIMULATED from a documented probability model, "
    "not recorded attendance. Metrics show how well a model recovers the simulation "
    "rules; they do not establish real-world attendance prediction performance."
)


def ensure_dirs() -> None:
    for d in (ORIGINAL_DIR, PROCESSED_DIR, SYNTHETIC_DIR, MODELS_DIR,
              DATABASE_DIR, EXPORTS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
