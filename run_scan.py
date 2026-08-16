"""전 종목 스캔 1회 실행 — 지금 시장에서 숏 셋업 후보를 찾아서 보여준다.

사용법:
    python run_scan.py                     # 기본: 24h +20%, 거래대금 3M, OI +20%
    python run_scan.py --min-change 30     # 더 강한 펌핑만
    python run_scan.py --min-volume 10     # 거래대금 10M 이상만 (단위: 백만)

찾은 후보를 자동으로 계속 감시하려면:
    python run_watch.py --scan
"""

import argparse

from bot import scanner


def main() -> None:
    parser = argparse.ArgumentParser(description="숏 셋업 후보 전 종목 스캔")
    parser.add_argument("--min-change", type=float, default=scanner.MIN_CHANGE_PCT,
                        help="24h 상승률 최소 %% (기본 20)")
    parser.add_argument("--min-volume", type=float,
                        default=scanner.MIN_QUOTE_VOLUME / 1e6,
                        help="24h 거래대금 최소 (백만 USDT, 기본 3)")
    parser.add_argument("--min-oi-change", type=float, default=scanner.MIN_OI_CHANGE_PCT,
                        help="24h OI 증가율 최소 %% (기본 20)")
    args = parser.parse_args()

    print("전 종목 스캔 중... (수십 초 걸릴 수 있음)")
    candidates = scanner.scan(args.min_change, args.min_volume * 1e6,
                              args.min_oi_change)
    if not candidates:
        print("조건을 만족하는 후보가 없습니다. 시장이 조용하거나 임계치가 높은 상태 — "
              "--min-change 를 낮춰서 다시 시도해 보세요.")
        return

    print(f"\n후보 {len(candidates)}개 (급등 + 유동성 + OI 급증):")
    for i, c in enumerate(candidates, 1):
        print(f"  {i}. {c.describe()}")

    syms = " ".join(c.symbol for c in candidates[:5])
    print(f"\n지금 바로 분석: python run_analyze.py {candidates[0].symbol}")
    print(f"자동 감시 시작: python run_watch.py {syms}")
    print(f"스캐너 연동 감시(추천): python run_watch.py --scan")


if __name__ == "__main__":
    main()
