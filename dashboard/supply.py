"""需給: 上値のしこり（価格帯別の出来高）と信用残（DESIGN.md 26章）。

依頼（2026-10-10）: キオクシアやフジクラなど、需給が重くてなかなか上がらない。分析の観点に信用状況と需給を加えてほしい。

1. 上値のしこり（日足から。全銘柄・毎日）
   直近120営業日（約半年）の出来高を、その日の安値〜高値に均等に割り振って価格帯別の出来高を作り、
   **今の値段より上で売買された出来高の割合**（above）と、出来高で重みをつけた平均の値段（vwap。半年の買い手の平均コストの目安）、
   今の値段のすぐ上で出来高が厚い価格帯（壁）を出す。上で買った人は戻れば売りたい（戻り売り）ので、above が大きいほど上値が重い。

   約2年の日足（472銘柄、2025-01〜2026-09 を前半・後半に分けた）で、次の20営業日の対市場（全銘柄の中央値）を数えた:

     上値の出来高   勝った割合（前半／後半）  中央値（前半／後半）
     30%未満          54%／52%               +0.7／+0.4%
     30〜50%          49%／52%               −0.1／+0.4%
     50〜70%          46%／47%               −0.6／−0.6%
     70%以上          47%／48%               −0.6／−0.3%

   しこりが半分を超える銘柄は、市場に負ける割合がやや高い（前半・後半で同じ向き）。60日で上げている銘柄の中でも同じ向き。
   ただし差は小さく（勝った割合で5〜8pt）、平均では上回ることもある（少数の大きな戻りが平均を押し上げる）。
   売買代金30億円以上の大型では前半の差がほとんど無かった。だから「上がりにくさ」の説明に使い、注文の条件・並べ方には使わない。

2. 信用残（Yahoo!ファイナンスの銘柄ページ。週1回・前週末の残高）
   信用買残・信用売残・信用倍率と前週比。買残を20日平均の出来高で割った「買残は出来高の何日分か」（取組の重さ）、
   1週で買残が急に増えた（+30%以上）、買残の増減と株価の向きの組み合わせ（下げながら買残が増える＝戻り売りの予備軍が増えている、など）を事実として出す。
   過去の信用残は取れないので、先の値動きとの関係は検証できていない。週ごとの残高を docs/data/margin.json にためて、
   たまってから前半で決めて後半で確かめる。それまでは数字と事実だけを出し、判定・注文には使わない。

すべて純関数（取得は sources/margin.py）。入力は ohlc.json（dates と stocks: {コード: [[始,高,安,終,出来高(100株)] か null, ...]}）。
"""
from statistics import mean, median

VP_N = 120          # 価格帯別の出来高を数える日数（約半年）
VP_MIN = 96         # そのうち日足がこれだけ無ければ出さない（上場から日が浅い銘柄）
HEAVY = 50.0        # 上値の出来高の割合がこれ以上 → しこりが多い（%）
LIGHT = 30.0        # これ未満 → 軽い（%）
WALL_UP = 15.0      # 壁を探す範囲（今の値段から上に何%まで）
BINS = 30           # 価格帯の数（120日の安値〜高値を等分）
H = 20              # 検証: 次の何営業日の対市場か
STEP = 5            # 検証は5営業日おきに数える（20日の重なりが多く、毎日数えても標本は増えない）
CLS = ((0, LIGHT, "light"), (LIGHT, HEAVY, "mid"), (HEAVY, 70.0, "heavy"), (70.0, 101.0, "heavier"))
CLS_LABEL = {"light": f"{LIGHT:g}%未満", "mid": f"{LIGHT:g}〜{HEAVY:g}%", "heavy": f"{HEAVY:g}〜70%", "heavier": "70%以上"}
UNIT = 100          # ohlc の出来高は 100株単位

# 信用残
DAYS_HEAVY = 5.0    # 買残が20日平均の出来高のこの日数以上 → 取組が重い（目安。未検証）
RATIO_LONG = 5.0    # 信用倍率がこれ以上 → 買い長（目安。未検証）
RATIO_SHORT = 1.0   # これ未満 → 売り長
JUMP = 30.0         # 1週で買残がこれ以上（%）増えた → 新しい買い方が多い（目安。未検証）
HIST_WEEKS = 26     # 銘柄ごとにためる週の数
SPLIT_K = (2, 3, 4, 5, 10)   # 分割とみなす倍率（adjust_splits）

