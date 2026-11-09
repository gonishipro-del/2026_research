"""段階4（前半）: 傾向スコア（ロジット）の推定。

ai_flag ~ 共変量（config の covariates）。対象は1業種なので業種は説明変数に入れない。
距離には線形予測子（ロジットの傾向スコア）を使う。
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm

from core import review


def load_analysis(run_id: str, covariates: list[str]) -> pd.DataFrame:
    """段階3の分析対象（in_analysis=1）。index は証券コード。"""
    dataset, _ = review.build_dataset(run_id)
    frame = dataset[dataset["in_analysis"] == 1].copy()
    frame["code"] = frame["code"].astype(str)
    frame = frame.set_index("code")
    frame["ai_flag"] = frame["ai_flag"].astype(int)
    for column in covariates:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def fit(frame: pd.DataFrame, covariates: list[str]) -> dict:
    """ロジットを推定し、係数・標準誤差・疑似R²・線形予測子を返す。"""
    design = sm.add_constant(frame[covariates].astype(float), has_constant="add")
    notes: list[str] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = sm.Logit(frame["ai_flag"].astype(float), design).fit(disp=0, maxiter=200)
    notes += [str(warning.message) for warning in caught]
    converged = bool(model.mle_retvals.get("converged", True))
    if not converged:
        notes.append("ロジットの収束に失敗（推定値は参考）")
    if (model.params.abs() > 30).any() or (model.bse > 30).any():
        notes.append("準完全分離の疑い（係数または標準誤差が極端に大きい）")
    linear = pd.Series(design.to_numpy() @ model.params.to_numpy(), index=frame.index, name="lp")
    return {
        "coef": {k: float(v) for k, v in model.params.items()},
        "se": {k: float(v) for k, v in model.bse.items()},
        "pseudo_r2": float(model.prsquared),
        "converged": converged,
        "n": int(len(frame)),
        "n_treated": int(frame["ai_flag"].sum()),
        "n_control": int((frame["ai_flag"] == 0).sum()),
        "warnings": notes,
        "lp": linear,
        "ps": 1 / (1 + np.exp(-linear)),
    }


def config_name(replacement: bool, caliper_sd: float) -> str:
    return f"{'rep' if replacement else 'norep'}_c{caliper_sd:g}"


def matching_grid(settings: dict) -> list[tuple[bool, float]]:
    """感度分析を含む（復元あり・なし）×（キャリパー）の組み合わせ。"""
    calipers = sorted(set(settings["caliper_grid"]) | {settings["caliper_sd"]})
    return [(replacement, caliper) for replacement in (False, True) for caliper in calipers]
