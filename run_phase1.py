"""Phase 1 실행 스크립트 — 데이터 수집 + CVD 계산 + 검증 차트 저장.

사용법:
    python run_phase1.py                    # 기본: BTCUSDT 1시간봉
    python run_phase1.py PEPEUSDT           # 다른 코인
    python run_phase1.py PEPEUSDT --interval 15m --limit 400

완료 기준: output/ 폴더에 저장된 차트를 CoinGlass(또는 TradingView의
CoinGlass 지표)와 나란히 놓고 모양이 같은지 눈으로 확인한다.
"""

import argparse
import os

from bot import collector, indicators
from bot.chart import save_verification_chart


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1: 수집 + CVD 검증")
    parser.add_argument("symbol", nargs="?", default="BTCUSDT",
                        help="심볼 (기본 BTCUSDT)")
    parser.add_argument("--interval", default="1h",
                        help="캔들 간격: 5m, 15m, 1h, 4h (기본 1h)")
    parser.add_argument("--limit", type=int, default=500,
                        help="캔들 개수 (기본 500, 최대 1000)")
    args = parser.parse_args()
    symbol = args.symbol.upper()

    print(f"[1/4] {symbol} 선물 캔들 {args.limit}개 수집 중...")
    futures = collector.fetch_futures_klines(symbol, args.interval, args.limit)
    print(f"[2/4] {symbol} 현물 캔들 수집 중...")
    spot = collector.fetch_spot_klines(symbol, args.interval, args.limit)
    print(f"[3/4] OI·펀딩비 수집 중...")
    # OI 히스토리는 5m/15m/30m/1h/2h/4h/6h/12h/1d만 지원
    oi_period = args.interval if args.interval != "1m" else "5m"
    oi = collector.fetch_open_interest(symbol, oi_period, min(args.limit, 500))
    funding = collector.fetch_funding(symbol, 500)
    # 차트 x축을 캔들 구간과 맞춘다
    start = futures["time"].iloc[0]
    funding = funding[funding["time"] >= start]

    futures = indicators.add_cvd(futures)
    spot = indicators.add_cvd(spot)

    os.makedirs("output", exist_ok=True)
    path = os.path.join("output", f"phase1_{symbol}_{args.interval}.png")
    print(f"[4/4] 차트 저장 중... -> {path}")
    save_verification_chart(symbol, args.interval, futures, spot, oi, funding, path)

    last = futures.iloc[-1]
    print()
    print(f"기간          : {futures['time'].iloc[0]:%Y-%m-%d %H:%M} ~ "
          f"{last['time']:%Y-%m-%d %H:%M} UTC ({len(futures)}개 캔들)")
    print(f"현재가        : {last['close']:,.6g}")
    print(f"선물 CVD      : {last['cvd']:,.0f} USDT")
    print(f"현물 CVD      : {spot['cvd'].iloc[-1]:,.0f} USDT")
    print(f"선물/현물 CVD 변화 비율(전체 기간): "
          f"{abs(last['cvd']) / max(abs(spot['cvd'].iloc[-1]), 1):,.1f}배")
    print(f"OI (USDT)     : {oi['open_interest_usd'].iloc[-1]:,.0f}")
    print(f"최근 펀딩비   : {funding['funding_rate'].iloc[-1] * 100:+.4f}%")
    print()
    print(f"이제 {path} 를 열어 CoinGlass 차트와 모양을 비교해 보세요.")


if __name__ == "__main__":
    main()
