"""백테스트 (Phase 6) — 과거 데이터로 전략을 리플레이해서 승률을 추정한다.

원칙: 백테스트 전용 로직을 만들지 않는다.
  - 시그널 판정: 라이브와 같은 analyzer.analyze()를 바(bar) 단위로 호출
  - 기록·중복 방지·결과 판정·통계: 라이브와 같은 store 모듈을 그대로 사용
  (전용 로직이 따로 있으면 "백테스트에서만 이기는 전략"이 나온다)

한계 (정직하게):
  - Binance OI 히스토리는 최근 30일까지만 제공 → 백테스트도 최대 30일
  - 미래 캔들을 미리 볼 수 없도록 매 바마다 그 시점까지의 데이터만 잘라서
    analyze에 넘긴다 (look-ahead 방지)
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from bot import collector, indicators, store
from bot.analyzer import MIN_BARS, analyze, _bars_per_day

MAX_DAYS = 30  # Binance OI 히스토리 한계


def fetch_history(symbol: str, interval: str, days: int) -> dict:
    """백테스트에 필요한 과거 데이터 일체. 현물이 없으면 spot은 빈 DataFrame."""
    days = min(days, MAX_DAYS)
    end = datetime.now(timezone.utc)
    # 시작 시점에도 분석 창(8일치)이 있어야 첫 바부터 판정이 가능하다
    start = end - timedelta(days=days + 8)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    futures = indicators.add_cvd(
        collector.fetch_futures_klines_range(symbol, interval, start_ms, end_ms))
    try:
        spot = indicators.add_cvd(
            collector.fetch_spot_klines_range(symbol, interval, start_ms, end_ms))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            spot = pd.DataFrame(columns=list(futures.columns))  # 선물 전용
        else:
            raise
    oi_period = interval if interval not in ("1m", "3m") else "5m"
    oi = collector.fetch_open_interest_range(symbol, oi_period, start_ms, end_ms)
    funding = collector.fetch_funding(symbol, 1000)
    if len(funding) and len(futures):
        funding = funding[funding["time"] >= futures["time"].iloc[0]]
    return {"futures": futures, "spot": spot, "oi": oi, "funding": funding}


STAGE_RANK = {"no_setup": 0, "watching": 1, "armed": 2, "triggered": 3, "collapsed": 4}
RESET_AFTER = 6  # run_watch와 같은 래치: 낮은 단계가 이만큼 연속되면 해제


def replay(symbol: str, interval: str, data: dict, db_path: str,
           days: int, step: int = 1, progress=print) -> dict:
    """매 바마다 그 시점까지의 데이터로 analyze를 돌려 시그널을 기록한다.

    기록 시점은 라이브 감시(run_watch)의 알림 래치와 같은 규칙을 쓴다 —
    단계가 '올라갈 때'만 기록하고, 낮은 단계가 RESET_AFTER바 연속되면
    래치를 푼다. (매 바 기록하면 박스 레벨이 흘러갈 때마다 같은 셋업이
    수십 건 쌓여 통계가 오염된다. store의 12h 중복 방지가 2차 방어선)
    """
    futures = data["futures"].reset_index(drop=True)
    spot, oi, funding = data["spot"], data["oi"], data["funding"]
    n = len(futures)
    day = _bars_per_day(interval)
    # 마지막 `days`일 구간만 판정 대상으로 리플레이 (앞 8일은 분석 창 재료)
    start_bar = max(MIN_BARS, n - days * day)
    counts = {"bars": 0, "armed": 0, "triggered": 0}
    seen_ids: set = set()
    notified_rank, low_streak = 0, 0

    for i in range(start_bar, n + 1, step):
        t = futures["time"].iloc[i - 1]
        r = analyze(symbol, interval,
                    futures.iloc[:i],
                    spot[spot["time"] <= t] if len(spot) else spot,
                    oi[oi["time"] <= t] if len(oi) else oi,
                    funding[funding["time"] <= t] if len(funding) else funding)
        counts["bars"] += 1
        rank = STAGE_RANK[r.stage]
        if rank > notified_rank:
            notified_rank, low_streak = rank, 0
            if r.stage in ("armed", "triggered"):
                sid = store.record_signal(r, db_path, now=t.to_pydatetime())
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    counts[r.stage] += 1
                    progress(f"  [{t:%m-%d %H:%M}] {r.stage.upper()} "
                             f"(점수 {r.total}/100, 가격 {r.price:,.6g})")
        elif rank < notified_rank:
            low_streak += 1
            if low_streak >= RESET_AFTER:
                notified_rank, low_streak = rank, 0
        else:
            low_streak = 0
    return counts


def evaluate(data_by_symbol: dict, interval: str, db_path: str) -> None:
    """기록된 시그널의 결과를 과거 데이터로 채운다 (store와 같은 판정 규칙)."""
    def fetch(symbol, _interval, _limit):
        return data_by_symbol[symbol]["futures"]

    # '지금'을 데이터 끝 + 하루로 두면 모든 호라이즌이 마감된 것으로 계산된다
    ends = [d["futures"]["time"].iloc[-1] for d in data_by_symbol.values()
            if len(d["futures"])]
    now = (max(ends) + pd.Timedelta(hours=25)).to_pydatetime()
    store.update_outcomes(fetch, db_path, now=now)
