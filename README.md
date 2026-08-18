# coinsunmul — 코인 선물 숏 시그널 봇

고수의 "오픈북" 지표(선물 CVD·현물 CVD·미결제약정·펀딩비)를 코드로 옮겨,
**차트를 사람 대신 읽고** 숏 셋업의 단계를 판정하고 진입가/손절가/목표가를
제시하며, 진입 시점까지 감시하다가 알려주는 봇. 전체 설계는 [PLAN.md](PLAN.md).

## 핵심 명령 4개

```bash
python run_watch.py --scan            # 전자동(추천): 스캔 → 편입 → 감시 → 알림
python run_scan.py                    # 스캔 1회: 지금 시장의 숏 셋업 후보 목록
python run_analyze.py PEPEUSDT        # 즉시 분석: 단계 판정 + 트레이드 플랜
python run_stats.py                   # 성적표: 지금까지 시그널의 승률·수익률 통계
```

`run_watch.py --scan` 은 30분마다 전 종목을 스캔해서 "24h 급등 + 유동성 +
OI 급증" 후보를 감시 목록에 자동 편입하고(최대 8개), 셋업 구조가 사라지면
자동 제외한다. 코인을 직접 고를 필요가 없다.

`run_analyze.py` 출력 예시:

```
■ TEST-ALT (1h) — 현재가 0.0232902
판정: [ARMED] 준비 완료 — 지지 이탈(트리거)만 대기
점수: 65/100 (컨텍스트 40/40 · 셋업 25/40 · 트리거 0/20)

A. 컨텍스트
  ✓ 급등 감지 [15/15] — 24h -5.3%, 펌핑폭 +72%
  ✓ OI 급증 [15/15] — 펌핑 이후 OI +82.0%
  ✓ 선물 주도 펌핑 [10/10] — 선물 CVD +6,078,062 vs 현물 +136,115 (44.7배)
...
트레이드 플랜 (숏)
  진입: 0.0231294 하향 이탈 확인 후 (현재가 대비 -0.7%)
  손절: 0.0250428 (진입가 대비 +8.3%) — 박스 상단 위
  1차 목표: 0.0206021 (펌핑 50% 되돌림) · 손익비 1.3
  2차 목표: 0.0151479 (펌핑 시작점)
  무효화: 신고가(0.0250428 위) 갱신 시 셋업 폐기
```

### 단계(stage)의 의미

| 단계 | 뜻 | 봇의 행동 |
|------|----|-----------|
| `no_setup` | 숏 셋업 구조가 아님 | 조용히 넘어감 |
| `watching` | 펌핑 확인, 셋업 완성 대기 | 감시 지속 |
| `armed` | 구조 완성 — 지지 이탈만 대기 | 트레이드 플랜 제시 |
| `triggered` | **트리거 발동 — 계획된 진입 자리** | 🚨 알림 |
| `collapsed` | 이미 붕괴 — 놓친 자리 | 추격 금지 경고 |

### 승률 검증 — 4할 타자 만들기 (Phase 5)

`run_watch.py` 가 돌면서 모든 armed/🚨 시그널을 `signals.sqlite3` 에 자동
기록하고, 1·4·24시간 뒤 수익률과 "손절 vs 1차 목표 중 어느 쪽을 먼저
쳤는지"를 자동으로 채운다. `python run_stats.py` 로 언제든 성적표를 본다:

- 시그널 이후 수익률 (숏 기준: 가격이 내려가면 양수 = 시그널 적중)
- 트레이드 플랜 시뮬레이션 승률 (1차 목표 도달 = 승, 손절 = 패)
- 점수 구간별 승률 — **이 수치가 임계치(70점)를 데이터로 조정하는 근거다**

표본 50건이 쌓이기 전까지는 통계가 아니라 소음이다. 봇을 며칠 켜두고
성적표부터 확인한 뒤에 실거래 여부를 판단할 것.

## 처음 설정

