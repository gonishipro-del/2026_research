"""ファクターCSVのパース（ヘッダ・フッタ、欠損コード、%変換）と検証。"""
from __future__ import annotations

import zipfile

import numpy as np
import pytest

from core import factors

HEADER = ("This file was created using the 202609 Bloomberg database.\n\n"
          "Missing data are indicated by -99.99.\n\n\n\n")
COLUMNS_5 = ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"


def _daily_rows(count: int = 400) -> str:
    import pandas as pd

    dates = pd.bdate_range("2024-01-01", periods=count)
    return "".join(f"{d:%Y%m%d}    ,1.00    ,-0.50   ,0.25   ,0.10    ,-0.20    ,0.01\n" for d in dates)


def _write(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_skips_header_and_stops_at_footer(tmp_path):
    body = HEADER + COLUMNS_5 + _daily_rows(5) + "\n Annual Factors: January-December \n" + COLUMNS_5 + "2024,1,2,3,4,5,6\n"
    percent, header, preamble = factors.parse_text(body)
    assert header == factors.FACTOR_COLUMNS
    assert len(percent) == 5  # フッタ以降（年次の表）は読まない
    assert preamble[0].startswith("This file was created")


def test_percent_converted_and_missing_codes_become_nan(tmp_path):
    rows = _daily_rows(300).splitlines()
    rows[3] = rows[3].replace("1.00", "-99.99", 1)
    rows[4] = rows[4].replace("0.25", "-999", 1)
    path = _write(tmp_path, "Japan_5_Factors_Daily.csv", HEADER + COLUMNS_5 + "\n".join(rows) + "\n")
    report = factors.validate(path)
    assert report["ok"], report["errors"]
    assert report["missing_counts"]["-99.99"]["Mkt-RF"] == 1
    assert report["missing_counts"]["-999.0"]["HML"] == 1
    loaded = factors.load(path)
    assert np.isnan(loaded["Mkt-RF"].iloc[3]) and np.isnan(loaded["HML"].iloc[4])
    assert loaded["Mkt-RF"].iloc[0] == pytest.approx(0.01)
    assert loaded["RF"].iloc[0] == pytest.approx(0.0001)


def test_zip_is_read(tmp_path):
    csv_body = HEADER + COLUMNS_5 + _daily_rows(300)
    path = tmp_path / "Japan_5_Factors_Daily_CSV.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Japan_5_Factors_Daily.csv", csv_body)
    report = factors.validate(path)
    assert report["ok"], report["errors"]
    assert report["rows"] == 300


def test_monthly_file_is_rejected(tmp_path):
    body = HEADER + COLUMNS_5 + "".join(f"2024{m:02d},1,2,3,4,5,0.1\n" for m in range(1, 13))
    report = factors.validate(_write(tmp_path, "Japan_5_Factors.csv", body))
    assert not report["ok"]
    assert any("日次" in error for error in report["errors"])


def test_three_factor_file_is_rejected(tmp_path):
    body = HEADER + ",Mkt-RF,SMB,HML,RF\n" + "".join(
        line.rsplit(",", 3)[0] + ",0.01\n" for line in _daily_rows(300).splitlines())
    report = factors.validate(_write(tmp_path, "Japan_3_Factors_Daily.csv", body))
    assert not report["ok"]
    assert any("5ファクター" in error for error in report["errors"])


@pytest.mark.parametrize("name", ["Developed_5_Factors_Daily.csv", "Asia_Pacific_ex_Japan_5_Factors_Daily.csv",
                                  "F-F_Research_Data_5_Factors_2x3_daily.csv"])
def test_other_regions_are_rejected(tmp_path, name):
    report = factors.validate(_write(tmp_path, name, HEADER + COLUMNS_5 + _daily_rows(300)))
    assert not report["ok"]
