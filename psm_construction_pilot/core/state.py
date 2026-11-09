"""state/state.json の読み書き。

アプリとバックグラウンドの処理が同時に書き込むため、ロックファイルで排他し、
一時ファイルへ書いてから置き換える（書きかけの JSON を読ませない）。
"""
from __future__ import annotations

import csv
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

import config

_LOCK_TIMEOUT_SEC = 20.0
_STALE_LOCK_SEC = 60.0


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def file_lock(lock_path: Path | None = None) -> Iterator[None]:
    lock_path = lock_path or config.STATE_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_SEC
    while True:
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(handle)
            break
        except FileExistsError:
            # 異常終了で残ったロックは一定時間で破棄する
            try:
                if time.time() - lock_path.stat().st_mtime > _STALE_LOCK_SEC:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"ロックを取得できません: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load() -> dict:
    return read_json(config.STATE_JSON, {"current_run": None, "runs": {}})


def update(mutate: Callable[[dict], object]) -> object:
    """ロック下で state を読み、mutate(state) で変更して保存する。mutate の戻り値を返す。"""
    with file_lock():
        current = load()
        result = mutate(current)
        write_json_atomic(config.STATE_JSON, current)
    return result


def append_csv(path: Path, columns: list[str], row: dict) -> None:
    """追記専用の CSV に1行足す（ヘッダは初回のみ）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        is_new = not path.exists()
        with path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            if is_new:
                writer.writeheader()
            writer.writerow({column: row.get(column, "") for column in columns})
