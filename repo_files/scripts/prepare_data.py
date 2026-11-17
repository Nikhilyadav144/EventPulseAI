"""Build data/processed/ from the original workbook.

Usage:
    python scripts/prepare_data.py                       # uses data/original/<workbook>
    python scripts/prepare_data.py --source ../College_Event_Attendance_ML_Final.xlsx
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dataset_pipeline import build_processed_dataset  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="Reference workbook to archive (copied unchanged, read-only)")
    a = ap.parse_args()
    df, meta = build_processed_dataset(a.source)
    print(f"Processed dataset: {meta['rows']} rows, {meta['students']} students, {meta['events']} events")
    print("Entity-level repair:", json.dumps(meta["entity_repair_filled"]))
    print("Left for pipeline imputation:", json.dumps(meta["remaining_missing"]))


if __name__ == "__main__":
    main()
