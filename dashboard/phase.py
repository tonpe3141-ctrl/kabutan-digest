"""局面と対処: 急落・伸びすぎ・崩れの後に、過去の同じ形で何が起きたか（36年の日経平均）と、次の営業日の備え（DESIGN.md 28章）。

依頼（2026-10-17）: 下げ続きの後の木曜の急落 → 金曜の小さな反発 → 金曜の米国安で、月曜の急落に対処できなかった。暴落の翌営業日の
対処、上昇局面の利確の逃し、需給の流れを、考え方と手法として出してほしい。

方針（CLAUDE.md の約束どおり）:
  - **予測ではなく、同じ形の過去の分布**。形の定義（CRASH・US_DROP・EXT など）は定数で公開し、件数・前半／後半・比べる相手（全日）を必ず並べる。
  - 形は値動きだけで決め、毎回 36年の日経平均（docs/data/cache/index_long.json）から数え直す。先読みしない（米国の夜は東京の寄りの前に終わったもの）。
  - **注文の条件・並べ方には使わない**（買う候補を出すのは swing.py の2つのルールだけ）。対処は「持ち株・買い・注文の置き方」の考え方と、
    その根拠の数字。数字が小さい形（件数 20 未満）はそう書く。
  - 「天井のサイン」を作らない。伸びすぎは「次の5日の一服」と「一部だけ減らす」の損得だけを言う（全部売る・崩れたら売るは負けた）。

研究（2026-10-17、このモジュールで 1991-01〜2026-10 を数えた値。28章の表）:
  急落（1日 −3%以下）178回: 4日以内に急落日の安値を割った 79%、5日の最安値は中央値 −2.2%、20日後 +1.6%（全日 +0.4%）。
  急落の2日後の騰落は平均 +0.35%（全日 +0.02%）。「2日後の投げ」は平均では見えない。
  急落 → 翌日反発 → その夜の米国 −1%以下 17回: 翌朝の窓 −1.4%、4日以内に急落日の安値割れ 71%、20日後 −2.6%（プラス 24%）。前半・後半とも下。
  25日線から +8%以上 59回: 次の5日 −0.6%（前半・後半とも）。半分減らして +2%に戻るか10日で戻す、は持ち続けると同じか少し上。
  崩れたら全部売る（20日の高値 −3ATR、25日線の上で買い戻し）は、2013年以降の年率を +14.1% → +5.6% に下げた。
  信用買残（JPX、2002年〜）が1週で −5%以上減った週（投げ）の後4週は +2.2%（プラス69%。全週 +0.9%）。前半・後半とも上。
"""
from __future__ import annotations

import bisect
import json
import os
import statistics
from datetime import date, timedelta

LONG_PATH = "docs/data/cache/index_long.json"
FLOWS_PATH = "docs/data/flows.json"
LOG_PATH = "docs/data/phase_log.json"

SPLIT = "2008-01-01"        # 日経の前半（1990〜2007）・後半（2008〜）
MSPLIT = "2014-01-01"       # 信用残（2002〜）の前半・後半
START = 260                 # 1年の高値・200日線に足りる日から数える
CRASH = -3.0                # 急落: 1日の騰落（%）
STREAK = -3.0               # 下げ続き: 急落の前の5日の騰落（%）
US_DROP = -1.0              # その夜の米国（S&P500）の下げ（%）
US_UP = 0.5                 # 同じく上げ
EXT = 8.0                   # 伸びすぎ: 25日線からの離れ（%）
BREAK_DAYS, BREAK_PCT = 10, -8.0     # 上げの後の最初の崩れ: 1年の高値を付けてから10日以内に、その高値から −8%
EVENT_GAP = 3               # 3営業日以内の続きは同じ出来事として数える
LOW_DAYS = 4                # 急落日の安値を割るかを見る日数（翌日から）
LIMITS = (2, 3, 4)          # 買いの指値（基準の引けから −%、翌日から3日有効）
TRIM_FRAC, TRIM_BACK, TRIM_DAYS = 0.5, 2.0, 10     # 伸びすぎで半分減らし、25日線から +2%まで戻るか10日で戻す
TREND_ATR = 3               # 崩れたら売る: 20日の高値 − 3ATR を引けで割ったら翌日の寄りで売り、25日線の上で買い戻す
COST = 0.0005               # 片道の売買コスト（日経の売買を口座の出し入れに見立てる）
NAGE, SEIRI, TSUMI = -5.0, -10.0, 25.0   # 信用買残: 1週の減り（投げ）・4週の減り（整理）・26週の増え（積み上がり。4週でもまだ増）
SMALL = 20                  # これ未満の件数は「少ない」と書く

LABELS = {
    "failed": "急落→反発→米国安",
    "rebound": "急落の翌日の反発",
    "crash_streak": "下げ続きの後の急落",
    "crash": "急落",
    "after_crash": "急落のあと",
    "top_break": "上げの後の最初の崩れ",
    "extended": "伸びすぎ",
    "normal": "平常",
}
TONE = {"failed": "down", "rebound": "warn", "crash_streak": "down", "crash": "down", "after_crash": "warn",
        "top_break": "warn", "extended": "warn", "normal": "neutral"}
DEFS = {
    "crash": f"日経平均が1日 {CRASH:.0f}%以下",
    "crash_streak": f"急落の前の5日も {STREAK:.0f}%以下（下げ続きの後の急落）",
    "rebound": "急落の翌日に上げて引けた",
    "failed": f"急落の翌日に反発し、その夜の S&P500 が {US_DROP:.0f}%以下",
    "rebound_mid": f"急落の翌日に反発し、その夜の S&P500 が {US_DROP:.0f}%〜+{US_UP}%",
    "rebound_up": f"急落の翌日に反発し、その夜の S&P500 が +{US_UP}%以上",
    "crash_us_down": f"急落し、その夜の S&P500 も {US_DROP:.0f}%以下",
    "crash_us_mid": f"急落し、その夜の S&P500 が {US_DROP:.0f}%〜+{US_UP}%",
    "crash_us_up": f"急落し、その夜の S&P500 が +{US_UP}%以上",
    "extended": f"25日線から +{EXT:.0f}%以上",
    "top_break": f"1年の高値を付けてから{BREAK_DAYS}日以内に、その高値から {BREAK_PCT:.0f}%",
}


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def _mean(x):
    return round(sum(x) / len(x), 2) if x else None


