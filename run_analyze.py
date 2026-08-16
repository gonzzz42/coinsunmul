"""즉시 분석 — 지금 이 코인이 숏 셋업의 어느 단계인지 판정하고
진입가/손절가/목표가를 제시한다.

사용법:
    python run_analyze.py PEPEUSDT                 # 15분봉 기본
    python run_analyze.py BTCUSDT --interval 1h
    python run_analyze.py PEPEUSDT --chart         # 검증용 차트도 저장 (선택)
"""

import argparse

from bot import collector, indicators
from bot.analyzer import analyze, format_report


def fetch_all(symbol: str, interval: str, limit: int):
    futures = indicators.add_cvd(collector.fetch_futures_klines(symbol, interval, limit))
    spot = indicators.add_cvd(collector.fetch_spot_klines(symbol, interval, limit))
    oi_period = interval if interval != "1m" else "5m"
    oi = collector.fetch_open_interest(symbol, oi_period, min(limit, 500))
    funding = collector.fetch_funding(symbol, 500)
    funding = funding[funding["time"] >= futures["time"].iloc[0]]
    return futures, spot, oi, funding


def main() -> None:
    parser = argparse.ArgumentParser(description="숏 셋업 즉시 분석")
    parser.add_argument("symbol", nargs="?", default="BTCUSDT")
    parser.add_argument("--interval", default="15m",
                        help="캔들 간격: 5m, 15m, 1h, 4h (기본 15m)")
    parser.add_argument("--limit", type=int, default=800,
                        help="캔들 개수 (기본 800, 최대 1000)")
    parser.add_argument("--chart", action="store_true",
                        help="검증용 차트 PNG도 저장 (데이터가 맞는지 확인할 때만)")
    args = parser.parse_args()
    symbol = args.symbol.upper()

    futures, spot, oi, funding = fetch_all(symbol, args.interval, args.limit)
    result = analyze(symbol, args.interval, futures, spot, oi, funding)
    print()
    print(format_report(result))

    if args.chart:
        import os
        from bot.chart import save_verification_chart
        os.makedirs("output", exist_ok=True)
        path = os.path.join("output", f"verify_{symbol}_{args.interval}.png")
        save_verification_chart(symbol, args.interval, futures, spot, oi, funding, path)
        print(f"\n(검증용 차트 저장: {path})")


if __name__ == "__main__":
    main()
