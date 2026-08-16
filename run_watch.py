"""감시 체계 — 지정한 코인을 N분마다 전문가 관점으로 재분석하고,
단계가 바뀌거나 진입 시그널이 발동하면 알림을 보낸다.

사용법:
    python run_watch.py PEPEUSDT                       # 15분봉, 5분마다 점검
    python run_watch.py PEPEUSDT --interval 1h --every 15
    python run_watch.py PEPEUSDT DOGEUSDT              # 여러 코인 동시 감시

동작:
  - 시작할 때 각 코인의 전체 분석 보고서를 한 번 출력
  - 이후에는 단계(stage)가 바뀔 때만 보고서를 다시 보낸다
    (특히 armed -> triggered 순간이 계획된 진입 알림)
  - 텔레그램 환경변수가 있으면 알림을 텔레그램으로도 전송 (bot/notifier.py 참고)
  - 중지: Ctrl+C
"""

import argparse
import time
from datetime import datetime

from bot.analyzer import STAGE_LABEL, analyze, format_report
from bot.notifier import Notifier
from run_analyze import fetch_all

# 이 순서로 단계가 올라갈 때만 "전진"으로 본다
STAGE_RANK = {"no_setup": 0, "watching": 1, "armed": 2, "triggered": 3, "collapsed": 4}


def check_once(symbol: str, interval: str, limit: int):
    futures, spot, oi, funding = fetch_all(symbol, interval, limit)
    return analyze(symbol, interval, futures, spot, oi, funding)


def main() -> None:
    parser = argparse.ArgumentParser(description="숏 셋업 감시 체계")
    parser.add_argument("symbols", nargs="+", help="감시할 심볼 (여러 개 가능)")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--limit", type=int, default=800)
    parser.add_argument("--every", type=int, default=5,
                        help="점검 주기(분), 기본 5분")
    args = parser.parse_args()
    symbols = [s.upper() for s in args.symbols]

    notifier = Notifier()
    print(f"감시 시작: {', '.join(symbols)} ({args.interval}봉, {args.every}분마다 점검)")
    print(f"텔레그램 알림: {'켜짐' if notifier.telegram_on else '꺼짐 (콘솔만)'}")
    print("중지하려면 Ctrl+C\n")

    last_stage: dict[str, str] = {}
    while True:
        for symbol in symbols:
            now = datetime.now().strftime("%H:%M")
            try:
                result = check_once(symbol, args.interval, args.limit)
            except Exception as e:  # 네트워크 오류 등 — 감시는 계속한다
                print(f"[{now}] {symbol} 점검 실패: {e} (다음 주기에 재시도)")
                continue

            prev = last_stage.get(symbol)
            if prev is None:
                # 시작 시 전체 보고서 1회
                notifier.send(format_report(result))
                print()
            elif result.stage != prev:
                arrow = "⬆" if STAGE_RANK[result.stage] > STAGE_RANK[prev] else "⬇"
                header = (f"{arrow} {symbol} 단계 변경: {STAGE_LABEL[prev]}"
                          f" → {STAGE_LABEL[result.stage]}")
                if result.stage == "triggered":
                    header = f"🚨 {symbol} 진입 시그널 발동!\n" + header
                notifier.send(header + "\n\n" + format_report(result))
                print()
            else:
                # 변화 없음 — 콘솔에 한 줄만 (생존 확인)
                print(f"[{now}] {symbol}: {result.stage} 유지 "
                      f"(점수 {result.total}/100, 현재가 {result.price:,.6g})")
            last_stage[symbol] = result.stage
        time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