def _med(x):
    return round(statistics.median(x), 2) if x else None


def _pct(x, f):
    return round(100 * sum(1 for v in x if f(v)) / len(x)) if x else None


class Series:
    """日経平均の四本値と S&P500 の終値。r[i] は前日比（%）。"""

    def __init__(self, n225: list, spx: list):
        rows = [r for r in n225 if len(r) >= 5 and all(isinstance(x, (int, float)) and x > 0 for x in r[1:5])]
        rows.sort(key=lambda r: r[0])
        self.d = [r[0] for r in rows]
        self.o = [float(r[1]) for r in rows]
        self.h = [float(r[2]) for r in rows]
        self.l = [float(r[3]) for r in rows]
        self.c = [float(r[4]) for r in rows]
        self.n = len(rows)
        self.r = [None] + [(self.c[i] / self.c[i - 1] - 1) * 100 for i in range(1, self.n)]
        sp = sorted((x for x in spx if len(x) >= 2 and x[1]), key=lambda x: x[0])
        self.sd = [x[0] for x in sp]
        self.sc = [float(x[1]) for x in sp]
        tr = [0.0] + [max(self.h[i], self.c[i - 1]) - min(self.l[i], self.c[i - 1]) for i in range(1, self.n)]
        self.atr = [None] * self.n
        for i in range(20, self.n):
            self.atr[i] = sum(tr[i - 19:i + 1]) / 20
        self._cs = [0.0]
        for x in self.c:
            self._cs.append(self._cs[-1] + x)

    def sma(self, i: int, k: int) -> float:
        return (self._cs[i + 1] - self._cs[i + 1 - k]) / k

    def dev25(self, i: int) -> float:
        return (self.c[i] / self.sma(i, 25) - 1) * 100

    def us_before(self, day: str) -> float | None:
        """東京の day の寄りの前に終わった米国の1日の騰落（%）。"""
        k = bisect.bisect_left(self.sd, day) - 1
        return (self.sc[k] / self.sc[k - 1] - 1) * 100 if k >= 1 else None

    def us_after(self, i: int) -> float | None:
        """東京の i の引けのあとの米国（＝翌営業日の寄りの前）。i が最後の日なら分からない（None）。"""
        return self.us_before(self.d[i + 1]) if i + 1 < self.n else None


# ---------- 形 ----------

def is_crash(S: Series, i: int) -> bool:
    return i >= 1 and S.r[i] is not None and S.r[i] <= CRASH


def is_streak(S: Series, i: int) -> bool:
    return is_crash(S, i) and i >= 6 and (S.c[i - 1] / S.c[i - 6] - 1) * 100 <= STREAK


def is_rebound(S: Series, i: int) -> bool:
    return is_crash(S, i - 1) and S.r[i] is not None and S.r[i] > 0


def is_extended(S: Series, i: int) -> bool:
    return i >= 25 and S.dev25(i) >= EXT


def is_top_break(S: Series, i: int) -> bool:
    if i < START:
        return False
    hi = max(S.c[i - BREAK_DAYS:i])
    return hi >= max(S.c[i - 250:i - BREAK_DAYS]) and (S.c[i] / hi - 1) * 100 <= BREAK_PCT


def _us(lo, hi):
    def f(S, i):
        u = S.us_after(i)
        return u is not None and lo < u <= hi
    return f


_US_DOWN, _US_MID, _US_UP = _us(-99, US_DROP), _us(US_DROP, US_UP - 1e-9), _us(US_UP - 1e-9, 99)
# key: (条件, 安値の基準の日のずれ)  安値の基準は急落の日（反発の形は1日前）
PATTERNS = {
    "crash": (is_crash, 0),
    "crash_streak": (is_streak, 0),
    "rebound": (is_rebound, -1),
    "failed": (lambda S, i: is_rebound(S, i) and _US_DOWN(S, i), -1),
    "rebound_mid": (lambda S, i: is_rebound(S, i) and _US_MID(S, i), -1),
    "rebound_up": (lambda S, i: is_rebound(S, i) and _US_UP(S, i), -1),
    "crash_us_down": (lambda S, i: is_crash(S, i) and _US_DOWN(S, i), 0),
    "crash_us_mid": (lambda S, i: is_crash(S, i) and _US_MID(S, i), 0),
    "crash_us_up": (lambda S, i: is_crash(S, i) and _US_UP(S, i), 0),
    "extended": (is_extended, None),
    "top_break": (is_top_break, None),
}
SCENARIOS = {"crash": ("crash_us_down", "crash_us_mid", "crash_us_up"),
             "rebound": ("failed", "rebound_mid", "rebound_up")}


def events(S: Series, cond, end: int | None = None) -> list[int]:
    """形に当たった日（3営業日以内の続きは最初の日だけ）。20日先まで分かる日だけ。"""
    end = S.n - 21 if end is None else end
    out, last = [], -99
    for i in range(START, end):
        if cond(S, i) and i - last >= EVENT_GAP:
            out.append(i)
            last = i
    return out


