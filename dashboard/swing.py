"""短期の押し目買い（売買タブの中心になる、1つの売買ルール）。

作戦（温度計の型・作戦ボード）と発掘（台帳）が別々に「買う候補」を出していて、どれで入るのかが分かりにくかった。
2022-10〜2026-09 の四本値（約380銘柄）で、場中の安値での損切り・窓開け・売買コストまで再現して検証し、
勝率がはっきり高く、前半（2023-24）で決めて後半（2025-26）でも崩れなかったルールだけを残した（DESIGN.md 13章）。

  入口   : 上昇トレンド（終値 > 200日線、かつ 50日線 > 200日線）の銘柄が、2日RSI < 10 まで短く押した日。
           20日平均の売買代金が30億円以上（指値が約定しやすく、1銘柄の事情や突然の注目で振れにくい。DESIGN.md 23章で
           10億円から引き上げた）、株価300円以上
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

もう1つ、同じ押しの入口を「大きなトレンドの中の1〜3か月の調整」に当てる中期の押し目を置く（DESIGN.md 21章）:

  形     : 12か月の強さ（20営業日前の終値 ÷ 250営業日前の終値。直近1か月を除く）がその日のユニバースの上位1/3、
           終値 > 200日線 かつ 200日線が20営業日前より上、60営業日の最高値（終値）から20〜59営業日たち、そこから −10% 以下
  入口   : その形の銘柄が 2日RSI < 10 まで短く押した日（売買代金・株価の条件は短期と同じ）。買いは短期と同じ翌日だけの指値
  損切り : min(調整の安値（高値の日からの終値の最安）, 約定値) − 1×ATR(14)。調整の安値を割ったら「調整明け」の見立て違い
  売り   : 60営業日たったら引けで売る（売り指値は置かない。伸びる銘柄を早く売らない）
  枠     : 中期の保有は6銘柄まで（1件は短期と同じ資金の10%）。並べ方は25日線からの下離れの大きい順
  1銘柄に1つの計画: 中期の形の押しは中期の計画で出し、短期の注文には出さない

すべて純関数。同じ日足からは同じ注文が出る（tests で固定）。検証（verify）と、アプリが出した注文の実績（paper_update）は
同じ simulate() / simulate_mid() で測る。口座の再現（account）も同じ結果を、本番どおりの置き方（引けで決めて翌日だけ有効、
約定しなければ資金はその日遊ぶ）で並べたもの。「予測」ではなく、過去に同じ形で同じ注文を置いたらこうだった、という規則。
"""
import math
from collections import Counter
from statistics import mean, median

RSI_N = 2
RSI_MAX = 10.0          # 2日RSI がこれ未満で入口
MA_LONG = 200
MA_MID = 50
LIQ_MIN = 30.0          # 20日平均の売買代金（億円）。前半で 10/20/30/50/100 を比べて決め、後半で確かめた（DESIGN.md 23章）
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
# 中期の押し目（DESIGN.md 21章。2023-24 の成績だけで出口と枠を決め、2025-26 で確かめた）
MID_MOM_N = 250         # 12か月の強さ = 20営業日前の終値 ÷ 250営業日前の終値 − 1（直近1か月は短い押し・戻りの雑音なので除く）
MID_MOM_SKIP = 20
MID_TOP = 2 / 3         # その日のユニバース（売買代金・株価の条件を満たす銘柄）の中で、強さの順位がこれ以上（上位1/3）
MID_SLOPE_N = 20        # 200日線が20営業日前より上（大きなトレンドが上向き）
MID_HI_N = 60           # 調整の起点 = 60営業日の最高値（終値）
MID_AGE = (20, 59)      # 高値から20〜59営業日（1〜3か月の調整）
MID_DEPTH = -10.0       # 高値から −10% 以下
MID_STOP_ATR = 1.0      # 損切り = min(調整の安値, 約定値) − 1×ATR
MID_HOLD = 60           # 60営業日たったら引けで売る
MID_SLOTS = 6           # 中期の保有は6銘柄まで（1件は SLOT_PCT）
RULES = {"rsi_n": RSI_N, "rsi_max": RSI_MAX, "ma_long": MA_LONG, "ma_mid": MA_MID, "liq_min": LIQ_MIN,
         "min_price": MIN_PRICE,
         "entry_atr": ENTRY_ATR, "stop_atr": STOP_ATR, "exit_n": EXIT_N, "floor": FLOOR, "max_hold": MAX_HOLD,
         "max_orders": MAX_ORDERS, "sector_cap": SECTOR_CAP, "slot_pct": SLOT_PCT, "cost": COST,
         "peer_n": PEER_N, "peer_dip": PEER_DIP, "peer_up": PEER_UP, "peer_lag": PEER_LAG,
         "mid_mom_n": MID_MOM_N, "mid_mom_skip": MID_MOM_SKIP, "mid_top": round(MID_TOP, 4), "mid_slope_n": MID_SLOPE_N,
         "mid_hi_n": MID_HI_N, "mid_age": list(MID_AGE), "mid_depth": MID_DEPTH, "mid_stop_atr": MID_STOP_ATR,
         "mid_hold": MID_HOLD, "mid_slots": MID_SLOTS}


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


