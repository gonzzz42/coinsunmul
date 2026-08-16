"""설치 확인 + 분석 엔진 테스트 — 인터넷 없이 가짜 데이터로 전체를 돌린다.

사용법: python test_pipeline.py
고수 차트와 같은 시나리오(횡보 -> 선물 주도 펌핑 -> 고점 횡보 -> 붕괴)를
만들어서, 각 시점에서 분석 엔진이 올바른 단계를 판정하는지 확인한다.
"""

import random

import pandas as pd

from bot.analyzer import analyze, format_report
from bot.collector import _klines_to_df
from bot.indicators import add_cvd

random.seed(42)
N = 300
T0 = 1755000000000  # ms
HOUR = 3600 * 1000
PUMP_START, BOX_START, DUMP_START = 100, 160, 260


def synth_klines(futures: bool) -> list:
    rows = []
    price = 0.0150
    for i in range(N):
        if i < PUMP_START:            # 횡보
            drift, vol_base, buy_ratio = 0.0, 2e6, 0.50
        elif i < BOX_START:           # 펌핑: 선물만 강한 매수 우위
            drift, vol_base = 0.00015, 12e6
            buy_ratio = 0.62 if futures else 0.53
        elif i < DUMP_START:          # 고점 횡보
            drift, vol_base, buy_ratio = 0.0, 6e6, 0.51
        else:                         # 붕괴
            drift, vol_base = -0.0003, 20e6
            buy_ratio = 0.35 if futures else 0.45
        o = price
        c = max(price + drift + random.gauss(0, 0.0002), 0.003)
        h, l = max(o, c) * 1.004, min(o, c) * 0.996
        vol = vol_base * (1 + random.random())
        if not futures:
            vol *= 0.08               # 현물 거래량은 훨씬 작다
        tbv = vol * buy_ratio
        rows.append([T0 + i * HOUR, o, h, l, c, vol, T0 + (i + 1) * HOUR - 1,
                     vol * c, 1000, tbv, tbv * c, 0])
        price = c
    return rows


def synth_oi(times: pd.Series) -> pd.DataFrame:
    level, rows = 300e6, []
    for i in range(N):
        if PUMP_START <= i < DUMP_START:
            level *= 1.004
        elif i >= DUMP_START:
            level *= 0.996
        rows.append({"time": times.iloc[i], "open_interest": level / 0.02,
                     "open_interest_usd": level})
    return pd.DataFrame(rows)


def synth_funding(times: pd.Series) -> pd.DataFrame:
    rows = []
    for i in range(0, N, 8):
        rate = 0.0001 if i < PUMP_START else (0.0008 if i < DUMP_START else -0.0003)
        rows.append({"time": times.iloc[i], "funding_rate": rate})
    return pd.DataFrame(rows)


def snapshot(futures, spot, oi, funding, upto: int):
    """i번째 캔들 시점까지만 보이는 데이터로 자른다 (그 시점의 실시간 분석 흉내)."""
    t = futures["time"].iloc[upto - 1]
    return (futures.iloc[:upto].reset_index(drop=True),
            spot.iloc[:upto].reset_index(drop=True),
            oi[oi["time"] <= t].reset_index(drop=True),
            funding[funding["time"] <= t].reset_index(drop=True))


def main() -> None:
    futures = add_cvd(_klines_to_df(synth_klines(True)))
    spot = add_cvd(_klines_to_df(synth_klines(False)))
    oi = synth_oi(futures["time"])
    funding = synth_funding(futures["time"])

    scenarios = [
        ("펌핑 전 (횡보)", 90, {"no_setup"}),
        ("고점 박스 형성 후", 250, {"armed", "watching"}),
        ("붕괴 이후", N, {"collapsed", "triggered"}),
    ]
    for name, upto, expected in scenarios:
        result = analyze("TEST-ALT", "1h", *snapshot(futures, spot, oi, funding, upto))
        status = "OK" if result.stage in expected else "FAIL"
        print(f"[{status}] {name}: stage={result.stage} (기대: {expected}) "
              f"점수 {result.total}/100")
        assert result.stage in expected, f"{name}: {result.stage} not in {expected}"

    print()
    print("── 박스 형성 시점(캔들 250)의 보고서 예시 ──")
    result = analyze("TEST-ALT", "1h", *snapshot(futures, spot, oi, funding, 250))
    print(format_report(result))
    print()
    print("테스트 통과 — 설치와 분석 엔진이 정상입니다.")
    print("이제 run_analyze.py(즉시 분석) / run_watch.py(감시)를 실행하세요.")


if __name__ == "__main__":
    main()