def _one(S: Series, ev: list[int], ref: int | None) -> dict | None:
    if not ev:
        return None
    c, o, lo, hi = S.c, S.o, S.l, S.h
    gap = [(o[i + 1] / c[i] - 1) * 100 for i in ev]
    out = {
        "n": len(ev),
        "gap": _mean(gap), "gap_med": _med(gap),
        "d1": _mean([S.r[i + 1] for i in ev]),
        "min5_med": _med([(min(lo[i + 1:i + 6]) / c[i] - 1) * 100 for i in ev]),
        "max3_med": _med([(max(hi[i + 1:i + 4]) / c[i] - 1) * 100 for i in ev]),
        "r5": _mean([(c[i + 5] / c[i] - 1) * 100 for i in ev]),
        "r20": _mean([(c[i + 20] / c[i] - 1) * 100 for i in ev]),
        "r20_med": _med([(c[i + 20] / c[i] - 1) * 100 for i in ev]),
        "p20": _pct([c[i + 20] - c[i] for i in ev], lambda v: v > 0),
        "dd20": _pct([(min(lo[i + 1:i + 21]) / c[i] - 1) * 100 for i in ev], lambda v: v <= -5),
        "open_r20": _mean([(c[i + 20] / o[i + 1] - 1) * 100 for i in ev]),     # 翌朝の寄りで買って（売らずに）20日目の引けまで
        "open_p20": _pct([c[i + 20] - o[i + 1] for i in ev], lambda v: v > 0),
    }
    if ref is not None:
        out["low_break"] = _pct(ev, lambda i: min(lo[i + 1:i + 1 + LOW_DAYS]) < lo[i + ref])
    for d in LIMITS:                    # 基準の引けの −d% に買い指値（翌日から3日。寄りで割れていれば寄り）
        got = []
        for i in ev:
            p = c[i] * (1 - d / 100)
            for j in range(i + 1, i + 4):
                if lo[j] <= p:
                    got.append((c[i + 20] / min(o[j], p) - 1) * 100)
                    break
        out[f"buy{d}"] = {"fill": round(100 * len(got) / len(ev)), "r20": _mean(got)}
    wait = []                           # 売ると決めた日に、寄りで売らずに基準の引けへ売り指値（3日。届かなければ3日目の引け）
    for i in ev:
        p, got = c[i], None
        for j in range(i + 1, i + 4):
            if hi[j] >= p:
                got = max(o[j], p)
                break
        wait.append(((got if got is not None else c[i + 3]) / o[i + 1] - 1) * 100)
    out["sell_wait"] = _mean(wait)
    out["sell_wait_win"] = _pct(wait, lambda v: v > 0)
    return out


def stats(S: Series, key: str, end: int | None = None) -> dict:
    cond, ref = PATTERNS[key]
    ev = events(S, cond, end)
    first = [i for i in ev if S.d[i] < SPLIT]
    second = [i for i in ev if S.d[i] >= SPLIT]
    cases = [{"date": S.d[i], "r": round(S.r[i], 1), "gap": round((S.o[i + 1] / S.c[i] - 1) * 100, 1),
              "min5": round((min(S.l[i + 1:i + 6]) / S.c[i] - 1) * 100, 1),
              "r5": round((S.c[i + 5] / S.c[i] - 1) * 100, 1), "r20": round((S.c[i + 20] / S.c[i] - 1) * 100, 1)}
             for i in ev[-8:]][::-1]
    return {"key": key, "def": DEFS.get(key), "all": _one(S, ev, ref), "first": _one(S, first, ref),
            "second": _one(S, second, ref), "cases": cases}


def baseline(S: Series) -> dict:
    ev = list(range(START, S.n - 21))
    o = _one(S, ev, 0)
    o["low_break"] = _pct(ev, lambda i: min(S.l[i + 1:i + 1 + LOW_DAYS]) < S.l[i])
    return {k: o.get(k) for k in ("n", "gap", "d1", "min5_med", "r5", "r20", "p20", "dd20", "low_break")}


def day_by_day(S: Series) -> dict:
    """急落の翌日〜5日目の、1日ずつの性質（「2日後に投げが来る」を確かめる）。全日と並べる。"""
    ev = events(S, is_crash)
    base = list(range(START, S.n - 21))
    rows = []
    for k in range(1, 6):
        rows.append({
            "k": k,
            "ret": _mean([S.r[i + k] for i in ev]),
            "ret_first": _mean([S.r[i + k] for i in ev if S.d[i] < SPLIT]),
            "ret_second": _mean([S.r[i + k] for i in ev if S.d[i] >= SPLIT]),
            "dip": _mean([(S.l[i + k] / S.o[i + k] - 1) * 100 for i in ev]),
            "new_low": _pct(ev, lambda i: S.l[i + k] < min(S.l[i:i + k])),
            "base_ret": _mean([S.r[i + k] for i in base]),
            "base_new_low": _pct(base, lambda i: S.l[i + k] < min(S.l[i:i + k])),
        })
    bottom = [0] * 11
    for i in ev:
        bottom[min(range(11), key=lambda k: S.l[i + k])] += 1
    return {"n": len(ev), "rows": rows, "bottom": bottom}


# ---------- 口座の出し入れ（崩れたら売る・伸びすぎで一部減らす） ----------

def _account(S: Series, lo: str, hi: str, mode: str) -> dict:
    idx = [j for j in range(START, S.n - 1) if lo <= S.d[j] < hi]
    if len(idx) < 250:
        return {}
    w, eq, pk, mdd, since, acts, days_in = 1.0, 1.0, 1.0, 0.0, None, 0, 0
    for i in idx:
        nw = w
        if mode == "trend":
            if w == 1.0 and S.c[i] < max(S.c[i - 19:i + 1]) - TREND_ATR * S.atr[i]:
                nw, acts = 0.0, acts + 1
            elif w == 0.0 and S.c[i] > S.sma(i, 25):
                nw = 1.0
        elif mode == "trim":
            dv = S.dev25(i)
            if w == 1.0 and dv >= EXT:
                nw, since, acts = 1 - TRIM_FRAC, i, acts + 1
            elif w < 1.0 and (dv <= TRIM_BACK or i - since >= TRIM_DAYS):
                nw = 1.0
        r = (1 + w * (S.o[i + 1] / S.c[i] - 1)) * (1 + nw * (S.c[i + 1] / S.o[i + 1] - 1)) - abs(nw - w) * COST
        eq *= r
        w = nw
        pk = max(pk, eq)
        mdd = min(mdd, eq / pk - 1)
        days_in += w
    yrs = len(idx) / 245
    return {"cagr": round((eq ** (1 / yrs) - 1) * 100, 1), "mdd": round(mdd * 100), "acts": acts,
            "in": round(100 * days_in / len(idx))}


