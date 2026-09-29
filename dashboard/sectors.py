"""業種の強弱: どの業種に追い風が吹いていて、どの業種が負けているか（DESIGN.md 17章）。

業種 = 押し目買いの「業種の中の位置」と同じグループ（swing.peer_groups: テーマ辞書の主テーマ、無ければ日経の業種。
4銘柄以上）。業種の値動きは構成銘柄の等ウェイト（日次騰落の平均を積み上げる）、「市場」は日足キャッシュの全銘柄の等ウェイト。

  強さ = 次の4つの、その日の全業種の中での順位（0〜1）の平均。値動きの結果だけで決まる（ニュースや見立てを混ぜない）
           市場との差（60日）・市場との差（120日）・業種の200日線からの位置・50日線より上にある構成銘柄の割合
  勢い = 市場との差（10日）の順位
  4象限 = 強さ・勢いが全業種の真ん中より上か下か
           先行（強い・勢いあり）／一服（強い・勢いなし）／出遅れ（弱い・勢いなし）／改善（弱い・勢いあり）

4年の検証（研究用の再現。2023-07〜2026-09、前半 2023-24 で決めて後半 2025-26 で確かめた）:
  強さの上位1/5 の業種は、次の20営業日に市場を平均 +1.2%／+2.2%（前半／後半）上回り、下位1/5 は −0.4%／−1.1%。
  ただし業種1つ1つが市場に勝った割合は 53%／52% と5割強で、平均は大きく勝つ業種に引っ張られている。
  「改善」（弱い業種の戻り）は次の20日も市場に負けた（−0.4%／−0.7%）。短い期間（5日・10日）の強さだけでは先は読めない。
  日経の業種（225銘柄を業種で束ねたもの）では、同じ物差しの効き目がはっきりしなかった。
注文（swing.py）の条件にも並べ方にも使わない。押し目買いの注文は強い業種の中で成績が良く、弱い業種（下位1/5）で悪かったが、
並べ方や見送りに入れた口座の再現は、後半（確かめる期間）で良くならなかった。

ここは純関数だけ。入出力は thermo_run.py。
"""
from collections import Counter
from statistics import mean

RS_MID, RS_LONG = 60, 120        # 強さに使う市場との差の日数
MOM_N = 10                       # 勢いに使う市場との差の日数
BR_MA = 50                       # 構成銘柄の何日線より上かを数える
TREND_MA = 200                   # 業種の値動きの長い移動平均
FLOW_S, FLOW_L = 5, 60           # 売買代金の増え方（直近5日の平均 ÷ 60日の平均、市場と比べる）
TOP, BOTTOM = 0.8, 0.2           # 強さの順位がこれより上＝上位1/5、これ以下＝下位1/5
TRAIL = 10                       # 4象限の図に描く軌跡の日数
V_STEP, V_H = 5, 20              # 検証: 5営業日おきに、次の20営業日の市場との差を測る
MAX_DAY = 50.0                   # 1日の騰落（%）がこれを超える値は分割の取りこぼしとみなして捨てる
MIN_GROUPS = 8                   # 業種がこれ未満の日は順位を付けない

QUADS = {"lead": "先行", "fade": "一服", "lag": "出遅れ", "turn": "改善"}
TIERS = ((TOP, "strong", "強い"), (0.6, "up", "やや強い"), (0.4, "mid", "中位"),
         (BOTTOM, "down", "やや弱い"), (-1.0, "weak", "弱い"))

# 日経の業種 → 米国市場からの連想（config.SECTOR_LINKS の業種名。寄り前の sector_outlook と突き合わせる）
LINK_OF = {
    "電気機器": "電気機器（半導体・電子部品）", "精密機器": "精密機器", "通信": "情報・通信業", "サービス": "情報・通信業",
    "自動車": "輸送用機器（自動車）", "機械": "機械", "銀行": "銀行業", "保険": "保険業", "証券": "証券・商品先物取引業",
    "石油": "鉱業・石油・石炭製品", "鉱業": "鉱業・石油・石炭製品", "商社": "卸売業（商社）", "鉄鋼": "鉄鋼・非鉄金属",
    "非鉄・金属": "鉄鋼・非鉄金属", "海運": "海運業", "医薬品": "医薬品", "不動産": "不動産業", "建設": "建設業",
    "電力": "電気・ガス業", "ガス": "電気・ガス業", "食品": "食料品", "小売業": "小売業", "陸運": "陸運業",
    "鉄道・バス": "陸運業",
}


