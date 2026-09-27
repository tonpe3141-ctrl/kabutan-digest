"""短期の押し目買い（作戦タブの中心になる、1つの売買ルール）。

作戦（温度計の型・作戦ボード）と発掘（台帳）が別々に「買う候補」を出していて、どれで入るのかが分かりにくかった。
2022-10〜2026-09 の四本値（約380銘柄）で、場中の安値での損切り・窓開け・売買コストまで再現して検証し、
勝率がはっきり高く、前半（2023-24）で決めて後半（2025-26）でも崩れなかったルールだけを残した（DESIGN.md 13章）。

  入口   : 上昇トレンド（終値 > 200日線、かつ 50日線 > 200日線）の銘柄が、2日RSI < 10 まで短く押した日。
           20日平均の売買代金が10億円以上（指値が約定しやすく、1銘柄の事情で振れにくい）、株価300円以上
           （呼値1円が値段の0.3%を超える低位株は、指値と損切りの刻みが粗すぎる）
  買い   : 翌営業日だけ有効の指値 = その日の終値 − 0.5×ATR(14)（押した日の、さらに下で拾う）
  売り   : 翌日から毎日、指値 = 直近4日の終値の平均（「終値が5日線を上回ったら売る」を前もって置ける注文にしたもの）
  損切り : 約定値 − 3×ATR(14)。場中に割ったらその価格で、寄りで割っていたら寄りで
  期限   : 10営業日たっても売れなければ引けで売る
  並べ方 : 25日線からの下離れが大きい順。1日5銘柄まで・同じ業種は2銘柄まで

すべて純関数。同じ日足からは同じ注文が出る（tests で固定）。検証（verify）と、アプリが出した注文の実績（paper_update）は
同じ simulate() で測る。「予測」ではなく、過去に同じ形で同じ注文を置いたらこうだった、という規則。
"""
import math
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
MAX_HOLD = 10           # 営業日
MAX_ORDERS = 5          # 1日に出す注文の数
SECTOR_CAP = 2          # 同じ業種は2銘柄まで
COST = 0.1              # 往復の売買コスト（%）
WARMUP = MA_LONG        # 200日線が引けるまでは判定しない
RULES = {"rsi_n": RSI_N, "rsi_max": RSI_MAX, "ma_long": MA_LONG, "ma_mid": MA_MID, "liq_min": LIQ_MIN,
         "min_price": MIN_PRICE,
         "entry_atr": ENTRY_ATR, "stop_atr": STOP_ATR, "exit_n": EXIT_N, "max_hold": MAX_HOLD,
         "max_orders": MAX_ORDERS, "sector_cap": SECTOR_CAP, "cost": COST}


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


def sell_level(ind: dict, j: int):
    """j 日目に置く売りの指値（直近4日＝j−4〜j−1 日目の終値の平均。j 日目の寄り前に決まっている）。"""
    if j < EXIT_N:
        return None
    return sum(ind["c"][j - EXIT_N:j]) / EXIT_N


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


# ==================== 1回の売買の再現 ====================