PERIODS = (("前半", "1991-01-01", SPLIT), ("後半", SPLIT, "2100-01-01"), ("2013〜", "2013-01-01", "2100-01-01"))


def exposure(S: Series) -> dict:
    return {mode: [{"label": lab, **_account(S, lo, hi, mode)} for lab, lo, hi in PERIODS]
            for mode in ("hold", "trend", "trim")}


# ---------- 信用残と投資部門別（JPX） ----------

def _margin_rows(margin: list[dict]) -> list[dict]:
    B = [m.get("buy") or 0 for m in margin]
    out = []
    for k, m in enumerate(margin):
        if k < 1 or not B[k - 1]:
            continue
        out.append({**m, "w1": round((B[k] / B[k - 1] - 1) * 100, 1),
                    "w4": round((B[k] / B[k - 4] - 1) * 100, 1) if k >= 4 and B[k - 4] else None,
                    "w26": round((B[k] / B[k - 26] - 1) * 100, 1) if k >= 26 and B[k - 26] else None})
    return out


def margin_stats(S: Series, margin: list[dict]) -> dict:
    """週の信用買残の形ごとの、その後4週（20営業日）の日経。公表（翌週の火曜）のあとの水曜の引けから数える。"""
    rows = []
    for m in _margin_rows(margin):
        day = (date.fromisoformat(m["date"]) + timedelta(days=5)).isoformat()
        i = bisect.bisect_left(S.d, day)
        if i + 21 >= S.n or m.get("w26") is None:
            continue
        rows.append({**m, "f20": (S.c[i + 20] / S.c[i] - 1) * 100,
                     "dd20": (min(S.l[i + 1:i + 21]) / S.c[i] - 1) * 100})

    def one(sub):
        if not sub:
            return None
        return {"n": len(sub), "f20": _mean([r["f20"] for r in sub]), "p20": _pct([r["f20"] for r in sub], lambda v: v > 0),
                "dd20": _pct([r["dd20"] for r in sub], lambda v: v <= -5)}

    def split(sub):
        return {"all": one(sub), "first": one([r for r in sub if r["date"] < MSPLIT]),
                "second": one([r for r in sub if r["date"] >= MSPLIT])}

    return {
        "base": split(rows),
        "nage": split([r for r in rows if r["w1"] <= NAGE]),
        "seiri": split([r for r in rows if (r["w4"] or 0) <= SEIRI]),
        "tsumi": split([r for r in rows if (r["w26"] or 0) >= TSUMI and (r["w4"] or 0) > 0]),
        "from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None,
    }


def margin_now(margin: list[dict]) -> dict | None:
    rows = _margin_rows(margin)
    if not rows:
        return None
    m = rows[-1]
    flags = []
    if m["w1"] <= NAGE:
        flags.append("nage")
    if (m["w4"] or 0) <= SEIRI:
        flags.append("seiri")
    if (m["w26"] or 0) >= TSUMI and (m["w4"] or 0) > 0:
        flags.append("tsumi")
    return {"date": m["date"], "buy": round(m["buy"] / 1e6, 2), "sell": round(m["sell"] / 1e6, 2),
            "ratio": round(m["buy"] / m["sell"], 1) if m.get("sell") else None,
            "w1": m["w1"], "w4": m["w4"], "w26": m["w26"], "flags": flags,
            "recent": [{"date": r["date"], "buy": round(r["buy"] / 1e6, 2), "w1": r["w1"]} for r in rows[-6:]]}


def investors_now(inv: list[dict]) -> dict | None:
    if not inv:
        return None
    rows = sorted(inv, key=lambda r: r["end"])[-8:]
    last = rows[-1]
    streak = 0
    for r in reversed(rows):
        if (r.get("foreign") or 0) * (last.get("foreign") or 0) > 0:
            streak += 1
        else:
            break
    return {"week": f"{last['start']}〜{last['end']}", "end": last["end"], "rows": rows[::-1],
            "foreign_streak": streak, "foreign_sum4": round(sum(r.get("foreign") or 0 for r in rows[-4:]), 1),
            "indiv_sum4": round(sum(r.get("indiv") or 0 for r in rows[-4:]), 1)}


# ---------- 今の局面 ----------

def _yen(x: float) -> int:
    return int(round(x))


def ladder(S: Series) -> list[dict]:
    """次の引けがいくらなら形が変わるか（値段のはしご）。"""
    i = S.n - 1
    out = [{"key": "crash", "price": _yen(S.c[i] * (1 + CRASH / 100)), "text": f"これ以下で引けると「急落」（{CRASH:.0f}%）"}]
    if i >= 25:
        s24 = S._cs[i + 1] - S._cs[i + 1 - 24]
        k = 1 + EXT / 100
        out.append({"key": "extended", "price": _yen(k * s24 / (25 - k)), "text": f"これ以上で引けると「伸びすぎ」（25日線 +{EXT:.0f}%）"})
        out.append({"key": "ma25", "price": _yen(S.sma(i, 25)), "text": "25日線（今日の値）"})
    if i >= START:
        hi = max(S.c[i - BREAK_DAYS + 1:i + 1])
        if hi >= max(S.c[i - 250:i - BREAK_DAYS + 1]) and S.c[i] > hi * (1 + BREAK_PCT / 100):
            out.append({"key": "top_break", "price": _yen(hi * (1 + BREAK_PCT / 100)),
                        "text": f"これ以下で引けると「上げの後の最初の崩れ」（高値 {hi:,.0f} から {BREAK_PCT:.0f}%）"})
    return sorted(out, key=lambda x: -x["price"])


