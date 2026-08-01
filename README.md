# 한강버스 운항 현황 모니터

서울 [한강버스](https://hgbus.co.kr/)(마곡–망원–여의도–압구정–옥수–뚝섬–잠실)의
운항 여부를 주기적으로 확인해서, status.claude.com 스타일의 상태 페이지로
결항(outage) 이력을 기록하는 프로젝트입니다.

- **상태 페이지**: `docs/index.html` — GitHub Pages 로 서빙 (설정: Settings → Pages → `docs/`)
- **수집 주기**: GitHub Actions 크론으로 15분 간격 (`.github/workflows/monitor.yml`)
- **기록 데이터**: `data/history/YYYY-MM.jsonl` (원본 체크 기록), `docs/summary.json` (페이지용 집계)

## 동작 방식

```
GitHub Actions (15분 크론)
  └─ python -m monitor check
       ├─ 공식 웹사이트(hgbus.co.kr) 요청
       ├─ 페이지 텍스트에서 결항 키워드 탐지 → 상태 분류
       ├─ data/history/YYYY-MM.jsonl 에 기록 (append)
       └─ docs/summary.json 재생성 → 커밋/푸시
```

### 상태 분류

| 상태 | 의미 | 장애 집계 |
|---|---|---|
| `operational` | 정상 운항 (결항 키워드 없음) | — |
| `partial` | 일부 결항 / 지연·단축 운항 | 50% 가중 |
| `suspended` | 전면 결항 / 운항 중단 | 100% |
| `closed` | 운항 시간 외 (심야 등) | 집계 제외 |
| `unknown` | 사이트 접속 실패·차단 | 집계 제외 |

- 가동률(%)은 **운항 시간 내에 측정된 체크**만으로 계산합니다.
- 하루 단위 색상: 전면 결항 60분 이상 = 빨강(결항), 그 미만·일부 결항 = 노랑, 나머지 = 초록.
- 연속된 결항 체크(45분 이내 공백은 병합)를 하나의 인시던트로 묶어 이력에 남깁니다.
- 결항 키워드·운항 시간표·임계값은 모두 `config.json` 에서 수정합니다.

### 중요: 해외 IP 차단에 대하여

hgbus.co.kr(및 서울시 사이트들)은 **해외 IP 를 차단**하는 것으로 확인됐습니다.
GitHub Actions 기본 러너는 해외(미국) IP 이므로 체크가 403 으로 실패할 수 있습니다.
이 경우 기록은 `unknown`(확인 불가)으로 남으며 **결항으로 오집계되지 않습니다.**

해결 방법 (택 1):

1. **국내에서 크론 실행** — 국내 서버/NAS/집 PC 에서
   `*/15 * * * * cd /path/to/repo && python -m monitor check && git ... push`
2. **셀프 호스티드 러너** — 국내 머신을 GitHub Actions self-hosted runner 로 등록
3. **프록시 경유** — 워크플로에 `HTTPS_PROXY` 환경변수로 국내 프록시 지정
4. **수동 기록** — 결항 공지를 보면 수동으로 기록 (아래 참고)

`HGBUS_SOURCE_URL` 환경변수로 확인할 URL 을 바꿀 수도 있습니다
(예: 더 안정적인 운항 공지 API 를 찾은 경우).

## 사용법

```bash
# 1회 확인 + 기록 + 페이지 데이터 갱신
python -m monitor check

# 기록으로부터 docs/summary.json 만 재생성
python -m monitor build

# 수동 기록 (자동 감지가 안 될 때 — 수동 기록이 자동 감지보다 우선함)
python -m monitor record --status suspended --note "풍랑주의보로 전면 결항" \
  --start 2026-08-01T09:00:00+09:00 --end 2026-08-01T18:00:00+09:00
# --end 생략 시 "진행 중" 인시던트로 표시됨
```

로컬에서 페이지 미리보기:

```bash
python -m monitor build
python -m http.server -d docs 8000   # http://localhost:8000
```

테스트:

```bash
pip install pytest
python -m pytest tests/
```

## 초기 설정 체크리스트

1. GitHub 저장소 Settings → Pages → Source: `Deploy from a branch`, Branch: 기본 브랜치 / `docs` 선택
2. Actions 탭에서 `monitor` 워크플로가 도는지 확인 (workflow_dispatch 로 수동 실행 가능)
3. 러너에서 hgbus.co.kr 이 403 이면 위의 "해외 IP 차단" 항목 참고
4. 실제 사이트의 결항 공지 문구를 보고 `config.json` 의 키워드를 다듬기

## 한계와 주의

- 결항 감지는 공식 사이트의 **공지 문구 키워드 매칭**에 의존합니다. 사이트 개편이나
  문구 변화("내일 결항 예정" 같은 예고 공지)에 따라 오탐/미탐이 있을 수 있으니,
  초기에는 `data/history/` 의 `matched_keywords` 를 보면서 키워드를 조정하세요.
- 운항 시간표(`config.json` 의 `schedule`)는 계절/요일에 따라 달라질 수 있으므로
  공식 시간표에 맞게 갱신이 필요합니다.
