"""감시 체계(Watcher) 오프라인 테스트 — 인터넷 없이 알림·편입·제외 로직을 검증한다.

사용법: python test_watch.py
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import run_watch
from bot import scanner
from bot.analyzer import Analysis

sent = []


class FakeNotifier:
    telegram_on = False
    fail_next = False

    def send(self, text):
        if self.fail_next:
            self.fail_next = False
            return False
        sent.append(text.split("\n")[0])
        return True


def fake_result(symbol, stage):
    return Analysis(symbol=symbol, interval="15m", stage=stage, price=1.0)


def make_watcher(**kw):
    args = SimpleNamespace(symbols=[], interval="15m", limit=800, every=5,
                           scan=True, scan_every=30, max_auto=2)
    for k, v in kw.items():
        setattr(args, k, v)
    return run_watch.Watcher(args, FakeNotifier())


def main() -> None:
    # 1) 스캔 편입: max_auto 한도
    w = make_watcher()
    scanner.scan = lambda: [scanner.Candidate("AAAUSDT", 40, 8e6, 60),
                            scanner.Candidate("BBBUSDT", 35, 5e6, 30),
                            scanner.Candidate("CCCUSDT", 25, 4e6, 25)]
    w.maybe_scan()
    assert w.symbols == ["AAAUSDT", "BBBUSDT"], w.symbols
    print("[OK] 스캔 편입: max_auto 한도 지킴 (2/3)")

    # 2) 알림 규칙: 첫 보고서 → 상승만 알림 → 유지 시 침묵
    stages = {"AAAUSDT": iter(["watching", "armed", "armed", "triggered", "collapsed"]),
              "BBBUSDT": iter(["no_setup"] * 13)}
    run_watch.check_once = lambda sym, *a, **kw: fake_result(sym, next(stages[sym]))
    w.check_symbol("AAAUSDT")
    assert sent[-1].startswith("🔎 스캐너 편입"), sent[-1]
    w.check_symbol("AAAUSDT")
    assert sent[-1].startswith("⬆"), sent[-1]
    n = len(sent)
    w.check_symbol("AAAUSDT")
    assert len(sent) == n, "단계 유지 시 알림이 없어야 함"
    w.check_symbol("AAAUSDT")
    assert sent[-1].startswith("🚨"), sent[-1]
    w.check_symbol("AAAUSDT")
    assert sent[-1].startswith("✖"), sent[-1]
    print("[OK] 알림 규칙: 첫 보고서 → ⬆ → (유지 시 침묵) → 🚨 → ✖")

    # 3) 자동 제외 + 쿨다운: 제외된 코인은 다음 스캔에서 재편입되지 않는다
    w.check_symbol("BBBUSDT")
    for _ in range(12):
        w.check_symbol("BBBUSDT")
    assert "BBBUSDT" not in w.symbols
    assert "BBBUSDT" in w.evicted
    n = len(sent)
    scanner.scan = lambda: [scanner.Candidate("BBBUSDT", 35, 5e6, 30)]
    w.next_scan = datetime.min          # 강제로 다음 스캔 실행
    w.maybe_scan()                      # 스캐너는 여전히 BBBUSDT를 후보로 줌
    assert "BBBUSDT" not in w.symbols, "쿨다운 중 재편입 금지"
    assert len(sent) == n, "재편입 알림이 반복되면 안 됨"
    w.evicted["BBBUSDT"] = datetime.now() - timedelta(seconds=1)  # 쿨다운 만료
    w.next_scan = datetime.min
    w.maybe_scan()
    assert "BBBUSDT" in w.symbols, "쿨다운이 끝나면 재편입 가능"
    print("[OK] 자동 제외 + 4시간 쿨다운: 편입 알림 반복 방지")

    # 4) 스캔 실패 시 30분이 아니라 다음 점검 주기(5분)에 재시도
    w2 = make_watcher()
    def boom():
        raise RuntimeError("network down")
    scanner.scan = boom
    before = datetime.now()
    w2.maybe_scan()
    wait = (w2.next_scan - before).total_seconds() / 60
    assert wait <= w2.args.every + 0.1, f"실패 시 재시도가 {wait:.0f}분 뒤로 밀림"
    print("[OK] 스캔 실패: 5분 뒤 재시도 (30분 공백 없음)")

    # 5) 전송 실패 시 다음 주기에 재전송
    w.manual.add("DDDUSDT")
    w.symbols.append("DDDUSDT")
    stages["DDDUSDT"] = iter(["armed", "armed"])
    w.notifier.fail_next = True
    w.check_symbol("DDDUSDT")
    assert "DDDUSDT" in w.pending
    w.check_symbol("DDDUSDT")
    assert "DDDUSDT" not in w.pending
    print("[OK] 텔레그램 전송 실패: 다음 주기에 자동 재전송")

    # 6) OI 조회 실패는 '신규'와 다르게 표기
    c = scanner.Candidate("XUSDT", 30, 5e6, oi_error=True)
    assert "확인 실패" in c.describe()
    c2 = scanner.Candidate("YUSDT", 30, 5e6, oi_change_pct=None)
    assert "신규" in c2.describe()
    print("[OK] OI 확인 실패와 신규 상장 구분 표기")

    print("\nWatcher 테스트 전체 통과")


if __name__ == "__main__":
    main()
