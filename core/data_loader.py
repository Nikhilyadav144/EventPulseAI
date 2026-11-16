"""Reading datasets safely.

Rules enforced here:
* Original reference files are only ever read, never written.
* From the reference workbook only the ``ML_Dataset`` sheet is read.
  ``Raw_Merged_Data`` (real names, emails, phone numbers, roll numbers) is never opened.
* Uploaded files containing personal-data columns are rejected.
"""
from __future__ import annotations

import hashlib
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from config import settings
from core.schema import COLUMN_ALIASES, FORBIDDEN_SHEETS, PII_COLUMNS

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}
MAX_FILE_BYTES = 50 * 1024 * 1024


class DataImportError(ValueError):
    """Raised when a file cannot be imported. The message is user-facing."""


@dataclass
class LoadResult:
    df: pd.DataFrame
    path: Path
    sheet: str | None
    sha256: str
    warnings: list[str] = field(default_factory=list)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalise(df: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if not c.startswith("Unnamed:")]]
    renames = {}
    for old, new in COLUMN_ALIASES.items():
        if old in df.columns and new not in df.columns:
            renames[old] = new
    if renames:
        df = df.rename(columns=renames)
        warnings.append("Renamed columns: " + ", ".join(f"{a} -> {b}" for a, b in renames.items()))
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            s = df[col].astype("string").str.strip()
            s = s.mask(s == "", pd.NA)
            df[col] = s.astype(object).where(s.notna(), None)
    df = df.dropna(how="all").reset_index(drop=True)
    return df


def _reject_pii(df: pd.DataFrame) -> None:
    found = sorted(PII_COLUMNS.intersection(df.columns))
    if found:
        raise DataImportError(
            "This file contains personal-data columns (" + ", ".join(found) + "). "
            "EventPulse AI does not import personal information. Remove these columns "
            "and use pseudonymous Student_IDs instead."
        )


def load_table(path: str | Path, sheet: str | None = None) -> LoadResult:
    """Load a user-supplied CSV/Excel file with validation of the container format."""
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise DataImportError(f"File not found: {path}")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise DataImportError(
            f"Unsupported file type '{ext or '(none)'}'. Use CSV (.csv) or Excel (.xlsx)."
        )
    if path.stat().st_size == 0:
        raise DataImportError("The file is empty.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise DataImportError("The file is larger than 50 MB.")

    warnings: list[str] = []
    chosen_sheet = None
    try:
        if ext == ".csv":
            df = pd.read_csv(path, encoding_errors="replace")
        else:
            xls = pd.ExcelFile(path, engine="openpyxl")
            names = [n for n in xls.sheet_names if n not in FORBIDDEN_SHEETS]
            if sheet is not None:
                if sheet in FORBIDDEN_SHEETS:
                    raise DataImportError(f"Sheet '{sheet}' contains personal data and cannot be imported.")
                if sheet not in xls.sheet_names:
                    raise DataImportError(f"Sheet '{sheet}' not found. Available: {', '.join(names)}")
                chosen_sheet = sheet
            elif settings.ML_SHEET in names:
                chosen_sheet = settings.ML_SHEET
            elif names:
                chosen_sheet = names[0]
                if len(names) > 1:
                    warnings.append(f"Workbook has several sheets; using the first one, '{chosen_sheet}'.")
            else:
                raise DataImportError("The workbook has no importable sheets.")
            df = pd.read_excel(xls, sheet_name=chosen_sheet)
    except DataImportError:
        raise
    except Exception as exc:  # corrupt / mislabelled files
        raise DataImportError(f"Could not read the file as {ext[1:].upper()}: {exc}") from exc

    _reject_pii(df)
    df = _normalise(df, warnings)
    if df.empty:
        raise DataImportError("The file has a header but no data rows.")
    return LoadResult(df=df, path=path, sheet=chosen_sheet, sha256=file_sha256(path), warnings=warnings)


def load_original_ml_dataset(workbook: str | Path | None = None) -> LoadResult:
    """Read the reference ML_Dataset sheet (read-only)."""
    workbook = Path(workbook or settings.ORIGINAL_WORKBOOK)
    return load_table(workbook, sheet=settings.ML_SHEET)


def archive_original(source: str | Path, dest_dir: str | Path | None = None) -> Path:
    """Copy a reference file into data/original/ unchanged and mark it read-only.

    Refuses to overwrite an existing archived file whose contents differ.
    """
    source = Path(source)
    dest_dir = Path(dest_dir or settings.ORIGINAL_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source.name
    src_hash = file_sha256(source)
    if dest.exists():
        if file_sha256(dest) != src_hash:
            raise FileExistsError(
                f"{dest} already exists with different contents; refusing to overwrite."
            )
        return dest
    shutil.copy2(source, dest)
    dest.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return dest
