"""段階の進捗・ログ・企業ごとのエラーの記録（出力フォルダにファイルで残す）。

バックグラウンド実行中もアプリから読めるよう、すべてファイル経由でやり取りする。
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from core import runs, state

ERROR_COLUMNS = ["timestamp", "code", "name", "category", "message"]
_WRITE_INTERVAL_SEC = 0.5


class Progress:
    def __init__(self, run_id: str, number: int, *, echo: bool = False) -> None:
        self.directory = runs.stage_dir(run_id, number)
        self.echo = echo
        self._last_write = 0.0

    @property
    def log_path(self) -> Path:
        return self.directory / "log.txt"

    @property
    def errors_path(self) -> Path:
        return self.directory / "errors.csv"

    @property
    def progress_path(self) -> Path:
        return self.directory / "progress.json"

    def reset(self) -> None:
        for path in (self.log_path, self.errors_path, self.progress_path):
            path.unlink(missing_ok=True)

    def log(self, message: str) -> None:
        line = f"{state.now()} {message}"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self.echo:
            print(line, flush=True)

    def error(self, code: str, name: str, category: str, message: str) -> None:
        """企業ごとのエラー。握りつぶさず errors.csv に残す。"""
        state.append_csv(self.errors_path, ERROR_COLUMNS, {
            "timestamp": state.now(), "code": code, "name": name,
            "category": category, "message": message,
        })
        self.log(f"[エラー] {code} {name}: {category} {message}")

    def update(self, done: int, total: int, message: str = "", *, force: bool = False) -> None:
        if not force and time.monotonic() - self._last_write < _WRITE_INTERVAL_SEC and done < total:
            return
        self._last_write = time.monotonic()
        state.write_json_atomic(self.progress_path, {
            "done": done, "total": total, "message": message, "updated_at": state.now(),
        })


def read_progress(run_id: str, number: int) -> dict | None:
    return state.read_json(runs.stage_dir(run_id, number) / "progress.json", None)


def read_log(run_id: str, number: int, tail: int = 200) -> str:
    path = runs.stage_dir(run_id, number) / "log.txt"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-tail:])


def read_errors(run_id: str, number: int) -> pd.DataFrame:
    path = runs.stage_dir(run_id, number) / "errors.csv"
    if not path.exists():
        return pd.DataFrame(columns=ERROR_COLUMNS)
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
