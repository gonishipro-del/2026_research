"""テスト共通: 状態・出力・キャッシュの場所を一時フォルダに差し替える（実データの状態を汚さない）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PILOT_DIR = Path(__file__).resolve().parents[1]
if str(PILOT_DIR) not in sys.path:
    sys.path.insert(0, str(PILOT_DIR))

import config  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    paths = {
        "CONFIG_YAML": tmp_path / "config.yaml",
        "OUTPUT_DIR": tmp_path / "output",
        "STATE_DIR": state_dir,
        "STATE_JSON": state_dir / "state.json",
        "STATE_LOCK": state_dir / ".state.lock",
        "MANUAL_OVERRIDES_CSV": state_dir / "manual_overrides.csv",
        "RUN_HISTORY_CSV": state_dir / "run_history.csv",
        "CONFIG_HISTORY_CSV": state_dir / "config_history.csv",
        "INPUTS_JSON": state_dir / "inputs.json",
        "INPUT_HISTORY_CSV": state_dir / "input_history.csv",
        "DATASETS_JSON": state_dir / "datasets.json",
        "DATASETS_DIR": tmp_path / "datasets",
        "TEXT_CACHE_DIR": tmp_path / "cache" / "pdf_text",
        "PAGE_IMAGE_CACHE_DIR": tmp_path / "cache" / "page_images",
        "PRICE_CACHE_DIR": tmp_path / "cache" / "prices",
        "MANUAL_PDF_DIR": tmp_path / "manual" / "pdfs",
    }
    for name, value in paths.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "INPUT_DIRS", {"factors": tmp_path / "inputs" / "factors",
                                               "master": tmp_path / "inputs" / "master"})
    for folder in config.INPUT_DIRS.values():
        folder.mkdir(parents=True)
    return tmp_path
