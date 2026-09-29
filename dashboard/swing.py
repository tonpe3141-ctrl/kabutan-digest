"""短期の押し目買い（売買タブの中心になる、1つの売買ルール）。

作戦（温度計の型・作戦ボード）と発掘（台帳）が別々に「買う候補」を出していて、どれで入るのかが分かりにくかった。
2022-10〜2026-09 の四本値（約380銘柄）で、場中の安値での損切り・窓開け・売買コストまで再現して検証し、
勝率がはっきり高く、前半（2023-24）で決めて後半（2025-26）でも崩れなかったルールだけを残した（DESIGN.md 13章）。

  入口   : 上昇トレンド（終値 > 200日線、かつ 50日線 > 200日線）の銘柄が、2日RSI < 10 まで短く押した日。
           20日平均の売買代金が10億円以上（指値が約定しやすく、1銘柄の事情で振れにくい）、株価300円以上
           （呼値1円が値段の0.3%を超える低位株は、指値と損切りの刻みが粗すぎる）
  買い   : 翌営業日だけ有効の指値 = その日の終値 − 0.5×ATR(14)（押した日の、さらに下で拾う）
  売り   : 翌日から毎日、指値 = 直近4日の終値の平均（「終値が5日線を上回ったら売る」を前もって置ける注文にしたもの）。
           ただし約定値 +0.2% より下には置かない（含み損のうちは売り指値で損を確定させず、戻りを待つ。DESIGN.md 15章）
  損切り : 約定値 − 3×ATR(14)。場中に割ったらその価格で、寄りで割っていたら寄りで
  期限   : 10営業日たっても売れなければ引けで売る
  業種   : 同じ業種（テーマ辞書の主テーマ、無ければ日経の業種）の20日騰落（自分を除いた中央値）と比べる。
           業種が +3% 以上上げているのに、自分も業種並み（差が −5pt より上）の押しは注文を出さない（見送り）。
           業種ぐるみの押し（業種 −5% 以下）と出遅れの押し（業種は上げ、自分は −5pt 以上遅れ）が成績の良い形（DESIGN.md 14章）
  並べ方 : 25日線からの下離れ ＋ 業種より遅れている分（マイナスだけ）が大きい順。1日5銘柄まで・同じ業種は2銘柄まで
  株数   : 1件の注文の金額 = 資金の10%（金額をそろえる）。保有中に使っていない資金の範囲で、並べ方の順に置く。
           損切りまでの幅から株数を決める（1回の損 = 資金の1%）より、同じ張り具合で口座が速く増えた（DESIGN.md 16章）

すべて純関数。同じ日足からは同じ注文が出る（tests で固定）。検証（verify）と、アプリが出した注文の実績（paper_update）は
同じ simulate() で測る。口座の再現（account）も同じ simulate() の結果を、本番どおりの置き方（引けで決めて翌日だけ有効、
約定しなければ資金はその日遊ぶ）で並べたもの。「予測」ではなく、過去に同じ形で同じ注文を置いたらこうだった、という規則。
"""
import math
from collections import Counter
from statistics import mean, median

RSI_N = 2
RSI_MAX = 10.0          # 2日RSI がこれ未満で入口
MA_LONG = 200
MA_MID = 50
LIQ_MIN = 10.0          # 20日平均の売買代金（億円）
MIN_PRICE = 300.0       # 株価の下限（円）
ATR_N = 14
ENTRY_ATR = 0.5         # 買いの指値 = 終値 − 0.5×ATR
STOP_ATR = 3.0          # 損切り = 約定値 − 3×ATR
EXIT_N = 4              # 売りの指値 = 直近4日の終値の平均
FLOOR = 0.2             # 売りの指値の下限 = 約定値 ×（1 + 0.2%）。往復の売買コスト（0.1%）を引いてもプラスで終わる値
MAX_HOLD = 10           # 営業日
MAX_ORDERS = 5          # 1日に出す注文の数
SECTOR_CAP = 2          # 同じ業種は2銘柄まで
SLOT_PCT = 10.0         # 1件の注文の金額（資金の %）。1日5件までなので、1日に新しく入るのは資金の半分まで
COST = 0.1              # 往復の売買コスト（%）
WARMUP = MA_LONG        # 200日線が引けるまでは判定しない
# 業種の中での位置（DESIGN.md 14章。2023-24 の成績だけで決め、2025-26 で確かめた）
PEER_N = 20             # 業種と比べる騰落の日数
PEER_MIN = 4            # グループの銘柄数（ユニバースの中）の下限。少ないと1銘柄で中央値が振れる
PEER_OTHERS = 3         # 自分を除いて、この数以上の銘柄の騰落がそろった日だけ比べる
PEER_DIP = -5.0         # 業種の20日騰落（自分を除いた中央値）がこれ以下 → 業種ぐるみの押し
PEER_UP = 3.0           # これ以上 → 業種が上げている
PEER_LAG = -5.0         # 業種との差がこれ以下 → 業種より下げが大きい／出遅れている
PEER_CLASSES = {
    "dip_lag": "業種ぐるみの押し・業種の中でも下げが大きい",
    "dip": "業種ぐるみの押し",
    "lag": "出遅れの押し",
    "plain": "ふつうの押し",
    "none": "比べる業種なし",
    "hot": "業種の上げに沿った押し（見送り）",
}
PEER_SKIP = ("hot",)    # 注文を出さない形
RULES = {"rsi_n": RSI_N, "rsi_max": RSI_MAX, "ma_long": MA_LONG, "ma_mid": MA_MID, "liq_min": LIQ_MIN,
         "min_price": MIN_PRICE,
         "entry_atr": ENTRY_ATR, "stop_atr": STOP_ATR, "exit_n": EXIT_N, "floor": FLOOR, "max_hold": MAX_HOLD,
         "max_orders": MAX_ORDERS, "sector_cap": SECTOR_CAP, "slot_pct": SLOT_PCT, "cost": COST,
         "peer_n": PEER_N, "peer_dip": PEER_DIP, "peer_up": PEER_UP, "peer_lag": PEER_LAG}


