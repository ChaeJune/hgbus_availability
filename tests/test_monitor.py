"""checker/summary 로직 테스트 (네트워크 없이 실행)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from monitor.checker import classify_text, run_check, strip_html
from monitor.core import (
    CLOSED,
    OPERATIONAL,
    PARTIAL,
    SUSPENDED,
    UNKNOWN,
    in_service_hours,
    load_config,
)
from monitor.store import append_check, append_override, load_checks
from monitor.summary import build_summary, compute_uptime, derive_incidents

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture()
def config():
    return load_config()


# ---------- 분류 ----------

def test_classify_operational(config):
    text = "한강버스 오늘도 정상 운항합니다. 시간표를 확인하세요."
    assert classify_text(text, config) == (OPERATIONAL, [])


def test_classify_suspended(config):
    text = "기상 악화(풍랑)로 금일 운항 중단합니다. 이용에 참고 바랍니다."
    status, hits = classify_text(text, config)
    assert status == SUSPENDED
    assert "금일 운항 중단" in hits


def test_classify_suspended_ignores_whitespace(config):
    text = "전면  결항 안내"
    status, _ = classify_text(text, config)
    assert status == SUSPENDED


def test_classify_partial(config):
    text = "수위 상승으로 일부 결항이 발생하고 있습니다."
    status, hits = classify_text(text, config)
    assert status == PARTIAL
    assert hits == ["일부 결항"]


def test_strip_html():
    markup = "<html><script>var x=1;</script><body><p>정상 &amp; 운항</p></body></html>"
    assert strip_html(markup) == "정상 & 운항"


# ---------- 운항 시간 ----------

def test_service_hours_weekday(config):
    assert in_service_hours(datetime(2026, 7, 31, 7, 0, tzinfo=KST), config)  # 금
    assert not in_service_hours(datetime(2026, 7, 31, 5, 0, tzinfo=KST), config)
    assert not in_service_hours(datetime(2026, 7, 31, 23, 0, tzinfo=KST), config)


def test_service_hours_weekend(config):
    assert not in_service_hours(datetime(2026, 8, 1, 7, 0, tzinfo=KST), config)  # 토, 09:30 이전
    assert in_service_hours(datetime(2026, 8, 1, 10, 0, tzinfo=KST), config)


# ---------- run_check (fetch 목킹) ----------

def test_run_check_blocked_is_unknown(config, monkeypatch):
    """해외 IP 차단(403)은 결항이 아니라 unknown 이어야 한다."""
    monkeypatch.setattr(
        "monitor.checker.fetch", lambda url, timeout=20: (403, "Forbidden", 120)
    )
    record = run_check(config, datetime(2026, 7, 31, 10, 0, tzinfo=KST))
    assert record["service_status"] == UNKNOWN
    assert record["website_ok"] is False


def test_run_check_network_error_is_unknown(config, monkeypatch):
    def boom(url, timeout=20):
        raise OSError("connection refused")

    monkeypatch.setattr("monitor.checker.fetch", boom)
    record = run_check(config, datetime(2026, 7, 31, 10, 0, tzinfo=KST))
    assert record["service_status"] == UNKNOWN
    assert "reason" in record


def test_run_check_night_is_closed(config, monkeypatch):
    monkeypatch.setattr(
        "monitor.checker.fetch",
        lambda url, timeout=20: (200, "<p>정상 운항</p>", 100),
    )
    record = run_check(config, datetime(2026, 7, 31, 2, 0, tzinfo=KST))
    assert record["service_status"] == CLOSED


def test_run_check_suspended(config, monkeypatch):
    monkeypatch.setattr(
        "monitor.checker.fetch",
        lambda url, timeout=20: (200, "<p>호우로 금일 결항합니다</p>", 100),
    )
    record = run_check(config, datetime(2026, 7, 31, 10, 0, tzinfo=KST))
    assert record["service_status"] == SUSPENDED
    assert record["matched_keywords"] == ["금일 결항"]


# ---------- 저장/집계 ----------

def make_history(tmp_path: Path, config, records):
    history = tmp_path / "history"
    for record in records:
        append_check(record, history)
    return history


def synth_day(day: datetime, statuses: list[str]) -> list[dict]:
    """운항시간 내 15분 간격 체크 레코드 생성."""
    records = []
    t = day.replace(hour=9, minute=0)
    for status in statuses:
        records.append(
            {
                "ts": t.isoformat(timespec="seconds"),
                "source": "website",
                "in_service_hours": True,
                "http_status": 200,
                "website_ok": True,
                "service_status": status,
            }
        )
        t += timedelta(minutes=15)
    return records


def test_summary_end_to_end(tmp_path, config):
    base = datetime(2026, 7, 30, tzinfo=KST)
    records = synth_day(base, [OPERATIONAL] * 4 + [SUSPENDED] * 5 + [OPERATIONAL] * 3)
    records += synth_day(base + timedelta(days=1), [OPERATIONAL] * 8)
    history = make_history(tmp_path, config, records)
    out = tmp_path / "summary.json"

    summary = build_summary(
        config,
        history_dir=history,
        overrides_path=tmp_path / "overrides.jsonl",
        output_path=out,
        now=datetime(2026, 7, 31, 12, 0, tzinfo=KST),
    )

    days = {d["date"]: d for d in summary["days"]}
    assert days["2026-07-30"]["status"] == "outage"  # 75분 결항 ≥ 60분 임계
    assert days["2026-07-30"]["suspended_minutes"] == 75
    assert days["2026-07-31"]["status"] == "operational"

    assert len(summary["incidents"]) == 1
    incident = summary["incidents"][0]
    assert incident["severity"] == "suspended"
    assert incident["ongoing"] is False
    assert incident["duration_minutes"] == 75

    assert summary["current"]["status"] == OPERATIONAL
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["uptime"]["d90"] is not None


def test_unknown_not_counted_as_outage(tmp_path, config):
    base = datetime(2026, 7, 30, tzinfo=KST)
    records = synth_day(base, [UNKNOWN] * 10)
    history = make_history(tmp_path, config, records)

    summary = build_summary(
        config,
        history_dir=history,
        overrides_path=tmp_path / "overrides.jsonl",
        output_path=tmp_path / "summary.json",
        now=datetime(2026, 7, 31, 12, 0, tzinfo=KST),
    )
    days = {d["date"]: d for d in summary["days"]}
    assert days["2026-07-30"]["status"] == "no-data"
    assert summary["incidents"] == []
    assert summary["uptime"]["d90"] is None


def test_short_suspension_is_degraded_day(tmp_path, config):
    base = datetime(2026, 7, 30, tzinfo=KST)
    records = synth_day(base, [OPERATIONAL] * 5 + [SUSPENDED] * 2 + [OPERATIONAL] * 5)
    history = make_history(tmp_path, config, records)
    summary = build_summary(
        config,
        history_dir=history,
        overrides_path=tmp_path / "overrides.jsonl",
        output_path=tmp_path / "summary.json",
        now=datetime(2026, 7, 31, 12, 0, tzinfo=KST),
    )
    days = {d["date"]: d for d in summary["days"]}
    assert days["2026-07-30"]["status"] == "degraded"  # 30분 < 60분 임계


def test_manual_override_wins(tmp_path, config):
    base = datetime(2026, 7, 30, tzinfo=KST)
    records = synth_day(base, [UNKNOWN] * 8)  # 사이트 확인 불가 상황
    history = make_history(tmp_path, config, records)
    overrides = tmp_path / "overrides.jsonl"
    append_override(
        {
            "start": "2026-07-30T09:00:00+09:00",
            "end": "2026-07-30T11:00:00+09:00",
            "status": SUSPENDED,
            "note": "풍랑주의보 (수동 기록)",
        },
        overrides,
    )

    summary = build_summary(
        config,
        history_dir=history,
        overrides_path=overrides,
        output_path=tmp_path / "summary.json",
        now=datetime(2026, 7, 31, 12, 0, tzinfo=KST),
    )
    days = {d["date"]: d for d in summary["days"]}
    assert days["2026-07-30"]["status"] == "outage"
    assert "풍랑주의보 (수동 기록)" in days["2026-07-30"]["notes"]
    assert len(summary["incidents"]) == 1
    assert summary["incidents"][0]["notes"] == ["풍랑주의보 (수동 기록)"]


def test_incident_gap_merging(config):
    base = datetime(2026, 7, 30, 10, 0, tzinfo=KST)
    records = []
    # 결항 → 30분 공백(체크 누락) → 결항: 하나의 인시던트로 병합돼야 함
    for offset in (0, 15, 45, 60):
        ts = base + timedelta(minutes=offset)
        records.append(
            {
                "ts": ts.isoformat(timespec="seconds"),
                "_ts": ts,
                "in_service_hours": True,
                "service_status": SUSPENDED,
                "website_ok": True,
                "http_status": 200,
            }
        )
    incidents = derive_incidents(records, [], config)
    assert len(incidents) == 1


def test_compute_uptime():
    days = [
        {"measured_checks": 10, "suspended_minutes": 0, "partial_minutes": 0},
        {"measured_checks": 10, "suspended_minutes": 75, "partial_minutes": 30},
    ]
    # 측정 300분 중 결항 75 + 일부결항 30*0.5 = 90분
    assert compute_uptime(days, 30, 15) == 70.0


def test_load_checks_filters_by_time(tmp_path, config):
    history = tmp_path / "history"
    old = datetime(2026, 1, 1, 10, 0, tzinfo=KST)
    new = datetime(2026, 7, 30, 10, 0, tzinfo=KST)
    for ts in (old, new):
        append_check(
            {"ts": ts.isoformat(timespec="seconds"), "service_status": OPERATIONAL,
             "in_service_hours": True, "website_ok": True, "http_status": 200},
            history,
        )
    loaded = load_checks(new - timedelta(days=30), config, history)
    assert len(loaded) == 1