def r1(v):
    return None if v is None else round(v, 1)


def pct_rank(vals: dict) -> dict:
    """{名前: 値} → {名前: 順位（0〜1、大きいほど上。同じ値は平均の順位）}。pandas の rank(pct=True) と同じ。"""
    items = sorted(vals.items(), key=lambda kv: kv[1])
    n = len(items)
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        for k in range(i, j + 1):
            out[items[k][0]] = ((i + j) / 2 + 1) / n
        i = j + 1
    return out


def tier_of(k) -> str | None:
    if k is None:
        return None
    for lo, key, _ in TIERS:
        if k > lo:
            return key
    return "weak"


def quad_of(k, m) -> str | None:
    if k is None or m is None:
        return None
    if k > 0.5:
        return "lead" if m > 0.5 else "fade"
    return "turn" if m > 0.5 else "lag"


# ==================== 系列 ====================


def _returns(closes: list) -> list:
    out = [None] * len(closes)
    for t in range(1, len(closes)):
        a, b = closes[t - 1], closes[t]
        if a and b:
            x = (b / a - 1) * 100
            out[t] = x if abs(x) <= MAX_DAY else None
    return out


def ew_index(rets: list[list]) -> list[float]:
    """等ウェイトの指数（初日=1）。その日に騰落がそろった銘柄の平均で積み上げる。"""
    n = len(rets[0]) if rets else 0
    lvl = [1.0] * n
    for t in range(1, n):
        xs = [r[t] for r in rets if r[t] is not None]
        lvl[t] = lvl[t - 1] * (1 + mean(xs) / 100) if xs else lvl[t - 1]
    return lvl


def _ma_above(closes: list, n: int = BR_MA) -> list:
    """各日に終値が n 日線より上か（True/False、線が引けなければ None）。n 本のうち9割以上そろった日だけ。"""
    out = [None] * len(closes)
    for t in range(n - 1, len(closes)):
        c = closes[t]
        if c is None:
            continue
        w = [x for x in closes[t - n + 1:t + 1] if x is not None]
        if len(w) >= n * 0.9:
            out[t] = c > sum(w) / len(w)
    return out


def _chg(xs: list, t: int, n: int):
    if t < n or not xs[t - n]:
        return None
    return (xs[t] / xs[t - n] - 1) * 100


