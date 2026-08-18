"""다중 거래소 집계 (Phase 4) — CoinGlass의 '집계 OI'와 같은 개념.

미결제약정(OI)을 Binance + Bybit + OKX 3곳에서 모아 USD 기준으로 합산한다.
청산 연료가 얼마나 쌓였는지는 한 거래소만 봐서는 절반만 보는 것이라서다.

왜 OI만 집계하나:
  - CVD: 무료 API에서 taker 매수/매도 구분을 주는 곳이 Binance뿐이다.
    (Bybit/OKX 캔들에는 없음) Binance가 알트 선물 거래량의 대부분을 차지해
    Binance CVD만으로도 모양은 대표성이 있다.
  - 펀딩비: 거래소별 정산 주기·기준이 달라 단순 합산이 오히려 왜곡이다.
    Binance 기준을 유지한다.

한 거래소 조회가 실패하면 그 거래소만 빼고 합산한다 (감시가 멈추면 안 됨).
"""

import pandas as pd
import requests

BYBIT_BASE = "https://api.bybit.com"
OKX_BASE = "https://www.okx.com"

# Binance 캔들 간격 -> Bybit OI intervalTime
BYBIT_INTERVAL = {"1m": "5min", "3m": "5min", "5m": "5min", "15m": "15min",
                  "30m": "30min", "1h": "1h", "2h": "1h", "4h": "4h",
                  "6h": "4h", "12h": "1d", "1d": "1d"}
# Binance 캔들 간격 -> OKX rubik period (5m/1H/1D만 지원)
OKX_PERIOD = {"1m": "5m", "3m": "5m", "5m": "5m", "15m": "5m", "30m": "5m",
              "1h": "1H", "2h": "1H", "4h": "1H", "6h": "1H", "12h": "1H",
              "1d": "1D"}


def base_ccy(symbol: str) -> str:
    """Binance 선물 심볼 -> 기초 코인. 예: 1000PEPEUSDT -> PEPE, BTCUSDT -> BTC"""
    base = symbol.removesuffix("USDT").removesuffix("USDC")
    for prefix in ("1000000", "1000", "1M"):
        if base.startswith(prefix) and len(base) > len(prefix):
            return base[len(prefix):]
    return base


def fetch_bybit_oi(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """Bybit 무기한 OI (코인 수량 기준). 심볼 표기는 Binance와 같다."""
    resp = requests.get(f"{BYBIT_BASE}/v5/market/open-interest",
                        params={"category": "linear", "symbol": symbol,
                                "intervalTime": BYBIT_INTERVAL.get(interval, "1h"),
                                "limit": min(limit, 200)},
                        timeout=15)
    resp.raise_for_status()
    data = resp.json()
    rows = (data.get("result") or {}).get("list") or []
    if not rows:
        return pd.DataFrame(columns=["time", "oi_coins"])
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
    df["oi_coins"] = df["openInterest"].astype(float)
    return df[["time", "oi_coins"]].sort_values("time").reset_index(drop=True)


def fetch_okx_oi_usd(symbol: str, interval: str) -> pd.DataFrame:
    """OKX 파생상품 OI (USD 기준, 코인 단위 통계라 심볼이 아닌 기초 코인으로 조회)."""
    resp = requests.get(f"{OKX_BASE}/api/v5/rubik/stat/contracts/open-interest-volume",
                        params={"ccy": base_ccy(symbol),
                                "period": OKX_PERIOD.get(interval, "1H")},
                        timeout=15)
    resp.raise_for_status()
    rows = resp.json().get("data") or []
    if not rows:
        return pd.DataFrame(columns=["time", "oi_usd"])
    df = pd.DataFrame(rows, columns=["ts", "oi", "vol"])
    df["time"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df["oi_usd"] = df["oi"].astype(float)
    return df[["time", "oi_usd"]].sort_values("time").reset_index(drop=True)


def _asof_join(base_times: pd.Series, other: pd.DataFrame, col: str) -> pd.Series:
    """other의 값을 base 시각마다 '그 시각 이전의 최신 값'으로 붙인다."""
    if not len(other):
        return pd.Series([0.0] * len(base_times))
    merged = pd.merge_asof(
        pd.DataFrame({"time": base_times}).sort_values("time"),
        other.sort_values("time"), on="time", direction="backward")
    return merged[col].fillna(0.0)


def aggregate_oi(symbol: str, interval: str,
                 binance_oi: pd.DataFrame,
                 binance_close: pd.DataFrame) -> tuple:
    """Binance OI에 Bybit·OKX를 합산한 (DataFrame, 포함된 거래소 목록)을 돌려준다.

    binance_oi: collector.fetch_open_interest 결과 (time, open_interest_usd, ...)
    binance_close: 선물 캔들 (time, close) — Bybit 코인 수량을 USD로 환산할 때 사용
    실패한 거래소는 조용히 빼고, Binance만 남으면 그대로 돌려준다.
    """
    if not len(binance_oi):
        return binance_oi, ["binance"]
    out = binance_oi.copy()
    total = out["open_interest_usd"].astype(float).copy()
    sources = ["binance"]

    try:
        bybit = fetch_bybit_oi(symbol, interval)
        if len(bybit):
            coins = _asof_join(out["time"], bybit, "oi_coins")
            price = _asof_join(out["time"], binance_close[["time", "close"]], "close")
            total = total + (coins * price)
            sources.append("bybit")
    except (requests.RequestException, KeyError, ValueError):
        pass

    try:
        okx = fetch_okx_oi_usd(symbol, interval)
        if len(okx):
            total = total + _asof_join(out["time"], okx, "oi_usd")
            sources.append("okx")
    except (requests.RequestException, KeyError, ValueError):
        pass

    out["open_interest_usd"] = total
    return out, sources