# ==================== 中期の押し目（大きなトレンドの中の1〜3か月の調整） ====================


def mid_state(ind: dict | None, i: int) -> dict | None:
    """i 日目の引けまでで分かる、中期の形の材料。12か月の強さ（mom）、大きなトレンドが上向きか（up）、
    60営業日の最高値（hi）とそこからの営業日数（age）・下げ（dd）、高値の日からの終値の最安（low = 調整の安値）。"""
    if not ind or i < MID_MOM_N or i >= len(ind["c"]):
        return None
    c = ind["c"]
    m200, m200p = _ma(ind, i, MA_LONG), _ma(ind, i - MID_SLOPE_N, MA_LONG)
    if m200 is None or m200p is None or not c[i - MID_MOM_N]:
        return None
    w = c[i - MID_HI_N + 1:i + 1]
    k = max(range(len(w)), key=lambda x: (w[x], x))     # 同じ値なら新しいほうを高値の日にする
    hi = w[k]
    return {"mom": (c[i - MID_MOM_SKIP] / c[i - MID_MOM_N] - 1) * 100, "up": bool(c[i] > m200 and m200 > m200p),
            "hi": hi, "age": len(w) - 1 - k, "dd": (c[i] / hi - 1) * 100, "low": min(w[k:])}


def mid_liquid(ind: dict, i: int) -> bool:
    """強さの順位に入れる銘柄（短期と同じ売買代金・株価の条件）。"""
    tv = ind["tv"][i]
    return tv is not None and tv >= LIQ_MIN and ind["c"][i] >= MIN_PRICE


def mid_ranks(moms: dict[str, float]) -> dict[str, float]:
    """同じ日の12か月の強さの順位（0 = 最も弱い、1 = 最も強い）。"""
    order = sorted((v, c) for c, v in moms.items() if v is not None)
    n = len(order)
    return {c: k / max(1, n - 1) for k, (_, c) in enumerate(order)}


def is_mid_shape(st: dict | None, rank: float | None) -> bool:
    """中期の形（大きなトレンドの中の1〜3か月の調整）か。押した日かどうかは見ない。"""
    return bool(st and rank is not None and rank >= MID_TOP and st["up"]
                and MID_AGE[0] <= st["age"] <= MID_AGE[1] and st["dd"] <= MID_DEPTH)


def is_mid_signal(ind: dict, i: int, st: dict | None, rank: float | None) -> bool:
    """i 日目の引けで、中期の押し目の買いの指値を置く条件がそろったか（中期の形で、2日RSI < 10 まで押した日）。"""
    rs, atr = ind["rsi"][i], ind["atr"][i]
    return bool(is_mid_shape(st, rank) and rs is not None and rs < RSI_MAX and atr and mid_liquid(ind, i))


def mid_stop(low: float, entry: float, atr: float) -> float:
    """損切り = min(調整の安値, 約定値) − 1ATR（約定値が調整の安値より下なら、約定値から1ATR）。"""
    return min(low, entry) - MID_STOP_ATR * atr