class Panel:
    """業種ごとの値動き（指数・市場との比）と、日ごとの強さ・勢い。"""

    def __init__(self, dates: list[str], closes: dict[str, list], groups: dict[str, str]):
        self.dates = dates
        self.n = len(dates)
        rets = {c: _returns(a) for c, a in closes.items()}
        self.uni = ew_index(list(rets.values()))
        self.members: dict[str, list[str]] = {}
        for c, g in groups.items():
            if c in closes:
                self.members.setdefault(g, []).append(c)
        above = {c: _ma_above(closes[c]) for cs in self.members.values() for c in cs}
        self.idx, self.rel, self.br = {}, {}, {}
        for g, cs in self.members.items():
            lvl = ew_index([rets[c] for c in cs])
            self.idx[g] = lvl
            self.rel[g] = [a / b for a, b in zip(lvl, self.uni)]
            br = [None] * self.n
            for t in range(self.n):
                xs = [above[c][t] for c in cs if above[c][t] is not None]
                if xs:
                    br[t] = sum(xs) / len(xs) * 100
            self.br[g] = br
        self._at: dict[int, dict] = {}

    def feats(self, g: str, t: int) -> dict:
        idx, rel = self.idx[g], self.rel[g]
        m200 = sum(idx[t - TREND_MA + 1:t + 1]) / TREND_MA if t >= TREND_MA - 1 else None
        return {"rs10": _chg(rel, t, MOM_N), "rs60": _chg(rel, t, RS_MID), "rs120": _chg(rel, t, RS_LONG),
                "ma200": (idx[t] / m200 - 1) * 100 if m200 else None, "br50": self.br[g][t]}

    def at(self, t: int) -> dict[str, dict]:
        """t 日目の引けまでで決まる、業種ごとの強さ（score・k）と勢い（m）。そろわない日は空。"""
        if t in self._at:
            return self._at[t]
        fs = {g: self.feats(g, t) for g in self.members}
        ok = {g: f for g, f in fs.items() if all(f[k] is not None for k in ("rs10", "rs60", "rs120", "ma200", "br50"))}
        out = {}
        if len(ok) >= MIN_GROUPS:
            parts = [pct_rank({g: f[k] for g, f in ok.items()}) for k in ("rs60", "rs120", "ma200", "br50")]
            score = {g: mean(p[g] for p in parts) for g in ok}
            k = pct_rank(score)
            m = pct_rank({g: f["rs10"] for g, f in ok.items()})
            order = sorted(ok, key=lambda g: (-score[g], g))
            for i, g in enumerate(order):
                out[g] = {"score": score[g], "k": k[g], "m": m[g], "rank": i + 1, **ok[g]}
        self._at[t] = out
        return out

    def fwd(self, g: str, t: int, h: int = V_H):
        """t 日目の引けで見て、翌営業日の引けから h 日の市場との差（%）。"""
        rel = self.rel[g]
        if t + 1 + h >= self.n:
            return None
        return (rel[t + 1 + h] / rel[t + 1] - 1) * 100


# ==================== 今日の一覧 ====================


def _flow(closes: dict, vols: dict, codes: list[str], t: int):
    """直近5日の売買代金の平均 ÷ 60日の平均（代金が取れた銘柄の合計）。"""
    s5 = s60 = 0.0
    for c in codes:
        cl, vo = closes.get(c) or [], vols.get(c) or []
        w = [(cl[k] or 0) * (vo[k] or 0) for k in range(max(0, t - FLOW_L + 1), t + 1)]
        if len(w) < FLOW_L:
            continue
        s5 += sum(w[-FLOW_S:]) / FLOW_S
        s60 += sum(w) / FLOW_L
    return s5 / s60 if s60 > 0 else None


