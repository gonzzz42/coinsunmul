"""시그널 기록·승률 추적 — "4할 타자 만들기"의 핵심 모듈.

armed/triggered 알림이 나갈 때마다 SQLite에 기록하고, 시간이 지나면
결과를 자동으로 채운다:
  - ret_1h/4h/24h: 시그널 시점 대비 수익률(%). 숏 기준이므로
    가격이 '내려가면' 양수 = 시그널이 맞았다는 뜻.
  - first_touch: 기록된 트레이드 플랜대로 진입했다면 손절과 1차 목표 중
    어느 쪽을 먼저 쳤는지 (target1 = 승 / stop = 패 / no_fill = 미진입 /
    none = 24시간 내 둘 다 안 닿음 / ambiguous = 같은 캔들에서 둘 다 → 보수적으로 패 취급)

이 통계가 쌓여야 점수·임계치를 감이 아니라 데이터로 조정할 수 있다.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from bot.analyzer import INTERVAL_MINUTES, Analysis

# 실행 위치(cwd)와 무관하게 항상 프로젝트 폴더의 같은 DB를 쓴다 —
# 상대 경로면 다른 폴더에서 실행할 때마다 빈 DB가 새로 생겨 기록이 갈라진다.
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "signals.sqlite3")
HORIZONS = [("ret_1h", 1), ("ret_4h", 4), ("ret_24h", 24)]
# 같은 심볼·같은 단계의 시그널이 이 시간 안에 비슷한 레벨로 또 오면 중복으로 본다
DEDUP_HOURS = 12
DEDUP_TOL = 0.005  # entry/stop 0.5% 이내면 같은 셋업

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,               -- 시그널 시각 (UTC ISO)
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    stage TEXT NOT NULL,            -- armed / triggered
    total INTEGER, score_a INTEGER, score_b INTEGER, score_c INTEGER,
    price REAL, entry REAL, stop REAL, target1 REAL, target2 REAL,
    pump_gain_pct REAL,
    ret_1h REAL, ret_4h REAL, ret_24h REAL,
    first_touch TEXT,               -- NULL = 아직 미정
    done INTEGER DEFAULT 0          -- 1 = 더 채울 것 없음
)
"""


def _conn(path: str = DB_PATH) -> sqlite3.Connection:
    # timeout: run_watch와 run_stats가 동시에 써도 락 대기로 버틴다
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 읽기·쓰기 동시성 개선
    conn.execute(SCHEMA)
    return conn


def _close_to(a: float, b: float, tol: float = DEDUP_TOL) -> bool:
    return b != 0 and abs(a / b - 1) <= tol


def record_signal(a: Analysis, path: str = DB_PATH, now: datetime | None = None) -> int:
    """armed/triggered 시그널 1건을 기록하고 id를 돌려준다.

    같은 심볼·같은 단계가 12시간 안에 비슷한 레벨(0.5% 이내)로 이미 기록돼
    있으면 중복으로 보고 그 id를 돌려준다 — 경계에서 armed<->watching으로
    진동하거나 봇을 재시작할 때 같은 셋업이 여러 행으로 쌓여 승률 통계를
    오염시키는 것을 막는다. (armed 후 triggered는 단계가 달라 둘 다 기록됨)
    """
    now = now or datetime.now(timezone.utc)
    with _conn(path) as conn:
        since = (now - timedelta(hours=DEDUP_HOURS)).isoformat()
        for row in conn.execute(
                "SELECT id, entry, stop FROM signals "
                "WHERE symbol = ? AND stage = ? AND ts >= ?",
                (a.symbol, a.stage, since)):
            if _close_to(row["entry"], a.entry) and _close_to(row["stop"], a.stop):
                return int(row["id"])  # 같은 셋업 — 새로 기록하지 않음
        cur = conn.execute(
            "INSERT INTO signals (ts, symbol, interval, stage, total, score_a, "
            "score_b, score_c, price, entry, stop, target1, target2, pump_gain_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now.isoformat(), a.symbol, a.interval, a.stage, a.total, a.score_a,
             a.score_b, a.score_c, a.price, a.entry, a.stop, a.target1, a.target2,
             a.pump_gain_pct))
        return int(cur.lastrowid)


