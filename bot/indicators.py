"""캔들 데이터에서 지표를 계산하는 모듈.

핵심은 CVD(누적 체결 델타):
  한 캔들의 델타 = taker 매수량 - taker 매도량
                = taker_buy_volume - (volume - taker_buy_volume)
                = 2 * taker_buy_volume - volume
  CVD = 델타의 누적 합

taker(시장가 주문을 낸 쪽)가 매수면 "사고 싶어서 즉시 산 것"이므로,
델타가 양수면 공격적 매수 우위, 음수면 공격적 매도 우위다.
CoinGlass의 CVD 지표와 같은 계산 방식이다.
"""

import pandas as pd


def add_cvd(klines: pd.DataFrame, quote: bool = True) -> pd.DataFrame:
    """캔들 DataFrame에 delta, cvd 컬럼을 추가해서 돌려준다.

    quote=True면 거래대금(USDT) 기준으로 계산한다.
    CoinGlass 집계 CVD가 달러 기준이라 비교할 때는 quote 기준이 맞다.
    """
    df = klines.copy()
    if quote:
        buy = df["taker_buy_quote_volume"]
        total = df["quote_volume"]
    else:
        buy = df["taker_buy_volume"]
        total = df["volume"]
    df["delta"] = 2 * buy - total
    df["cvd"] = df["delta"].cumsum()
    return df


def pct_change_over(series: pd.Series, bars: int) -> float:
    """마지막 값이 bars개 전 값 대비 몇 % 변했는지. (다음 Phase의 조건 평가에 사용)"""
    if len(series) <= bars:
        return 0.0
    old = series.iloc[-1 - bars]
    if old == 0:
        return 0.0
    return (series.iloc[-1] / old - 1) * 100