def r1(v):
    return None if v is None else round(v, 1)


def r2(v):
    return None if v is None else round(v, 2)


# ==================== 呼値 ====================
# 一般の銘柄の呼値の単位。TOPIX500 採用銘柄はもっと細かいが、粗い単位の倍数はどちらでも有効な価格になる。
TICKS = ((3000, 1), (5000, 5), (30000, 10), (50000, 50), (300000, 100), (500000, 500), (3000000, 1000))


def tick(p: float) -> int:
    for lim, t in TICKS:
        if p <= lim:
            return t
    return 5000


def tick_down(p):
    if p is None:
        return None
    t = tick(p)
    return math.floor(p / t + 1e-9) * t


def tick_up(p):
    if p is None:
        return None
    t = tick(p)
    return math.ceil(p / t - 1e-9) * t


# ==================== 日足の扱い ====================


def compact(dates: list[str], rows: list) -> list[tuple]:
    """キャッシュの行（dates と同じ長さ、[o, h, l, c, 出来高(100株)] か null）を
    [(日付, 始値, 高値, 安値, 終値, 出来高(株))] の取れた日だけにする。"""
    out = []
    for d, r in zip(dates, rows or []):
        if not r or r[3] is None or r[3] <= 0:
            continue
        o, h, l, c = (float(x) if x is not None else float(r[3]) for x in r[:4])
        out.append((d, o, max(h, o, c), min(l, o, c), c, (r[4] or 0) * 100))
    return out


def indicators(bars: list[tuple]) -> dict:
    """日ごとの指標（列ごとのリスト）。i 日目の値は i 日目の引けまでの値だけで決まる（先読みしない）。"""
    n = len(bars)
    d = [b[0] for b in bars]
    o = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    c = [b[4] for b in bars]
    v = [b[5] for b in bars]
    pre = [0.0]
    for x in c:
        pre.append(pre[-1] + x)
    tr = [None] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, n)]
    atr = [None] * n
    for i in range(ATR_N, n):
        atr[i] = sum(tr[i - ATR_N + 1:i + 1]) / ATR_N
    # Wilder の RSI（平滑化 1/N）。最初の値動きから積み上げる
    a = 1 / RSI_N
    ag = [None] * n
    al = [None] * n
    rsi = [None] * n
    for i in range(1, n):
        g, ls = max(c[i] - c[i - 1], 0.0), max(c[i - 1] - c[i], 0.0)
        if i == 1:
            ag[i], al[i] = g, ls
        else:
            ag[i] = ag[i - 1] + a * (g - ag[i - 1])
            al[i] = al[i - 1] + a * (ls - al[i - 1])
        rsi[i] = (100.0 if ag[i] > 0 else 50.0) if al[i] == 0 else 100 - 100 / (1 + ag[i] / al[i])
    tv = [None] * n
    for i in range(n):
        w = [c[k] * v[k] for k in range(max(0, i - 19), i + 1)]
        if len(w) >= 15:
            tv[i] = sum(w) / len(w) / 1e8
    return {"d": d, "o": o, "h": h, "l": lo, "c": c, "pre": pre, "atr": atr, "ag": ag, "al": al, "rsi": rsi, "tv": tv}


def _ma(ind: dict, i: int, k: int):
    if i + 1 < k:
        return None
    return (ind["pre"][i + 1] - ind["pre"][i + 1 - k]) / k


