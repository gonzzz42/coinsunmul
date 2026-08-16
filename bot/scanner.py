"""전 종목 스캐너 — Binance USDT 무기한 전체에서 숏 셋업 후보를 찾는다.

선별 기준 (PLAN.md의 A. 컨텍스트 필터와 같은 논리):
  1. 24시간 상승률이 크다        -> 펌핑 코인
  2. 거래대금이 충분하다          -> 유동성 없는 잡코인 제외 (진입/청산 가능해야 함)
  3. OI가 하루 사이 크게 늘었다   -> 레버리지가 쌓이는 중

API 호출량을 아끼는 구조:
  - 전 종목 시세는 한 번의 호출로 받는다 (/fapi/v1/ticker/24hr)
  - OI 확인은 1·2차를 통과한 상위 후보에만 개별 호출한다
"""

import time
from dataclasses import dataclass

import pandas as pd
import requests

from bot import collector

# 기본 임계치 (Phase 5에서 통계로 조정할 가설값)
MIN_CHANGE_PCT = 20.0        # 24h 상승률 최소
MIN_QUOTE_VOLUME = 3_000_000  # 24h 거래대금 최소 (USDT)
MIN_OI_CHANGE_PCT = 20.0     # 24h OI 증가율 최소
MAX_OI_CHECKS = 15           # OI 개별 조회는 상위 N개만


@dataclass
class Candidate:
    symbol: str
    change_pct: float          # 24h 상승률
    quote_volume: float        # 24h 거래대금 (USDT)
    oi_change_pct: float | None = None  # 24h OI 증가율 (None = 이력 없음/신규)

    def describe(self) -> str:
        oi = (f"OI {self.oi_change_pct:+.0f}%" if self.oi_change_pct is not None
              else "OI 이력 없음(신규)")
        return (f"{self.symbol}: 24h {self.change_pct:+.1f}%, "
                f"거래대금 {self.quote_volume / 1e6:,.0f}M, {oi}")


def fetch_perp_symbols() -> set:
    """거래 중인 USDT 무기한 심볼 목록."""
    resp = requests.get(f"{collector.FUTURES_BASE}/fapi/v1/exchangeInfo", timeout=15)
    resp.raise_for_status()
    return {s["symbol"] for s in resp.json()["symbols"]
            if s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"}


def fetch_tickers() -> list:
    """전 종목 24시간 시세 (호출 1번)."""
    resp = requests.get(f"{collector.FUTURES_BASE}/fapi/v1/ticker/24hr", timeout=15)
    resp.raise_for_status()
    return resp.json()


def filter_gainers(tickers: list, perp_symbols: set,
                   min_change: float = MIN_CHANGE_PCT,
                   min_volume: float = MIN_QUOTE_VOLUME) -> list:
    """급등 + 유동성 필터 (순수 로직 — 네트워크 없음, 테스트 가능).
    상승률 내림차순으로 정렬한 Candidate 리스트를 돌려준다."""
    out = []
    for t in tickers:
        try:
            symbol = t["symbol"]
            change = float(t["priceChangePercent"])
            volume = float(t["quoteVolume"])
        except (KeyError, TypeError, ValueError):
            continue
        if symbol not in perp_symbols:
            continue
        if change >= min_change and volume >= min_volume:
            out.append(Candidate(symbol, change, volume))
    out.sort(key=lambda c: c.change_pct, reverse=True)
    return out


def oi_change_24h(symbol: str) -> float | None:
    """24시간 OI 증가율(%). 이력이 없으면 None."""
    oi = collector.fetch_open_interest(symbol, "1h", 26)
    if len(oi) < 2:
        return None
    first = float(oi["open_interest_usd"].iloc[0])
    last = float(oi["open_interest_usd"].iloc[-1])
    if first <= 0:
        return None
    return (last / first - 1) * 100


def scan(min_change: float = MIN_CHANGE_PCT,
         min_volume: float = MIN_QUOTE_VOLUME,
         min_oi_change: float = MIN_OI_CHANGE_PCT) -> list:
    """전 종목 스캔. '급등 + 유동성 + OI 급증'을 모두 만족하는 후보를 돌려준다.
    OI 이력이 없는 신규 상장 코인은 판단 불가이므로 후보에 남긴다 (표시로 구분)."""
    perp_symbols = fetch_perp_symbols()
    gainers = filter_gainers(fetch_tickers(), perp_symbols, min_change, min_volume)

    candidates = []
    for c in gainers[:MAX_OI_CHECKS]:
        try:
            c.oi_change_pct = oi_change_24h(c.symbol)
        except requests.RequestException:
            c.oi_change_pct = None
        if c.oi_change_pct is None or c.oi_change_pct >= min_oi_change:
            candidates.append(c)
        time.sleep(0.2)  # 개별 조회 사이 간격 (요청 제한 보호)
    return candidates
