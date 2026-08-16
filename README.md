# coinsunmul — 코인 선물 숏 시그널 봇

고수의 "오픈북" 지표(선물 CVD·현물 CVD·미결제약정·펀딩비)를 코드로 옮겨,
고확률 숏 진입 후보를 텔레그램으로 알려주는 봇을 만드는 프로젝트.
전체 설계와 로드맵은 [PLAN.md](PLAN.md) 참고.

## 진행 상황

- [x] Phase 0~1: Binance 수집기 + CVD 계산 + 검증 차트
- [ ] Phase 2: 단일 코인 감시 알림 (텔레그램)
- [ ] Phase 3: 전 종목 스캐너 + 트리거
- [ ] Phase 4: 다중 거래소 집계 (Bybit, OKX)
- [ ] Phase 5: 승률 검증
- [ ] Phase 6: 백테스트, 반자동 주문

## 처음 설정 (Phase 0)

1. [python.org](https://www.python.org/downloads/)에서 Python 3.11 이상 설치
   (Windows는 설치 화면에서 **"Add Python to PATH" 반드시 체크**)
2. 터미널(명령 프롬프트)에서 이 폴더로 이동한 뒤:

```bash
# 가상환경 만들기 (처음 한 번만)
python -m venv .venv

# 가상환경 켜기 — Windows
.venv\Scripts\activate
# 가상환경 켜기 — Mac
source .venv/bin/activate

# 라이브러리 설치 (처음 한 번만)
pip install -r requirements.txt
```

3. 설치가 잘 됐는지 확인 (인터넷 없이 가짜 데이터로 전체 파이프라인을 돌려본다):

```bash
python test_pipeline.py
```

`output/pipeline_test.png` 파일이 생기고 "테스트 통과"가 출력되면 준비 완료.

## Phase 1 실행 — 데이터 수집 + CVD 검증

```bash
python run_phase1.py                          # 기본: BTCUSDT 1시간봉 500개
python run_phase1.py PEPEUSDT                 # 다른 코인
python run_phase1.py DOGEUSDT --interval 15m  # 15분봉
```

실행하면 `output/phase1_BTCUSDT_1h.png` 같은 차트 파일이 생긴다.
패널 순서는 고수 화면과 같다: **가격 → 선물 CVD → 현물 CVD → 펀딩비 → OI**

**완료 기준**: 이 차트를 [CoinGlass](https://www.coinglass.com/) 또는
TradingView의 CoinGlass 지표와 나란히 놓고 **모양이 같은지** 눈으로 확인한다.
(내 CVD는 Binance 단일 거래소, CoinGlass는 여러 거래소 집계라서
절대값은 다르지만 모양(언제 오르고 언제 꺾이는지)은 같아야 정상)

## 폴더 구조

```
bot/
  collector.py    Binance 공개 API 수집 (캔들·OI·펀딩비, API 키 불필요)
  indicators.py   CVD 등 지표 계산
  chart.py        검증용 차트 그리기
run_phase1.py     Phase 1 실행 스크립트
test_pipeline.py  인터넷 없이 설치 확인용 테스트
PLAN.md           전체 설계·로드맵
```

## 자주 생기는 문제

- `python: command not found` → 설치할 때 "Add Python to PATH"를 안 켰을 가능성.
  Windows는 `py` 명령으로 대신 시도.
- 차트에 한글이 네모(□)로 나옴 → 한글 폰트가 없는 환경. 자동으로 영문 라벨로
  바뀌므로 기능에는 문제없다.
- `418` 또는 `429` 오류 → Binance 요청 제한. 몇 분 기다렸다가 다시 실행.