def is_signal(ind: dict, i: int) -> bool:
    """i 日目の引けで、翌日に買いの指値を置く条件がそろったか。"""
    if i < WARMUP - 1:
        return False
    c, rs, atr, tv = ind["c"][i], ind["rsi"][i], ind["atr"][i], ind["tv"][i]
    m200, m50 = _ma(ind, i, MA_LONG), _ma(ind, i, MA_MID)
    return bool(rs is not None and atr and tv is not None and m200 and m50 and c >= MIN_PRICE
                and c > m200 and m50 > m200 and rs < RSI_MAX and tv >= LIQ_MIN)


def sell_level(ind: dict, j: int, entry: float | None = None):
    """j 日目に置く売りの指値（直近4日＝j−4〜j−1 日目の終値の平均。j 日目の寄り前に決まっている）。

    entry（約定値）を渡すと、約定値 ×（1 + FLOOR%）を下限にする。直近4日の平均が約定値より下にあるうちに
    売り指値で手仕舞うと、戻りの途中で小さな損を確定させることになる（4年の検証で、そうした売りの多くは
    10営業日のうちに約定値の上へ戻っていた。DESIGN.md 15章）。"""
    if j < EXIT_N:
        return None
    g = sum(ind["c"][j - EXIT_N:j]) / EXIT_N
    if entry is not None:
        g = max(g, entry * (1 + FLOOR / 100))
    return g


def trigger_price(ind: dict, i: int):
    """次の営業日の終値がいくら未満なら 2日RSI < 10 になるか（その終値で入口に入る）。

    Wilder の平滑化は1次の漸化式なので、次の終値 p に対する RSI の境目は式で解ける。
    下げる側（p < 終値）: 平均の上げ幅は (1−a) 倍に縮み、平均の下げ幅に a(c−p) が足される。
    """
    if i < 1 or ind["ag"][i] is None:
        return None
    a = 1 / RSI_N
    r = RSI_MAX / (100 - RSI_MAX)
    c, ag, al = ind["c"][i], ind["ag"][i], ind["al"][i]
    if ag - r * al > 0:
        return c - (1 - a) * (ag - r * al) / (r * a)
    return c + (1 - a) * (r * al - ag) / a


def trend_holds_at(ind: dict, i: int, p: float) -> bool:
    """次の終値が p だったときも、上昇トレンド（終値 > 200日線 > …, 50日線 > 200日線）が保たれるか。"""
    if i + 2 < MA_LONG:
        return False
    pre = ind["pre"]
    m200 = (pre[i + 1] - pre[i + 2 - MA_LONG] + p) / MA_LONG
    m50 = (pre[i + 1] - pre[i + 2 - MA_MID] + p) / MA_MID
    return p > m200 and m50 > m200


# ==================== 業種の中での位置 ====================


def peer_groups(codes, theme_stocks: dict, sector_of: dict, min_n: int = PEER_MIN) -> dict[str, str]:
    """銘柄 → 比べるグループ。テーマ辞書の最初のテーマ（主テーマ）、無ければ日経の業種。

    ユニバースに min_n 銘柄以上あるグループだけ使う。テーマの「機械」と日経の業種の「機械」は別のグループ。"""
    raw = {}
    for c in codes:
        th = ((theme_stocks or {}).get(c) or {}).get("themes") or []
        if th:
            raw[c] = th[0]
        elif sector_of.get(c):
            raw[c] = f"{sector_of[c]}（業種）"
    cnt = Counter(raw.values())
    return {c: g for c, g in raw.items() if cnt[g] >= min_n}


def ret_n(ind: dict | None, i: int, n: int = PEER_N):
    """i 日目までの n 営業日の騰落率（%）。"""
    if not ind or i < n or not ind["c"][i - n]:
        return None
    return (ind["c"][i] / ind["c"][i - n] - 1) * 100


def peer_class(g20, rel20) -> str:
    """業種の20日騰落（自分を除いた中央値）と、業種との差から、押しの形を決める。"""
    if g20 is None or rel20 is None:
        return "none"
    if g20 <= PEER_DIP:
        return "dip_lag" if rel20 <= PEER_LAG else "dip"
    if g20 >= PEER_UP:
        return "lag" if rel20 <= PEER_LAG else "hot"
    return "plain"


def peer_context(rets: dict[str, float | None], groups: dict[str, str]) -> dict[str, dict]:
    """同じ日の各銘柄の20日騰落から、業種（自分を除いた中央値）・業種との差・押しの形。

    自分を除くのは、1銘柄の急落がそのまま「業種の下げ」に数えられないようにするため。"""
    by: dict[str, list[str]] = {}
    for c, g in groups.items():
        if rets.get(c) is not None:
            by.setdefault(g, []).append(c)
    out = {}
    for g, cs in by.items():
        for c in cs:
            others = [rets[x] for x in cs if x != c]
            if len(others) < PEER_OTHERS:
                continue
            m = median(others)
            rel = rets[c] - m
            out[c] = {"g": g, "g20": r1(m), "rel20": r1(rel), "r20": r1(rets[c]), "peers": len(others),
                      "cls": peer_class(m, rel)}
    return out


