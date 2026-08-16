"""전문가 관점 분석 엔진 — 차트를 사람 대신 읽고 결론을 낸다.

입력: 수집된 선물/현물 캔들(CVD 포함), OI, 펀딩비
출력: 지금 이 코인이 숏 셋업의 어느 단계인지 + 진입가/손절가/목표가

단계(stage):
  no_setup   해당 없음 — 펌핑 구조가 아님
  watching   감시 중 — 펌핑은 확인, 셋업(무너질 준비)은 아직
  armed      준비 완료 — 구조 완성, 지지 이탈(트리거)만 대기
  triggered  진입 시그널 — 트리거 발동, 지금이 계획된 진입 자리
  collapsed  이미 붕괴 — 놓친 자리, 추격 숏 금지
"""

from dataclasses import dataclass, field

import pandas as pd

INTERVAL_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440,
}

# 점수 임계치 (Phase 5에서 실제 승률 통계로 조정할 가설값)
A_PASS = 25   # 컨텍스트 통과
B_PASS = 25   # 셋업 통과
C_PASS = 10   # 트리거 발동


@dataclass
class Check:
    name: str
    points: int
    max_points: int
    detail: str

    @property
    def passed(self) -> bool:
        return self.points > 0


@dataclass
class Analysis:
    symbol: str
    interval: str
    stage: str
    price: float
    checks_a: list = field(default_factory=list)
    checks_b: list = field(default_factory=list)
    checks_c: list = field(default_factory=list)
    # 가격 레벨 (펌핑 구조가 있을 때만 의미가 있다)
    entry: float = 0.0        # 진입 트리거: 이 가격 하향 이탈 시 진입
    stop: float = 0.0         # 손절: 박스 상단 위
    target1: float = 0.0      # 1차 목표: 펌핑 구간 50% 되돌림
    target2: float = 0.0      # 2차 목표: 펌핑 시작점 부근
    box_bars: int = 0
    pump_gain_pct: float = 0.0
    notes: list = field(default_factory=list)

    @property
    def score_a(self) -> int:
        return sum(c.points for c in self.checks_a)

    @property
    def score_b(self) -> int:
        return sum(c.points for c in self.checks_b)

    @property
    def score_c(self) -> int:
        return sum(c.points for c in self.checks_c)

    @property
    def total(self) -> int:
        return self.score_a + self.score_b + self.score_c

    @property
    def risk_pct(self) -> float:
        """진입가 대비 손절까지의 거리(%)"""
        if self.entry <= 0:
            return 0.0
        return (self.stop / self.entry - 1) * 100

    @property
    def rr1(self) -> float:
        """1차 목표 기준 손익비 (reward / risk)"""
        risk = self.stop - self.entry
        reward = self.entry - self.target1
        if risk <= 0:
            return 0.0
        return reward / risk


