"""ファイル名 {証券コード}_{企業名}_{YYYYMMDD}.pdf のパース。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# 証券コードは4桁数字、または新方式の英字入り（例: 543A）。優先株等の5桁も許容する
_FILENAME = re.compile(
    r"^(?P<code>[0-9A-Z]{4,5})_(?P<name>.+)_(?P<date>\d{8})\.pdf$", re.IGNORECASE
)


@dataclass(frozen=True)
class FileInfo:
    code: str
    name: str
    announce_date: date
    path: Path


def parse_filename(path: Path) -> FileInfo | None:
    """パースできなければ None を返す（呼び出し側でログに残す）。"""
    matched = _FILENAME.match(path.name)
    if matched is None:
        return None
    raw = matched.group("date")
    try:
        announce = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None
    return FileInfo(
        code=matched.group("code").upper(),
        name=matched.group("name"),
        announce_date=announce,
        path=path,
    )
