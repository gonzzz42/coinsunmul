"""기록·승률 모듈(bot/store.py) 오프라인 테스트 — 인터넷 없이 검증한다.

사용법: python test_store.py
"""

import os

import pandas as pd

from bot import store
from bot.analyzer import Analysis

DB = "output/test_signals.sqlite3"
T0 = pd.Timestamp("2026-08-18 00:00:00+00:00")
H = pd.Timedelta(hours=1)


def sig(symbol, stage, price, entry, stop, target1, total=75):
    a = Analysis(symbol=symbol, interval="1h", stage=stage, price=price)
    a.entry, a.stop, a.target1, a.target2 = entry, stop, target1, target1 * 0.9
    a.checks_a = []  # 점수는 total로 직접 검증하지 않는다 (기본 0점)
    return a


def klines(rows):
    """rows = [(시작시각 오프셋h, high, low, close), ...]"""
    return pd.DataFrame({
        "time": [T0 + h * H for h, *_ in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
    })


# 시나리오별 캔들 경로 (시그널은 전부 T0에 발생)
PATHS = {
    # 승리: 하락해서 T0+5h에 1차 목표(90) 터치. 손절(105)은 안 닿음
    "SIGA": klines([(-2, 101, 99, 100), (-1, 101, 99, 100), (0, 100, 96, 97),
                    (1, 98, 95, 96), (2, 97, 93, 94), (3, 95, 91, 92),
                    (4, 93, 90.5, 91), (5, 92, 89, 90),
                    *[(h, 91, 87, 88) for h in range(6, 30)]]),
    # 미진입: armed인데 가격이 진입가(95)까지 안 내려옴
    "SIGB": klines([(0, 100, 97, 99), *[(h, 101, 96, 99) for h in range(1, 30)]]),
    # 패배: 진입가(95) 체결 후 반등해서 손절(105) 터치
    "SIGC": klines([(0, 100, 97, 98), (1, 99, 96, 97), (2, 98, 94, 95),
                    (3, 101, 95, 100), (4, 104, 99, 103), (5, 106, 102, 105),
                    *[(h, 107, 103, 105) for h in range(6, 30)]]),
}


PATHS["SIGGAP"] = PATHS["SIGA"]  # data_gap 시나리오용 (같은 경로, 다른 심볼)


def fake_fetch(symbol, interval, limit):
    return PATHS[symbol]


def main() -> None:
    os.makedirs("output", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)

    now0 = T0.to_pydatetime()
    store.record_signal(sig("SIGA", "triggered", 100, 100, 105, 90), DB, now0)
    store.record_signal(sig("SIGB", "armed", 100, 95, 105, 85), DB, now0)
    store.record_signal(sig("SIGC", "armed", 100, 95, 105, 85), DB, now0)

    # 2시간 뒤: 1h 결과만 채워져야 하고 done은 아직 아님
    store.update_outcomes(fake_fetch, DB, (T0 + 2 * H).to_pydatetime())
    rows = {r["symbol"]: r for r in store._conn(DB).execute("SELECT * FROM signals")}
    a = rows["SIGA"]
    assert abs(a["ret_1h"] - 3.0) < 1e-9, a["ret_1h"]   # T0+1h 가격 = T0 캔들 종가 97
    assert a["ret_4h"] is None and a["done"] == 0
    print("[OK] 2시간 시점: 1h 수익률만 채움 (T0 캔들 종가 97 → +3%)")

    # 25시간 뒤: 전부 채워지고 done=1
    store.update_outcomes(fake_fetch, DB, (T0 + 25 * H).to_pydatetime())
    rows = {r["symbol"]: r for r in store._conn(DB).execute("SELECT * FROM signals")}
    a, b, c = rows["SIGA"], rows["SIGB"], rows["SIGC"]
    assert abs(a["ret_4h"] - 8.0) < 1e-9 and abs(a["ret_24h"] - 12.0) < 1e-9
    assert a["first_touch"] == "target1" and a["done"] == 1
    assert b["first_touch"] == "no_fill", b["first_touch"]
    assert c["first_touch"] == "stop", c["first_touch"]
    print("[OK] 25시간 시점: 승(target1) / 미진입(no_fill) / 패(stop) 판정")

    # 중복 방지: 같은 심볼·단계·비슷한 레벨이 12시간 안에 또 오면 기록하지 않는다
    n_before = store.stats(DB)["count"]
    id1 = store.record_signal(sig("SIGA", "triggered", 100, 100, 105, 90), DB,
                              (T0 + H).to_pydatetime())
    id2 = store.record_signal(sig("SIGA", "triggered", 99.8, 100.2, 105.3, 90), DB,
                              (T0 + 2 * H).to_pydatetime())
    assert id1 == id2 and store.stats(DB)["count"] == n_before, \
        "진동/재시작으로 인한 같은 셋업 중복 기록을 막아야 함"
    print("[OK] 중복 방지: 같은 셋업(레벨 0.5% 이내) 재기록 차단")

    # 데이터 공백: 캔들 범위가 시그널보다 늦게 시작하면 data_gap으로 종결
    store.record_signal(sig("SIGGAP", "triggered", 100, 100, 105, 90), DB,
                        (T0 - pd.Timedelta(days=30)).to_pydatetime())
    store.update_outcomes(fake_fetch, DB, (T0 + 25 * H).to_pydatetime())
    gap = store._conn(DB).execute(
        "SELECT * FROM signals ORDER BY id DESC LIMIT 1").fetchone()
    assert gap["first_touch"] == "data_gap" and gap["done"] == 1
    print("[OK] 오래된 시그널: data_gap 처리로 무한 재조회 방지")

    s = store.stats(DB)
    assert s["count"] == 4
    assert s["trades"]["target1"] == 1 and s["trades"]["stop"] == 1
    assert abs(s["trades"]["win_rate"] - 50.0) < 1e-9
    assert s["horizons"]["ret_24h"]["n"] == 1
    print("[OK] 통계: 체결 기준 승률 50% (승1 패1), 수익률 표본 집계")
    print()
    print(store.format_stats(s))
    print("\nstore 테스트 전체 통과")


if __name__ == "__main__":
    main()