def state(S: Series, us_now: float | None) -> dict:
    """最後の引けの形。us_now は最後の引けのあとの米国の騰落（寄り前・前場に分かっている。大引ではまだ無い＝None）。"""
    i = S.n - 1
    keys = [k for k, f in (("crash", is_crash), ("crash_streak", is_streak), ("rebound", is_rebound),
                            ("extended", is_extended), ("top_break", is_top_break)) if f(S, i)]
    if "rebound" in keys and us_now is not None:
        keys.append("failed" if us_now <= US_DROP else "rebound_up" if us_now >= US_UP else "rebound_mid")
    if "crash" in keys and us_now is not None:
        keys.append("crash_us_down" if us_now <= US_DROP else "crash_us_up" if us_now >= US_UP else "crash_us_mid")
    crash = None
    for j in range(i, max(i - 6, 0), -1):          # 直近5営業日の急落
        if is_crash(S, j):
            crash = {"date": S.d[j], "days": i - j, "r": round(S.r[j], 1), "low": round(S.l[j]),
                     "close": round(S.c[j]), "broken": i > j and min(S.l[j + 1:i + 1]) < S.l[j],
                     "since": round((S.c[i] / S.c[j] - 1) * 100, 1)}
            break
    if crash and not keys:
        keys.append("after_crash")
    hi_i = max(range(max(0, i - 250), i + 1), key=lambda k: S.c[k])
    for primary in ("failed", "rebound", "crash_streak", "crash", "top_break", "after_crash", "extended"):
        if primary in keys:
            break
    else:
        primary = "normal"
    return {"date": S.d[i], "close": round(S.c[i], 2), "chg": round(S.r[i], 2) if S.r[i] is not None else None,
            "dev25": round(S.dev25(i), 1) if i >= 25 else None,
            "r5": round((S.c[i] / S.c[i - 5] - 1) * 100, 1) if i >= 5 else None,
            "from_high": round((S.c[i] / S.c[hi_i] - 1) * 100, 1), "high": round(S.c[hi_i]), "high_date": S.d[hi_i],
            "us": round(us_now, 2) if us_now is not None else None, "crash": crash, "keys": keys, "primary": primary}


def _fmt(v, d=1, sign=True):
    if v is None:
        return "—"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def _few(n):
    return f"（{n}回。少ない）" if n is not None and n < SMALL else f"（{n}回）"


