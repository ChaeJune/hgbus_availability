"""공식 웹사이트를 확인해 운항 상태를 분류한다.

주의: hgbus.co.kr 는 해외 IP 를 차단하는 것으로 보인다. 접속 실패(차단 포함)는
결항이 아니라 UNKNOWN 으로 기록해 오탐(false outage)을 막는다.
"""

from __future__ import annotations

import gzip
import html as html_lib
import os
import re
import time as time_mod
import urllib.error
import urllib.request
from datetime import datetime

from .core import (
    CLOSED,
    OPERATIONAL,
    PARTIAL,
    SUSPENDED,
    UNKNOWN,
    in_service_hours,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def fetch(url: str, timeout: int = 20) -> tuple[int, str, int]:
    """URL 을 가져와 (HTTP 상태코드, 본문, 응답시간 ms)를 반환한다.

    HTTP 오류 응답(4xx/5xx)도 예외 대신 상태코드로 돌려준다.
    네트워크 수준 실패만 예외를 그대로 올린다.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip",
        },
    )
    started = time_mod.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            elapsed = int((time_mod.monotonic() - started) * 1000)
            return response.status, body.decode("utf-8", errors="replace"), elapsed
    except urllib.error.HTTPError as err:
        elapsed = int((time_mod.monotonic() - started) * 1000)
        body = err.read() or b""
        return err.code, body.decode("utf-8", errors="replace"), elapsed


def strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?s)<[^>]+>", " ", markup)
    markup = html_lib.unescape(markup)
    return re.sub(r"\s+", " ", markup).strip()


def classify_text(text: str, config: dict) -> tuple[str, list[str]]:
    """페이지 텍스트에서 결항 관련 키워드를 찾아 상태를 분류한다."""
    keywords = config["keywords"]
    compact = re.sub(r"\s+", "", text)

    def hits(words: list[str]) -> list[str]:
        return [w for w in words if re.sub(r"\s+", "", w) in compact]

    suspended_hits = hits(keywords["suspended"])
    if suspended_hits:
        return SUSPENDED, suspended_hits
    partial_hits = hits(keywords["partial"])
    if partial_hits:
        return PARTIAL, partial_hits
    return OPERATIONAL, []


def run_check(config: dict, when: datetime) -> dict:
    """모든 소스를 확인해 체크 레코드 하나를 만든다."""
    source = config["sources"][0]
    url = os.environ.get("HGBUS_SOURCE_URL", source["url"])

    record: dict = {
        "ts": when.isoformat(timespec="seconds"),
        "source": source["name"],
        "in_service_hours": in_service_hours(when, config),
    }

    try:
        status_code, body, latency = fetch(url, source.get("timeout_seconds", 20))
        record["http_status"] = status_code
        record["latency_ms"] = latency
        record["website_ok"] = 200 <= status_code < 400
        if record["website_ok"]:
            service_status, matched = classify_text(strip_html(body), config)
            record["service_status"] = service_status
            if matched:
                record["matched_keywords"] = matched
        else:
            # 403 등: 해외 IP 차단일 가능성이 높으므로 결항으로 단정하지 않는다.
            record["service_status"] = UNKNOWN
            record["reason"] = f"http_{status_code}"
    except Exception as err:  # noqa: BLE001 - 네트워크 실패는 전부 unknown 처리
        record["http_status"] = None
        record["website_ok"] = False
        record["service_status"] = UNKNOWN
        record["reason"] = f"{type(err).__name__}: {err}"[:200]

    if not record["in_service_hours"] and record["service_status"] in (
        OPERATIONAL,
        UNKNOWN,
    ):
        # 운항 시간 외에는 결항 공지가 없는 한 '운항 시간 외'로 기록한다.
        record["service_status"] = CLOSED

    return record
