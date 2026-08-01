"""CLI 진입점.

사용법:
  python -m monitor check                 # 1회 확인 + 기록 + 페이지 데이터 갱신
  python -m monitor build                 # 기록으로부터 페이지 데이터만 재생성
  python -m monitor record --status suspended --note "풍랑주의보로 전면 결항" \
      [--start 2026-08-01T09:00+09:00] [--end 2026-08-01T18:00+09:00]
                                          # 수동 기록(공지 수기 입력). --end 생략 시 진행 중
"""

from __future__ import annotations

import argparse
import json
import sys

from .checker import run_check
from .core import STATUS_LABELS, load_config, now_local
from .store import append_check, append_override
from .summary import build_summary


def cmd_check(_args: argparse.Namespace) -> int:
    config = load_config()
    record = run_check(config, now_local(config))
    append_check(record)
    build_summary(config)
    print(json.dumps(record, ensure_ascii=False))
    return 0


def cmd_build(_args: argparse.Namespace) -> int:
    config = load_config()
    summary = build_summary(config)
    current = summary["current"]
    print(f"현재 상태: {current['label']} / 기록 일수: {len(summary['days'])}")
    return 0


def cmd_demo(_args: argparse.Namespace) -> int:
    from .demo import generate_demo_summary

    generate_demo_summary()
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    config = load_config()
    if args.status not in STATUS_LABELS:
        print(f"올바르지 않은 상태: {args.status} (가능: {', '.join(STATUS_LABELS)})")
        return 1
    record = {
        "start": args.start or now_local(config).isoformat(timespec="seconds"),
        "end": args.end,
        "status": args.status,
        "note": args.note,
        "recorded_at": now_local(config).isoformat(timespec="seconds"),
    }
    append_override(record)
    build_summary(config)
    print(json.dumps(record, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="monitor", description="한강버스 운항 상태 모니터")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="상태 확인 1회 실행").set_defaults(func=cmd_check)
    sub.add_parser("build", help="summary.json 재생성").set_defaults(func=cmd_build)
    sub.add_parser("demo", help="샘플 데이터로 페이지 미리보기용 summary 생성").set_defaults(
        func=cmd_demo
    )

    record = sub.add_parser("record", help="수동 기록 추가")
    record.add_argument("--status", required=True, help="operational|partial|suspended")
    record.add_argument("--note", required=True, help="사유 (예: 풍랑주의보)")
    record.add_argument("--start", help="시작 시각 ISO8601, 생략 시 현재")
    record.add_argument("--end", help="종료 시각 ISO8601, 생략 시 진행 중")
    record.set_defaults(func=cmd_record)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