def playbook(st: dict, sx: dict, base: dict, slot: str, mg: dict | None = None, ms: dict | None = None) -> list[dict]:
    """局面ごとの対処（持ち株・買い・注文）。数字は sx（形ごとの統計の all／first／second）から。断定せず、根拠を併記する。"""
    p, out = st["primary"], []

    def g(key, half="all"):
        return ((sx.get(key) or {}).get(half)) or {}

    if p in ("rebound", "failed") or "rebound" in st["keys"]:
        f, mid, up = g("failed"), g("rebound_mid"), g("rebound_up")
        if p == "failed":
            f1, f2 = g("failed", "first"), g("failed", "second")
            out.append({"who": "全体", "text": f"今週の形: 急落の翌日の反発のあと、米国が {_fmt(st['us'])}%。過去{_few(f.get('n'))}は翌朝の窓 中央値 {_fmt(f.get('gap_med'))}%、"
                        f"{LOW_DAYS}日以内に急落の日の安値を割った {f.get('low_break')}%、20日後 {_fmt(f.get('r20'))}%（プラス {f.get('p20')}%。前半 {_fmt(f1.get('r20'))}／後半 {_fmt(f2.get('r20'))}）。"
                        f"急落一般（20日後 {_fmt(g('crash').get('r20'))}%）と違い、戻りが続かなかった形"})
            out.append({"who": "買い", "text": f"寄りで買わない: 5日以内の最安値は前日の引けから中央値 {_fmt(f.get('min5_med'))}%。買うなら前日の引けの −4%に指値を分けて"
                        f"（3日以内の約定 {(f.get('buy4') or {}).get('fill')}%、約定した分の20日後 {_fmt((f.get('buy4') or {}).get('r20'))}%。寄りで買うと {_fmt(f.get('open_r20'))}%）"})
            out.append({"who": "持ち株", "text": f"戻りを待つ売り指値は効かない: 前日の引けに売り指値を置いて待つと、寄りで売るより平均 {_fmt(f.get('sell_wait'))}pt。"
                        f"売ると決めた銘柄は寄りで、持ち続ける銘柄は撤退ライン（逆指値）を必ず置く。寄りで売らずに20日持つと寄りより平均 {_fmt(f.get('open_r20'))}%（寄りより上だったのは {f.get('open_p20')}%）"})
        else:
            out.append({"who": "全体", "text": f"急落の翌日に反発した。次の朝は今夜の米国次第: 米国 {US_DROP:.0f}%以下なら過去{_few(f.get('n'))}は翌朝の窓 {_fmt(f.get('gap_med'))}%・"
                        f"20日後 {_fmt(f.get('r20'))}%、+{US_UP}%以上なら{_few(up.get('n'))} 窓 {_fmt(up.get('gap_med'))}%・20日後 {_fmt(up.get('r20'))}%。反発の日に安心しない"})
            out.append({"who": "注文", "text": f"今夜のうちに決めておく: 米国が {US_DROP:.0f}%以下で終わったら、寄りの成行の買いは取り消し、買うなら今日の引けの −4%に指値。"
                        "持ち株は撤退ラインに届いた銘柄だけ寄りで売る（戻り待ちの指値は効かなかった）"})
    elif p in ("crash", "crash_streak"):
        c = g(p)
        out.append({"who": "全体", "text": f"急落の日。過去{_few(c.get('n'))}は{LOW_DAYS}日以内に今日の安値を割った {c.get('low_break')}%（全日 {base.get('low_break')}%）、"
                    f"5日以内の最安値は中央値 {_fmt(c.get('min5_med'))}%。それでも20日後は平均 {_fmt(c.get('r20'))}%（全日 {_fmt(base.get('r20'))}%）で、急落一般は戻りやすい"})
        out.append({"who": "持ち株", "text": "投げない: 撤退ラインに届いた銘柄だけを売る。急落の2日後は、36年の日経では弱い日ではなかった（追証の期限の売りは平均では見えない）。"
                    "弱かったのは「翌日に反発して、その夜の米国が下げた」組み合わせ（下の米国別の表）"})
        b2, b3 = c.get("buy2") or {}, c.get("buy3") or {}
        out.append({"who": "買い", "text": f"翌朝の寄りで飛びつかない: 買うなら今日の引けの −2〜3%に指値（3日以内の約定 {b2.get('fill')}%／{b3.get('fill')}%、"
                    f"約定した分の20日後 {_fmt(b2.get('r20'))}%／{_fmt(b3.get('r20'))}%。翌朝の寄りで買うと {_fmt(c.get('open_r20'))}%）"})
        if slot == "taibike":
            d, u = g("crash_us_down"), g("crash_us_up")
            out.append({"who": "注文", "text": f"今夜の米国で翌朝が変わる: 米国 {US_DROP:.0f}%以下なら過去{_few(d.get('n'))} 翌朝の窓 {_fmt(d.get('gap_med'))}%、"
                        f"+{US_UP}%以上なら{_few(u.get('n'))} {_fmt(u.get('gap_med'))}%"})
    elif p == "after_crash":
        cr = st["crash"]
        c = g("crash")
        out.append({"who": "全体", "text": f"{cr['date'][5:].replace('-', '/')} の急落（{_fmt(cr['r'])}%）から{cr['days']}営業日目。急落の日の安値（{cr['low']:,}）を"
                    + ("すでに割った。" if cr["broken"] else "まだ割っていない。") +
                    f"過去の急落では{LOW_DAYS}日以内に割った {c.get('low_break')}%、底（10日の最安値）は急落の日〜2日後が多い"})
        out.append({"who": "持ち株", "text": "撤退ラインに届いた銘柄だけ売る。急落一般は20日後に戻りやすい（平均 "
                    f"{_fmt(c.get('r20'))}%）が、米国安が重なった日は形が変わる"})
    elif p == "top_break":
        t = g("top_break")
        out.append({"who": "全体", "text": f"1年の高値 {st['high']:,}（{st['high_date'][5:].replace('-', '/')}）から {_fmt(st['from_high'])}%。上げの後の最初の崩れ{_few(t.get('n'))}は、"
                    f"20日以内にさらに −5%の下げが来た {t.get('dd20')}%（全日 {base.get('dd20')}%）、20日後 {_fmt(t.get('r20'))}%"})
        out.append({"who": "持ち株", "text": "崩れたら全部売るは長い目で負けた（2013年以降の年率 +14% → +6%前後）。売るなら撤退ラインに届いた銘柄と、"
                    "負けの銘柄が戻った日（保有の「売り時」）だけ"})
    elif p == "extended":
        e, e1, e2 = g("extended"), g("extended", "first"), g("extended", "second")
        out.append({"who": "全体", "text": f"25日線から {_fmt(st['dev25'])}%。過去{_few(e.get('n'))}は次の5日 {_fmt(e.get('r5'))}%（前半 {_fmt(e1.get('r5'))}／後半 {_fmt(e2.get('r5'))}）、"
                    f"20日後 {_fmt(e.get('r20'))}%（全日 {_fmt(base.get('r20'))}%）。下げのサインではなく、一服しやすい"})
        out.append({"who": "持ち株", "text": f"利確は一部だけ: 半分減らして、25日線 +{TRIM_BACK:.0f}%まで戻るか{TRIM_DAYS}日で戻す、は持ち続けるのと同じか少し上だった。"
                    "全部売る・崩れたら売るは平均を下げた。近いうちに売ると決めている銘柄は、伸びすぎの翌日の寄りが有利（27章）"})
        out.append({"who": "買い", "text": "新しく買うのは急がない（次の5日は一服しやすい）。押し目買いのルールの注文はルールどおり"})
    else:
        out.append({"who": "全体", "text": "特別な形はない。注文はルールどおり、撤退ラインは置いたまま。下の値段のはしごで、どこから形が変わるかを先に見ておく"})
    out.extend(supply_lines(mg, ms))
    return out


def supply_lines(mg: dict | None, ms: dict | None) -> list[dict]:
    """信用買残（市場全体）の形の一言。研究の数字は margin_stats（4週＝20営業日後）。注文の条件には使わない。"""
    if not mg or not ms:
        return []
    base = (ms.get("base") or {}).get("all") or {}
    out = []
    for fl in mg.get("flags") or []:
        r, r1, r2 = ((ms.get(fl) or {}).get(h) or {} for h in ("all", "first", "second"))
        if fl == "tsumi":
            out.append({"who": "需給", "text": f"信用買残（市場全体、{mg['date'][5:].replace('-', '/')}の週）が26週で {_fmt(mg['w26'])}%、4週でもまだ増えている。"
                        f"この形の後4週に −5%の下げが来た割合 {r.get('dd20')}%（全週 {base.get('dd20')}%。前半 {r1.get('dd20')}／後半 {r2.get('dd20')}%）。"
                        "平均の騰落は全週と変わらないが、下げると投げの燃料になる。振れ幅の印で、下げの合図ではない"})
        elif fl in ("nage", "seiri"):
            what = f"1週で {_fmt(mg['w1'])}%" if fl == "nage" else f"4週で {_fmt(mg['w4'])}%"
            out.append({"who": "需給", "text": f"信用買残（市場全体、{mg['date'][5:].replace('-', '/')}の週）が{what}減った（{'投げ' if fl == 'nage' else '整理'}）。"
                        f"この形の後4週の日経は平均 {_fmt(r.get('f20'))}%（プラス {r.get('p20')}%。全週 {_fmt(base.get('f20'))}%。前半 {_fmt(r1.get('f20'))}／後半 {_fmt(r2.get('f20'))}%）"})
    return out


