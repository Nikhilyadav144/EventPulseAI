import stat

import pandas as pd
import pytest

from core.data_loader import (DataImportError, archive_original, file_sha256, load_original_ml_dataset,
                              load_table)


def test_loads_only_ml_dataset_sheet(original_workbook):
    r = load_original_ml_dataset(original_workbook)
    assert r.sheet == "ML_Dataset"
    assert r.df.shape == (383, 24)
    for pii in ("Name", "Email_Address", "Mobile_No", "Roll_Number"):
        assert pii not in r.df.columns


def test_weather_renamed_to_forecast(raw_ml):
    assert "Weather_Forecast" in raw_ml.columns and "Weather" not in raw_ml.columns


def test_personal_data_sheet_is_refused(original_workbook):
    with pytest.raises(DataImportError, match="personal data"):
        load_table(original_workbook, sheet="Raw_Merged_Data")


def test_pii_columns_rejected(tmp_path):
    p = tmp_path / "with_pii.csv"
    pd.DataFrame({"Student_ID": ["S1"], "Email_Address": ["a@b.c"], "Year": [1]}).to_csv(p, index=False)
    with pytest.raises(DataImportError, match="personal-data"):
        load_table(p)


@pytest.mark.parametrize("name", ["data.txt", "data.json", "data.xls", "noext"])
def test_invalid_formats(tmp_path, name):
    p = tmp_path / name
    p.write_text("a,b\n1,2\n")
    with pytest.raises(DataImportError, match="Unsupported file type"):
        load_table(p)


def test_empty_and_header_only(tmp_path):
    e = tmp_path / "empty.csv"
    e.write_text("")
    with pytest.raises(DataImportError, match="empty"):
        load_table(e)
    h = tmp_path / "header.csv"
    h.write_text("Student_ID,Year\n")
    with pytest.raises(DataImportError, match="no data rows"):
        load_table(h)


def test_corrupt_excel(tmp_path):
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"this is not a zip file")
    with pytest.raises(DataImportError, match="Could not read"):
        load_table(p)


def test_missing_file():
    with pytest.raises(DataImportError, match="not found"):
        load_table("/no/such/file.csv")


def test_excel_upload_and_whitespace_cleaning(tmp_path):
    p = tmp_path / "upload.xlsx"
    pd.DataFrame({" Student_ID ": ["S1 ", "S2"], "Weather": ["Clear", ""]}).to_excel(p, index=False)
    r = load_table(p)
    assert list(r.df.columns) == ["Student_ID", "Weather_Forecast"]
    assert r.df.loc[0, "Student_ID"] == "S1"
    assert pd.isna(r.df.loc[1, "Weather_Forecast"])


def test_archive_original_is_read_only_and_never_overwritten(tmp_path):
    src = tmp_path / "ref.csv"
    src.write_text("a\n1\n")
    dest_dir = tmp_path / "original"
    d = archive_original(src, dest_dir)
    assert file_sha256(d) == file_sha256(src)
    assert not (d.stat().st_mode & stat.S_IWUSR)
    assert archive_original(src, dest_dir) == d        # identical copy: no-op
    src.chmod(0o644)
    src.write_text("a\n2\n")
    with pytest.raises(FileExistsError):
        archive_original(src, dest_dir)


def test_original_workbook_unchanged_by_pipeline(original_workbook, tmp_path):
    from core.dataset_pipeline import build_processed_dataset
    before = file_sha256(original_workbook)
    build_processed_dataset(None, tmp_path / "p.csv", tmp_path / "p.json")
    assert file_sha256(original_workbook) == before