def peer_board(ctx: dict[str, dict], rets: dict[str, float | None], groups: dict[str, str]) -> list[dict]:
    """業種ぐるみで下げている／上げているグループと、その中の銘柄を業種との差の順に（アプリの「業種の中の位置」）。"""
    by: dict[str, list[str]] = {}
    for c, g in groups.items():
        if rets.get(c) is not None:
            by.setdefault(g, []).append(c)
    out = []
    for g, cs in by.items():
        if len(cs) < PEER_OTHERS + 1:
            continue
        m = median(rets[c] for c in cs)
        kind = "dip" if m <= PEER_DIP else "up" if m >= PEER_UP else None
        if not kind:
            continue
        mem = sorted((c for c in cs if c in ctx), key=lambda c: (ctx[c]["rel20"], c))
        out.append({"g": g, "g20": r1(m), "n": len(cs), "kind": kind,
                    "members": [{"code": c, "r20": ctx[c]["r20"], "rel20": ctx[c]["rel20"]} for c in mem]})
    out.sort(key=lambda x: (x["kind"] != "dip", x["g20"] if x["kind"] == "dip" else -x["g20"]))
    return out


# ==================== 1回の売買の再現 ====================


def simulate(ind: dict, i: int, max_hold: int = MAX_HOLD, cost: float = COST) -> dict | None:
    """i 日目の引けで出した注文を、翌日以降の四本値で再現する。

    買い: i+1 日目だけ有効の指値。安値が指値以下なら約定（寄りが指値より下なら寄りで）。
    約定した日も、安値が損切り以下なら損切り（同じ日に両方なら損切りを先に数える＝保守的に）。
    売り指値は直近4日の終値の平均、ただし約定値 +FLOOR% が下限（sell_level）。
    翌日から: 寄りが損切り以下 → 寄りで損切り／寄りが売り指値以上 → 寄りで売り／
              安値が損切り以下 → 損切り／高値が売り指値以上 → 売り指値／max_hold 日目 → 引けで売り。
    まだ結果が出ていなければ、約定前は None、約定後は open=True を返す。
    """
    n = len(ind["c"])
    atr = ind["atr"][i]
    if atr is None:
        return None
    limit = ind["c"][i] - ENTRY_ATR * atr
    j = i + 1
    if j >= n:
        return None
    if ind["l"][j] > limit:
        return {"filled": False, "limit": r2(limit)}
    e = min(ind["o"][j], limit)
    stop = e - STOP_ATR * atr
    res = {"filled": True, "in": ind["d"][j], "entry": r2(e), "stop": r2(stop)}
    x = why = None
    k = j
    if ind["l"][j] <= stop:
        x, why = stop, "stop"
    while x is None and k + 1 < n:
        k += 1
        o, h, lo, c = ind["o"][k], ind["h"][k], ind["l"][k], ind["c"][k]
        g = sell_level(ind, k, e)
        if o <= stop:
            x, why = o, "stop"
        elif g is not None and o >= g:
            x, why = o, "sell"
        elif lo <= stop:
            x, why = stop, "stop"
        elif g is not None and h >= g:
            x, why = g, "sell"
        elif k - j >= max_hold:
            x, why = c, "time"
    if x is None:
        return {**res, "open": True, "days": k - j + 1, "last": ind["c"][k]}
    ret = (x / e - 1) * 100 - cost
    return {**res, "out": ind["d"][k], "exit": r2(x), "why": why, "ret": r2(ret), "days": k - j + 1}


# ==================== 成績 ====================


