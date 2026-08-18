"""감시 체계 — 코인을 N분마다 전문가 관점으로 재분석하고,
단계가 올라가거나 진입 시그널이 발동하면 알림을 보낸다.

사용법:
    python run_watch.py --scan                         # 전 종목 스캐너 연동 (추천)
    python run_watch.py PEPEUSDT                       # 직접 고른 코인 감시
    python run_watch.py PEPEUSDT --scan                # 직접 + 스캐너 둘 다
    python run_watch.py PEPEUSDT --interval 1h --every 15

동작:
  - --scan: 30분마다 전 종목을 스캔해서 '급등 + 유동성 + OI 급증' 후보를
    감시 목록에 자동 편입한다 (최대 --max-auto 개). 오래 no_setup이면 자동 제외.
  - 시작(또는 편입) 시 전체 분석 보고서를 한 번 보낸다
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
from datetime import datetime, timedelta

import requests

from bot import collector, scanner, store
from bot.analyzer import STAGE_LABEL, analyze, format_report
from bot.notifier import Notifier
from run_analyze import fetch_all

STAGE_RANK = {"no_setup": 0, "watching": 1, "armed": 2, "triggered": 3, "collapsed": 4}
RESET_AFTER = 6    # 낮은 단계가 이만큼 연속되면 알림 래치를 푼다
EVICT_AFTER = 12   # 자동 편입 코인이 no_setup을 이만큼 연속하면 목록에서 제외
EVICT_COOLDOWN_HOURS = 4  # 제외된 코인은 이 시간 동안 재편입하지 않는다
# 24h 급등 통계는 하루 동안 유지되므로, 쿨다운이 없으면 셋업 없는 코인이
# 편입 -> 1시간 감시 -> 제외 -> 다음 스캔에 재편입을 반복하며 같은 알림을 계속 보낸다.


def check_once(symbol: str, interval: str, limit: int, agg: bool = True):
    futures, spot, oi, funding, _ = fetch_all(symbol, interval, limit, agg)
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


class Watcher:
    """감시 상태(알림 래치·재전송 대기·자동 편입)를 관리한다."""

    def __init__(self, args, notifier: Notifier) -> None:
        self.args = args
        self.notifier = notifier
        self.symbols: list = [s.upper() for s in args.symbols]
        self.manual = set(self.symbols)      # 사용자가 직접 고른 코인은 제외하지 않는다
        self.notified_rank: dict = {}        # 심볼별 마지막으로 알림까지 보낸 단계
        self.low_streak: dict = {}           # 낮은 단계 연속 횟수 (래치 해제용)
        self.no_setup_streak: dict = {}      # 자동 편입 코인의 no_setup 연속 횟수
        self.pending: dict = {}              # 전송 실패로 재전송 대기 중인 알림
        self.intro: dict = {}                # 스캐너 편입 안내문 (첫 보고서에 붙임)
        self.next_scan = datetime.min        # 다음 스캔 시각
        self.evicted: dict = {}              # 제외 심볼 -> 재편입 허용 시각 (쿨다운)
        self.bad_symbols: set = set()        # 400(심볼 없음)으로 제외 — 재편입 금지
        self.next_outcome = datetime.min     # 다음 결과(승률 기록) 업데이트 시각

    # ── 스캐너 연동 ──────────────────────────────────────────────
    def maybe_scan(self) -> None:
        now = datetime.now()
        if not self.args.scan or now < self.next_scan:
            return
        print(f"[{now:%H:%M}] 전 종목 스캔 중...")
        try:
            candidates = scanner.scan()
        except Exception as e:
            # 일시 오류로 30분을 통째로 쉬지 않도록 다음 점검 주기에 재시도
            self.next_scan = now + timedelta(minutes=self.args.every)
            print(f"  스캔 실패: {e} ({self.args.every}분 뒤 재시도)")
            return
        self.next_scan = now + timedelta(minutes=self.args.scan_every)
        auto_count = sum(1 for s in self.symbols if s not in self.manual)
        for c in candidates:
            if c.symbol in self.symbols or c.symbol in self.bad_symbols:
                continue
            allowed_at = self.evicted.get(c.symbol)
            if allowed_at is not None:
                if now < allowed_at:
                    continue  # 쿨다운 중 — 최근에 제외된 코인
                del self.evicted[c.symbol]
            if auto_count >= self.args.max_auto:
                print(f"  자동 감시 한도({self.args.max_auto}개) 도달 — {c.symbol} 보류")
                continue
            self.symbols.append(c.symbol)
            self.intro[c.symbol] = f"🔎 스캐너 편입 — {c.describe()}"
            auto_count += 1
            print(f"  편입: {c.describe()}")
        if not candidates:
            print("  조건을 만족하는 신규 후보 없음")

    def evict_if_stale(self, symbol: str, stage: str) -> bool:
        """자동 편입 코인이 셋업 구조를 잃고 오래 지나면 목록에서 뺀다."""
        if symbol in self.manual:
            return False
        if stage == "no_setup":
            self.no_setup_streak[symbol] = self.no_setup_streak.get(symbol, 0) + 1
            if self.no_setup_streak[symbol] >= EVICT_AFTER:
                self.drop(symbol)
                self.evicted[symbol] = (datetime.now()
                                        + timedelta(hours=EVICT_COOLDOWN_HOURS))
                print(f"  {symbol}: 셋업 구조가 사라진 지 오래 — 감시 목록에서 제외 "
                      f"(재편입은 {EVICT_COOLDOWN_HOURS}시간 뒤부터)")
                return True
        else:
            self.no_setup_streak[symbol] = 0
        return False

    def drop(self, symbol: str) -> None:
        if symbol in self.symbols:
            self.symbols.remove(symbol)
        for d in (self.notified_rank, self.low_streak,
                  self.no_setup_streak, self.pending, self.intro):
            d.pop(symbol, None)

    # ── 시그널 기록 (Phase 5: 승률 통계의 재료) ──────────────────
    def record(self, result) -> None:
        if result.stage not in ("armed", "triggered"):
            return
        try:
            store.record_signal(result)
        except Exception as e:  # 기록 실패가 감시를 멈추면 안 된다
            print(f"  (시그널 기록 실패: {e})")

    def maybe_update_outcomes(self) -> None:
        """기록된 시그널의 1h/4h/24h 결과를 주기적으로 채운다 (30분마다)."""
        if datetime.now() < self.next_outcome:
            return
        self.next_outcome = datetime.now() + timedelta(minutes=30)
        try:
            store.update_outcomes(collector.fetch_futures_klines)
        except Exception as e:
            print(f"  (승률 기록 업데이트 실패: {e} — 다음에 재시도)")

    # ── 알림 (전송 실패 시 재전송 대기) ──────────────────────────
    def notify(self, symbol: str, text: str) -> None:
        if not self.notifier.send(text):
            self.pending[symbol] = text

    def retry_pending(self, symbol: str) -> None:
        if symbol in self.pending and self.notifier.send(self.pending[symbol]):
            del self.pending[symbol]

    # ── 심볼 1개 점검 ────────────────────────────────────────────
    def check_symbol(self, symbol: str) -> None:
        now = datetime.now().strftime("%H:%M")
        self.retry_pending(symbol)
        try:
            result = check_once(symbol, self.args.interval, self.args.limit,
                                agg=not getattr(self.args, "no_agg", False))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                print(f"[{now}] {symbol}: 심볼을 찾을 수 없음 (오타이거나 상장 폐지) "
                      f"— 감시 목록에서 제외")
                self.drop(symbol)
                self.bad_symbols.add(symbol)  # 스캐너가 다시 편입하지 않도록
            else:
                print(f"[{now}] {symbol} 점검 실패: {e} (다음 주기에 재시도)")
            return
        except Exception as e:  # 네트워크 오류 등 — 감시는 계속한다
            print(f"[{now}] {symbol} 점검 실패: {e} (다음 주기에 재시도)")
            return

        if self.evict_if_stale(symbol, result.stage):
            return

        rank = STAGE_RANK[result.stage]
        prev_rank = self.notified_rank.get(symbol)

        if prev_rank is None:
            # 시작·편입 시 전체 보고서 1회 (triggered면 🚨 헤더 포함)
            text = build_alert("", result) if result.stage == "triggered" \
                else format_report(result)
            if symbol in self.intro:
                text = self.intro.pop(symbol) + "\n\n" + text
            self.notified_rank[symbol] = rank
            self.notify(symbol, text)
            self.record(result)
            print()
        elif rank > prev_rank:
            prev_stage = next((k for k, v in STAGE_RANK.items() if v == prev_rank), "")
            self.notified_rank[symbol] = rank
            self.low_streak[symbol] = 0
            self.notify(symbol, build_alert(prev_stage, result))
            self.record(result)
            print()
        else:
            print(f"[{now}] {symbol}: {result.stage} "
                  f"(점수 {result.total}/100, 현재가 {result.price:,.6g})")
            if rank < prev_rank:
                self.low_streak[symbol] = self.low_streak.get(symbol, 0) + 1
                if self.low_streak[symbol] >= RESET_AFTER:
                    self.notified_rank[symbol] = rank
                    self.low_streak[symbol] = 0
            else:
                self.low_streak[symbol] = 0

    def run(self) -> None:
        while True:
            self.maybe_scan()
            for symbol in list(self.symbols):
                self.check_symbol(symbol)
                time.sleep(0.3)  # 요청 제한 보호
            self.maybe_update_outcomes()
            if not self.symbols and not self.args.scan:
                print("감시할 심볼이 없어 종료합니다.")
                return
            time.sleep(self.args.every * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="숏 셋업 감시 체계")
    parser.add_argument("symbols", nargs="*", help="감시할 심볼 (생략하고 --scan만 써도 됨)")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--limit", type=int, default=800)
    parser.add_argument("--every", type=int, default=5,
                        help="점검 주기(분), 기본 5분")
    parser.add_argument("--scan", action="store_true",
                        help="전 종목 스캐너로 후보를 자동 편입")
    parser.add_argument("--scan-every", type=int, default=30,
                        help="스캔 주기(분), 기본 30분")
    parser.add_argument("--max-auto", type=int, default=8,
                        help="자동 편입 최대 개수 (기본 8)")
    parser.add_argument("--no-agg", action="store_true",
                        help="다중 거래소 OI 집계 끄기 (Binance 단독)")
    args = parser.parse_args()
    if not args.symbols and not args.scan:
        parser.error("감시할 심볼을 주거나 --scan 을 켜세요. 예: "
                     "python run_watch.py --scan")

    notifier = Notifier()
    target = ", ".join(s.upper() for s in args.symbols) if args.symbols else "(스캐너 자동)"
    print(f"감시 시작: {target} ({args.interval}봉, {args.every}분마다 점검"
          + (f", {args.scan_every}분마다 스캔" if args.scan else "") + ")")
    print(f"텔레그램 알림: {'켜짐' if notifier.telegram_on else '꺼짐 (콘솔만)'}")
    print("중지하려면 Ctrl+C\n")

    Watcher(args, notifier).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n감시 종료")