def headline(st: dict, sx: dict) -> str:
    p = st["primary"]
    a = lambda k: (sx.get(k) or {}).get("all") or {}     # noqa: E731
    if p == "failed":
        f = a("failed")
        return f"急落→反発→米国{_fmt(st['us'])}%。過去{f.get('n')}回は翌朝{_fmt(f.get('gap_med'))}%・20日後{_fmt(f.get('r20'))}%"
    if p == "rebound":
        f = a("failed")
        return f"急落の翌日の反発。今夜の米国が{US_DROP:.0f}%以下なら過去{f.get('n')}回は20日後{_fmt(f.get('r20'))}%"
    if p in ("crash", "crash_streak"):
        c = a(p)
        return f"{LABELS[p]}（{_fmt(st['chg'])}%）。過去{c.get('n')}回は{LOW_DAYS}日以内に安値割れ{c.get('low_break')}%・20日後{_fmt(c.get('r20'))}%"
    if p == "after_crash":
        cr = st["crash"]
        return f"急落から{cr['days']}営業日目。急落の日の安値を" + ("割った" if cr["broken"] else "まだ割っていない")
    if p == "top_break":
        return f"上げの後の最初の崩れ（高値から{_fmt(st['from_high'])}%）"
    if p == "extended":
        return f"伸びすぎ（25日線から{_fmt(st['dev25'])}%）。次の5日は一服しやすい"
    return "平常（急落・伸びすぎの形なし）"


# ---------- 答え合わせの記録 ----------

def log_update(log: list[dict], S: Series, st: dict | None, sx: dict | None, record: bool) -> list[dict]:
    """局面の日（平常以外）を記録し、その後の実際（翌朝の窓・安値割れ・5日の最安値・5日後・20日後）を埋める。"""
    log = [x for x in (log or []) if x.get("date")]
    if record and st and st["primary"] != "normal" and not any(x["date"] == st["date"] for x in log):
        k = st["primary"]
        key = "crash" if k == "after_crash" else k
        a = ((sx or {}).get(key) or {}).get("all") or {}
        ref = st["crash"]["low"] if st.get("crash") else None
        pick = lambda d: {kk: d.get(kk) for kk in ("n", "gap_med", "low_break", "min5_med", "r5", "r20", "p20")}   # noqa: E731
        root = "rebound" if "rebound" in st["keys"] else "crash" if "crash" in st["keys"] else None
        log.append({"date": st["date"], "key": k, "label": LABELS[k], "close": st["close"], "ref_low": ref,
                    "expect": pick(a),
                    "by_us": {kk: pick(((sx or {}).get(kk) or {}).get("all") or {}) for kk in SCENARIOS[root]} if root else None})
    pos = {d: i for i, d in enumerate(S.d)}
    for x in log:
        i = pos.get(x["date"])
        if i is None:
            continue
        act = x.get("actual") or {}
        left = S.n - 1 - i
        if left >= 1:
            u = S.us_after(i)
            act["us"] = round(u, 2) if u is not None else None
            act["gap"] = round((S.o[i + 1] / S.c[i] - 1) * 100, 2)
            act["d1"] = round(S.r[i + 1], 2)
        if x.get("ref_low") and left >= 1:
            act["low_broken"] = min(S.l[i + 1:i + 1 + min(LOW_DAYS, left)]) < x["ref_low"]
            act["low_days"] = min(LOW_DAYS, left)
        if left >= 5:
            act["min5"] = round((min(S.l[i + 1:i + 6]) / S.c[i] - 1) * 100, 2)
            act["r5"] = round((S.c[i + 5] / S.c[i] - 1) * 100, 2)
        if left >= 20:
            act["r20"] = round((S.c[i + 20] / S.c[i] - 1) * 100, 2)
        if act:
            x["actual"] = act
    return log[-120:]


# ---------- 長い日足の更新 ----------

def update_long(long: dict, payload: dict, slot: str, fetch_ohlc=None, fetch_close=None, today: str | None = None) -> str:
    """index_long.json に新しい日を足す。CNBC の日足（四本値）を先に、取れなければ大引の指数のクォートで今日だけ足す。
    S&P500 は CNBC の日足か、寄り前の米国のクォート（asof の日の終値）。例外は投げず、何をしたかの1行を返す。"""
    n225 = long.setdefault("n225", [])
    spx = long.setdefault("spx", [])
    last_n = n225[-1][0] if n225 else "1990-01-01"
    last_s = spx[-1][0] if spx else "1990-01-01"
    notes = []
    start = date.fromisoformat(min(last_n, last_s)) - timedelta(days=10)
    end = date.fromisoformat(today) if today else date.today()
    if fetch_ohlc and last_n < (today or "9999"):
        rows = fetch_ohlc(".N225", start, end + timedelta(days=1)) or []
        have = {r[0] for r in n225}
        add = [[d, round(o, 2), round(h, 2), round(lo, 2), round(c, 2)] for d, o, h, lo, c, *_ in rows
               if d > last_n and d not in have and min(o, h, lo, c) > 0 and (not today or d <= today)]
        n225.extend(add)
        if add:
            notes.append(f"日経 +{len(add)}日（CNBC）")
    nk = (payload.get("indices") or {}).get("nikkei") or {}
    if (slot == "taibike" and nk.get("asof") and not nk.get("stale") and nk["asof"] > n225[-1][0]
            and all(nk.get(k) for k in ("open", "high", "low", "close"))):
        n225.append([nk["asof"], nk["open"], nk["high"], nk["low"], nk["close"]])
        notes.append(f"日経 {nk['asof']}（大引のクォート）")
    if fetch_close and last_s < (today or "9999"):
        rows = fetch_close(".SPX", start, end + timedelta(days=1)) or []
        add = [[d, round(c, 2)] for d, c in rows if d > spx[-1][0]] if spx else []
        spx.extend(add)
        if add:
            notes.append(f"S&P500 +{len(add)}日")
    q = (payload.get("us") or {}).get("spx") or {}
    if q.get("asof") and q.get("last") and spx and q["asof"] > spx[-1][0]:
        spx.append([q["asof"], round(q["last"], 2)])
        notes.append(f"S&P500 {q['asof']}（クォート）")
    n225.sort(key=lambda r: r[0])
    return "・".join(notes) or "追加なし"