def summarize(trades: list[dict]) -> dict:
    """約定して結果が出た売買の要約。勝率は売買コスト込みでプラスだった割合。"""
    rs = [t["ret"] for t in trades if t.get("ret") is not None]
    if not rs:
        return {"n": 0}
    win = [x for x in rs if x > 0]
    loss = [x for x in rs if x <= 0]
    why = [t.get("why") for t in trades if t.get("ret") is not None]
    days = [t["days"] for t in trades if t.get("ret") is not None]
    return {"n": len(rs), "win": round(len(win) / len(rs) * 100), "avg": r2(mean(rs)), "med": r2(median(rs)),
            "pf": r2(sum(win) / -sum(loss)) if loss and sum(loss) < 0 else None,
            "avg_win": r2(mean(win)) if win else None, "avg_loss": r2(mean(loss)) if loss else None,
            "worst": r2(min(rs)), "days": r1(mean(days)),
            # どれくらいの期間で終わるか: 約定した日の翌日までに手仕舞った割合と、1回の平均を保有日数で割った値
            "d2": round(sum(1 for x in days if x <= 2) / len(days) * 100),
            "per_day": r2(mean(rs) / mean(days)),
            "stops": round(why.count("stop") / len(rs) * 100), "sells": round(why.count("sell") / len(rs) * 100),
            "times": round(why.count("time") / len(rs) * 100),
            # 勝ちの中身を正直に: +0.5% 以下の小さな勝ち（売り指値の下限で終わった売りが多い）と、−5% 以下の大きな負け
            "small": round(sum(1 for x in rs if 0 < x <= 0.5) / len(rs) * 100),
            "big_loss": round(sum(1 for x in rs if x <= -5) / len(rs) * 100)}