def mid_order_of(ind: dict, st: dict) -> dict:
    """最新の日の引けで出す中期の押し目の注文（次の営業日だけ有効）。売りの指値は置かず、60営業日たったら引けで売る。"""
    i = len(ind["c"]) - 1
    c, atr = ind["c"][i], ind["atr"][i]
    limit = tick_down(c - ENTRY_ATR * atr)
    stop = tick_down(mid_stop(st["low"], limit, atr))
    return {"asof": ind["d"][i], "close": c, "limit": limit, "to_limit": r1((limit / c - 1) * 100),
            "stop": stop, "stop_pct": r1((stop / limit - 1) * 100), "atr": r2(atr), "low": st["low"],
            "hi": st["hi"], "to_hi": r1((st["hi"] / limit - 1) * 100), "age": st["age"], "dd": r1(st["dd"]),
            "mom": r1(st["mom"]), "hold": MID_HOLD}


def mid_holds_at(ind: dict, i: int, p: float) -> bool:
    """次の終値が p だったときも、大きなトレンドの条件（終値 > 200日線 かつ 200日線が20営業日前より上）が保たれるか。"""
    if i + 2 < MA_LONG + MID_SLOPE_N:
        return False
    pre = ind["pre"]
    m200 = (pre[i + 1] - pre[i + 2 - MA_LONG] + p) / MA_LONG
    m200p = _ma(ind, i + 1 - MID_SLOPE_N, MA_LONG)
    return bool(m200p and p > m200 > m200p)


def mid_key(ind: dict, i: int) -> float:
    """中期の並べ方（小さいほど先）: 25日線からの下離れ。"""
    m25 = _ma(ind, i, 25)
    return (ind["c"][i] / m25 - 1) * 100 if m25 else 0.0


# ==================== 1回の売買の再現 ====================


def simulate_mid(ind: dict, i: int, low: float, max_hold: int = MID_HOLD, cost: float = COST) -> dict | None:
    """i 日目の引けで出した中期の押し目の注文を、翌日以降の四本値で再現する。

    買いは simulate() と同じ（i+1 日目だけ有効の指値）。損切り = min(調整の安値, 約定値) − 1ATR。
    約定した日も安値が損切り以下なら損切り。翌日から: 寄りが損切り以下 → 寄りで／安値が損切り以下 → 損切りで／
    max_hold 営業日目 → 引けで売る。売りの指値は置かない。"""
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
    stop = mid_stop(low, e, atr)
    res = {"filled": True, "in": ind["d"][j], "entry": r2(e), "stop": r2(stop)}
    x = why = None
    k = j
    if ind["l"][j] <= stop:
        x, why = stop, "stop"
    while x is None and k + 1 < n:
        k += 1
        if ind["o"][k] <= stop:
            x, why = ind["o"][k], "stop"
        elif ind["l"][k] <= stop:
            x, why = stop, "stop"
        elif k - j >= max_hold:
            x, why = ind["c"][k], "time"
    if x is None:
        return {**res, "open": True, "days": k - j + 1, "last": ind["c"][k]}
    ret = (x / e - 1) * 100 - cost
    return {**res, "out": ind["d"][k], "exit": r2(x), "why": why, "ret": r2(ret), "days": k - j + 1}


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
            slot: float = SLOT_PCT, max_orders: int = MAX_ORDERS, cap: int = SECTOR_CAP,
            mid_on: dict[str, list[dict]] | None = None, mid_slots: int = MID_SLOTS) -> dict | None:
    """本番どおりに注文を置いた口座の再現（DESIGN.md 16章・21章）。

    days: 営業日（古い順）。orders_on: {引けの日: [{"code", "res"}, ...]}（並べ方の順。res は simulate() の結果）。
    close_of(code, 日付) -> その日の終値（無ければ None）。
    各営業日の引けで: その日に手仕舞った代金を現金に戻し、持っている銘柄を終値で値洗いし、並べ方の順に、保有中でない銘柄・
    同じ業種が cap 未満のものへ、資産の slot% ずつ、取り置いていない現金の範囲で max_orders 件まで注文を置く（翌日だけ有効）。
    翌日: 約定した注文は保有に、約定しなかった注文の資金は現金に戻す（その日は遊ぶ）。
    mid_on（中期の押し目の注文。res は simulate_mid() の結果）を渡すと、短期より先に、中期の保有と置いた注文が
    mid_slots 件になるまで同じ金額で置く（業種の上限は短期の保有だけで数える）。
    1回ごとの成績（verify の all）は資金の制約を置かない数え方。口座では、約定しなかった注文の資金が遊ぶ分と、
    保有中で新しい注文を置けない分だけ、1回ごとの平均から想像するより伸びが遅い。どれくらいの期間でプラスになるかは
    月末の資産で見る（m_up: 前の月末より増えた月の割合、q_up: 3か月前より増えていた割合、under: 高値を更新できなかった
    最長の営業日数）。
    """
    sector_of = sector_of or {}
    if len(days) < 2:
        return None
    cash, pos, pending = 1.0, [], []
    eq, util, taken, taken_mid = [], [], [], []
    for d in days:
        for o, amt, mid in pending:
            r = o["res"]
            if r.get("filled"):
                pos.append({"code": o["code"], "amt": amt, "entry": r["entry"], "out": r.get("out"),
                            "ret": r.get("ret"), "px": r["entry"], "mid": mid})
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
                (taken_mid if p["mid"] else taken).append(p["ret"])
            else:
                keep.append(p)
        pos = keep
        inv = sum(p["amt"] * p["px"] / p["entry"] for p in pos)
        val = cash + inv
        eq.append((d, val))
        util.append(inv / val if val > 0 else 0.0)
        held = {p["code"] for p in pos}
        if mid_on:
            n_mid = sum(1 for p in pos if p["mid"])
            for o in mid_on.get(d) or []:
                if n_mid >= mid_slots:
                    break
                if o["code"] in held or not o.get("res"):
                    continue
                want = val * slot / 100
                amt = min(cash, want)
                if amt < want * 0.3:
                    break
                cash -= amt
                pending.append((o, amt, True))
                held.add(o["code"])
                n_mid += 1
        per = Counter(sector_of.get(p["code"]) for p in pos if not p["mid"] and sector_of.get(p["code"]))
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
            pending.append((o, amt, False))
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
            "mid_n": len(taken_mid), "mid_win": round(sum(1 for x in taken_mid if x > 0) / len(taken_mid) * 100) if taken_mid else None,
            "mid_avg": r2(mean(taken_mid)) if taken_mid else None, "mid_slots": mid_slots if mid_on else 0,
            "months": len(ups), "m_up": round(sum(ups) / len(ups) * 100) if ups else None,
            "q_up": round(sum(q) / len(q) * 100) if q else None, "under": under, "curve": curve}


