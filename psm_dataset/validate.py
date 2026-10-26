"""抽出値の妥当性チェック。異常値は値を捨てず、備考用の文字列として返す。"""
from __future__ import annotations

from . import config


def check_record(record: dict, cand: dict, equity: float | None) -> list[str]:
    notes: list[str] = []

    leverage = record.get("leverage")
    low, high = config.LEVERAGE_RANGE
    if leverage is not None and not (low <= leverage <= high):
        notes.append(f"【妥当性】レバレッジ{leverage:.3f}が{low}〜{high}の範囲外")

    # サマリー頁の自己資本比率と、自己資本÷総資産の整合
    total_assets, ratio = record.get("total_assets"), cand.get("equity_ratio")
    if equity is not None and total_assets and ratio is not None:
        own_ratio = equity / total_assets * 100
        if abs(own_ratio - ratio) > config.EQUITY_RATIO_TOLERANCE_PT:
            notes.append(f"【妥当性】自己資本比率の不整合（開示{ratio:.1f}% / 算出{own_ratio:.1f}%）")

    deviation = record.get("bps_deviation")
    if deviation is not None and abs(deviation) > config.BPS_DEVIATION_TOLERANCE:
        notes.append(f"【妥当性】BPSの乖離（自前{record['bps_calc']:,.2f} / 開示{record['bps_disclosed']:,.2f}、{deviation:+.1%}）")

    if record.get("pbr") is not None and record["pbr"] <= 0:
        notes.append("【妥当性】PBRが0以下（自己資本が負の可能性）")
    return notes
