"""설치 확인용 테스트 — 인터넷 없이 가짜 데이터로 전체 파이프라인을 돌린다.

사용법: python test_pipeline.py
성공하면 output/pipeline_test.png 가 생기고 "테스트 통과"가 출력된다.

가짜 데이터는 고수 차트와 같은 시나리오를 흉내낸다:
횡보 -> 선물 주도 펌핑(현물은 미미) -> 고점 횡보(OI 증가) -> 붕괴
"""

import os
import random

import pandas as pd

from bot.collector import _klines_to_df
from bot.indicators import add_cvd
from bot.chart import save_verification_chart

random.seed(42)
N = 300
T0 = 1755000000000  # ms
HOUR = 3600 * 1000


def synth_klines(futures: bool) -> list:
    rows = []
    price = 0.0150
    for i in range(N):
        if i < 100:  # 횡보
            drift, vol_base, buy_ratio = 0.0, 2e6, 0.50
        elif i < 160:  # 펌핑: 선물만 강한 매수 우위
            drift, vol_base = 0.00015, 12e6
            buy_ratio = 0.62 if futures else 0.53
        elif i < 260:  # 고점 횡보
            drift, vol_base, buy_ratio = 0.0, 6e6, 0.51
        else:  # 붕괴
            drift, vol_base = -0.0003, 20e6
            buy_ratio = 0.35 if futures else 0.45
        o = price
        c = max(price + drift + random.gauss(0, 0.0002), 0.003)
        h, l = max(o, c) * 1.004, min(o, c) * 0.996
        vol = vol_base * (1 + random.random())
        if not futures:
            vol *= 0.08  # 현물 거래량은 훨씬 작다
        tbv = vol * buy_ratio
        rows.append([T0 + i * HOUR, o, h, l, c, vol, T0 + (i + 1) * HOUR - 1,
                     vol * c, 1000, tbv, tbv * c, 0])
        price = c
    return rows


def main() -> None:
    futures = add_cvd(_klines_to_df(synth_klines(True)))
    spot = add_cvd(_klines_to_df(synth_klines(False)))

    level, oi_rows = 300e6, []
    for i in range(N):
        if 100 <= i < 260:
            level *= 1.004
        elif i >= 260:
            level *= 0.996
        oi_rows.append({"time": futures["time"].iloc[i],
                        "open_interest": level / 0.02,
                        "open_interest_usd": level})
    oi = pd.DataFrame(oi_rows)

    fund_rows = []
    for i in range(0, N, 8):
        rate = 0.0001 if i < 100 else (0.0008 if i < 260 else -0.0003)
        fund_rows.append({"time": futures["time"].iloc[i], "funding_rate": rate})
    funding = pd.DataFrame(fund_rows)

    assert len(futures) == N and "cvd" in futures.columns
    ratio = abs(futures["cvd"].iloc[-1]) / max(abs(spot["cvd"].iloc[-1]), 1)
    assert ratio > 5, "펌핑 시나리오에서 선물/현물 CVD 비율이 커야 한다"

    os.makedirs("output", exist_ok=True)
    path = os.path.join("output", "pipeline_test.png")
    save_verification_chart("TEST-ALT", "1h", futures, spot, oi, funding, path)
    assert os.path.getsize(path) > 10_000

    print(f"선물 CVD: {futures['cvd'].iloc[-1]:,.0f} / "
          f"현물 CVD: {spot['cvd'].iloc[-1]:,.0f} (비율 {ratio:.0f}배)")
    print(f"차트 저장: {path}")
    print("테스트 통과 — 설치가 정상입니다. 이제 run_phase1.py 를 실행하세요.")


if __name__ == "__main__":
    main()
