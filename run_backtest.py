"""백테스트 실행 — 과거 데이터로 전략을 리플레이해서 승률을 추정한다.

사용법:
    python run_backtest.py PEPEUSDT                    # 최근 30일, 15분봉
    python run_backtest.py PEPEUSDT DOGEUSDT --days 14
    python run_backtest.py BTCUSDT --interval 1h

결과는 output/backtest.sqlite3 에 저장되고, 통계는 run_stats.py와 같은
형식으로 출력된다. (Binance OI 히스토리 한계로 최대 30일)
"""

import argparse
import os
import sys

import requests

from bot import backtest, store


def main() -> None:
    parser = argparse.ArgumentParser(description="숏 전략 백테스트")
    parser.add_argument("symbols", nargs="+", help="심볼 (여러 개 가능)")
    parser.add_argument("--interval", default="15m",
                        help="캔들 간격 (기본 15m)")
    parser.add_argument("--days", type=int, default=30,
                        help="리플레이 기간(일), 최대 30 (기본 30)")
    parser.add_argument("--step", type=int, default=1,
                        help="N바마다 판정 (기본 1 = 매 바)")
    args = parser.parse_args()
    days = min(args.days, backtest.MAX_DAYS)
    if args.days > backtest.MAX_DAYS:
        print(f"(Binance OI 히스토리 한계로 {backtest.MAX_DAYS}일로 제한합니다)")

    os.makedirs("output", exist_ok=True)
    db = os.path.join("output", "backtest.sqlite3")
    if os.path.exists(db):
        os.remove(db)  # 백테스트는 매번 깨끗한 DB에서 시작

    data_by_symbol = {}
    for symbol in [s.upper() for s in args.symbols]:
        print(f"\n{symbol}: 과거 {days}일 데이터 수집 중... (수십 초 걸릴 수 있음)")
        try:
            data = backtest.fetch_history(symbol, args.interval, days)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                print(f"  심볼을 찾을 수 없음 — 건너뜀")
                continue
            raise
        if len(data["futures"]) < 100:
            print(f"  캔들이 {len(data['futures'])}개뿐 — 건너뜀 (상장 직후 코인)")
            continue
        data_by_symbol[symbol] = data
        print(f"  캔들 {len(data['futures'])}개, OI {len(data['oi'])}개 — 리플레이 시작")
        counts = backtest.replay(symbol, args.interval, data, db, days, args.step)
        print(f"  판정 {counts['bars']}바: armed {counts['armed']}건, "
              f"triggered {counts['triggered']}건")

    if not data_by_symbol:
        print("\n백테스트할 데이터가 없습니다.")
        sys.exit(1)

    print("\n결과 판정 중...")
    backtest.evaluate(data_by_symbol, args.interval, db)
    print()
    print("=" * 46)
    print(f"백테스트 성적표 ({days}일, {args.interval}봉)")
    print("=" * 46)
    print(store.format_stats(store.stats(db)))
    print(f"\n상세 기록: {db} (run_stats.py --no-update 로는 라이브 DB를 보므로 "
          f"이 파일은 sqlite 뷰어로 열어 확인)")


if __name__ == "__main__":
    main()
