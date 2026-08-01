"""공통 유틸: 설정 로딩, 시간대, 운항 시간표 판정, 상태 상수."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# 선박 운항 상태
OPERATIONAL = "operational"  # 정상 운항
PARTIAL = "partial"          # 일부 결항 / 지연·단축 운항
SUSPENDED = "suspended"      # 전면 결항 / 운항 중단
CLOSED = "closed"            # 운항 시간 외 (심야 등) — 장애로 집계하지 않음
UNKNOWN = "unknown"          # 확인 불가(사이트 접속 실패 등) — 장애로 집계하지 않음

SERVICE_STATUSES = (OPERATIONAL, PARTIAL, SUSPENDED)

STATUS_LABELS = {
    OPERATIONAL: "정상 운항",
    PARTIAL: "일부 결항",
    SUSPENDED: "전면 결항",
    CLOSED: "운항 시간 외",
    UNKNOWN: "확인 불가",
}

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
OVERRIDES_PATH = DATA_DIR / "overrides.jsonl"
SUMMARY_PATH = ROOT / "docs" / "summary.json"


def load_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def tz(config: dict) -> ZoneInfo:
    return ZoneInfo(config.get("timezone", "Asia/Seoul"))


def now_local(config: dict) -> datetime:
    return datetime.now(tz(config))


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def service_window(when: datetime, config: dict) -> tuple[time, time]:
    """해당 날짜의 운항 시작/종료 시각을 반환한다."""
    schedule = config["schedule"]
    key = "weekend" if when.weekday() >= 5 else "weekday"
    window = schedule[key]
    return _parse_hhmm(window["start"]), _parse_hhmm(window["end"])


def in_service_hours(when: datetime, config: dict) -> bool:
    start, end = service_window(when, config)
    return start <= when.time() <= end


def service_minutes_for_day(when: datetime, config: dict) -> int:
    start, end = service_window(when, config)
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def service_overlap_minutes(a: datetime, b: datetime, config: dict) -> float:
    """a~b 구간 중 운항 시간에 해당하는 분량(운항 시간 기준 경과)."""
    if b <= a:
        return 0.0
    total = 0.0
    day = a.replace(hour=0, minute=0, second=0, microsecond=0)
    while day.date() <= b.date():
        start_t, end_t = service_window(day, config)
        window_start = day.replace(hour=start_t.hour, minute=start_t.minute)
        window_end = day.replace(hour=end_t.hour, minute=end_t.minute)
        lo = max(a, window_start)
        hi = min(b, window_end)
        if hi > lo:
            total += (hi - lo).total_seconds() / 60
        day += timedelta(days=1)
    return total
