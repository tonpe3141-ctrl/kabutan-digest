"""同業の急騰・急落と、動かなかった仲間（連想の検証。DESIGN.md 25章）。

「同じテーマの銘柄が決算や材料で窓を開けて急騰した日に、まだ動いていない仲間は、あとから追いつくのか」を
日足だけで確かめ、今日その形に当たる銘柄を並べる。

  急騰（急落）… その日の市場（全銘柄の前日比の中央値）との差が +5% 以上（−5% 以下）、出来高が直前20日平均の2倍以上、
                売買代金の20日平均が30億円以上（押し目買いと同じ下限）
  仲間     … 同じグループ（swing.peer_groups: テーマ辞書の主テーマ、無ければ日経の業種）の、売買代金30億円以上の銘柄
  動かなかった … その日の市場との差が +1% 未満（急落なら −1% より上）。+3% 以上は「一緒に動いた」

4年の四本値（2022-09〜2026-10・423銘柄。この verify をそのまま当てた。前半〜2024-08・後半。閾値は先に決めて動かさない）で
測った、先の市場との差の平均（前半／後半）:

  急騰の日に動かなかった仲間  5日後 +0.42／+1.10%（同じ日の全銘柄 +0.53／+0.60%）、20日後 +2.06／+2.65%（+1.97／+2.40%）
  一緒に動いた仲間            5日後 +0.10／+1.12%、20日後 +1.04／+5.15%
  急騰した銘柄そのもの        5日後 +0.13／+0.31%、20日後 +2.22／+2.37%
  研究用の再現で閾値を3%・8%に変えても、前半は差が無く、後半だけ +0.3〜+0.6pt 上だった

前半と後半で向きがそろわないので、「出遅れた仲間は追いつく」とは言えない。だから注文・並べ方には使わず、
材料の把握（どこに連想が向かいうるか）と、毎日の数え直し（verify）を並べて出すだけにする。
平均が全銘柄より上なのは、売買代金30億円以上の大型ほど中央値より上に出やすい（今の採用銘柄だけで測る生存者の偏りも）ため。

すべて純関数。入力は ohlc.json（dates と stocks: {コード: [[始,高,安,終,出来高(100株)] か null, ...]}）。
"""
from statistics import mean

EV = 5.0            # 急騰・急落とみなす市場との差（%）
VR = 2.0            # 出来高が直前20日平均の何倍以上か
LIQ = 30.0          # 売買代金の20日平均（億円）の下限。押し目買い（swing.LIQ_MIN）と同じ
LAG = 1.0           # 仲間が「動かなかった」とみなす市場との差（%）
FOL = 3.0           # 仲間が「一緒に動いた」とみなす市場との差（%）
HS = (1, 5, 20)     # 先の何営業日で測るか
WARM = 30           # 指標が出る最初の日
UNIT = 100          # ohlc の出来高は 100株単位
PEERS_MAX = 5       # 今日の急騰に並べる仲間の数
EVENTS_MAX = 8      # 今日の急騰・急落の数

RULE_TEXT = (f"市場との差 ±{EV:g}% 以上・出来高が20日平均の{VR:g}倍以上・売買代金{LIQ:g}億円以上の銘柄が出た日に、"
             f"同じテーマ（業種）の仲間で市場との差が {LAG:g}% 未満だった銘柄")


def _series(stocks: dict, n: int):
    """銘柄ごとに、前日比（%）・売買代金の20日平均（億円）・出来高の直前20日平均に対する倍率。"""
    out = {}
    for code, s in stocks.items():
        d1 = [None] * n
        tv = [0.0] * n
        vr = [0.0] * n
        vals, vols = [], []
        for t in range(n):
            b = s[t] if t < len(s) else None
            if b and t and s[t - 1] and s[t - 1][3] and b[3]:
                x = (b[3] / s[t - 1][3] - 1) * 100
                d1[t] = x if abs(x) < 50 else None          # 分割の取りこぼしは捨てる
            prev = [v for v in vols[-20:] if v]
            if b and len(prev) >= 15:
                vr[t] = (b[4] or 0) / mean(prev) if mean(prev) else 0.0
            vals.append(b[3] * (b[4] or 0) * UNIT / 1e8 if b and b[3] else None)
            vols.append(b[4] if b else None)
            win = [v for v in vals[-20:] if v is not None]
            tv[t] = mean(win) if len(win) >= 15 else 0.0
        out[code] = (d1, tv, vr)
    return out


