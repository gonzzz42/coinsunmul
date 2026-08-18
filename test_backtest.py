"""백테스트 엔진 오프라인 테스트 — 합성 펌핑-덤핑 데이터를 리플레이한다.

사용법: python test_backtest.py
"""

import os

import test_pipeline as tp
from bot import backtest, store
from bot.collector import _klines_to_df
from bot.indicators import add_cvd

DB = "output/backtest_test.sqlite3"


def main() -> None:
    os.makedirs("output", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)

    futures = add_cvd(_klines_to_df(tp.synth_klines(True)))
    spot = add_cvd(_klines_to_df(tp.synth_klines(False)))
    data = {"futures": futures, "spot": spot,
            "oi": tp.synth_oi(futures["time"]),
            "funding": tp.synth_funding(futures["time"])}

    # 펌핑 이전(bar 84)부터 마지막까지 매 바 리플레이 (look-ahead 없음)
    counts = backtest.replay("TEST-ALT", "1h", data, DB, days=9,
                             progress=lambda *a: None)
    assert counts["bars"] > 200, counts
    assert counts["armed"] >= 1, "박스 구간에서 armed가 잡혀야 함"
    assert counts["armed"] <= 5, \
        f"래치가 있으면 한 셋업에서 armed가 수십 건 쌓이면 안 됨: {counts['armed']}건"
    assert counts["triggered"] >= 1, "이탈 확정 바에서 triggered가 잡혀야 함"
    print(f"[OK] 리플레이 {counts['bars']}바: armed {counts['armed']}건, "
          f"triggered {counts['triggered']}건 (중복 제거 후)")

    backtest.evaluate({"TEST-ALT": data}, "1h", DB)
    rows = [dict(r) for r in store._conn(DB).execute("SELECT * FROM signals")]
    trig = [r for r in rows if r["stage"] == "triggered"]
    assert trig, "triggered 기록이 있어야 함"
    assert all(r["first_touch"] == "target1" for r in trig), \
        [r["first_touch"] for r in trig]
    assert all(r["done"] == 1 for r in trig)
    assert all(r["ret_24h"] is not None and r["ret_24h"] > 0 for r in trig), \
        "덤핑 시나리오에서 triggered 숏은 24h 수익이 양수여야 함"
    print(f"[OK] 결과 판정: triggered {len(trig)}건 전부 1차 목표 도달(승), "
          f"24h 수익 양수")

    s = store.stats(DB)
    assert "win_rate" in s["trades"]
    print(f"[OK] 통계 집계: 체결 기준 승률 {s['trades']['win_rate']:.0f}%")
    print()
    print(store.format_stats(s))
    print("\nbacktest 테스트 전체 통과")


if __name__ == "__main__":
    main()