RULE_TEXT = (f"直近{VP_N}営業日の出来高を日々の安値〜高値に割り振り、今の値段より上で売買された割合。"
             f"{HEAVY:g}%以上をしこりが多いとする")


def _above(rows: list, price: float) -> tuple[float, float] | None:
    """rows（[始,高,安,終,出来高] の取れた日）のうち、price より上で売買された出来高の割合（%）と、出来高加重の平均の値段。"""
    tot = above = wsum = 0.0
    for o, hi, lo, c, v in rows:
        v = v or 0
        if v <= 0:
            continue
        tot += v
        wsum += (hi + lo + c) / 3 * v
        if lo >= price:
            above += v if lo > price else 0.0
        elif hi > price:
            above += v * (hi - price) / (hi - lo)
    if tot <= 0:
        return None
    return above / tot * 100, wsum / tot


def _window(s: list, t: int) -> list:
    return [x for x in s[max(0, t - VP_N + 1): t + 1] if x and x[3] and x[1] and x[2]]


def profile(s: list, t: int | None = None) -> dict | None:
    """t 日目（省略時は最新）の引けでの上値のしこり。足りなければ None。"""
    if not s:
        return None
    t = len(s) - 1 if t is None else t
    if not s[t] or not s[t][3]:
        return None
    rows = _window(s, t)
    if len(rows) < VP_MIN:
        return None
    price = s[t][3]
    r = _above(rows, price)
    if not r:
        return None
    above, vwap = r
    lo = min(x[2] for x in rows)
    hi = max(x[1] for x in rows)
    out = {"above": round(above, 1), "vwap": round(vwap, 1), "gap": round((price / vwap - 1) * 100, 1),
           "hi": hi, "lo": lo, "cls": cls_of(above)}
    out["wall"] = wall(rows, price)
    return out


def cls_of(above: float) -> str:
    for a, b, k in CLS:
        if a <= above < b:
            return k
    return "heavier"


def bands(rows: list, n: int = BINS) -> list[tuple[float, float, float]]:
    """120日の安値〜高値を n 等分した価格帯ごとの出来高（[(下限, 上限, 出来高の割合%)]）。"""
    lo = min(x[2] for x in rows)
    hi = max(x[1] for x in rows)
    if hi <= lo:
        return []
    w = (hi - lo) / n
    vol = [0.0] * n
    tot = 0.0
    for _, h, l, _, v in rows:
        v = v or 0
        if v <= 0:
            continue
        tot += v
        if h <= l:
            vol[min(n - 1, int((l - lo) / w))] += v
            continue
        a = max(0, min(n - 1, int((l - lo) / w)))
        b = max(0, min(n - 1, int((h - lo) / w)))
        for k in range(a, b + 1):
            seg = min(h, lo + (k + 1) * w) - max(l, lo + k * w)
            if seg > 0:
                vol[k] += v * seg / (h - l)
    if tot <= 0:
        return []
    return [(lo + k * w, lo + (k + 1) * w, vol[k] / tot * 100) for k in range(n)]


def wall(rows: list, price: float) -> dict | None:
    """今の値段から上 WALL_UP% までで、出来高がいちばん厚い価格帯（隣り合う2帯をまとめて数える）。

    戻り売りが出やすい価格帯の目安。帯の出来高が、帯の平均（100/BINS %）の1.5倍に届かなければ出さない。"""
    bs = bands(rows)
    if len(bs) < 2:
        return None
    top = price * (1 + WALL_UP / 100)
    best = None
    def above_part(k):                          # 今の値段をまたぐ帯は、上の部分だけを数える
        a, b, x = bs[k]
        return x if a >= price else x * max(0.0, b - price) / (b - a)
    for k in range(len(bs) - 1):
        a, b = bs[k][0], bs[k + 1][1]
        if b <= price or a >= top:
            continue
        share = above_part(k) + above_part(k + 1)
        if best is None or share > best[2]:
            best = (max(a, price), b, share)
    if not best or best[2] < 2 * 100 / len(bs) * 1.5:
        return None
    return {"lo": round(best[0]), "hi": round(best[1]), "pct": round(best[2], 1),
            "to": round((best[0] / price - 1) * 100, 1)}