def _closed_close_at(df: pd.DataFrame, when: pd.Timestamp,
                     interval_min: int, now: pd.Timestamp) -> float | None:
    """when 시각 기준의 가격 = when 이전에 '마감된' 마지막 캔들의 종가.
    아직 그 시각이 안 됐거나 범위 밖이면 None."""
    if when > now:
        return None  # 호라이즌이 아직 안 됨 — 미리 채우면 안 된다
    ends = df["time"] + pd.Timedelta(minutes=interval_min)
    rows = df[ends <= when]
    if not len(rows):
        return None
    return float(rows.iloc[-1]["close"])


def _first_touch(df: pd.DataFrame, sig: sqlite3.Row, ts: pd.Timestamp,
                 deadline: pd.Timestamp) -> str | None:
    """시그널 이후 24시간 동안 트레이드 플랜의 결과를 캔들 경로로 판정한다.
    None = 아직 결정 안 됨 (기한 내 데이터가 더 필요함)."""
    path = df[(df["time"] > ts) & (df["time"] <= deadline)]
    entered = sig["stage"] == "triggered"  # triggered는 시그널 즉시 진입으로 본다
    for _, candle in path.iterrows():
        if not entered:
            if float(candle["low"]) <= sig["entry"]:
                entered = True  # armed: 박스 하단에 걸어둔 진입이 체결됨
            else:
                continue
        hit_stop = float(candle["high"]) >= sig["stop"]
        hit_target = float(candle["low"]) <= sig["target1"]
        if hit_stop and hit_target:
            return "ambiguous"   # 같은 캔들에서 둘 다 — 보수적으로 패 취급
        if hit_stop:
            return "stop"
        if hit_target:
            return "target1"
    # 기한까지의 캔들을 전부 확인했는가?
    if len(df) and df["time"].iloc[-1] >= deadline:
        return "none" if entered else "no_fill"
    return None


