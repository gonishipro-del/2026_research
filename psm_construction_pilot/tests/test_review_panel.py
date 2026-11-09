"""要確認の処理タブ：表示の単位と共変量の単位の換算。"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from ui import review_panel  # noqa: E402


@pytest.mark.parametrize("variable, raw", [
    ("log_total_assets", math.log(8_412_419e6)),
    ("roa_ordinary", 0.068),
    ("leverage", 0.6407),
    ("pbr", 1.02),
    ("profit_change", -0.0123),
])
def test_display_and_input_units_round_trip(variable, raw):
    _, _, to_display, from_display = review_panel.UNITS[variable]
    assert from_display(to_display(raw)) == pytest.approx(raw)


def test_inputs_are_in_readable_units():
    assert review_panel.UNITS["log_total_assets"][3](8_412_419) == pytest.approx(math.log(8_412_419e6))  # 百万円
    assert review_panel.UNITS["leverage"][3](64.07) == pytest.approx(0.6407)  # %
    assert review_panel._display("leverage", "0.6407") == "64.070 %"
    assert review_panel._display("log_total_assets", str(math.log(8_412_419e6))) == "8,412,419 百万円"