def _bars_per_day(interval: str) -> int:
    minutes = INTERVAL_MINUTES.get(interval, 60)
    return max(1, 1440 // minutes)


def _pct(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new / old - 1) * 100


def analyze(symbol: str, interval: str,
            futures: pd.DataFrame, spot: pd.DataFrame,
            oi: pd.DataFrame, funding: pd.DataFrame) -> Analysis:
    """모든 조건을 평가해서 단계와 가격 레벨을 담은 Analysis를 돌려준다.

    futures/spot은 indicators.add_cvd()를 거친 DataFrame이어야 한다.
    """
    f = futures.reset_index(drop=True)
    s = spot.reset_index(drop=True)
    day = _bars_per_day(interval)
    price = float(f["close"].iloc[-1])
    a = Analysis(symbol=symbol, interval=interval, stage="no_setup", price=price)

    # ── 펌핑 구조 찾기 ────────────────────────────────────────────
    # 1) 최근 8일 창에서 고점과 그 이전의 바닥(펌핑 시작점)을 찾고
    # 2) "천장 구간(top zone)" = 펌핑폭의 상위 30% 가격대에 머문 캔들들로 박스를 잡는다.
    #    고점 캔들 하나가 아니라 구간으로 잡아야 박스 안 노이즈에 흔들리지 않는다.
    look = min(len(f), day * 8)
    start = len(f) - look
    win = f.iloc[start:]
    hi_pos = int(win["high"].idxmax())        # 고점 캔들 위치
    high_price = float(f["high"].iloc[hi_pos])
    base_prelim = float(f["low"].iloc[start:hi_pos + 1].min())
    top_threshold = base_prelim + (high_price - base_prelim) * 0.7

    closes = f["close"]
    in_top = closes >= top_threshold
    top_idx = [i for i in range(start, len(f)) if bool(in_top.iloc[i])]
    if top_idx:
        box_start, box_end = top_idx[0], top_idx[-1]
    else:
        box_start = box_end = hi_pos
    pump_base = float(f["low"].iloc[start:box_start + 1].min())
    a.pump_gain_pct = _pct(high_price, pump_base)

    # 박스 범위 (마지막 캔들이 박스에 포함되면 진행 중이므로 레벨 계산에서 제외)
    box_span = f.iloc[box_start:box_end + 1]
    if box_end == len(f) - 1 and len(box_span) > 1:
        box_levels = box_span.iloc[:-1]
    else:
        box_levels = box_span
    a.box_bars = len(box_levels)
    # 레벨은 천장 구간 전체가 아니라 "최근 1일의 박스"로 잡는다.
    # 천장에서 오래 횡보할수록 전체 범위는 넓어지는데, 실제 진입/손절은
    # 이탈 직전에 만들어진 좁은 박스를 기준으로 해야 손익비가 나온다.
    recent_box = box_levels.iloc[-min(len(box_levels), day):] if len(box_levels) \
        else f.iloc[-2:-1]
    box_high = float(recent_box["high"].max())
    box_low = float(recent_box["low"].min())

    # ── 가격 레벨 ────────────────────────────────────────────────
    a.entry = box_low
    a.stop = box_high * 1.005                 # 최근 박스 상단 + 0.5% 여유
    a.target1 = high_price - (high_price - pump_base) * 0.5
    a.target2 = pump_base
    if a.rr1 < 1.5 and a.entry > 0:
        a.notes.append(f"손익비 {a.rr1:.1f}로 낮음 — 진입 매력이 떨어지는 자리")

    # ── A. 컨텍스트: 이 코인을 감시할 이유가 있나? (40점) ─────────
    chg24 = _pct(price, float(f["close"].iloc[-day - 1])) if len(f) > day else 0.0
    pts = 15 if (chg24 >= 30 or a.pump_gain_pct >= 50) else (8 if a.pump_gain_pct >= 25 else 0)
    a.checks_a.append(Check("급등 감지", pts, 15,
                            f"24h {chg24:+.1f}%, 펌핑폭 +{a.pump_gain_pct:.0f}%"))

    # OI 증가는 24시간이 아니라 "펌핑이 시작된 뒤 얼마나 쌓였나"로 잰다
    oi_chg = 0.0
    if len(oi) >= 2:
        pump_start_time = f["time"].iloc[max(start, box_start - day * 3)]
        ref = oi[oi["time"] <= pump_start_time]
        ref_val = float(ref["open_interest_usd"].iloc[-1]) if len(ref) \
            else float(oi["open_interest_usd"].iloc[0])
        oi_chg = _pct(float(oi["open_interest_usd"].iloc[-1]), ref_val)
    pts = 15 if oi_chg >= 50 else (8 if oi_chg >= 20 else 0)
    a.checks_a.append(Check("OI 급증", pts, 15, f"펌핑 이후 OI {oi_chg:+.1f}%"))

    # 펌핑 구간의 선물 CVD 증가량 vs 현물 CVD 증가량
    pump_ref = max(start, box_start - day * 3)   # 펌핑 시작 부근
    s_base = min(pump_ref, len(s) - 1)
    fut_d = float(f["cvd"].iloc[-1] - f["cvd"].iloc[pump_ref])
    spot_d = float(s["cvd"].iloc[-1] - s["cvd"].iloc[s_base])
    ratio = abs(fut_d) / max(abs(spot_d), 1e-9)
    futures_led = fut_d > 0 and (spot_d <= 0 or ratio >= 3)
    pts = 10 if (futures_led and ratio >= 5) else (5 if futures_led else 0)
    a.checks_a.append(Check("선물 주도 펌핑", pts, 10,
                            f"선물 CVD +{fut_d:,.0f} vs 현물 {spot_d:+,.0f} ({ratio:.1f}배)"))

    # ── B. 셋업: 무너질 준비가 됐나? (40점) ──────────────────────
    recent = max(6, day // 4)
    price_recent_hi = float(f["high"].iloc[-recent:].max())
    price_prior_hi = float(f["high"].iloc[-look:-recent].max()) if look > recent else price_recent_hi
    cvd_recent_hi = float(f["cvd"].iloc[-recent:].max())
    cvd_prior_hi = float(f["cvd"].iloc[-look:-recent].max()) if look > recent else cvd_recent_hi
    diverging = price_recent_hi >= price_prior_hi * 0.999 and cvd_recent_hi < cvd_prior_hi
    a.checks_b.append(Check("약세 다이버전스", 15 if diverging else 0, 15,
                            "가격은 고점 근처인데 선물 CVD 고점 미갱신" if diverging
                            else "다이버전스 없음"))

    fr_last = float(funding["funding_rate"].iloc[-1]) if len(funding) else 0.0
    fr_prev = float(funding["funding_rate"].iloc[-4:-1].mean()) if len(funding) >= 4 else fr_last
    overheated = fr_last >= 0.0005              # 8시간당 +0.05% 이상 = 롱 과열
    jumped = abs(fr_last - fr_prev) >= 0.0005   # 급변 자체도 신호
    a.checks_b.append(Check("펀딩비 과열/급변", 10 if (overheated or jumped) else 0, 10,
                            f"최근 {fr_last * 100:+.4f}% (직전 평균 {fr_prev * 100:+.4f}%)"))

    oi_hold = False
    if len(oi) >= 2 and a.box_bars >= max(4, day // 6):
        ref = oi[oi["time"] <= f["time"].iloc[box_start]]
        oi_at_box = float(ref["open_interest_usd"].iloc[-1]) if len(ref) \
            else float(oi["open_interest_usd"].iloc[0])
        oi_now = float(oi["open_interest_usd"].iloc[-1])
        oi_hold = oi_now >= oi_at_box * 0.95
    a.checks_b.append(Check("고점 횡보 중 OI 유지", 15 if oi_hold else 0, 15,
                            f"박스 {a.box_bars}캔들 동안 OI "
                            + ("유지·증가 (롱이 탈출 못 함)" if oi_hold else "감소 또는 박스 미형성")))

    # ── C. 트리거: 지금인가? (20점) ──────────────────────────────
    broke = price < box_low and a.box_bars >= 2
    a.checks_c.append(Check("지지(박스 하단) 이탈", 10 if broke else 0, 10,
                            f"박스 하단 {box_low:,.6g} vs 현재가 {price:,.6g}"))

    avg_abs_delta = float(f["delta"].abs().iloc[-day:].mean()) if len(f) >= day else 0.0
    last_delta = float(f["delta"].iloc[-1])
    heavy_sell = last_delta < 0 and abs(last_delta) >= 2 * max(avg_abs_delta, 1e-9)
    a.checks_c.append(Check("대량 매도 압력", 10 if heavy_sell else 0, 10,
                            f"현재 캔들 델타 {last_delta:,.0f} (하루 평균의 "
                            f"{abs(last_delta) / max(avg_abs_delta, 1e-9):.1f}배)"))

    # ── 단계 판정 ────────────────────────────────────────────────
    if price < a.target1 and a.pump_gain_pct >= 25:
        # 이미 펌핑폭의 절반 이상 무너짐 — 계획했던 자리는 지나갔다
        a.stage = "collapsed"
        a.notes.append("계획된 진입 자리(박스 이탈 직후)를 지나 이미 급락함. "
                       "추격 숏은 반등(숏 스퀴즈)에 청산되기 쉬운 자리 — 다음 기회를 기다릴 것")
    elif a.score_a < A_PASS:
        a.stage = "no_setup"
    elif a.score_b < B_PASS or a.box_bars < 3:
        a.stage = "watching"
        if a.box_bars < 3:
            a.notes.append("펌핑 직후라 천장 박스가 아직 안 만들어짐 — 박스 형성 대기")
    elif a.score_c < C_PASS:
        a.stage = "armed"
    else:
        a.stage = "triggered"
    return a


STAGE_LABEL = {
    "no_setup": "해당 없음 — 숏 셋업 구조가 아님",
    "watching": "감시 중 — 펌핑 확인, 셋업 완성 대기",
    "armed": "준비 완료 — 지지 이탈(트리거)만 대기",
    "triggered": "진입 시그널 — 계획된 진입 자리 도달",
    "collapsed": "이미 붕괴 — 추격 금지, 다음 기회 대기",
}


def format_report(a: Analysis) -> str:
    """분석 결과를 사람이 읽는 보고서로 만든다 (텔레그램/콘솔 공용)."""
    lines = []
    lines.append(f"■ {a.symbol} ({a.interval}) — 현재가 {a.price:,.6g}")
    lines.append(f"판정: [{a.stage.upper()}] {STAGE_LABEL[a.stage]}")
    lines.append(f"점수: {a.total}/100 (컨텍스트 {a.score_a}/40 · "
                 f"셋업 {a.score_b}/40 · 트리거 {a.score_c}/20)")
    lines.append("")

    for title, checks in (("A. 컨텍스트", a.checks_a),
                          ("B. 셋업", a.checks_b),
                          ("C. 트리거", a.checks_c)):
        lines.append(title)
        for c in checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"  {mark} {c.name} [{c.points}/{c.max_points}] — {c.detail}")
    lines.append("")

    if a.stage in ("armed", "triggered"):
        lines.append("트레이드 플랜 (숏)")
        lines.append(f"  진입: {a.entry:,.6g} 하향 이탈 확인 후 "
                     f"(현재가 대비 {_pct(a.entry, a.price):+.1f}%)")
        lines.append(f"  손절: {a.stop:,.6g} (진입가 대비 +{a.risk_pct:.1f}%) — 박스 상단 위")
        lines.append(f"  1차 목표: {a.target1:,.6g} (펌핑 50% 되돌림) · 손익비 {a.rr1:.1f}")
        lines.append(f"  2차 목표: {a.target2:,.6g} (펌핑 시작점)")
        lines.append(f"  무효화: 신고가({a.stop:,.6g} 위) 갱신 시 셋업 폐기")
    elif a.stage == "watching":
        lines.append(f"참고 레벨: 천장 박스 하단 {a.entry:,.6g} / 상단 {a.stop:,.6g} "
                     f"(박스 {a.box_bars}캔들, 펌핑폭 +{a.pump_gain_pct:.0f}%)")

    for note in a.notes:
        lines.append(f"⚠ {note}")
    return "\n".join(lines)