def market(ser: dict, n: int) -> list:
    out = [0.0] * n
    for t in range(1, n):
        xs = sorted(d1[t] for d1, _, _ in ser.values() if d1[t] is not None)
        if len(xs) >= 20:
            k = len(xs)
            out[t] = xs[k // 2] if k % 2 else (xs[k // 2 - 1] + xs[k // 2]) / 2
    return out


def _fwd(stocks: dict, code: str, t: int, h: int, mk: list, n: int):
    s = stocks[code]
    if t + h >= n or not s[t] or not s[t + h] or not s[t][3] or not s[t + h][3]:
        return None
    m = 1.0
    for k in range(t + 1, t + h + 1):
        m *= 1 + mk[k] / 100
    return (s[t + h][3] / s[t][3] - 1) * 100 - (m - 1) * 100


def events_on(t: int, ser: dict, groups: dict, mk: list) -> dict:
    """t 日目の急騰・急落（グループ → [(コード, 市場との差, 出来高倍率)]）と、流動性のある銘柄。"""
    up, down, liq = {}, {}, set()
    for code, (d1, tv, vr) in ser.items():
        if d1[t] is None or tv[t] < LIQ:
            continue
        liq.add(code)
        g = groups.get(code)
        ex = d1[t] - mk[t]
        if g and vr[t] >= VR:
            if ex >= EV:
                up.setdefault(g, []).append((code, ex, vr[t]))
            elif ex <= -EV:
                down.setdefault(g, []).append((code, ex, vr[t]))
    return {"up": up, "down": down, "liq": liq}


def _agg(xs: list):
    if len(xs) < 20:
        return {"n": len(xs)}
    return {"n": len(xs), "avg": round(mean(xs), 2), "pos": round(100 * sum(1 for x in xs if x > 0) / len(xs))}


def verify(dates: list, stocks: dict, groups: dict, ser: dict | None = None, mk: list | None = None) -> dict | None:
    """急騰の日に動かなかった仲間・一緒に動いた仲間・急騰した銘柄の、先の市場との差。比べる相手は同じ日の流動性のある全銘柄。

    先読みしない（形は t 日目の引けで決め、結果は t+h 日目の引け）。前半・後半に分ける。"""
    n = len(dates)
    if n < WARM + 60:
        return None
    ser = ser or _series(stocks, n)
    mk = mk or market(ser, n)
    mid = n // 2
    keys = ("base", "lag", "fol", "ev")
    acc = {k: [[[] for _ in HS] for _ in (0, 1)] for k in keys}
    n_ev = [0, 0]
    for t in range(WARM, n - 1):
        e = events_on(t, ser, groups, mk)
        if not e["up"]:
            continue
        half = 0 if t < mid else 1
        n_ev[half] += sum(len(v) for v in e["up"].values())
        ev_codes = {c for v in e["up"].values() for c, _, _ in v}
        for code in e["liq"]:
            ex = ser[code][0][t] - mk[t]
            g = groups.get(code)
            if code in ev_codes:
                kind = "ev"
            elif g in e["up"]:
                kind = "lag" if ex < LAG else "fol" if ex >= FOL else None
            else:
                kind = None
            for j, h in enumerate(HS):
                v = _fwd(stocks, code, t, h, mk, n)
                if v is None:
                    continue
                acc["base"][half][j].append(v)
                if kind:
                    acc[kind][half][j].append(v)
    return {"from": dates[WARM], "to": dates[n - 2], "split": dates[mid], "hs": list(HS), "events": n_ev,
            **{k: [[_agg(x) for x in acc[k][half]] for half in (0, 1)] for k in keys}}


def today(dates: list, stocks: dict, groups: dict, names: dict, ser: dict | None = None, mk: list | None = None) -> list[dict]:
    """最新の日の急騰・急落と、同じグループで動かなかった仲間（売買代金の多い順）。"""
    n = len(dates)
    if n < WARM + 1:
        return []
    ser = ser or _series(stocks, n)
    mk = mk or market(ser, n)
    t = n - 1
    e = events_on(t, ser, groups, mk)
    rows = []
    for kind, evs in (("up", e["up"]), ("down", e["down"])):
        for g, lst in evs.items():
            lst.sort(key=lambda x: -abs(x[1]))
            mates = []
            for code in e["liq"]:
                if groups.get(code) != g or any(code == c for c, _, _ in lst):
                    continue
                ex = ser[code][0][t] - mk[t]
                if (kind == "up" and ex < LAG) or (kind == "down" and ex > -LAG):
                    mates.append({"code": code, "name": names.get(code) or code, "d1": round(ser[code][0][t], 1),
                                  "ex": round(ex, 1), "tv": round(ser[code][1][t])})
            mates.sort(key=lambda x: -x["tv"])
            rows.append({"kind": kind, "g": g.replace("（業種）", ""), "asof": dates[t], "mkt": round(mk[t], 2),
                         "ev": [{"code": c, "name": names.get(c) or c, "d1": round(ser[c][0][t], 1), "ex": round(x, 1),
                                 "vr": round(v, 1)} for c, x, v in lst[:3]],
                         "mates": mates[:PEERS_MAX], "n_mates": len(mates)})
    rows.sort(key=lambda r: (r["kind"] != "up", -max(abs(x["ex"]) for x in r["ev"])))
    return rows[:EVENTS_MAX]


def block(dates: list, stocks: dict, groups: dict, names: dict) -> dict | None:
    """thermo.json の spill（規則・今日・検証）。"""
    n = len(dates)
    if n < WARM + 1:
        return None
    ser = _series(stocks, n)
    mk = market(ser, n)
    return {"rule": RULE_TEXT, "ev": EV, "vr": VR, "liq": LIQ, "lag": LAG, "fol": FOL,
            "today": today(dates, stocks, groups, names, ser, mk), "verify": verify(dates, stocks, groups, ser, mk)}