def board(dates: list[str], closes: dict[str, list], vols: dict[str, list], groups: dict[str, str],
          sector_of: dict | None = None, events: list[dict] | None = None, panel: Panel | None = None) -> dict | None:
    """最新の日の業種の強弱（強い順）。closes / vols は dates と同じ長さ（取れない日は None）。

    events（業績修正などの開示。code と dir）を渡すと、業種ごとに上方／下方の件数を数える。"""
    p = panel or Panel(dates, closes, groups)
    t = p.n - 1
    now = p.at(t) if t >= 0 else {}
    if not now:
        return None
    sector_of = sector_of or {}
    ev: dict[str, list[int]] = {}
    for e in events or []:
        g = groups.get(e.get("code"))
        if g and e.get("dir") in ("up", "down"):
            ev.setdefault(g, [0, 0])[0 if e["dir"] == "up" else 1] += 1
    uflow = _flow(closes, vols, list(closes), t)
    back5, back20 = p.at(t - 5) if t >= 5 else {}, p.at(t - 20) if t >= 20 else {}
    trail_t = [t - k for k in range(TRAIL - 1, -1, -1) if t - k >= 0]
    trail_at = [p.at(x) for x in trail_t]
    rows = []
    for g, s in sorted(now.items(), key=lambda kv: kv[1]["rank"]):
        cs = p.members[g]
        idx = p.idx[g]
        secs = Counter(sector_of[c] for c in cs if sector_of.get(c))
        dom = secs.most_common(1)[0][0] if secs else None
        fl = _flow(closes, vols, cs, t)
        mem = []
        for c in cs:
            cl = closes[c]
            r20, r60 = _chg(cl, t, 20) if cl[t] else None, _chg(cl, t, 60) if cl[t] else None
            if r20 is None:
                continue
            mem.append({"code": c, "r20": r1(r20), "r60": r1(r60)})
        mem.sort(key=lambda x: (-x["r20"], x["code"]))
        rows.append({
            "g": g, "n": len(cs), "rank": s["rank"], "score": round(s["score"] * 100), "k": round(s["k"], 3),
            "mom": round(s["m"] * 100), "tier": tier_of(s["k"]), "quad": quad_of(s["k"], s["m"]),
            "d1": r1(_chg(idx, t, 1)), "r5": r1(_chg(idx, t, 5)), "r20": r1(_chg(idx, t, 20)), "r60": r1(_chg(idx, t, 60)),
            "rs5": r1(_chg(p.rel[g], t, 5)), "rs10": r1(s["rs10"]), "rs20": r1(_chg(p.rel[g], t, 20)),
            "rs60": r1(s["rs60"]), "rs120": r1(s["rs120"]), "ma200": r1(s["ma200"]), "br50": round(s["br50"]),
            "flow": round(fl / uflow, 2) if fl and uflow else None,
            "rank5": (back5.get(g) or {}).get("rank"), "rank20": (back20.get(g) or {}).get("rank"),
            "trail": [[round(a[g]["score"] * 100), round(a[g]["m"] * 100)] if g in a else None for a in trail_at],
            "sector": dom, "link": LINK_OF.get(dom), "rev": ev.get(g),
            "members": mem,
        })
    u = p.uni
    return {"asof": dates[t], "n": len(rows), "rows": rows,
            "market": {"r5": r1(_chg(u, t, 5)), "r20": r1(_chg(u, t, 20)), "r60": r1(_chg(u, t, 60)),
                       "stocks": len(closes)},
            "quads": QUADS, "tiers": {key: label for _, key, label in TIERS},
            "rules": {"rs": [RS_MID, RS_LONG], "mom": MOM_N, "br_ma": BR_MA, "trend_ma": TREND_MA,
                      "top": TOP, "bottom": BOTTOM, "flow": [FLOW_S, FLOW_L]}}


# ==================== 検証 ====================


def _avg(xs):
    return r1(mean(xs)) if xs else None


def _pos(xs):
    return round(sum(1 for x in xs if x > 0) / len(xs) * 100) if xs else None


def verify(panel: Panel, step: int = V_STEP, h: int = V_H) -> dict | None:
    """日足キャッシュの期間で、step 営業日おきに強さの順位を付け、次の h 営業日の市場との差を測る（先読みしない）。

    上位1/5・下位1/5・全体の平均と、市場に勝った割合。上位の平均が下位の平均を上回った日の割合。4象限ごとの平均。"""
    p = panel
    top, bot, allx = [], [], []
    quad: dict[str, list[float]] = {q: [] for q in QUADS}
    beat = []
    used = []
    for t in range(0, p.n - 1 - h, step):
        s = p.at(t)
        if not s:
            continue
        tt, bb = [], []
        for g, x in s.items():
            f = p.fwd(g, t, h)
            if f is None:
                continue
            allx.append(f)
            quad[quad_of(x["k"], x["m"])].append(f)
            if x["k"] > TOP:
                tt.append(f)
            elif x["k"] <= BOTTOM:
                bb.append(f)
        if tt and bb:
            top += tt
            bot += bb
            beat.append(mean(tt) > mean(bb))
            used.append(p.dates[t])
    if not used:
        return None
    return {"from": used[0], "to": used[-1], "dates": len(used), "h": h, "step": step,
            "top": {"avg": _avg(top), "pos": _pos(top), "n": len(top)},
            "bottom": {"avg": _avg(bot), "pos": _pos(bot), "n": len(bot)},
            "all": {"avg": _avg(allx), "pos": _pos(allx), "n": len(allx)},
            "beat": round(sum(beat) / len(beat) * 100),
            "quad": {q: {"avg": _avg(xs), "pos": _pos(xs), "n": len(xs)} for q, xs in quad.items()},
            "note": f"{step}営業日おきに強さの順位を付け、翌営業日の引けから{h}営業日の、業種の等ウェイトの値動きと"
                    "市場（日足キャッシュの全銘柄の等ウェイト）の差。業種は今のテーマ辞書で束ねている（生存者の偏りを含む）。"}