def simulate(ind: dict, i: int, max_hold: int = MAX_HOLD, cost: float = COST) -> dict | None:
    """i 日目の引けで出した注文を、翌日以降の四本値で再現する。

    買い: i+1 日目だけ有効の指値。安値が指値以下なら約定（寄りが指値より下なら寄りで）。
    約定した日も、安値が損切り以下なら損切り（同じ日に両方なら損切りを先に数える＝保守的に）。
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
        g = sell_level(ind, k)
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
    return {"n": len(rs), "win": round(len(win) / len(rs) * 100), "avg": r2(mean(rs)), "med": r2(median(rs)),
            "pf": r2(sum(win) / -sum(loss)) if loss and sum(loss) < 0 else None,
            "avg_win": r2(mean(win)) if win else None, "avg_loss": r2(mean(loss)) if loss else None,
            "worst": r2(min(rs)), "days": r1(mean(t["days"] for t in trades if t.get("ret") is not None)),
            "stops": round(why.count("stop") / len(rs) * 100), "sells": round(why.count("sell") / len(rs) * 100),
            "times": round(why.count("time") / len(rs) * 100)}


def verify(stocks: dict[str, list[tuple]], recent: int = 120) -> dict | None:
    """ユニバース全体で、毎日このルールで注文を置いていたらどうだったかを再現する（銘柄ごと・重ならないように）。

    stocks: {code: compact() の出力}。比べる相手は「同じ銘柄を毎日、翌日の寄りで買って5営業日後の引けで売る」。
    期間は日足キャッシュの長さ（200日線が引けるようになってから）。直近 recent 営業日の分も別に出す。
    """
    trades, base = [], []
    cal: set[str] = set()
    for code, bars in stocks.items():
        if len(bars) < WARMUP + 5:
            continue
        ind = indicators(bars)
        n = len(bars)
        cal.update(ind["d"][WARMUP - 1:])
        busy = -1
        for i in range(WARMUP - 1, n - 1):
            if i + 6 < n:
                base.append((ind["d"][i], (ind["c"][i + 6] / ind["o"][i + 1] - 1) * 100 - COST))
            if i <= busy or not is_signal(ind, i):
                continue
            res = simulate(ind, i)
            if res is None:
                continue
            if not res["filled"]:
                continue
            if res.get("open"):
                break
            trades.append({"code": code, "sig": ind["d"][i], **res})
            busy = ind["d"].index(res["out"])
    if not cal:
        return None
    days = sorted(cal)
    cut = days[-recent] if len(days) > recent else days[0]
    half = days[len(days) // 2]
    b_all = [r for _, r in base]
    return {
        "from": days[0], "to": days[-1], "universe": len(stocks), "days": len(days),
        "all": summarize(trades),
        "early": {**summarize([t for t in trades if t["in"] < half]), "from": days[0], "to": half},
        "late": {**summarize([t for t in trades if t["in"] >= half]), "from": half, "to": days[-1]},
        "recent": {**summarize([t for t in trades if t["in"] >= cut]), "from": cut},
        "base": {"n": len(b_all), "win": round(sum(1 for x in b_all if x > 0) / len(b_all) * 100) if b_all else None,
                 "avg": r2(mean(b_all)) if b_all else None},
        "note": "四本値で再現: 翌日だけ有効の指値、場中の安値での損切り（寄りで割れていれば寄り）、毎日の売り指値、"
                "10営業日で手仕舞い、往復0.1%のコスト。同じ銘柄は手仕舞うまで次の注文を数えない。"
                "同時に持つ数の上限は置いていない（1回ごとの成績）。",
    }


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
           # 明日（次の営業日）に置く売りの指値（保有中の人向け）
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
    # 約定した日の翌日に置く売り指値の目安（約定した日の終値を指値と同じと仮定）
    sell = tick_up((sum(ind["c"][i - EXIT_N + 2:i + 1]) + limit) / EXIT_N)
    return {"asof": ind["d"][i], "close": c, "limit": limit, "to_limit": r1((limit / c - 1) * 100),
            "stop": stop, "stop_pct": r1((stop / limit - 1) * 100), "sell": sell,
            "sell_pct": r1((sell / limit - 1) * 100), "atr": r2(atr)}


def rank_orders(rows: list[dict], sector_of: dict[str, str | None], limit: int = MAX_ORDERS,
                cap: int = SECTOR_CAP) -> tuple[list[dict], list[dict]]:
    """注文対象を25日線からの下離れが大きい順に並べ、上限と業種の偏りの上限で「注文」と「次点」に分ける。"""
    rows = sorted(rows, key=lambda r: (r.get("dev25") if r.get("dev25") is not None else 0, r["code"]))
    picked, rest, per = [], [], {}
    for r in rows:
        s = sector_of.get(r["code"])
        if len(picked) < limit and (not s or per.get(s, 0) < cap):
            picked.append(r)
            if s:
                per[s] = per.get(s, 0) + 1
        else:
            rest.append(r)
    return picked, rest


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