def update_outcomes(fetch_klines, path: str = DB_PATH,
                    now: datetime | None = None) -> int:
    """미완 시그널의 수익률·first_touch를 채운다. 채운 항목 수를 돌려준다.

    fetch_klines(symbol, interval, limit) -> time/high/low/close 컬럼의 DataFrame
    (collector.fetch_futures_klines를 그대로 넘기면 된다)
    """
    now = now or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now)

    # 1) 미완 시그널 목록만 읽고 즉시 DB에서 손을 뗀다
    with _conn(path) as conn:
        rows = conn.execute("SELECT * FROM signals WHERE done = 0").fetchall()
    if not rows:
        return 0

    # 2) 네트워크 조회는 전부 DB 락 밖에서 — 쓰기 트랜잭션을 잡은 채
    #    네트워크를 기다리면 그 사이 record_signal이 'database is locked'로
    #    실패해 시그널이 유실된다.
    cache: dict = {}
    for sig in rows:
        key = (sig["symbol"], sig["interval"])
        if key in cache:
            continue
        interval_min = INTERVAL_MINUTES.get(sig["interval"], 60)
        # 24시간 추적 + 여유가 창에 들어오도록 캔들 수를 계산한다
        # (1m은 1000개=16.7시간뿐이라 24h 결과가 영원히 안 채워졌었다)
        need = min(1500, max(1000, 26 * 60 // interval_min + 2))
        try:
            cache[key] = fetch_klines(sig["symbol"], sig["interval"], need)
        except Exception:
            cache[key] = None  # 이번 라운드는 건너뛰고 다음에 재시도

    # 3) 계산 후 짧은 쓰기 트랜잭션으로 한 번에 반영
    pending_updates: list = []
    for sig in rows:
        ts = pd.Timestamp(sig["ts"])
        interval_min = INTERVAL_MINUTES.get(sig["interval"], 60)
        df = cache[(sig["symbol"], sig["interval"])]
        if df is None or not len(df):
            continue

        updates: dict = {}
        if df["time"].iloc[0] > ts:
            # 시그널이 조회 가능한 캔들 범위보다 오래됨 — 더는 채울 수 없다
            updates["done"] = 1
            if sig["first_touch"] is None:
                updates["first_touch"] = "data_gap"
        else:
            for col, hours in HORIZONS:
                if sig[col] is not None:
                    continue
                when = ts + timedelta(hours=hours)
                close = _closed_close_at(df, pd.Timestamp(when), interval_min, now_ts)
                if close is not None and sig["price"]:
                    # 숏 기준: 가격이 내려가면 양수
                    updates[col] = (sig["price"] - close) / sig["price"] * 100
            if sig["first_touch"] is None:
                touch = _first_touch(df, sig, ts, ts + timedelta(hours=24))
                if touch is not None:
                    updates["first_touch"] = touch
            ret24_done = sig["ret_24h"] is not None or "ret_24h" in updates
            touch_done = sig["first_touch"] is not None or "first_touch" in updates
            if ret24_done and touch_done:
                updates["done"] = 1

        if updates:
            pending_updates.append((sig["id"], updates))

    filled = 0
    if pending_updates:
        with _conn(path) as conn:
            for sig_id, updates in pending_updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE signals SET {sets} WHERE id = ?",
                             (*updates.values(), sig_id))
                filled += len(updates)
    return filled


def stats(path: str = DB_PATH) -> dict:
    """쌓인 기록으로 승률·수익률 통계를 계산한다."""
    with _conn(path) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM signals").fetchall()]
    out = {"count": len(rows), "by_stage": {}, "horizons": {}, "trades": {},
           "by_score": {}}
    for r in rows:
        out["by_stage"][r["stage"]] = out["by_stage"].get(r["stage"], 0) + 1

    trig = [r for r in rows if r["stage"] == "triggered"]
    for col, hours in HORIZONS:
        vals = [r[col] for r in trig if r[col] is not None]
        if vals:
            wins = sum(1 for v in vals if v > 0)
            out["horizons"][col] = {"n": len(vals), "win_rate": wins / len(vals) * 100,
                                    "avg_ret": sum(vals) / len(vals)}

    touches = [r["first_touch"] for r in rows if r["first_touch"]]
    for t in touches:
        out["trades"][t] = out["trades"].get(t, 0) + 1
    wins = out["trades"].get("target1", 0)
    losses = out["trades"].get("stop", 0) + out["trades"].get("ambiguous", 0)
    if wins + losses:
        out["trades"]["win_rate"] = wins / (wins + losses) * 100

    for lo, hi, label in ((70, 200, "70점 이상"), (0, 70, "70점 미만")):
        bucket = [r["ret_24h"] for r in trig
                  if r["ret_24h"] is not None and lo <= (r["total"] or 0) < hi]
        if bucket:
            wins = sum(1 for v in bucket if v > 0)
            out["by_score"][label] = {"n": len(bucket),
                                      "win_rate": wins / len(bucket) * 100}
    return out


def format_stats(s: dict) -> str:
    lines = [f"기록된 시그널: {s['count']}건"]
    if s["by_stage"]:
        lines.append("  " + " · ".join(f"{k} {v}건" for k, v in s["by_stage"].items()))
    if s["horizons"]:
        lines.append("")
        lines.append("triggered 시그널 이후 수익률 (숏 기준, 하락 = 양수):")
        for col, h in HORIZONS:
            if col in s["horizons"]:
                v = s["horizons"][col]
                lines.append(f"  {h:>2}시간 뒤: 승률 {v['win_rate']:.0f}% · "
                             f"평균 {v['avg_ret']:+.2f}% (표본 {v['n']})")
    if s["trades"]:
        lines.append("")
        lines.append("트레이드 플랜 시뮬레이션 (진입-손절-1차목표 기준):")
        name = {"target1": "1차 목표 도달(승)", "stop": "손절(패)",
                "ambiguous": "동시 터치(패 취급)", "none": "24h 내 미결",
                "no_fill": "미진입", "data_gap": "데이터 공백"}
        for k, label in name.items():
            if k in s["trades"]:
                lines.append(f"  {label}: {s['trades'][k]}건")
        if "win_rate" in s["trades"]:
            lines.append(f"  → 체결 기준 승률: {s['trades']['win_rate']:.0f}%")
    if s["by_score"]:
        lines.append("")
        lines.append("점수 구간별 24h 승률 (임계치 조정 근거):")
        for label, v in s["by_score"].items():
            lines.append(f"  {label}: {v['win_rate']:.0f}% (표본 {v['n']})")
    if s["count"] < 50:
        lines.append("")
        lines.append(f"⚠ 표본 {s['count']}건 — 통계로 판단하려면 최소 50건이 필요하다. "
                     f"그전까지 실거래 금지.")
    return "\n".join(lines)
