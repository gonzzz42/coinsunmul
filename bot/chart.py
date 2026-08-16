"""검증용 차트 — 고수 화면과 같은 순서로 패널을 쌓아서 PNG로 저장한다.

Phase 1의 목적은 "내가 계산한 지표가 CoinGlass와 같은 모양인가"를
눈으로 확인하는 것이므로, 패널 순서를 CoinGlass 화면과 맞춘다:
가격 -> 선물 CVD -> 현물 CVD -> 펀딩비 -> 미결제약정(OI)
"""

import matplotlib
matplotlib.use("Agg")  # 서버/터미널 환경에서도 파일 저장이 되도록

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager

INK = "#1f2733"       # 가격
BLUE = "#3564c4"      # 선물 CVD
TEAL = "#1f8a70"      # 현물 CVD, 펀딩 양수
RED = "#d24a57"       # 펀딩 음수
PURPLE = "#7a4fc9"    # OI
GRID = "#d7dce3"

_KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic",
                 "Noto Sans CJK KR", "Noto Sans KR"]


def _pick_korean_font() -> bool:
    """설치된 한글 폰트를 찾아서 적용. 없으면 False (영문 라벨 사용)."""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in _KOREAN_FONTS:
        if name in installed:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return True
    return False


def save_verification_chart(symbol: str, interval: str,
                            futures: pd.DataFrame, spot: pd.DataFrame,
                            oi: pd.DataFrame, funding: pd.DataFrame,
                            path: str) -> None:
    korean = _pick_korean_font()
    labels = {
        "price": "가격 (선물 종가)" if korean else "Price (futures close)",
        "fcvd": "선물 CVD (USDT)" if korean else "Futures CVD (USDT)",
        "scvd": "현물 CVD (USDT)" if korean else "Spot CVD (USDT)",
        "funding": "펀딩비" if korean else "Funding rate",
        "oi": "미결제약정 OI (USDT)" if korean else "Open interest (USDT)",
        "title": f"{symbol} {interval} — CoinGlass와 모양 비교용",
    }
    if not korean:
        labels["title"] = f"{symbol} {interval} — compare shape with CoinGlass"

    fig, axes = plt.subplots(5, 1, figsize=(11, 13), sharex=True,
                             gridspec_kw={"hspace": 0.35})
    fig.suptitle(labels["title"], fontsize=13, fontweight="bold", y=0.995)

    panels = [
        (axes[0], futures["time"], futures["close"], INK, labels["price"]),
        (axes[1], futures["time"], futures["cvd"], BLUE, labels["fcvd"]),
        (axes[2], spot["time"], spot["cvd"], TEAL, labels["scvd"]),
    ]
    for ax, x, y, color, title in panels:
        ax.plot(x, y, color=color, linewidth=1.6)
        # 마지막 값을 점 + 숫자로 강조 (CoinGlass 우측 가격표시와 같은 역할)
        ax.plot(x.iloc[-1], y.iloc[-1], "o", color=color, markersize=5)
        ax.annotate(f"{y.iloc[-1]:,.4g}", (x.iloc[-1], y.iloc[-1]),
                    textcoords="offset points", xytext=(6, 0),
                    fontsize=9, color=color)
        _style(ax, title)

    ax = axes[3]
    colors = [TEAL if v >= 0 else RED for v in funding["funding_rate"]]
    ax.bar(funding["time"], funding["funding_rate"] * 100,
           width=0.28, color=colors)
    ax.axhline(0, color=GRID, linewidth=1)
    _style(ax, labels["funding"] + " (%)")

    ax = axes[4]
    ax.plot(oi["time"], oi["open_interest_usd"], color=PURPLE, linewidth=1.6)
    ax.plot(oi["time"].iloc[-1], oi["open_interest_usd"].iloc[-1], "o",
            color=PURPLE, markersize=5)
    _style(ax, labels["oi"])

    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _style(ax, title: str) -> None:
    ax.set_title(title, fontsize=10.5, loc="left", fontweight="bold")
    ax.grid(color=GRID, linewidth=0.6, alpha=0.6)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(labelsize=8.5)
