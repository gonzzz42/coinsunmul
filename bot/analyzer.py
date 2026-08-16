"""전문가 관점 분석 엔진 — 차트를 사람 대신 읽고 결론을 낸다.

입력: 수집된 선물/현물 캔들(CVD 포함), OI, 펀딩비
출력: 지금 이 코인이 숏 셋업의 어느 단계인지 + 진입가/손절가/목표가

단계(stage):
  no_setup   해당 없음 — 펌핑 구조가 아님 (또는 데이터 부족)
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
MIN_BARS = 30  # 이보다 캔들이 적으면 분석 자체를 보류한다


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
    entry: float = 0.0        # 진입 트리거: 이 가격 하향 이탈 확정 시 진입
    stop: float = 0.0         # 손절: 최근 박스 상단 위
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


def _oi_at(oi: pd.DataFrame, when: pd.Timestamp) -> float:
    """해당 시각(또는 그 직전)의 OI 값. 없으면 첫 값."""
    ref = oi[oi["time"] <= when]
    if len(ref):
        return float(ref["open_interest_usd"].iloc[-1])
    return float(oi["open_interest_usd"].iloc[0])


def analyze(symbol: str, interval: str,
            futures: pd.DataFrame, spot: pd.DataFrame,
            oi: pd.DataFrame, funding: pd.DataFrame) -> Analysis:
    """모든 조건을 평가해서 단계와 가격 레벨을 담은 Analysis를 돌려준다.

    futures/spot은 indicators.add_cvd()를 거친 DataFrame이어야 한다.
    spot은 비어 있어도 된다 (현물 시장이 없는 선물 전용 코인).
    """
    f = futures.reset_index(drop=True)
    s = spot.reset_index(drop=True)
    day = _bars_per_day(interval)
    price = float(f["close"].iloc[-1]) if len(f) else 0.0
    a = Analysis(symbol=symbol, interval=interval, stage="no_setup", price=price)

    if len(f) < MIN_BARS:
        a.notes.append(f"선물 캔들이 {len(f)}개뿐이라 분석 보류 "
                       f"(최소 {MIN_BARS}개 필요 — 상장 직후 코인)")
        return a

    # ── 펌핑 구조 찾기 ────────────────────────────────────────────
    # 1) 최근 8일 창에서 고점과 그 이전의 바닥(펌핑 시작점)을 찾고
    # 2) "천장 구간(top zone)" = 펌핑폭의 상위 30% 가격대에 종가가 있는
    #    캔들들로 박스를 잡는다. 구간을 통째로 자르지 않고 조건을 만족하는
    #    캔들만 박스 멤버로 취급해야, 중간의 급락 딥이 박스 하단을 오염시키지 않는다.
    look = min(len(f), day * 8)
    start = len(f) - look
    win = f.iloc[start:]
    hi_pos = int(win["high"].idxmax())        # 고점 캔들 위치
    high_price = float(f["high"].iloc[hi_pos])
    base_prelim = float(f["low"].iloc[start:hi_pos + 1].min())
    top_threshold = base_prelim + (high_price - base_prelim) * 0.7

    closes = f["close"]
    member_idx = [i for i in range(start, len(f))
                  if float(closes.iloc[i]) >= top_threshold]
    box_start = member_idx[0] if member_idx else hi_pos
    pump_base = float(f["low"].iloc[start:box_start + 1].min())
    a.pump_gain_pct = _pct(high_price, pump_base)

    # 마지막 캔들은 진행 중이므로 레벨 계산에서 제외
    closed_members = [i for i in member_idx if i != len(f) - 1]
    a.box_bars = len(closed_members)
    # 레벨은 천장 구간 전체가 아니라 "최근 1일치 박스 멤버"로 잡는다 —
    # 오래 횡보할수록 전체 범위는 넓어지는데, 실제 진입/손절은
    # 이탈 직전의 좁은 박스 기준이어야 손익비가 나온다.
    recent_members = closed_members[-min(len(closed_members), day):]
    if recent_members:
        box_high = float(f["high"].iloc[recent_members].max())
        box_low = float(f["low"].iloc[recent_members].min())
    else:
        box_high, box_low = high_price, float(f["low"].iloc[-1])

    # ── 가격 레벨 ────────────────────────────────────────────────
    a.entry = box_low
    a.stop = box_high * 1.005                 # 최근 박스 상단 + 0.5% 여유
    a.target1 = high_price - (high_price - pump_base) * 0.5
    a.target2 = pump_base
    if 0 < a.rr1 < 1.5:
        a.notes.append(f"손익비 {a.rr1:.1f}로 낮음 — 진입 매력이 떨어지는 자리")

    # ── A. 컨텍스트: 이 코인을 감시할 이유가 있나? (40점) ─────────
    chg24 = _pct(price, float(f["close"].iloc[-day - 1])) if len(f) > day else 0.0
    pts = 15 if (chg24 >= 30 or a.pump_gain_pct >= 50) else (8 if a.pump_gain_pct >= 25 else 0)
    a.checks_a.append(Check("급등 감지", pts, 15,
                            f"24h {chg24:+.1f}%, 펌핑폭 +{a.pump_gain_pct:.0f}%"))

    # OI 증가는 24시간이 아니라 "펌핑이 시작된 뒤 얼마나 쌓였나"로 잰다
    pump_ref = max(start, box_start - day * 3)   # 펌핑 시작 부근
    pump_time = f["time"].iloc[pump_ref]
    oi_chg = 0.0
    if len(oi) >= 2:
        oi_chg = _pct(float(oi["open_interest_usd"].iloc[-1]), _oi_at(oi, pump_time))
    pts = 15 if oi_chg >= 50 else (8 if oi_chg >= 20 else 0)
    a.checks_a.append(Check("OI 급증", pts, 15, f"펌핑 이후 OI {oi_chg:+.1f}%"))

    # 펌핑 구간의 선물 CVD 증가량 vs 현물 CVD 증가량.
    # 현물은 선물과 캔들 개수가 다를 수 있으므로 반드시 '시간'으로 정렬한다.
    fut_d = float(f["cvd"].iloc[-1] - f["cvd"].iloc[pump_ref])
    if len(s) == 0:
        a.checks_a.append(Check("선물 주도 펌핑", 10, 10,
                                "현물 시장 없음(선물 전용 코인) — 현물 실수요가 없는 것이 확정"))
    elif s["time"].iloc[0] > pump_time:
        a.checks_a.append(Check("선물 주도 펌핑", 0, 10,
                                "현물 캔들이 펌핑 구간을 덮지 못함 — 판단 보류"))
    else:
        s_pos = min(int(s["time"].searchsorted(pump_time)), len(s) - 1)
        spot_d = float(s["cvd"].iloc[-1] - s["cvd"].iloc[s_pos])
        ratio = abs(fut_d) / max(abs(spot_d), 1e-9)
        futures_led = fut_d > 0 and (spot_d <= 0 or ratio >= 3)
        pts = 10 if (futures_led and (spot_d <= 0 or ratio >= 5)) else (5 if futures_led else 0)
        a.checks_a.append(Check("선물 주도 펌핑", pts, 10,
                                f"선물 CVD {fut_d:+,.0f} vs 현물 {spot_d:+,.0f}"
                                + (f" ({ratio:.1f}배)" if abs(spot_d) > 1 else "")))

    # ── B. 셋업: 무너질 준비가 됐나? (40점) ──────────────────────
    # 매수세 소진: 가격은 천장 박스에 머무는데 선물 CVD가 박스 시작보다
    # 늘지 않았다면, 박스를 지탱할 신규 매수가 끊긴 것. (고점 근처 재도전
    # 여부와 무관하게 박스가 유지되는 동안 안정적으로 평가된다)
    if a.box_bars >= 3:
        cvd_at_box = float(f["cvd"].iloc[box_start])
        cvd_now = float(f["cvd"].iloc[-1])
        exhausted = cvd_now <= cvd_at_box
        a.checks_b.append(Check("매수세 소진 (박스 내 CVD 정체)", 15 if exhausted else 0, 15,
                                f"박스 시작 대비 선물 CVD {cvd_now - cvd_at_box:+,.0f}"))
    else:
        a.checks_b.append(Check("매수세 소진 (박스 내 CVD 정체)", 0, 15, "박스 미형성"))

    fr_last = float(funding["funding_rate"].iloc[-1]) if len(funding) else 0.0
    fr_prev = float(funding["funding_rate"].iloc[-4:-1].mean()) if len(funding) >= 4 else fr_last
    overheated = fr_last >= 0.0005              # 8시간당 +0.05% 이상 = 롱 과열
    jumped = (fr_last - fr_prev) >= 0.0005      # 롱 과열 '방향'의 급변만 가점
    a.checks_b.append(Check("펀딩비 롱 과열", 10 if (overheated or jumped) else 0, 10,
                            f"최근 {fr_last * 100:+.4f}% (직전 평균 {fr_prev * 100:+.4f}%)"))
    if fr_last <= -0.0005:
        a.notes.append("펀딩 음수 — 숏이 이미 과밀. 숏 스퀴즈(급반등) 위험이 큰 자리")

    # OI 유지: 붕괴 캔들에서 청산으로 OI가 급감하면 '유지' 체크가 스스로
    # 무너지므로, 마지막 '박스 멤버 캔들'까지의 OI로 평가한다 (이탈 이후 제외).
    oi_hold = False
    if len(oi) >= 2 and a.box_bars >= max(4, day // 6) and closed_members:
        oi_at_box = _oi_at(oi, f["time"].iloc[box_start])
        oi_pre_break = _oi_at(oi, f["time"].iloc[closed_members[-1]])
        oi_hold = oi_pre_break >= oi_at_box * 0.95
    a.checks_b.append(Check("고점 횡보 중 OI 유지", 15 if oi_hold else 0, 15,
                            f"박스 {a.box_bars}캔들 동안 OI "
                            + ("유지·증가 (롱이 탈출 못 함)" if oi_hold else "감소 또는 박스 미형성")))

    # ── C. 트리거: 지금인가? (20점) ──────────────────────────────
    # 이탈은 '확정봉'(마감된 직전 캔들) 기준 — 진행 중 캔들이 박스 하단을
    # 넘나들 때마다 알림이 반복되는 것을 막는다.
    confirm_close = float(f["close"].iloc[-2]) if len(f) >= 2 else price
    broke = confirm_close < box_low and a.box_bars >= 3
    a.checks_c.append(Check("지지(박스 하단) 이탈 확정", 10 if broke else 0, 10,
                            f"박스 하단 {box_low:,.6g} vs 확정봉 종가 {confirm_close:,.6g}"))

    delta_win = f["delta"].abs().iloc[-min(day, len(f)):]
    avg_abs_delta = float(delta_win.mean())
    last_delta = float(f["delta"].iloc[-1])
    heavy_sell = (last_delta < 0 and avg_abs_delta > 0
                  and abs(last_delta) >= 2 * avg_abs_delta)
    a.checks_c.append(Check("대량 매도 압력", 10 if heavy_sell else 0, 10,
                            f"현재 캔들 델타 {last_delta:,.0f} "
                            f"(평균의 {abs(last_delta) / max(avg_abs_delta, 1e-9):.1f}배)"))

    # ── 단계 판정 ────────────────────────────────────────────────
    # 순서에 주의: 이탈 확정(broke)은 B보다 우선한다. 붕괴 캔들에서는
    # 청산 때문에 B 점수(OI 유지 등)가 무너지는 게 정상이라, B를 그 시점에
    # 다시 요구하면 정작 진입 시그널이 누락된다.
    a_ok = a.score_a >= A_PASS
    if price < a.target1 and a.pump_gain_pct >= 25:
        a.stage = "collapsed"
        a.notes.append("계획된 진입 자리(박스 이탈 직후)를 지나 이미 급락함. "
                       "추격 숏은 반등(숏 스퀴즈)에 청산되기 쉬운 자리 — 다음 기회를 기다릴 것")
    elif not a_ok:
        a.stage = "no_setup"
    elif broke:
        a.stage = "triggered"
        gap = _pct(price, a.entry)
        if gap < -2:
            a.notes.append(f"이탈 후 이미 {gap:+.1f}% 진행 — 시장가 추격 대신 "
                           f"박스 하단({a.entry:,.6g}) 리테스트를 기다릴 것")
    elif a.score_b < B_PASS or a.box_bars < 3:
        a.stage = "watching"
        if a.box_bars < 3:
            a.notes.append("펌핑 직후라 천장 박스가 아직 안 만들어짐 — 박스 형성 대기")
    else:
        a.stage = "armed"
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
        if not checks:
            continue
        lines.append(title)
        for c in checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"  {mark} {c.name} [{c.points}/{c.max_points}] — {c.detail}")
    if a.checks_a:
        lines.append("")

    if a.stage in ("armed", "triggered"):
        lines.append("트레이드 플랜 (숏)")
        lines.append(f"  진입: {a.entry:,.6g} 하향 이탈 확정 후 "
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