def _us_now(S: Series, payload: dict, slot: str) -> float | None:
    """最後の東京の引けのあとに終わった米国の騰落。大引では分からない（None）。"""
    if slot == "taibike" or not S.n:
        return None
    k = bisect.bisect_left(S.sd, S.d[-1])           # 東京の最後の日と同じ日付以降の米国の終値
    if k < len(S.sd) and k >= 1:
        return (S.sc[k] / S.sc[k - 1] - 1) * 100
    q = (payload.get("us") or {}).get("spx") or {}
    if q.get("asof") and q["asof"] >= S.d[-1] and q.get("change_pct") is not None:
        return float(q["change_pct"])
    return None


def build(slot: str, today: str, payload: dict, long: dict, flows: dict, log: list[dict]) -> tuple[dict | None, list[dict]]:
    """latest.json の各区分に載せる phase と、更新した答え合わせの記録を返す。"""
    S = Series(long.get("n225") or [], long.get("spx") or [])
    if S.n < START + 30:
        return None, log
    if slot == "taibike" and S.d[-1] != today:
        stale = True                                # 今日の日足が無い（休場日・取得失敗）。形は前の営業日のまま
    else:
        stale = False
    us_now = _us_now(S, payload, slot)
    st = state(S, us_now)
    keys = list(PATTERNS)
    sx = {k: stats(S, k) for k in keys}
    base = baseline(S)
    sc = None
    for root in ("rebound", "crash"):
        if root in st["keys"]:
            now = next((k for k in SCENARIOS[root] if k in st["keys"]), None)
            sc = {"root": root, "rows": [{"key": k, "def": DEFS[k], "now": k == now,
                                          **{kk: ((sx[k].get("all") or {}).get(kk)) for kk in
                                             ("n", "gap_med", "low_break", "min5_med", "r5", "r20", "p20")},
                                          "r20_first": (sx[k].get("first") or {}).get("r20"),
                                          "r20_second": (sx[k].get("second") or {}).get("r20")}
                                         for k in SCENARIOS[root]]}
            break
    mg = flows.get("margin") or []
    mnow, mstats = margin_now(mg), (margin_stats(S, mg) if mg else None)
    out = {
        "asof": st["date"], "slot": slot, "stale": stale, "primary": st["primary"], "label": LABELS[st["primary"]],
        "tone": TONE[st["primary"]], "headline": headline(st, sx), "state": st,
        "playbook": playbook(st, sx, base, slot, mnow, mstats), "scenarios": sc, "ladder": ladder(S),
        "focus": {k: sx[k] for k in st["keys"] if k in sx} | ({"crash": sx["crash"]} if st["primary"] == "after_crash" else {}),
        "margin": mnow, "investors": investors_now(flows.get("investors") or []),
        "research": {
            "from": S.d[START], "to": S.d[-1], "split": SPLIT, "base": base,
            "patterns": {k: {kk: sx[k][kk] for kk in ("def", "all", "first", "second")} for k in keys},
            "days": day_by_day(S), "exposure": exposure(S),
            "margin": mstats,
        },
        "rules": {"crash": CRASH, "streak": STREAK, "us_drop": US_DROP, "us_up": US_UP, "ext": EXT, "break_days": BREAK_DAYS,
                  "break_pct": BREAK_PCT, "low_days": LOW_DAYS, "trim": [TRIM_FRAC, TRIM_BACK, TRIM_DAYS], "trend_atr": TREND_ATR,
                  "nage": NAGE, "seiri": SEIRI, "tsumi": TSUMI, "small": SMALL, "msplit": MSPLIT},
    }
    log = log_update(log, S, st, sx, record=(slot == "taibike" and not stale))
    out["log"] = log[-12:][::-1]
    return out, log


def run(slot: str, today: str, payload: dict, fetch_ohlc=None, fetch_close=None, fetch_flows=None) -> dict | None:
    """収集から呼ぶ入口。長い日足と JPX の需給（大引だけ）を更新して書き、payload に載せる phase を返す。"""
    long = _read(LONG_PATH, {}) or {}
    note = update_long(long, payload, slot, fetch_ohlc, fetch_close, today)
    _write(LONG_PATH, long)
    print(f"    長い日足: {note}（日経 {len(long.get('n225') or [])}日）")
    flows = _read(FLOWS_PATH, {}) or {}
    if fetch_flows and slot == "taibike":
        added = fetch_flows(flows)
        if added:
            flows["updated_at"] = today
            _write(FLOWS_PATH, flows)
            print(f"    JPX の需給: {added}")
    log = _read(LOG_PATH, []) or []
    out, log = build(slot, today, payload, long, flows, log)
    _write(LOG_PATH, log)
    return out


def fetch_flows_jpx(flows: dict) -> str:
    """JPX から、まだ持っていない週の信用残・投資部門別を足す（例外を投げない）。"""
    from .sources import jpx
    msgs = []
    mg = flows.setdefault("margin", [])
    try:
        new = jpx.margin_weeks({m["date"] for m in mg})
    except Exception as e:                                   # noqa: BLE001  収集は止めない
        new, _ = [], print(f"    ⚠️ 信用残（JPX）: {e}")
    if new:
        mg.extend(new)
        mg.sort(key=lambda m: m["date"])
        msgs.append(f"信用残 +{len(new)}週（{new[0]['date']}〜）")
    inv = flows.setdefault("investors", [])
    try:
        newi = jpx.investor_weeks({r["end"] for r in inv})
    except Exception as e:                                   # noqa: BLE001
        newi, _ = [], print(f"    ⚠️ 投資部門別（JPX）: {e}")
    if newi:
        inv.extend(newi)
        inv.sort(key=lambda r: r["end"])
        del inv[:-104]
        msgs.append(f"投資部門別 +{len(newi)}週")
    return "・".join(msgs)
