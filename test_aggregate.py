"""다중 거래소 OI 집계(bot/aggregate.py) 오프라인 테스트.

사용법: python test_aggregate.py
"""

import pandas as pd
import requests

from bot import aggregate

T0 = pd.Timestamp("2026-08-18 00:00:00+00:00")
H = pd.Timedelta(hours=1)


def main() -> None:
    # 심볼 -> 기초 코인 변환
    assert aggregate.base_ccy("BTCUSDT") == "BTC"
    assert aggregate.base_ccy("1000PEPEUSDT") == "PEPE"
    assert aggregate.base_ccy("1MBABYDOGEUSDT") == "BABYDOGE"
    assert aggregate.base_ccy("1000000MOGUSDT") == "MOG"
    assert aggregate.base_ccy("1000USDT") == "1000"  # 접두사만 있으면 그대로
    print("[OK] 심볼 -> 기초 코인 변환 (1000/1M 접두사 처리)")

    binance_oi = pd.DataFrame({
        "time": [T0, T0 + H, T0 + 2 * H],
        "open_interest": [50.0, 50.0, 50.0],
        "open_interest_usd": [100.0, 100.0, 100.0],
    })
    binance_close = pd.DataFrame({"time": [T0 - H, T0, T0 + H, T0 + 2 * H],
                                  "close": [2.0, 2.0, 2.0, 2.0]})

    # 1) 3개 거래소 정상: Bybit(코인 10개 x 가격 2 = 20) + OKX(50) 합산
    aggregate.fetch_bybit_oi = lambda s, i, limit=200: pd.DataFrame(
        {"time": [T0 - pd.Timedelta(minutes=30), T0 + H],
         "oi_coins": [10.0, 10.0]})
    aggregate.fetch_okx_oi_usd = lambda s, i: pd.DataFrame(
        {"time": [T0, T0 + H, T0 + 2 * H], "oi_usd": [50.0, 50.0, 50.0]})
    out, sources = aggregate.aggregate_oi("PEPEUSDT", "1h", binance_oi, binance_close)
    assert sources == ["binance", "bybit", "okx"], sources
    assert list(out["open_interest_usd"]) == [170.0, 170.0, 170.0], \
        list(out["open_interest_usd"])
    print("[OK] 3개 거래소 합산: 100(Binance) + 20(Bybit) + 50(OKX) = 170")

    # 1b) 창 불일치: Bybit 히스토리가 늦게 시작해도(평평한 OI) 계단이 생기면 안 됨
    #     — 계단이 생기면 실제 변화 0%가 'OI 급증'으로 오판된다
    aggregate.fetch_bybit_oi = lambda s, i, limit=200: pd.DataFrame(
        {"time": [T0 + H, T0 + 2 * H], "oi_coins": [10.0, 10.0]})  # T0 이전 없음
    out, sources = aggregate.aggregate_oi("PEPEUSDT", "1h", binance_oi, binance_close)
    assert list(out["open_interest_usd"]) == [170.0, 170.0, 170.0], \
        f"창 시작 전 구간이 0으로 채워져 계단 발생: {list(out['open_interest_usd'])}"
    print("[OK] 거래소별 히스토리 창 불일치: 첫 관측값으로 채워 계단 없음")

    # 2) Bybit 실패: 조용히 빼고 나머지로 합산
    def boom(*a, **kw):
        raise requests.ConnectionError("bybit down")
    aggregate.fetch_bybit_oi = boom
    out, sources = aggregate.aggregate_oi("PEPEUSDT", "1h", binance_oi, binance_close)
    assert sources == ["binance", "okx"], sources
    assert list(out["open_interest_usd"]) == [150.0, 150.0, 150.0]
    print("[OK] 거래소 1곳 장애: 자동 제외하고 계속 (100 + 50 = 150)")

    # 3) 전부 실패: Binance 단독 원본 그대로
    aggregate.fetch_okx_oi_usd = boom
    out, sources = aggregate.aggregate_oi("PEPEUSDT", "1h", binance_oi, binance_close)
    assert sources == ["binance"]
    assert list(out["open_interest_usd"]) == [100.0, 100.0, 100.0]
    print("[OK] 전부 장애: Binance 단독으로 계속")

    # 4) Binance OI 자체가 비어 있으면 그대로 반환 (신규 코인)
    empty = pd.DataFrame(columns=["time", "open_interest", "open_interest_usd"])
    out, sources = aggregate.aggregate_oi("NEWUSDT", "1h", empty, binance_close)
    assert not len(out) and sources == ["binance"]
    print("[OK] 신규 코인(OI 없음): 빈 DataFrame 그대로 통과")

    print("\naggregate 테스트 전체 통과")


if __name__ == "__main__":
    main()
