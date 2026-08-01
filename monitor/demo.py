"""로컬 미리보기용 샘플 데이터 생성.

실제 기록(data/)은 건드리지 않고, 임시 디렉터리에 90일치 가짜 체크 기록을
만들어 docs/summary.json 만 덮어쓴다. `python -m monitor build` 를 다시 실행하면
실제 데이터 기준으로 되돌아간다.
"""

from __future__ import annotations

import random
import tempfile
from datetime import timedelta
from pathlib import Path

from .core import OPERATIONAL, PARTIAL, SUSPENDED, load_config, now_local
from .store import append_check
from .summary import build_summary


def generate_demo_summary(seed: int = 42) -> None:
    rng = random.Random(seed)
    config = load_config()
    now = now_local(config)

    with tempfile.TemporaryDirectory() as tmp:
        history = Path(tmp) / "history"
        for offset in range(config.get("history_days", 90) - 1, -1, -1):
            day = now - timedelta(days=offset)
            start = day.replace(hour=7 if day.weekday() < 5 else 10, minute=0)
            roll = rng.random()
            plan = "outage" if roll < 0.07 else "degraded" if roll < 0.13 else "ok"
            t = start
            while t.hour < 22 and t <= now:
                status = OPERATIONAL
                keywords: list[str] = []
                if plan == "outage" and 9 <= t.hour < 15:
                    status, keywords = SUSPENDED, ["금일 결항"]
                elif plan == "degraded" and 13 <= t.hour < 14:
                    status, keywords = PARTIAL, ["일부 결항"]
                record = {
                    "ts": t.isoformat(timespec="seconds"),
                    "source": "website",
                    "in_service_hours": True,
                    "http_status": 200,
                    "latency_ms": rng.randint(150, 600),
                    "website_ok": True,
                    "service_status": status,
                }
                if keywords:
                    record["matched_keywords"] = keywords
                append_check(record, history)
                t += timedelta(minutes=config.get("check_interval_minutes", 15))

        build_summary(
            config,
            history_dir=history,
            overrides_path=Path(tmp) / "overrides.jsonl",
            now=now,
        )
    print(
        "샘플 데이터로 docs/summary.json 을 생성했습니다.\n"
        "실제 데이터로 되돌리려면: python -m monitor build"
    )