def account(days: list[str], orders_on: dict[str, list[dict]], close_of, sector_of: dict | None = None,
            slot: float = SLOT_PCT, max_orders: int = MAX_ORDERS, cap: int = SECTOR_CAP) -> dict | None:
    """本番どおりに注文を置いた口座の再現（DESIGN.md 16章）。

    days: 営業日（古い順）。orders_on: {引けの日: [{"code", "res"}, ...]}（並べ方の順。res は simulate() の結果）。
    close_of(code, 日付) -> その日の終値（無ければ None）。
    各営業日の引けで: その日に手仕舞った代金を現金に戻し、持っている銘柄を終値で値洗いし、並べ方の順に、保有中でない銘柄・
    同じ業種が cap 未満のものへ、資産の slot% ずつ、取り置いていない現金の範囲で max_orders 件まで注文を置く（翌日だけ有効）。
    翌日: 約定した注文は保有に、約定しなかった注文の資金は現金に戻す（その日は遊ぶ）。
    1回ごとの成績（verify の all）は資金の制約を置かない数え方。口座では、約定しなかった注文の資金が遊ぶ分と、
    保有中で新しい注文を置けない分だけ、1回ごとの平均から想像するより伸びが遅い。どれくらいの期間でプラスになるかは
    月末の資産で見る（m_up: 前の月末より増えた月の割合、q_up: 3か月前より増えていた割合、under: 高値を更新できなかった
    最長の営業日数）。
    """
    sector_of = sector_of or {}
    if len(days) < 2:
        return None
    cash, pos, pending = 1.0, [], []
    eq, util, taken = [], [], []
    for d in days:
        for o, amt in pending:
            r = o["res"]
            if r.get("filled"):
                pos.append({"code": o["code"], "amt": amt, "entry": r["entry"], "out": r.get("out"),
                            "ret": r.get("ret"), "px": r["entry"]})
            else:
                cash += amt
        pending = []
        keep = []
        for p in pos:
            c = close_of(p["code"], d)
            if c:
                p["px"] = c
            if p["out"] is not None and p["out"] <= d:
                cash += p["amt"] * (1 + p["ret"] / 100)
                taken.append(p["ret"])
            else:
                keep.append(p)
        pos = keep
        inv = sum(p["amt"] * p["px"] / p["entry"] for p in pos)
        val = cash + inv
        eq.append((d, val))
        util.append(inv / val if val > 0 else 0.0)
        held = {p["code"] for p in pos}
        per = Counter(sector_of.get(p["code"]) for p in pos if sector_of.get(p["code"]))
        placed = 0
        for o in orders_on.get(d) or []:
            if placed >= max_orders:
                break
            if o["code"] in held or not o.get("res"):
                continue
            s = sector_of.get(o["code"])
            if s and per[s] >= cap:
                continue
            want = val * slot / 100
            amt = min(cash, want)
            if amt < want * 0.3:                   # 現金が1件の3割に満たなければ、その日はもう置かない
                break
            cash -= amt
            pending.append((o, amt))
            held.add(o["code"])
            placed += 1
            if s:
                per[s] += 1
    vals = [x for _, x in eq]
    peak = dd = 0.0
    run = under = 0
    for x in vals:
        if x >= peak:
            peak, run = x, 0
        else:
            run += 1
            under = max(under, run)
        dd = min(dd, x / peak - 1)
    n = len(vals)
    dr = [vals[k] / vals[k - 1] - 1 for k in range(1, n)]
    mu = mean(dr)
    sd = (sum((x - mu) ** 2 for x in dr) / len(dr)) ** 0.5
    month_end = {}
    for d, x in eq:
        month_end[d[:7]] = x
    mv = [vals[0]] + [month_end[m] for m in sorted(month_end)]
    ups = [mv[k] > mv[k - 1] for k in range(1, len(mv))]
    q = [mv[k] > mv[k - 3] for k in range(3, len(mv))]
    step = max(1, n // 80)
    curve = [[d, round(x, 4)] for d, x in eq[::step]]
    if curve[-1][0] != eq[-1][0]:
        curve.append([eq[-1][0], round(eq[-1][1], 4)])
    return {"from": days[0], "to": days[-1], "days": n, "slot": slot, "max_orders": max_orders,
            "cagr": r1(((vals[-1] / vals[0]) ** (245 / n) - 1) * 100) if n >= 60 else None,
            "dd": r1(dd * 100), "sharpe": r2(mu / sd * 245 ** 0.5) if sd > 0 and n >= 60 else None,
            "util": round(mean(util) * 100), "final": round(vals[-1], 4),
            "n": len(taken), "win": round(sum(1 for x in taken if x > 0) / len(taken) * 100) if taken else None,
            "avg": r2(mean(taken)) if taken else None,
            "months": len(ups), "m_up": round(sum(ups) / len(ups) * 100) if ups else None,
            "q_up": round(sum(q) / len(q) * 100) if q else None, "under": under, "curve": curve}


def verify(stocks: dict[str, list[tuple]], recent: int = 120, groups: dict[str, str] | None = None,
           sector_of: dict | None = None) -> dict | None:
    """ユニバース全体で、毎日このルールで注文を置いていたらどうだったかを再現する（銘柄ごと・重ならないように）。

    stocks: {code: compact() の出力}。比べる相手は「同じ銘柄を毎日、翌日の寄りで買って5営業日後の引けで売る」。
    groups（peer_groups の出力）を渡すと、その日の業種の中での位置で押しの形を決め、見送る形（PEER_SKIP）は
    注文に数えず、別に skipped として成績を出す。期間は日足キャッシュの長さ（200日線が引けるようになってから）。
    直近 recent 営業日の分も別に出す。account は、同じ期間に本番どおりの置き方（並べ方の順に1日5件まで・資金の10%ずつ・
    約定しなければ資金はその日遊ぶ）をした口座の再現（sector_of は同じ業種の上限に使う）。
    """
    inds = {code: indicators(bars) for code, bars in stocks.items() if len(bars) > PEER_N}
    rets_on: dict[str, dict[str, float | None]] = {}
    if groups:
        for code, ind in inds.items():
            if code in groups:
                for i in range(PEER_N, len(ind["c"])):
                    rets_on.setdefault(ind["d"][i], {})[code] = ret_n(ind, i)
    ctx_on: dict[str, dict] = {}

    def peer_at(d, code):
        if not groups or code not in groups:
            return None
        if d not in ctx_on:
            ctx_on[d] = peer_context(rets_on.get(d, {}), groups)
        return ctx_on[d].get(code)

    trades, skipped, base = [], [], []
    cal: set[str] = set()
    orders_on: dict[str, list[dict]] = {}          # 口座の再現: 引けの日 → 注文対象（見送りを除く）
    closes: dict[str, dict[str, float]] = {}
    for code, ind in inds.items():
        n = len(ind["c"])
        if n < WARMUP + 5:
            continue
        cal.update(ind["d"][WARMUP - 1:])
        closes[code] = dict(zip(ind["d"], ind["c"]))
        busy = skip_busy = -1
        halted = False                               # 結果の出ていない売買がある銘柄は、1回ごとの成績に数えるのをやめる
        for i in range(WARMUP - 1, n - 1):
            if i + 6 < n:
                base.append((ind["d"][i], (ind["c"][i + 6] / ind["o"][i + 1] - 1) * 100 - COST))
            if not is_signal(ind, i):
                continue
            pc = peer_at(ind["d"][i], code)
            k = pc["cls"] if pc else "none"
            res = simulate(ind, i)
            if k not in PEER_SKIP and res is not None:
                m25 = _ma(ind, i, 25)
                orders_on.setdefault(ind["d"][i], []).append(
                    {"code": code, "res": res,
                     "key": rank_key({"dev25": r1((ind["c"][i] / m25 - 1) * 100) if m25 else None, "peer": pc})})
            # 1回ごとの成績: 同じ銘柄は手仕舞うまで次の注文を数えない
            if halted or i <= busy or (k in PEER_SKIP and i <= skip_busy):
                continue
            if res is None or not res["filled"]:
                continue
            if k in PEER_SKIP:
                if not res.get("open"):
                    skipped.append({"code": code, "sig": ind["d"][i], "peer": k, **res})
                    skip_busy = ind["d"].index(res["out"])
                continue
            if res.get("open"):
                halted = True
                continue
            trades.append({"code": code, "sig": ind["d"][i], "peer": k, **res})
            busy = ind["d"].index(res["out"])
    if not cal:
        return None
    days = sorted(cal)
    for rows in orders_on.values():
        rows.sort(key=lambda o: (o["key"], o["code"]))
    cut = days[-recent] if len(days) > recent else days[0]
    half = days[len(days) // 2]
    b_all = [r for _, r in base]
    out = {
        "from": days[0], "to": days[-1], "universe": len(stocks), "days": len(days),
        "all": summarize(trades),
        "early": {**summarize([t for t in trades if t["in"] < half]), "from": days[0], "to": half},
        "late": {**summarize([t for t in trades if t["in"] >= half]), "from": half, "to": days[-1]},
        "recent": {**summarize([t for t in trades if t["in"] >= cut]), "from": cut},
        "base": {"n": len(b_all), "win": round(sum(1 for x in b_all if x > 0) / len(b_all) * 100) if b_all else None,
                 "avg": r2(mean(b_all)) if b_all else None},
        "note": "四本値で再現: 翌日だけ有効の指値、場中の安値での損切り（寄りで割れていれば寄り）、"
                "毎日の売り指値（直近4日の平均、約定値+0.2%が下限）、"
                "10営業日で手仕舞い、往復0.1%のコスト。同じ銘柄は手仕舞うまで次の注文を数えない。"
                "同時に持つ数の上限は置いていない（1回ごとの成績）。",
        # 本番どおりの置き方をした口座（1回ごとの成績と違い、約定しなかった注文の資金が遊ぶ分まで入る）
        "account": account(days, orders_on, lambda c, d: closes.get(c, {}).get(d), sector_of),
    }
    if groups:
        out["peer"] = {k: summarize([t for t in trades if t["peer"] == k]) for k in PEER_CLASSES if k not in PEER_SKIP}
        out["skipped"] = summarize(skipped)
        out["note"] += "業種の上げに沿った押し（見送り）は注文に数えず、同じ規則で測った成績を別に出す。"
    return out


# ==================== 今日の注文 ====================


def today_row(ind: dict) -> dict | None:
    """最新の日の指標と、このルールでの位置（注文対象・あと何％・トレンド外・商い不足）。"""
    n = len(ind["c"])
    if n < 30:
        return None
    i = n - 1
    c, atr = ind["c"][i], ind["atr"][i]
    m200, m50, m25 = _ma(ind, i, MA_LONG), _ma(ind, i, MA_MID), _ma(ind, i, 25)
    up = bool(m200 and m50 and c > m200 and m50 > m200)
    liq = ind["tv"][i] is not None and ind["tv"][i] >= LIQ_MIN and c >= MIN_PRICE
    sig = is_signal(ind, i)
    trig = trigger_price(ind, i)
    row = {"asof": ind["d"][i], "price": c, "rsi2": r1(ind["rsi"][i]), "atr": r2(atr),
           "atrp": r2(atr / c * 100) if atr else None, "tv": r1(ind["tv"][i]),
           "ma200": r1(m200), "ma50": r1(m50), "dev25": r1((c / m25 - 1) * 100) if m25 else None,
           "r5": r1((c / ind["c"][i - 5] - 1) * 100) if i >= 5 else None,
           "up": up, "liq": liq, "sig": sig,
           # 明日（次の営業日）に置く売りの指値のうち、直近4日の平均の部分（保有中の人向け）。
           # 下限（約定値 +FLOOR%）は約定値しだいなので、アプリが保有中の記録から max を取る
           "sell": r1(sum(ind["c"][i - EXIT_N + 1:i + 1]) / EXIT_N)}
    if sig:
        row["state"] = "signal"
    elif m200 is None:
        row["state"] = "short"                 # 200日線を引くだけの日足が無い
    elif not up:
        row["state"] = "out"
    elif not liq:
        row["state"] = "thin"
    else:
        row["state"] = "wait"
        if trig is not None and trig < c:
            ok = trend_holds_at(ind, i, trig)
            row["trig"] = r1(trig)
            row["to_trig"] = r1((trig / c - 1) * 100)
            row["trig_ok"] = ok                # その価格まで下げても上昇トレンドを保つか
    return row


def order_of(ind: dict) -> dict | None:
    """最新の日の引けで出す注文（次の営業日だけ有効）。"""
    i = len(ind["c"]) - 1
    if not is_signal(ind, i):
        return None
    c, atr = ind["c"][i], ind["atr"][i]
    limit = tick_down(c - ENTRY_ATR * atr)
    stop = tick_down(limit - STOP_ATR * atr)
    # 約定した日の翌日に置く売り指値の目安（約定した日の終値を指値と同じと仮定。下限は指値 +FLOOR%）
    floor = tick_up(limit * (1 + FLOOR / 100))
    sell = max(tick_up((sum(ind["c"][i - EXIT_N + 2:i + 1]) + limit) / EXIT_N), floor)
    return {"asof": ind["d"][i], "close": c, "limit": limit, "to_limit": r1((limit / c - 1) * 100),
            "stop": stop, "stop_pct": r1((stop / limit - 1) * 100), "sell": sell, "floor": floor,
            "sell_pct": r1((sell / limit - 1) * 100), "atr": r2(atr)}


def rank_key(r: dict) -> float:
    """並べ方の値（小さいほど先）。25日線からの下離れ ＋ 業種より遅れている分（マイナスだけ）。"""
    rel = (r.get("peer") or {}).get("rel20")
    dev = r.get("dev25") if r.get("dev25") is not None else 0
    return dev + (min(rel, 0) if rel is not None else 0)


def rank_orders(rows: list[dict], sector_of: dict[str, str | None], limit: int = MAX_ORDERS,
                cap: int = SECTOR_CAP) -> tuple[list[dict], list[dict], list[dict]]:
    """注文対象を「注文」「次点」「見送り」に分ける。

    見送り: 業種の上げに沿った押し（PEER_SKIP）。残りを rank_key の順に並べ、上限と業種の偏りの上限で注文と次点に。"""
    skip = [r for r in rows if (r.get("peer") or {}).get("cls") in PEER_SKIP]
    rows = sorted((r for r in rows if (r.get("peer") or {}).get("cls") not in PEER_SKIP),
                  key=lambda r: (rank_key(r), r["code"]))
    picked, rest, per = [], [], {}
    for r in rows:
        s = sector_of.get(r["code"])
        if len(picked) < limit and (not s or per.get(s, 0) < cap):
            picked.append(r)
            if s:
                per[s] = per.get(s, 0) + 1
        else:
            rest.append(r)
    return picked, rest, skip


# ==================== アプリが出した注文の実績 ====================


def paper_update(track: dict, today: str, orders: list[dict], ind_of) -> dict:
    """その日に出した注文（上位 MAX_ORDERS）を記録し、結果が出ていないものを四本値で測り直す。

    orders: [{code, name, asof, limit, stop, ...}]（asof の引けで出した注文）。
    ind_of(code) -> indicators() の出力（無ければ None）。結果が出た注文は凍結する（あとで分割調整されても変えない）。
    """
    entries = list(track.get("orders") or [])
    have = {(e["asof"], e["code"]) for e in entries}
    for o in orders:
        if (o["asof"], o["code"]) not in have:
            entries.append({"asof": o["asof"], "code": o["code"], "name": o.get("name"),
                            "limit": o.get("limit"), "stop": o.get("stop"), "done": False})
    for e in entries:
        if e.get("done"):
            continue
        ind = ind_of(e["code"])
        if not ind or e["asof"] not in ind["d"]:
            continue
        res = simulate(ind, ind["d"].index(e["asof"]))
        if res is None:
            continue
        for k in ("filled", "in", "entry", "stop", "out", "exit", "why", "ret", "days", "open", "last"):
            if k in res:
                e[k] = res[k]
            elif k in ("open", "last"):
                e.pop(k, None)
        e["done"] = (not res["filled"]) or not res.get("open")
    entries = entries[-600:]
    done = [e for e in entries if e.get("done") and e.get("filled")]
    issued = [e for e in entries if e.get("done") or e.get("filled")]
    st = summarize(done)
    st["fill"] = round(sum(1 for e in issued if e.get("filled")) / len(issued) * 100) if issued else None
    st["open"] = sum(1 for e in entries if e.get("filled") and not e.get("done"))
    st["issued"] = len(entries)
    st["since"] = entries[0]["asof"] if entries else None
    return {"updated_at": today, "orders": entries, "stats": st}


def paper_account(track: dict, days: list[str], close_of, sector_of: dict | None = None) -> dict | None:
    """アプリが出した注文を、出した日から本番どおりに（資金の SLOT_PCT% ずつ・空いている現金の範囲で）置いていたら、の口座。

    結果がまだ分からない注文（翌日の日足がまだ無い）は置かない。期間は最初の注文の日から days の最後まで。"""
    orders_on: dict[str, list[dict]] = {}
    for e in track.get("orders") or []:
        if e.get("filled") and e.get("entry") is not None:
            res = {"filled": True, "entry": e["entry"], "out": e.get("out"), "ret": e.get("ret")}
        elif e.get("done") and e.get("filled") is False:
            res = {"filled": False}
        else:
            res = None
        orders_on.setdefault(e["asof"], []).append({"code": e["code"], "res": res})
    if not orders_on:
        return None
    ds = [d for d in days if d >= min(orders_on)]
    return account(ds, orders_on, close_of, sector_of)
