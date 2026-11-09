"""入力ファイル（ファクター・上場銘柄一覧）の登録・検証・差し替え。

各入力フォルダには現在のファイルを1つだけ置く。差し替えたときは旧版を archive/ に移して残し、
下流の段階を「要再実行」にする。ファイル名・更新日時・SHA256 は state/inputs.json と
state/input_history.csv に記録する。

単体実行: python -m core.inputs   （両方の入力を検証して登録し、結果を表示）
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import config
from core import changes, factors, master, runs, stages, state

KINDS = {"factors": "ファクター（Japan 5 Factors 日次）", "master": "上場銘柄一覧（data_j.xlsx）"}
ALLOWED_SUFFIXES = {"factors": {".csv", ".zip"}, "master": {".xlsx"}}
HISTORY_COLUMNS = ["timestamp", "kind", "event", "file", "modified_at", "sha256", "ok", "detail"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_info(path: Path) -> dict:
    return {
        "file": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "sha256": sha256(path),
    }


def candidates(kind: str) -> list[Path]:
    folder = config.INPUT_DIRS[kind]
    return sorted(path for path in folder.iterdir()
                  if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES[kind])


def current_file(kind: str) -> Path:
    found = candidates(kind)
    if not found:
        raise FileNotFoundError(f"{KINDS[kind]} のファイルが {config.INPUT_DIRS[kind]} にありません")
    if len(found) > 1:
        raise ValueError(f"{config.INPUT_DIRS[kind]} にファイルが複数あります（1つにしてください）: "
                         f"{[path.name for path in found]}")
    return found[0]


def validate_file(kind: str, path: Path) -> dict:
    if kind == "factors":
        return factors.validate(path)
    targets = runs.run_config(runs.current_run()).get("target_industries")
    return master.validate(path, targets)


def registry() -> dict:
    return state.read_json(config.INPUTS_JSON, {})


def _record(kind: str, event: str, info: dict, report: dict) -> None:
    with state.file_lock(config.INPUTS_JSON.with_suffix(".lock")):
        current = registry()
        current[kind] = {**info, "registered_at": state.now(), "validation": report}
        state.write_json_atomic(config.INPUTS_JSON, current)
    state.append_csv(config.INPUT_HISTORY_CSV, HISTORY_COLUMNS, {
        "timestamp": state.now(), "kind": kind, "event": event, "file": info["file"],
        "modified_at": info["modified_at"], "sha256": info["sha256"], "ok": int(report["ok"]),
        "detail": "; ".join(report["errors"] + report.get("warnings", [])),
    })


def register_current(kind: str) -> dict:
    """フォルダの現在のファイルを検証し、記録が無いか中身が変わっていれば登録する。"""
    path = current_file(kind)
    info = file_info(path)
    known = registry().get(kind)
    if known and known["sha256"] == info["sha256"] and known["file"] == info["file"]:
        return known
    report = validate_file(kind, path)
    event = "登録" if not known else "フォルダ内のファイルが変更された"
    _record(kind, event, info, report)
    if known:
        # アプリを通さずに置き換えられた場合も、下流を「要再実行」にする
        what = f"入力ファイル（{KINDS[kind]}）の変更: {known['file']} → {info['file']}"
        run_id, _ = changes.begin_upstream_change(what, stages.INPUT_FIRST_STAGE[kind])
        changes.finish_upstream_change(run_id, what, stages.INPUT_FIRST_STAGE[kind])
    return registry()[kind]


def is_valid(kind: str) -> tuple[bool, str]:
    """段階の実行前に呼ぶ。入力が検証済みでなければ理由を返す。"""
    try:
        entry = register_current(kind)
    except (FileNotFoundError, ValueError) as error:
        return False, str(error)
    if not entry["validation"]["ok"]:
        return False, f"{KINDS[kind]} の検証に失敗: {'; '.join(entry['validation']['errors'])}"
    return True, ""


def replace(kind: str, filename: str, content: bytes) -> tuple[bool, dict]:
    """アップロードされたファイルで差し替える。検証に通らなければ差し替えずに結果を返す。"""
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES[kind]:
        return False, {"ok": False, "errors": [f"拡張子は {sorted(ALLOWED_SUFFIXES[kind])} のいずれか"],
                       "warnings": []}
    with tempfile.TemporaryDirectory() as workdir:
        staged = Path(workdir) / Path(filename).name
        staged.write_bytes(content)
        report = validate_file(kind, staged)
        if not report["ok"]:
            return False, report

        folder = config.INPUT_DIRS[kind]
        old = candidates(kind)
        what = f"入力ファイル（{KINDS[kind]}）の差し替え: {', '.join(p.name for p in old) or 'なし'} → {staged.name}"
        run_id, _ = changes.begin_upstream_change(what, stages.INPUT_FIRST_STAGE[kind])
        archive = folder / config.INPUT_ARCHIVE_DIRNAME
        archive.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for path in old:
            info = file_info(path)
            moved = archive / f"{stamp}_{path.name}"
            shutil.move(path, moved)
            state.append_csv(config.INPUT_HISTORY_CSV, HISTORY_COLUMNS, {
                "timestamp": state.now(), "kind": kind, "event": "旧版を退避", "file": moved.name,
                "modified_at": info["modified_at"], "sha256": info["sha256"], "ok": "", "detail": str(moved),
            })
        target = folder / staged.name
        shutil.copy2(staged, target)

    _record(kind, "差し替え", file_info(target), report)
    changes.finish_upstream_change(run_id, what, stages.INPUT_FIRST_STAGE[kind])
    return True, report


def main() -> int:
    ok = True
    for kind in KINDS:
        entry = register_current(kind)
        print(f"== {KINDS[kind]}")
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        ok &= entry["validation"]["ok"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
