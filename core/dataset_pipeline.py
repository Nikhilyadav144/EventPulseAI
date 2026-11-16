"""Builds the processed training dataset from the untouched original workbook."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import settings
from core.data_loader import archive_original, file_sha256, load_original_ml_dataset
from core.data_validation import validate
from core.preprocessing import consolidate_entities, prepare_frame
from core.schema import FORECAST_UNAVAILABLE, MODEL_INPUTS, STUDENT_ID, EVENT_ID, TARGET

WEATHER_PROVENANCE = {
    "meaning": "Event-day weather FORECAST available when the prediction is made.",
    "original_dataset": (
        "Values in the original ML_Dataset are SIMULATED event-day weather (Generation_Log section 6/8). "
        "They are treated as if a forecast had been available at registration time. The time at which a "
        "forecast was obtained was not recorded for these rows (forecast_obtained_at = null)."
    ),
    "unavailable_policy": (
        f"A blank forecast is stored as the explicit category '{FORECAST_UNAVAILABLE}'. No weather value "
        "is ever invented. For new events the app records the forecast and the date/time it was obtained."
    ),
}


def build_processed_dataset(source_workbook: str | Path | None = None,
                            out_csv: Path | None = None,
                            out_meta: Path | None = None) -> tuple[pd.DataFrame, dict]:
    settings.ensure_dirs()
    out_csv = Path(out_csv or settings.PROCESSED_DATASET)
    out_meta = Path(out_meta or settings.PROCESSED_METADATA)

    if source_workbook is not None:
        archived = archive_original(source_workbook)
    else:
        archived = settings.ORIGINAL_WORKBOOK
        if not archived.exists():
            raise FileNotFoundError(f"Original workbook not found at {archived}")

    loaded = load_original_ml_dataset(archived)
    raw = loaded.df
    report = validate(raw, mode="training")
    if not report.ok:
        raise ValueError("Original dataset failed validation:\n" + report.summary())

    typed = prepare_frame(raw)
    # prepare_frame turned blank forecasts into 'Unavailable'; the repair step may
    # restore an event's forecast from its other registrations.
    repaired, log = consolidate_entities(typed)

    keep = [STUDENT_ID, EVENT_ID] + MODEL_INPUTS + ["Distance_Category", TARGET]
    processed = repaired[keep].copy()
    processed.insert(0, "Data_Source", "ORIGINAL_HYBRID")
    processed.to_csv(out_csv, index=False)

    meta = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": archived.name,
        "source_sheet": loaded.sheet,
        "source_sha256": loaded.sha256,
        "output_file": out_csv.name,
        "output_sha256": file_sha256(out_csv),
        "rows": int(len(processed)),
        "columns": list(processed.columns),
        "students": int(processed[STUDENT_ID].nunique()),
        "events": int(processed[EVENT_ID].nunique()),
        "class_balance": {str(k): int(v) for k, v in processed[TARGET].value_counts().sort_index().items()},
        "data_source_label": "ORIGINAL_HYBRID = real pseudonymised student profile fields + SIMULATED event "
                             "layer and SIMULATED target (see original workbook Generation_Log).",
        "target_notice": settings.SIMULATED_TARGET_NOTICE,
        "steps": [
            "Read sheet ML_Dataset only (Raw_Merged_Data is never opened).",
            "Validated against core/schema.py.",
            "Renamed Weather -> Weather_Forecast; blank forecasts -> 'Unavailable'.",
            "Entity-level repair: a blank student-level value was filled only from the same Student_ID's "
            "other rows, a blank event-level value only from the same Event_ID's other rows. "
            "The target was never used. Remaining blanks are imputed inside the model pipeline "
            "(fitted on training rows only).",
            "Distance_Category rebuilt from Distance_From_Venue_KM (kept for analytics, not a model input).",
            "Added Data_Source column.",
        ],
        "entity_repair_filled": log.filled,
        "remaining_missing": {k: v for k, v in log.remaining_missing.items()},
        "weather_forecast": WEATHER_PROVENANCE,
        "validation": {"errors": report.errors, "warnings": report.warnings, "info": report.info},
    }
    out_meta.write_text(json.dumps(meta, indent=2))
    return processed, meta