def mid_context(inds: dict[str, dict]):
    """日ごとの12か月の強さの順位を引く関数 rank_at(日付, code) を返す（verify と研究で共用）。"""
    moms_on: dict[str, dict[str, float]] = {}
    for code, ind in inds.items():
        c = ind["c"]
        for i in range(MID_MOM_N, len(c)):
            if c[i - MID_MOM_N] and mid_liquid(ind, i):
                moms_on.setdefault(ind["d"][i], {})[code] = (c[i - MID_MOM_SKIP] / c[i - MID_MOM_N] - 1) * 100
    cache: dict[str, dict[str, float]] = {}

    def rank_at(d, code):
        if d not in cache:
            cache[d] = mid_ranks(moms_on.get(d, {}))
        return cache[d].get(code)
    return rank_at


def verify(stocks: dict[str, list[tuple]], recent: int = 120, groups: dict[str, str] | None = None,
           sector_of: dict | None = None) -> dict | None:
    """ユニバース全体で、毎日このルールで注文を置いていたらどうだったかを再現する（銘柄ごと・重ならないように）。

    stocks: {code: compact() の出力}。比べる相手は「同じ銘柄を毎日、翌日の寄りで買って5営業日後の引けで売る」。
    groups（peer_groups の出力）を渡すと、その日の業種の中での位置で押しの形を決め、見送る形（PEER_SKIP）は
    注文に数えず、別に skipped として成績を出す。期間は日足キャッシュの長さ（200日線が引けるようになってから）。
    直近 recent 営業日の分も別に出す。account は、同じ期間に本番どおりの置き方（並べ方の順に1日5件まで・資金の10%ずつ・
    約定しなければ資金はその日遊ぶ）をした口座の再現（sector_of は同じ業種の上限に使う）。

    中期の押し目（DESIGN.md 21章）: 中期の形の押しは中期の計画で出すので、短期の成績（all など）と注文からは除く。
    mid に中期の1回ごとの成績と比べる相手（12か月の強さ上位1/3・上昇トレンドの押しを、調整の条件なしで同じ出口で）を、
    account に短期と中期を合わせた口座を、account_prev に以前の置き方（短期だけ・中期の形も短期で）の口座を出す。
    """
    inds = {code: indicators(bars) for code, bars in stocks.items() if len(bars) > PEER_N}
    rank_at = mid_context(inds)
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
    mids, mid_base = [], []
    cal: set[str] = set()
    orders_on: dict[str, list[dict]] = {}          # 口座の再現: 引けの日 → 短期の注文対象（見送り・中期の形を除く）
    prev_on: dict[str, list[dict]] = {}            # 以前の置き方: 中期の形も短期の注文に
    mid_on: dict[str, list[dict]] = {}             # 中期の押し目の注文
    closes: dict[str, dict[str, float]] = {}
    for code, ind in inds.items():
        n = len(ind["c"])
        if n < WARMUP + 5:
            continue
        cal.update(ind["d"][WARMUP - 1:])
        closes[code] = dict(zip(ind["d"], ind["c"]))
        busy = skip_busy = mid_busy = mb_busy = -1
        halted = mid_halt = mb_halt = False          # 結果の出ていない売買がある銘柄は、1回ごとの成績に数えるのをやめる
        for i in range(WARMUP - 1, n - 1):
            if i + 6 < n:
                base.append((ind["d"][i], (ind["c"][i + 6] / ind["o"][i + 1] - 1) * 100 - COST))
            rs = ind["rsi"][i]
            is_mid = False
            if rs is not None and rs < RSI_MAX and i >= MID_MOM_N:
                st = mid_state(ind, i)
                rk = rank_at(ind["d"][i], code)
                if st and rk is not None and rk >= MID_TOP and st["up"] and ind["atr"][i] and mid_liquid(ind, i):
                    # 比べる相手: 強さ上位1/3・上昇トレンドの押し（調整の条件なし）を同じ出口で
                    is_mid = is_mid_signal(ind, i, st, rk)
                    mres = simulate_mid(ind, i, st["low"])
                    if is_mid and mres is not None:
                        mid_on.setdefault(ind["d"][i], []).append({"code": code, "res": mres, "key": mid_key(ind, i)})
                    if mres is not None and mres["filled"]:
                        for lst, flag in ((mid_base, "b"), (mids, "m")) if is_mid else ((mid_base, "b"),):
                            if flag == "b" and (mb_halt or i <= mb_busy) or flag == "m" and (mid_halt or i <= mid_busy):
                                continue
                            if mres.get("open"):
                                if flag == "b":
                                    mb_halt = True
                                else:
                                    mid_halt = True
                                continue
                            lst.append({"code": code, "sig": ind["d"][i], **mres})
                            if flag == "b":
                                mb_busy = ind["d"].index(mres["out"])
                            else:
                                mid_busy = ind["d"].index(mres["out"])
            if not is_signal(ind, i):
                continue
            pc = peer_at(ind["d"][i], code)
            k = pc["cls"] if pc else "none"
            res = simulate(ind, i)
            if k not in PEER_SKIP and res is not None:
                m25 = _ma(ind, i, 25)
                o = {"code": code, "res": res,
                     "key": rank_key({"dev25": r1((ind["c"][i] / m25 - 1) * 100) if m25 else None, "peer": pc})}
                prev_on.setdefault(ind["d"][i], []).append(o)
                if not is_mid:
                    orders_on.setdefault(ind["d"][i], []).append(o)
            if is_mid:                               # 中期の形の押しは中期の計画で出す（短期の成績に数えない）
                continue
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
    for on in (orders_on, prev_on, mid_on):
        for rows in on.values():
            rows.sort(key=lambda o: (o["key"], o["code"]))
    cut = days[-recent] if len(days) > recent else days[0]
    half = days[len(days) // 2]
    b_all = [r for _, r in base]
    close_of = lambda c, d: closes.get(c, {}).get(d)   # noqa: E731
    mid_days = sorted({t["in"] for t in mids + mid_base})

    def periods(ts, start=None):
        return {"all": summarize(ts),
                "early": {**summarize([t for t in ts if t["in"] < half]), "from": start or days[0], "to": half},
                "late": {**summarize([t for t in ts if t["in"] >= half]), "from": half, "to": days[-1]},
                "recent": {**summarize([t for t in ts if t["in"] >= cut]), "from": cut}}
    m0 = mid_days[0] if mid_days else None
    mid = {**periods(mids, m0), "base": periods(mid_base, m0), "from": m0,
           "note": "四本値で再現: 翌日だけ有効の指値、損切り＝min(調整の安値, 約定値)−1ATR（場中の安値で・寄りで割れていれば寄り）、"
                   "売りの指値は置かず60営業日目の引けで手仕舞い、往復0.1%のコスト。同じ銘柄は手仕舞うまで次の注文を数えない。"
                   "比べる相手は、12か月の強さ上位1/3・上昇トレンドの銘柄の押し（2日RSI<10）を、1〜3か月の調整の条件なしで同じ出口で。"}
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
                "同時に持つ数の上限は置いていない（1回ごとの成績）。中期の形の押しは中期の押し目で数える。",
        # 本番どおりの置き方をした口座（1回ごとの成績と違い、約定しなかった注文の資金が遊ぶ分まで入る）。短期と中期を合わせて
        "account": account(days, orders_on, close_of, sector_of, mid_on=mid_on),
        # 以前の置き方（短期だけ。中期の形の押しも短期の計画で）
        "account_prev": account(days, prev_on, close_of, sector_of),
        "mid": mid,
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


def paper_update(track: dict, today: str, orders: list[dict], ind_of, mid: bool = False) -> dict:
    """その日に出した注文（上位 MAX_ORDERS）を記録し、結果が出ていないものを四本値で測り直す。

    orders: [{code, name, asof, limit, stop, ...}]（asof の引けで出した注文）。
    ind_of(code) -> indicators() の出力（無ければ None）。結果が出た注文は凍結する（あとで分割調整されても変えない）。
    mid=True は中期の押し目の注文（注文の low＝調整の安値を覚えておき、simulate_mid で測る）。
    """
    entries = list(track.get("orders") or [])
    have = {(e["asof"], e["code"]) for e in entries}
    for o in orders:
        if (o["asof"], o["code"]) not in have:
            e = {"asof": o["asof"], "code": o["code"], "name": o.get("name"),
                 "limit": o.get("limit"), "stop": o.get("stop"), "done": False}
            if mid:
                e["low"] = o.get("low")
            entries.append(e)
    for e in entries:
        if e.get("done"):
            continue
        ind = ind_of(e["code"])
        if not ind or e["asof"] not in ind["d"]:
            continue
        if mid and e.get("low") is None:
            continue
        idx = ind["d"].index(e["asof"])
        res = simulate_mid(ind, idx, e["low"]) if mid else simulate(ind, idx)
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


def paper_account(track: dict, days: list[str], close_of, sector_of: dict | None = None,
                  mid_track: dict | None = None) -> dict | None:
    """アプリが出した注文を、出した日から本番どおりに（資金の SLOT_PCT% ずつ・空いている現金の範囲で）置いていたら、の口座。

    結果がまだ分からない注文（翌日の日足がまだ無い）は置かない。期間は最初の注文の日から days の最後まで。
    mid_track（中期の押し目の注文の記録）を渡すと、短期より先に中期の注文を置く（account の mid_on）。"""
    def on_of(tr):
        on: dict[str, list[dict]] = {}
        for e in (tr or {}).get("orders") or []:
            if e.get("filled") and e.get("entry") is not None:
                res = {"filled": True, "entry": e["entry"], "out": e.get("out"), "ret": e.get("ret")}
            elif e.get("done") and e.get("filled") is False:
                res = {"filled": False}
            else:
                res = None
            on.setdefault(e["asof"], []).append({"code": e["code"], "res": res})
        return on
    orders_on, mid_on = on_of(track), on_of(mid_track)
    if not orders_on and not mid_on:
        return None
    ds = [d for d in days if d >= min(list(orders_on) + list(mid_on))]
    return account(ds, orders_on, close_of, sector_of, mid_on=mid_on or None)
