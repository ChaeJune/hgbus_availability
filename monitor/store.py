"""체크 기록과 수동 기록(overrides)의 저장/로딩."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .core import HISTORY_DIR, OVERRIDES_PATH, tz


def _month_file(when: datetime, history_dir: Path) -> Path:
    return history_dir / f"{when.strftime('%Y-%m')}.jsonl"


def append_check(record: dict, history_dir: Path | None = None) -> Path:
    history_dir = history_dir or HISTORY_DIR
    history_dir.mkdir(parents=True, exist_ok=True)
    when = datetime.fromisoformat(record["ts"])
    path = _month_file(when, history_dir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_checks(
    since: datetime, config: dict, history_dir: Path | None = None
) -> list[dict]:
    """since 이후의 체크 레코드를 시간순으로 반환한다."""
    history_dir = history_dir or HISTORY_DIR
    if not history_dir.exists():
        return []
    records: list[dict] = []
    for path in sorted(history_dir.glob("*.jsonl")):
        # 파일명(YYYY-MM)으로 월 단위 필터링
        month_end = path.stem  # e.g. 2026-08
        if month_end < (since - timedelta(days=31)).strftime("%Y-%m"):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                ts = datetime.fromisoformat(record["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=tz(config))
                if ts >= since:
                    record["_ts"] = ts
                    records.append(record)
    records.sort(key=lambda r: r["_ts"])
    return records


def append_override(record: dict, path: Path | None = None) -> Path:
    """수동 기록(결항 공지 수기 입력 등)을 추가한다."""
    path = path or OVERRIDES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_overrides(config: dict, path: Path | None = None) -> list[dict]:
    path = path or OVERRIDES_PATH
    if not path.exists():
        return []
    overrides = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record["_start"] = datetime.fromisoformat(record["start"])
            record["_end"] = (
                datetime.fromisoformat(record["end"]) if record.get("end") else None
            )
            for key in ("_start", "_end"):
                if record[key] is not None and record[key].tzinfo is None:
                    record[key] = record[key].replace(tzinfo=tz(config))
            overrides.append(record)
    overrides.sort(key=lambda r: r["_start"])
    return overrides


def active_override(overrides: list[dict], when: datetime) -> dict | None:
    """해당 시각에 적용되는 수동 기록을 반환한다(늦게 시작한 것 우선)."""
    match = None
    for override in overrides:
        if override["_start"] <= when and (
            override["_end"] is None or when < override["_end"]
        ):
            match = override
    return match
