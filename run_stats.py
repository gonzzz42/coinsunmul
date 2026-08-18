"""승률 통계 — 기록된 시그널의 결과를 채우고 통계를 보여준다.

사용법:
    python run_stats.py              # 결과 업데이트 + 통계 출력
    python run_stats.py --no-update  # 네트워크 없이 저장된 통계만 출력

run_watch.py 가 돌면서 armed/🚨 시그널을 자동 기록하고 결과도 주기적으로
채우므로, 이 명령은 언제든 현재까지의 성적표를 확인하는 용도다.
"""

import argparse

from bot import collector, store


def main() -> None:
    parser = argparse.ArgumentParser(description="시그널 승률 통계")
    parser.add_argument("--no-update", action="store_true",
                        help="결과 업데이트 없이 저장된 통계만 출력")
    args = parser.parse_args()

    if not args.no_update:
        try:
            filled = store.update_outcomes(collector.fetch_futures_klines)
            if filled:
                print(f"(결과 {filled}개 항목 업데이트)\n")
        except Exception as e:
            print(f"(결과 업데이트 실패: {e} — 저장된 통계만 출력)\n")

    print(store.format_stats(store.stats()))


if __name__ == "__main__":
    main()