1. [python.org](https://www.python.org/downloads/)에서 Python 3.11 이상 설치
   (Windows는 설치 화면에서 **"Add Python to PATH" 반드시 체크**)
2. 터미널에서 이 폴더로 이동한 뒤:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (Mac: source .venv/bin/activate)
pip install -r requirements.txt
```

3. 설치 확인 (인터넷 없이 가짜 펌핑-덤핑 데이터로 분석 엔진까지 테스트):

```bash
python test_pipeline.py
```

"테스트 통과"가 나오면 준비 완료.

## 텔레그램 알림 켜기 (선택)

설정하지 않으면 콘솔 출력만 된다. 폰으로 알림을 받으려면:

1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 토큰 발급
2. 만든 봇에게 아무 메시지나 하나 보낸 뒤, 브라우저에서
   `https://api.telegram.org/bot<토큰>/getUpdates` 열어 `"chat":{"id":숫자}` 확인
3. 환경변수 설정 후 `run_watch.py` 실행:

```bash
# Windows (명령 프롬프트)
set TELEGRAM_BOT_TOKEN=123456:ABC...
set TELEGRAM_CHAT_ID=987654321
# Mac/Linux
export TELEGRAM_BOT_TOKEN=123456:ABC...
export TELEGRAM_CHAT_ID=987654321

python run_watch.py PEPEUSDT DOGEUSDT --every 5
```

## 옵션

```bash
python run_analyze.py PEPEUSDT --interval 1h     # 캔들 간격 (기본 15m)
python run_watch.py PEPEUSDT --every 15          # 점검 주기(분, 기본 5)
python run_watch.py PEPEUSDT --scan              # 직접 고른 코인 + 스캐너 둘 다
python run_watch.py --scan --max-auto 5          # 자동 편입 최대 개수 (기본 8)
python run_scan.py --min-change 30               # 24h +30% 이상만 (기본 20)
python run_scan.py --min-volume 10               # 거래대금 10M 이상만 (기본 3)
python run_analyze.py PEPEUSDT --chart           # 검증용 차트 PNG 저장 (선택)
```

`--chart`는 내가 계산한 CVD가 CoinGlass와 같은 모양인지 눈으로 확인하고
싶을 때만 쓰는 검증 도구다. 평소 분석·감시에는 필요 없다.

## 폴더 구조

```
bot/
  collector.py    Binance 공개 API 수집 (캔들·OI·펀딩비, API 키 불필요)
  indicators.py   CVD 등 지표 계산
  analyzer.py     분석 엔진: 구조 탐지 -> 점수 평가 -> 단계 판정 + 레벨 계산
  scanner.py      전 종목 스캐너: 급등 + 유동성 + OI 급증 후보 선별
  store.py        시그널 기록 + 승률·수익률 자동 추적 (SQLite)
  notifier.py     콘솔 + 텔레그램 알림
  chart.py        검증용 차트 (선택 도구)
run_watch.py      감시 체계 (--scan 으로 스캐너 연동, 시그널 자동 기록)
run_scan.py       스캔 1회 실행
run_analyze.py    즉시 분석
run_stats.py      승률·수익률 성적표
test_pipeline.py  설치·엔진 확인용 오프라인 테스트
test_watch.py     감시 체계 오프라인 테스트
test_store.py     기록·승률 모듈 오프라인 테스트
PLAN.md           전체 설계·로드맵
```

## 진행 상황

- [x] Phase 0~1: 수집기 + CVD 계산
- [x] Phase 2: 분석 엔진(단계 판정 + 트레이드 플랜) + 감시·알림
- [x] Phase 3: 전 종목 자동 스캐너 + 감시 자동 편입/제외
- [x] Phase 5: 시그널 기록 → 승률·수익률 통계 (점수 조정 근거)
- [ ] Phase 4: 다중 거래소 집계 (Bybit, OKX)
- [ ] Phase 6: 백테스트, 반자동 주문

## 주의

- 점수·임계치는 아직 **가설**이다. Phase 5에서 실제 승률로 검증하기 전까지
  알림은 참고용이며, 실거래 진입·손절 판단은 항상 수동으로 한다.
- 숏은 손실이 이론상 무한하다. `triggered` 알림이 와도 손절 없이 진입하지 않는다.

## 자주 생기는 문제

- `python: command not found` → "Add Python to PATH" 미체크. Windows는 `py`로 시도.
- `418`/`429` 오류 → Binance 요청 제한. 몇 분 후 재시도하거나 `--every`를 늘린다.
- 감시 중 네트워크가 끊겨도 봇은 죽지 않고 다음 주기에 재시도한다.
