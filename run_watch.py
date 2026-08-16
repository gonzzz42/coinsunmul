"""감시 체계 — 지정한 코인을 N분마다 전문가 관점으로 재분석하고,
단계가 올라가거나 진입 시그널이 발동하면 알림을 보낸다.

사용법:
    python run_watch.py PEPEUSDT                       # 15분봉, 5분마다 점검
    python run_watch.py PEPEUSDT --interval 1h --every 15
    python run_watch.py PEPEUSDT DOGEUSDT              # 여러 코인 동시 감시

동작:
  - 시작할 때 각 코인의 전체 분석 보고서를 한 번 출력
  - 이후에는 단계가 "올라갈 때"만 알림 (🚨 = 진입 시그널, ✖ = 붕괴).
    단계가 내려가는 건 콘솔에 한 줄만 남긴다 — 경계에서 오르락내리락하며
    같은 알림이 반복되는 것을 막기 위해서다. 내려간 상태가 6번 연속 유지되면
    래치를 풀어 다음 사이클의 새 셋업에서 다시 알림을 받는다.
  - 텔레그램 전송에 실패하면 다음 주기에 자동 재전송한다.
  - 존재하지 않는 심볼은 감시 목록에서 제외한다.
  - 중지: Ctrl+C
"""

import argparse
import time
from datetime import datetime

import requests

from bot.analyzer import STAGE_LABEL, analyze, format_report
from bot.notifier import Notifier
from run_analyze import fetch_all

STAGE_RANK = {"no_setup": 0, "watching": 1, "armed": 2, "triggered": 3, "collapsed": 4}
RESET_AFTER = 6   # 낮은 단계가 이만큼 연속되면 알림 래치를 푼다


def check_once(symbol: str, interval: str, limit: int):
    futures, spot, oi, funding = fetch_all(symbol, interval, limit)
    return analyze(symbol, interval, futures, spot, oi, funding)


def build_alert(prev_stage: str, result) -> str:
    if result.stage == "triggered":
        head = f"🚨 {result.symbol} 진입 시그널 발동!"
    elif result.stage == "collapsed":
        head = f"✖ {result.symbol} 붕괴 — 진입 기회 지나감"
    else:
        head = f"⬆ {result.symbol} 단계 상승"
    head += f"\n{STAGE_LABEL.get(prev_stage, '시작')} → {STAGE_LABEL[result.stage]}"
    return head + "\n\n" + format_report(result)


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

    notified_rank: dict[str, int] = {}   # 심볼별 마지막으로 '알림까지 보낸' 단계
    low_streak: dict[str, int] = {}      # 낮은 단계가 연속된 횟수 (래치 해제용)
    pending: dict[str, str] = {}         # 전송 실패로 재전송 대기 중인 알림

    while True:
        for symbol in list(symbols):
            now = datetime.now().strftime("%H:%M")

            # 지난 주기에 전송 실패한 알림부터 재시도
            if symbol in pending:
                if notifier.send(pending[symbol]):
                    del pending[symbol]

            try:
                result = check_once(symbol, args.interval, args.limit)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 400:
                    print(f"[{now}] {symbol}: 심볼을 찾을 수 없음 (오타이거나 "
                          f"상장 폐지) — 감시 목록에서 제외")
                    symbols.remove(symbol)
                else:
                    print(f"[{now}] {symbol} 점검 실패: {e} (다음 주기에 재시도)")
                continue
            except Exception as e:  # 네트워크 오류 등 — 감시는 계속한다
                print(f"[{now}] {symbol} 점검 실패: {e} (다음 주기에 재시도)")
                continue

            rank = STAGE_RANK[result.stage]
            prev_rank = notified_rank.get(symbol)

            if prev_rank is None:
                # 시작 시 전체 보고서 1회 (triggered 상태로 시작하면 🚨 포함)
                text = build_alert("", result) if result.stage == "triggered" \
                    else format_report(result)
                if notifier.send(text):
                    notified_rank[symbol] = rank
                else:
                    pending[symbol] = text
                    notified_rank[symbol] = rank
                print()
            elif rank > prev_rank:
                prev_stage = next((k for k, v in STAGE_RANK.items() if v == prev_rank), "")
                text = build_alert(prev_stage, result)
                if notifier.send(text):
                    notified_rank[symbol] = rank
                else:
                    pending[symbol] = text
                    notified_rank[symbol] = rank
                low_streak[symbol] = 0
                print()
            else:
                print(f"[{now}] {symbol}: {result.stage} "
                      f"(점수 {result.total}/100, 현재가 {result.price:,.6g})")
                # 낮은 단계가 오래 지속되면 래치를 풀어 다음 셋업에 대비
                if rank < prev_rank:
                    low_streak[symbol] = low_streak.get(symbol, 0) + 1
                    if low_streak[symbol] >= RESET_AFTER:
                        notified_rank[symbol] = rank
                        low_streak[symbol] = 0
                else:
                    low_streak[symbol] = 0

        if not symbols:
            print("감시할 심볼이 없어 종료합니다.")
            return
        time.sleep(args.every * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n감시 종료")
