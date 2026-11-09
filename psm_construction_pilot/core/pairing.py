"""マッチングの結果を、企業ごと・ペアごとに見やすい形にまとめる（画面表示用）。

言及あり（処置）・言及なし（対照）の各企業が、マッチした／脱落した／使われなかったのか、
誰とペアになったのかを企業名つきで返す。計算はせず、段階1・2・5（と7）の出力を読むだけ。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import ai_count, ingest, propensity, runs, stages

TREATED_MATCHED = "言及あり・ペアになった"
TREATED_DROPPED = "言及あり・脱落（相手なし）"
CONTROL_MATCHED = "言及なし・ペアになった"
CONTROL_UNUSED = "言及なし・使われなかった"
ROLES = [TREATED_MATCHED, TREATED_DROPPED, CONTROL_MATCHED, CONTROL_UNUSED]


def _names(run_id: str) -> pd.Series:
    return ingest.load_targets(run_id).set_index("code")["name"]


def load(run_id: str, config_name: str) -> dict:
    """{nodes, pairs, caliper_abs}。nodes は企業ごと、pairs はペアごとの表。"""
    out = runs.stage_dir(run_id, stages.MATCHING)
    names = _names(run_id)
    scores = pd.read_csv(out / "propensity.csv", dtype={"code": str}).set_index("code")
    pairs = pd.read_csv(out / "matches" / f"{config_name}.csv", dtype={"treated": str, "control": str})
    grid = pd.read_csv(out / "matching_grid.csv")
    caliper_abs = float(grid.loc[grid["config"] == config_name, "caliper_abs"].iloc[0])
    flags = ai_count.load_flags(run_id).set_index("code")

    nodes = scores.copy()
    nodes["name"] = nodes.index.map(names)
    nodes["ai_count"] = pd.to_numeric(nodes.index.map(flags["ai_count"]), errors="coerce")
    matched_t, matched_c = set(pairs["treated"]), set(pairs["control"])
    nodes["role"] = [
        (TREATED_MATCHED if code in matched_t else TREATED_DROPPED) if flag == 1
        else (CONTROL_MATCHED if code in matched_c else CONTROL_UNUSED)
        for code, flag in zip(nodes.index, nodes["ai_flag"])
    ]
    partners = {}
    for code in nodes.index:
        if code in matched_t:
            partners[code] = "、".join(pairs.loc[pairs["treated"] == code, "control"].map(names))
        elif code in matched_c:
            partners[code] = "、".join(pairs.loc[pairs["control"] == code, "treated"].map(names))
        else:
            partners[code] = ""
    nodes["partner"] = pd.Series(partners)
    nodes["times_used"] = [int((pairs["control"] == code).sum()) if flag == 0 else int(code in matched_t)
                           for code, flag in zip(nodes.index, nodes["ai_flag"])]
    nodes["ps_pct"] = nodes["ps"] * 100

    # 同じ段の点が重ならないよう、段内で少しだけ縦にずらす（ペアの線も同じ位置につなぐ）
    order = nodes.sort_values("lp").groupby("ai_flag").cumcount().reindex(nodes.index)
    nodes["y"] = np.where(nodes["ai_flag"] == 1, 1.0, 0.0) + ((order.to_numpy() % 4) - 1.5) * 0.045

    pairs = pairs.assign(
        treated_name=pairs["treated"].map(names), control_name=pairs["control"].map(names),
        ps_treated=pairs["treated"].map(scores["ps"]), ps_control=pairs["control"].map(scores["ps"]),
        y_treated=pairs["treated"].map(nodes["y"]), y_control=pairs["control"].map(nodes["y"]),
    )
    pairs["label"] = pairs["treated_name"] + " ↔ " + pairs["control_name"]
    return {"nodes": nodes, "pairs": pairs, "caliper_abs": caliper_abs}


def comparison(run_id: str, settings: dict, treated: str, control: str, nodes: pd.DataFrame) -> pd.DataFrame:
    """ペアの2社を、傾向スコアの説明変数などで横に並べた表（値は読みやすい単位に直す）。"""
    frame = propensity.load_analysis(run_id, settings["covariates"])
    rows = [
        ("AI言及の数（有効なヒット）", nodes.loc[treated, "ai_count"], nodes.loc[control, "ai_count"], "{:.0f}"),
        ("傾向スコア（言及しやすさ・%）", nodes.loc[treated, "ps_pct"], nodes.loc[control, "ps_pct"], "{:.1f}"),
    ]
    labels = {
        "log_total_assets": ("総資産（億円）", lambda v: float(np.exp(v)) / 1e8, "{:,.0f}"),
        "roa_ordinary": ("ROA（経常利益÷総資産・%）", lambda v: v * 100, "{:.2f}"),
        "leverage": ("負債比率（負債÷総資産・%）", lambda v: v * 100, "{:.1f}"),
        "pbr": ("PBR（倍）", lambda v: v, "{:.2f}"),
        "profit_change": ("利益の変化（Δ経常利益÷前期末総資産・%）", lambda v: v * 100, "{:.2f}"),
    }
    for column in settings["covariates"]:
        label, convert, fmt = labels.get(column, (column, lambda v: v, "{:.4g}"))
        rows.append((label, convert(frame.loc[treated, column]), convert(frame.loc[control, column]), fmt))
    return pd.DataFrame([{"項目": label, "言及あり（処置）": fmt.format(t), "言及なし（対照）": fmt.format(c)}
                         for label, t, c, fmt in rows])


def cars(run_id: str, treated: str, control: str, column: str) -> tuple[float, float] | None:
    """段階6のCAR（あれば）。(処置, 対照)。"""
    path = runs.stage_dir(run_id, stages.CAR) / "car.csv"
    if not path.exists():
        return None
    table = pd.read_csv(path, dtype={"code": str}).set_index("code")
    if column not in table.columns or treated not in table.index or control not in table.index:
        return None
    t, c = table.loc[treated, column], table.loc[control, column]
    return None if pd.isna(t) or pd.isna(c) else (float(t), float(c))
