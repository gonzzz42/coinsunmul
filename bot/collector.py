"""Binance 공개 API에서 데이터를 수집하는 모듈 (API 키 불필요).

Phase 1에서 수집하는 4가지:
  - 선물 캔들 (taker 매수량 포함) -> 선물 CVD 계산 재료
  - 현물 캔들 (taker 매수량 포함) -> 현물 CVD 계산 재료
  - 미결제약정(OI) 히스토리
  - 펀딩비 히스토리
"""

import time

import pandas as pd
import requests

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"

# 캔들 응답 배열의 각 인덱스 의미 (Binance 문서 기준)
# 0 시작시각, 1 시가, 2 고가, 3 저가, 4 종가, 5 거래량,
# 6 종료시각, 7 거래대금, 8 체결 수, 9 taker 매수 거래량, 10 taker 매수 거래대금
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
]


def _get(url: str, params: dict) -> list:
    """API 호출. 요청 제한(429)에 걸리면 잠시 기다렸다가 1회 재시도."""
    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code == 429:
        time.sleep(5)
        resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


KLINE_OUT_COLUMNS = ["time", "open", "high", "low", "close", "volume",
                     "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]


def _klines_to_df(raw: list) -> pd.DataFrame:
    if not raw:  # 상장 직후 등 데이터가 아예 없는 경우
        return pd.DataFrame(columns=KLINE_OUT_COLUMNS)
    df = pd.DataFrame(raw, columns=KLINE_COLUMNS)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    numeric = ["open", "high", "low", "close", "volume",
               "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]
    df[numeric] = df[numeric].astype(float)
    return df[KLINE_OUT_COLUMNS]


def fetch_futures_klines(symbol: str, interval: str = "1h", limit: int = 500) -> pd.DataFrame:
    """USDT 무기한 선물 캔들. 예: symbol='BTCUSDT'"""
    raw = _get(f"{FUTURES_BASE}/fapi/v1/klines",
               {"symbol": symbol, "interval": interval, "limit": limit})
    return _klines_to_df(raw)


def fetch_spot_klines(symbol: str, interval: str = "1h", limit: int = 500) -> pd.DataFrame:
    """현물 캔들. 선물과 같은 심볼 표기를 쓴다 (BTCUSDT)."""
    raw = _get(f"{SPOT_BASE}/api/v3/klines",
               {"symbol": symbol, "interval": interval, "limit": limit})
    return _klines_to_df(raw)


def fetch_open_interest(symbol: str, period: str = "1h", limit: int = 500) -> pd.DataFrame:
    """미결제약정(OI) 히스토리. Binance는 최근 30일까지만 제공한다."""
    raw = _get(f"{FUTURES_BASE}/futures/data/openInterestHist",
               {"symbol": symbol, "period": period, "limit": limit})
    if not raw:  # 신규 상장 코인은 OI 스냅샷이 아직 쌓이지 않아 []를 준다
        return pd.DataFrame(columns=["time", "open_interest", "open_interest_usd"])
    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["open_interest"] = df["sumOpenInterest"].astype(float)
    df["open_interest_usd"] = df["sumOpenInterestValue"].astype(float)
    return df[["time", "open_interest", "open_interest_usd"]]


def _klines_range(url: str, symbol: str, interval: str,
                  start_ms: int, end_ms: int) -> pd.DataFrame:
    """기간 지정 캔들 수집 (백테스트용). 1500개씩 페이지를 넘기며 모은다."""
    out: list = []
    cur = int(start_ms)
    while cur < end_ms:
        raw = _get(url, {"symbol": symbol, "interval": interval,
                         "startTime": cur, "endTime": int(end_ms), "limit": 1500})
        if not raw:
            break
        out.extend(raw)
        if len(raw) < 1500:
            break
        cur = int(raw[-1][0]) + 1  # 마지막 캔들 시작시각 다음부터
        time.sleep(0.15)           # 요청 제한 보호
    return _klines_to_df(out)


def fetch_futures_klines_range(symbol: str, interval: str,
                               start_ms: int, end_ms: int) -> pd.DataFrame:
    return _klines_range(f"{FUTURES_BASE}/fapi/v1/klines", symbol, interval,
                         start_ms, end_ms)


def fetch_spot_klines_range(symbol: str, interval: str,
                            start_ms: int, end_ms: int) -> pd.DataFrame:
    return _klines_range(f"{SPOT_BASE}/api/v3/klines", symbol, interval,
                         start_ms, end_ms)


def fetch_open_interest_range(symbol: str, period: str,
                              start_ms: int, end_ms: int) -> pd.DataFrame:
    """기간 지정 OI 수집. Binance는 최근 30일까지만 제공한다."""
    out: list = []
    cur = int(start_ms)
    while cur < end_ms:
        raw = _get(f"{FUTURES_BASE}/futures/data/openInterestHist",
                   {"symbol": symbol, "period": period,
                    "startTime": cur, "endTime": int(end_ms), "limit": 500})
        if not raw:
            break
        out.extend(raw)
        if len(raw) < 500:
            break
        cur = int(raw[-1]["timestamp"]) + 1
        time.sleep(0.15)
    if not out:
        return pd.DataFrame(columns=["time", "open_interest", "open_interest_usd"])
    df = pd.DataFrame(out)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["open_interest"] = df["sumOpenInterest"].astype(float)
    df["open_interest_usd"] = df["sumOpenInterestValue"].astype(float)
    return df[["time", "open_interest", "open_interest_usd"]]


def fetch_funding(symbol: str, limit: int = 500) -> pd.DataFrame:
    """펀딩비 히스토리. 보통 8시간마다 1건씩 쌓인다."""
    raw = _get(f"{FUTURES_BASE}/fapi/v1/fundingRate",
               {"symbol": symbol, "limit": limit})
    if not raw:  # 상장 8시간 미만이면 펀딩 정산 이력이 아직 없다
        return pd.DataFrame(columns=["time", "funding_rate"])
    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["funding_rate"] = df["fundingRate"].astype(float)
    return df[["time", "funding_rate"]]
