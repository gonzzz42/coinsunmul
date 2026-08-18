"""즉시 분석 — 지금 이 코인이 숏 셋업의 어느 단계인지 판정하고
진입가/손절가/목표가를 제시한다.

사용법:
    python run_analyze.py PEPEUSDT                 # 15분봉 기본
    python run_analyze.py BTCUSDT --interval 1h
    python run_analyze.py PEPEUSDT --chart         # 검증용 차트도 저장 (선택)
"""

import argparse
import sys

import pandas as pd
import requests

from bot import aggregate, collector, indicators
from bot.analyzer import analyze, format_report

# OI 히스토리 API는 5m 미만/3m을 지원하지 않는다
OI_PERIOD_MAP = {"1m": "5m", "3m": "5m"}


def fetch_all(symbol: str, interval: str, limit: int, agg: bool = True):
    """분석에 필요한 데이터 수집. 현물 시장이 없는 선물 전용 코인이면
    현물은 빈 DataFrame으로 돌려준다 (analyzer가 알아서 처리).
    agg=True면 OI를 Bybit·OKX와 합산한다 (실패한 거래소는 자동 제외).
    반환: (선물, 현물, OI, 펀딩, OI에 포함된 거래소 목록)"""
    futures = indicators.add_cvd(collector.fetch_futures_klines(symbol, interval, limit))
    try:
        spot = indicators.add_cvd(collector.fetch_spot_klines(symbol, interval, limit))
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            spot = pd.DataFrame(columns=list(futures.columns))  # 선물 전용 코인
        else:
            raise
    oi_period = OI_PERIOD_MAP.get(interval, interval)
    oi = collector.fetch_open_interest(symbol, oi_period, min(limit, 500))
    oi_sources = ["binance"]
    if agg:
        try:
            oi, oi_sources = aggregate.aggregate_oi(symbol, interval, oi, futures)
        except Exception:
            pass  # 집계 실패 시 Binance 단독으로 계속
    funding = collector.fetch_funding(symbol, 500)
    if len(funding) and len(futures):
        funding = funding[funding["time"] >= futures["time"].iloc[0]]
    return futures, spot, oi, funding, oi_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="숏 셋업 즉시 분석")
    parser.add_argument("symbol", nargs="?", default="BTCUSDT")
    parser.add_argument("--interval", default="15m",
                        help="캔들 간격: 5m, 15m, 1h, 4h (기본 15m)")
    parser.add_argument("--limit", type=int, default=800,
                        help="캔들 개수 (기본 800, 최대 1000)")
    parser.add_argument("--chart", action="store_true",
                        help="검증용 차트 PNG도 저장 (데이터가 맞는지 확인할 때만)")
    parser.add_argument("--no-agg", action="store_true",
                        help="다중 거래소 OI 집계 끄기 (Binance 단독)")
    args = parser.parse_args()
    symbol = args.symbol.upper()

    try:
        futures, spot, oi, funding, oi_sources = fetch_all(
            symbol, args.interval, args.limit, agg=not args.no_agg)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400:
            print(f"심볼을 찾을 수 없습니다: {symbol} — 오타이거나 Binance 선물에 "
                  f"없는 코인입니다. 예: BTCUSDT, PEPEUSDT")
            sys.exit(1)
        raise
    result = analyze(symbol, args.interval, futures, spot, oi, funding)
    print()
    print(format_report(result))
    print(f"\n(OI 집계: {' + '.join(oi_sources)})")

    if args.chart:
        import os
        from bot.chart import save_verification_chart
        os.makedirs("output", exist_ok=True)
        path = os.path.join("output", f"verify_{symbol}_{args.interval}.png")
        save_verification_chart(symbol, args.interval, futures, spot, oi, funding, path)
        print(f"\n(검증용 차트 저장: {path})")


if __name__ == "__main__":
    main()
