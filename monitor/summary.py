"""체크 기록을 집계해 상태 페이지용 summary.json 을 만든다."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .core import (
    CLOSED,
    OPERATIONAL,
    PARTIAL,
    SERVICE_STATUSES,
    STATUS_LABELS,
    SUMMARY_PATH,
    SUSPENDED,
    UNKNOWN,
    now_local,
    service_minutes_for_day,
    tz,
)
from .store import active_override, load_checks, load_overrides

SEVERITY = {OPERATIONAL: 0, UNKNOWN: 0, CLOSED: 0, PARTIAL: 1, SUSPENDED: 2}


def effective_status(record: dict, overrides: list[dict]) -> tuple[str, str | None]:
    """수동 기록이 있으면 그것을 우선 적용한 (상태, 메모)를 반환한다."""
    override = active_override(overrides, record["_ts"])
    if override is not None:
        return override["status"], override.get("note")
    return record["service_status"], None


def _day_status(counts: dict[str, int], suspended_minutes: int, config: dict) -> str:
    if counts[SUSPENDED] > 0:
        if suspended_minutes >= config.get("day_outage_threshold_minutes", 60):
            return "outage"
        return "degraded"
    if counts[PARTIAL] > 0:
        return "degraded"
    if counts[OPERATIONAL] > 0:
        return "operational"
    return "no-data"


def aggregate_days(
    checks: list[dict], overrides: list[dict], config: dict, today: datetime
) -> list[dict]:
    """최근 N일을 하루 단위로 집계한다(운항 시간 내 체크만 사용)."""
    interval = config.get("check_interval_minutes", 15)
    days_count = config.get("history_days", 90)

    by_day: dict[str, list[tuple[dict, str, str | None]]] = defaultdict(list)
    for record in checks:
        status, note = effective_status(record, overrides)
        by_day[record["_ts"].strftime("%Y-%m-%d")].append((record, status, note))

    days = []
    for offset in range(days_count - 1, -1, -1):
        day_dt = today - timedelta(days=offset)
        key = day_dt.strftime("%Y-%m-%d")
        entries = by_day.get(key, [])

        counts: dict[str, int] = defaultdict(int)
        website_fail = 0
        website_total = 0
        notes: list[str] = []
        for record, status, note in entries:
            if record.get("http_status") is not None or record.get("reason"):
                website_total += 1
                if not record.get("website_ok"):
                    website_fail += 1
            if not record["in_service_hours"]:
                continue
            counts[status] += 1
            if note and note not in notes:
                notes.append(note)
            for keyword in record.get("matched_keywords", []):
                if keyword not in notes:
                    notes.append(keyword)

        suspended_minutes = counts[SUSPENDED] * interval
        partial_minutes = counts[PARTIAL] * interval
        measured = sum(counts[s] for s in SERVICE_STATUSES)

        days.append(
            {
                "date": key,
                "status": _day_status(counts, suspended_minutes, config),
                "suspended_minutes": suspended_minutes,
                "partial_minutes": partial_minutes,
                "measured_checks": measured,
                "unknown_checks": counts[UNKNOWN],
                "service_minutes": service_minutes_for_day(day_dt, config),
                "website_status": _website_day_status(website_fail, website_total),
                "notes": notes[:5],
            }
        )
    return days


def _website_day_status(fail: int, total: int) -> str:
    if total == 0:
        return "no-data"
    ratio = fail / total
    if ratio >= 0.5:
        return "outage"
    if ratio > 0.05:
        return "degraded"
    return "operational"


def compute_uptime(days: list[dict], window: int, interval: int) -> float | None:
    recent = days[-window:]
    measured_minutes = sum(d["measured_checks"] * interval for d in recent)
    if measured_minutes == 0:
        return None
    down_minutes = sum(
        d["suspended_minutes"] + d["partial_minutes"] * 0.5 for d in recent
    )
    value = 100.0 * (1 - down_minutes / measured_minutes)
    return round(value, 2)


def derive_incidents(
    checks: list[dict], overrides: list[dict], config: dict
) -> list[dict]:
    """연속된 결항/일부결항 구간을 인시던트로 묶는다."""
    gap = timedelta(minutes=config.get("incident_gap_tolerance_minutes", 45))
    incidents: list[dict] = []
    current: dict | None = None

    for record in checks:
        if not record["in_service_hours"]:
            continue
        status, note = effective_status(record, overrides)
        if status in (SUSPENDED, PARTIAL):
            keywords = record.get("matched_keywords", [])
            if current is not None and record["_ts"] - current["_last"] <= gap:
                current["_last"] = record["_ts"]
                current["severity"] = max(current["severity"], SEVERITY[status])
                for extra in ([note] if note else []) + keywords:
                    if extra and extra not in current["notes"]:
                        current["notes"].append(extra)
            else:
                if current is not None:
                    incidents.append(current)
                current = {
                    "_start": record["_ts"],
                    "_last": record["_ts"],
                    "severity": SEVERITY[status],
                    "notes": [n for n in ([note] if note else []) + keywords if n],
                }
        elif status == OPERATIONAL and current is not None:
            incidents.append(current)
            current = None

    if current is not None:
        current["_ongoing"] = True
        incidents.append(current)

    interval = config.get("check_interval_minutes", 15)
    result = []
    for item in incidents:
        start = item["_start"]
        end = None if item.get("_ongoing") else item["_last"] + timedelta(
            minutes=interval
        )
        duration = None
        if end is not None:
            duration = int((end - start).total_seconds() // 60)
        result.append(
            {
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds") if end else None,
                "ongoing": item.get("_ongoing", False),
                "severity": "suspended" if item["severity"] >= 2 else "partial",
                "duration_minutes": duration,
                "notes": item["notes"][:5],
            }
        )
    result.reverse()  # 최신순
    return result


def build_summary(
    config: dict,
    history_dir: Path | None = None,
    overrides_path: Path | None = None,
    output_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or now_local(config)
    interval = config.get("check_interval_minutes", 15)
    since = now - timedelta(days=config.get("history_days", 90))
    checks = load_checks(since, config, history_dir)
    overrides = load_overrides(config, overrides_path)

    days = aggregate_days(checks, overrides, config, now)
    incidents = derive_incidents(checks, overrides, config)

    current: dict = {"status": UNKNOWN, "label": STATUS_LABELS[UNKNOWN]}
    if checks:
        latest = checks[-1]
        status, note = effective_status(latest, overrides)
        current = {
            "status": status,
            "label": STATUS_LABELS.get(status, status),
            "checked_at": latest["ts"],
            "website_ok": latest.get("website_ok"),
            "note": note,
            "matched_keywords": latest.get("matched_keywords", []),
            "reason": latest.get("reason"),
        }

    summary = {
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": config.get("timezone", "Asia/Seoul"),
        "current": current,
        "uptime": {
            "d30": compute_uptime(days, 30, interval),
            "d90": compute_uptime(days, 90, interval),
        },
        "days": days,
        "incidents": incidents[:100],
        "status_labels": STATUS_LABELS,
    }

    output_path = output_path or SUMMARY_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return summary