# ==================== 検証 ====================


def market_fwd(dates: list, stocks: dict, h: int = H) -> list:
    """各日 t の、全銘柄の h 日後の騰落（%）の中央値。先が無い日は None。"""
    n = len(dates)
    out = [None] * n
    for t in range(n - h):
        r = [(s[t + h][3] / s[t][3] - 1) * 100 for s in stocks.values()
             if t + h < len(s) and s[t] and s[t + h] and s[t][3] and s[t + h][3]]
        out[t] = median(r) if len(r) >= 20 else None
    return out


def _agg(xs: list):
    if len(xs) < 30:
        return None
    xs = sorted(xs)
    n = len(xs)
    return {"n": n, "avg": round(mean(xs), 2), "med": round(xs[n // 2], 2),
            "up": round(100 * sum(1 for x in xs if x > 0) / n)}


def verify(dates: list, stocks: dict, step: int = STEP):
    """上値の出来高の割合ごとに、次の H 営業日の対市場（全銘柄の中央値との差）。前半・後半に分ける。

    先読みしない（しこりは t 日目の引けまでの日足で決め、結果は t+H 日目）。今の採用銘柄だけで測る（生存者の偏り）。"""
    n = len(dates)
    if n < VP_N + H + 40:
        return None
    mk = market_fwd(dates, stocks)
    first, last = VP_N - 1, n - H - 1
    mid = (first + last) // 2
    groups = {k: ([], []) for _, _, k in CLS}
    groups["all"] = ([], [])
    for s in stocks.values():
        for t in range(first, last + 1, step):
            if t + H >= len(s) or not s[t] or not s[t + H] or not s[t][3] or not s[t + H][3] or mk[t] is None:
                continue
            rows = _window(s, t)
            if len(rows) < VP_MIN:
                continue
            r = _above(rows, s[t][3])
            if not r:
                continue
            ex = (s[t + H][3] / s[t][3] - 1) * 100 - mk[t]
            half = 0 if t < mid else 1
            groups[cls_of(r[0])][half].append(ex)
            groups["all"][half].append(ex)
    return {"from": dates[first], "to": dates[last], "split": dates[mid], "h": H, "step": step,
            "cls": {k: [_agg(a), _agg(b)] for k, (a, b) in groups.items()}}


def today_rows(dates: list, stocks: dict) -> dict:
    """最新の日の、銘柄ごとの上値のしこり {コード: {asof, above, vwap, gap, cls, wall}}。"""
    if not dates:
        return {}
    out = {}
    t = len(dates) - 1
    for code, s in stocks.items():
        if t >= len(s):
            continue
        p = profile(s, t)
        if p:
            p.pop("hi", None)
            p.pop("lo", None)
            out[code] = {"asof": dates[t], **p}
    return out


# ==================== 信用残 ====================


def merge_margin(store: dict, code: str, rows: list[dict] | None, today: str) -> None:
    """取得した週の信用残（[{date, buy, sell, ...}]。分割は調整していない生の株数）を履歴に足す。
    同じ日付は新しく取った値で差し替え、古い週は捨てる。"""
    e = store.setdefault("stocks", {}).setdefault(code, {"hist": []})
    e["fetched"] = today
    if not rows:
        return
    by = {x["date"]: x for x in e.get("hist") or [] if x.get("date")}
    for r in rows:
        if r and r.get("date"):
            by[r["date"]] = {k: r.get(k) for k in ("date", "buy", "sell", "buy_chg", "sell_chg", "ratio")}
    e["hist"] = sorted(by.values(), key=lambda x: x["date"])[-HIST_WEEKS:]


def adjust_splits(hist: list[dict]) -> tuple[list[dict], list[dict]]:
    """株式分割で株数が k 倍に見える週を見つけ、それより前の週の買残・売残を k 倍して今の株数にそろえる。

    分割の週は、買残と売残が同じ週に同じ整数倍（2・3・4・5・10倍）になり、信用倍率がほとんど変わらない。
    売残が無い（0）銘柄は確かめられないので補正しない。hist は古い順。戻り値は（補正した履歴、[{date, k}]）。"""
    rows = [dict(x) for x in hist]
    splits = []
    for i in range(len(rows) - 1, 0, -1):
        a, b = rows[i - 1], rows[i]
        if not (a.get("buy") and b.get("buy") and a.get("sell") and b.get("sell")):
            continue
        kb, ks = b["buy"] / a["buy"], b["sell"] / a["sell"]
        for k in SPLIT_K:
            if abs(kb / k - 1) < 0.15 and abs(ks / k - 1) < 0.3 and abs((kb / ks) - 1) < 0.3:
                splits.append({"date": b["date"], "k": k})
                for x in rows[:i]:
                    x["buy"] = round(x["buy"] * k) if x.get("buy") is not None else None
                    x["sell"] = round(x["sell"] * k) if x.get("sell") is not None else None
                break
    return rows, sorted(splits, key=lambda x: x["date"])


def due(store: dict, code: str, today: str, every: int = 3, week: int = 7) -> bool:
    """取り直す銘柄か。信用残は週1回（金曜の残高を翌週の火曜に公表）なので、最新の残高の日付から week 日たっていて、
    前に取りに行ってから every 日以上たった銘柄だけ。一度も取っていない銘柄は取る。"""
    from datetime import date as _date
    e = (store.get("stocks") or {}).get(code)
    if not e:
        return True
    try:
        t = _date.fromisoformat(today)
        if e.get("fetched") and (t - _date.fromisoformat(e["fetched"])).days < every:
            return False
        hist = e.get("hist") or []
        return not hist or (t - _date.fromisoformat(hist[-1]["date"])).days > week
    except (KeyError, ValueError, TypeError):
        return True


def margin_row(e: dict | None, s: list | None, dates: list) -> dict | None:
    """銘柄の信用残の要約。e は margin.json の1銘柄（hist）、s は ohlc の行（出来高と株価の向きに使う）。

    出すもの: 最新の週の買残・売残・倍率・前週比、買残が20日平均の出来高の何日分か、
    4週前からの買残の増減と、同じ期間の株価の騰落（下げながら買残が増えた、など。事実の組み合わせ）。"""
    hist, splits = adjust_splits((e or {}).get("hist") or [])
    if not hist:
        return None
    last = hist[-1]
    buy, sell = last.get("buy"), last.get("sell")
    prev = hist[-2] if len(hist) > 1 and _weeks(hist[-2]["date"], last["date"]) <= 2 else None
    # 前週比は分割を補正した前の週と比べる（Yahoo の「増減」は分割の週に株数の差がそのまま出る）
    out = {"date": last["date"], "buy": buy, "sell": sell,
           "buy_chg": buy - prev["buy"] if prev and buy is not None and prev.get("buy") is not None else last.get("buy_chg"),
           "sell_chg": sell - prev["sell"] if prev and sell is not None and prev.get("sell") is not None else last.get("sell_chg"),
           "ratio": last.get("ratio") if last.get("ratio") is not None else (round(buy / sell, 2) if buy and sell else None),
           "n": len(hist)}
    recent = [x for x in splits if _weeks(x["date"], last["date"]) <= 8]
    if recent:
        out["split"] = recent[-1]
    if prev is None and splits and splits[-1]["date"] == last["date"]:
        out["buy_chg"] = out["sell_chg"] = None      # 分割の週で前の週が無ければ増減は出さない
    if s and dates:
        vols = [x[4] * UNIT for x in s[-20:] if x and x[4]]
        if len(vols) >= 15 and buy:
            av = mean(vols)
            out["days"] = round(buy / av, 1) if av else None
    # 4週前（無ければ一番古い週）からの買残の増減と、同じ期間の株価
    base = next((x for x in reversed(hist[:-1]) if _weeks(x["date"], last["date"]) >= 4), hist[0] if len(hist) > 1 else None)
    if base and base.get("buy") and buy:
        out["w"] = _weeks(base["date"], last["date"])
        out["buy_w"] = round((buy / base["buy"] - 1) * 100, 1)
        p0, p1 = _close_on(s, dates, base["date"]), _close_on(s, dates, last["date"])
        if p0 and p1:
            out["px_w"] = round((p1 / p0 - 1) * 100, 1)
    out["notes"] = margin_notes(out)
    return out


def _weeks(a: str, b: str) -> int:
    from datetime import date as _date
    try:
        return round((_date.fromisoformat(b) - _date.fromisoformat(a)).days / 7)
    except ValueError:
        return 0


def _close_on(s: list | None, dates: list, d: str):
    """d の日（休場ならその前の営業日）の終値。"""
    if not s:
        return None
    best = None
    for dd, x in zip(dates, s):
        if dd > d:
            break
        if x and x[3]:
            best = x[3]
    return best


def margin_notes(m: dict) -> list[dict]:
    """信用残の数字から、事実の一言（目安。先の値動きは未検証）。tone は warn / info。"""
    out = []
    if m.get("days") is not None and m["days"] >= DAYS_HEAVY:
        out.append({"k": "days", "tone": "warn",
                    "t": f"買残が20日平均の出来高の{m['days']:g}日分（{DAYS_HEAVY:g}日分以上は取組が重い目安）。戻ると返済売りが出やすい"})
    buy, chg = m.get("buy"), m.get("buy_chg")
    if buy and chg and buy - chg > 0 and chg / (buy - chg) * 100 >= JUMP:
        out.append({"k": "jump", "tone": "warn",
                    "t": f"1週で買残が {chg / (buy - chg) * 100:+.0f}%（{chg / 1e4:+,.0f}万株）増えた。この週に信用で買った人の値段が、戻りで売りが出やすい価格帯になる"})
    r = m.get("ratio")
    if r is not None and r >= RATIO_LONG:
        out.append({"k": "long", "tone": "info", "t": f"信用倍率 {r:g}倍の買い長（売り残が少なく、買い戻しの支えが薄い。大型株ではよくある水準）"})
    elif r is not None and r < RATIO_SHORT:
        out.append({"k": "short", "tone": "info", "t": f"信用倍率 {r:g}倍の売り長（売り方の買い戻しが上げを支えることがある）"})
    bw, pw = m.get("buy_w"), m.get("px_w")
    if bw is not None and pw is not None and m.get("w"):
        if bw >= 10 and pw <= -5:
            out.append({"k": "trap", "tone": "warn",
                        "t": f"{m['w']}週で株価 {pw:+.1f}% の下げの中、買残が {bw:+.0f}% 増えた（下で拾った買い方が、戻りで売りに回りやすい）"})
        elif bw <= -10 and pw <= -5:
            out.append({"k": "flush", "tone": "info",
                        "t": f"{m['w']}週で株価 {pw:+.1f}%・買残 {bw:+.0f}%（下げの中で買残が減った＝投げ・整理が進んだ）"})
        elif bw >= 10 and pw >= 5:
            out.append({"k": "chase", "tone": "info",
                        "t": f"{m['w']}週で株価 {pw:+.1f}%・買残 {bw:+.0f}%（上げに信用の買いがついてきた）"})
    return out


def block(dates: list, stocks: dict, margin: dict | None = None) -> tuple:
    """thermo.json の supply と、銘柄ごとの jk（しこり）・mg（信用残）。"""
    rows = today_rows(dates, stocks)
    try:
        ver = verify(dates, stocks)
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  需給（しこり）の検証で例外: {e}")
        ver = None
    mg = {}
    for code, e in ((margin or {}).get("stocks") or {}).items():
        try:
            r = margin_row(e, stocks.get(code), dates)
        except Exception:                       # noqa: BLE001  1銘柄の不具合で止めない
            r = None
        if r:
            mg[code] = r
    heavy = sum(1 for r in rows.values() if r["above"] >= HEAVY)
    return ({"rules": {"n": VP_N, "heavy": HEAVY, "light": LIGHT, "wall_up": WALL_UP, "text": RULE_TEXT,
                       "days_heavy": DAYS_HEAVY, "ratio_long": RATIO_LONG, "ratio_short": RATIO_SHORT, "jump": JUMP,
                       "cls": CLS_LABEL},
             "verify": ver, "n": len(rows), "n_heavy": heavy, "n_margin": len(mg),
             "margin_asof": max((r["date"] for r in mg.values()), default=None)}, rows, mg)
