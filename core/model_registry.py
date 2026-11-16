"""Persisting trained pipelines with metadata."""
from __future__ import annotations

import json
import platform
import re
from datetime import datetime, timezone
from pathlib import Path

import joblib
import sklearn

from config import settings


class ModelCompatibilityError(RuntimeError):
    """The saved model was produced by an incompatible scikit-learn version."""


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def save_model(pipeline, metadata: dict, models_dir: Path | None = None) -> Path:
    models_dir = Path(models_dir or settings.MODELS_DIR)
    target = models_dir / metadata["model_id"]
    target.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, target / "model.joblib")
    meta = dict(metadata)
    meta.setdefault("saved_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    meta["environment"] = {"python": platform.python_version(), "scikit_learn": sklearn.__version__}
    (target / "metadata.json").write_text(json.dumps(meta, indent=2, default=_json_default))
    return target


def load_metadata(model_dir: Path) -> dict:
    return json.loads((Path(model_dir) / "metadata.json").read_text())


def load_model(model_dir: Path):
    model_dir = Path(model_dir)
    meta = load_metadata(model_dir)
    saved = meta.get("environment", {}).get("scikit_learn", "?")
    if saved.split(".")[:2] != sklearn.__version__.split(".")[:2]:
        raise ModelCompatibilityError(
            f"Model was saved with scikit-learn {saved}; this environment has {sklearn.__version__}. "
            "Retrain the models (python scripts/train_models.py) to continue."
        )
    return joblib.load(model_dir / "model.joblib"), meta


def list_models(models_dir: Path | None = None) -> list[dict]:
    models_dir = Path(models_dir or settings.MODELS_DIR)
    out = []
    for d in sorted(models_dir.glob("*/metadata.json")):
        m = json.loads(d.read_text())
        m["path"] = str(d.parent)
        out.append(m)
    return out


def set_active_model(model_id: str, models_dir: Path | None = None) -> None:
    models_dir = Path(models_dir or settings.MODELS_DIR)
    if not (models_dir / model_id / "model.joblib").exists():
        raise FileNotFoundError(f"No saved model '{model_id}'.")
    (models_dir / "active_model.json").write_text(json.dumps({"model_id": model_id}, indent=2))


def active_model_dir(models_dir: Path | None = None) -> Path:
    models_dir = Path(models_dir or settings.MODELS_DIR)
    f = models_dir / "active_model.json"
    if not f.exists():
        raise FileNotFoundError("No active model. Train models first (python scripts/train_models.py).")
    return models_dir / json.loads(f.read_text())["model_id"]


def _json_default(o):
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)
