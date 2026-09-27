"""相場温度計と逆張りガード。

目的は「当てる」ことではなく、**衝動と逆の側に立つための根拠つきの物差し**を出すこと。
  - 株価が上がる条件（業績・モメンタム・過熱感・原油・金利・為替・米国株・センチメント）を
    8つの軸で −2〜+2 に採点する。+ は「株の追い風／過熱の側」、− は「向かい風／悲観の側」。
  - 全部が追い風なら天井圏、全部が向かい風なら大底圏に多い形、という逆張りの読みを
    温度（0〜100）・一致度・変化の向きで表す。
  - その読みがこの相場で本当に成り立つかを、日足で温度計を過去に当てて確かめる（backtest）。
  - 業種・銘柄は「押し目」「売られすぎ・下げ止まり」「過熱（追いかけ注意）」に分け、
    待つ価格の目安（25日線）を付ける。
  - 開示への値動きの反応と見出しの論調で「出尽くし」を機械的に判定する。

すべて純関数。入力は日足・履歴・当日の値。同じ入力からは同じ出力が出る（tests で固定）。
「予測」とは言わない。どの帯も「過去にこういう形のとき、こうだった」という整理にとどめる。
"""
import bisect
import re
from statistics import mean, median, pstdev

from .config import SECTOR_MACRO_SENS

# ==================== 指標 ====================


def pct(a, b):
    """a の b に対する変化率（%）。"""
    if a is None or not b:
        return None
    return (a / b - 1) * 100


def ret(xs: list[float], n: int):
    """直近 n 本の騰落率（%）。"""
    if len(xs) <= n or not xs[-1 - n]:
        return None
    return pct(xs[-1], xs[-1 - n])


def sma(xs: list[float], n: int):
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def dev(xs: list[float], n: int):
    """n日移動平均からの乖離率（%）。"""
    m = sma(xs, n)
    return pct(xs[-1], m) if m else None


def rsi(xs: list[float], n: int = 14, window: int = 100):
    """Wilder の RSI。直近 window 本で計算する（毎日同じ長さで計算し、再現性を保つ）。"""
    xs = xs[-window:]
    if len(xs) <= n:
        return None
    gains = [max(0.0, xs[i] - xs[i - 1]) for i in range(1, len(xs))]
    losses = [max(0.0, xs[i - 1] - xs[i]) for i in range(1, len(xs))]
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + l) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)


def daily_vol(xs: list[float], n: int = 60):
    """日次騰落率の標準偏差（%）。"""
    xs = xs[-(n + 1):]
    r = [pct(xs[i], xs[i - 1]) for i in range(1, len(xs)) if xs[i - 1]]
    return pstdev(r) if len(r) >= 10 else None


def r1(v):
    return None if v is None else round(v, 1)


def r2(v):
    return None if v is None else round(v, 2)


# ==================== 系列の扱い ====================


def upto(series: list[tuple[str, float]], asof: str | None, strict: bool = False) -> list[float]:
    """asof までの終値（古い順）。strict=True なら asof の当日を含めない。"""
    if asof is None:
        return [c for _, c in series]
    dates = [d for d, _ in series]
    i = bisect.bisect_left(dates, asof) if strict else bisect.bisect_right(dates, asof)
    return [c for _, c in series[:i]]


def with_live(series: list[tuple[str, float]], today: str | None, live: float | None):
    """当日の場中値（または引け値）を末尾に差し込む。日足に当日があれば置き換える。"""
    s = list(series)
    if live is None or not today:
        return s
    if s and s[-1][0] == today:
        s[-1] = (today, live)
    elif not s or s[-1][0] < today:
        s.append((today, live))
    return s


# ==================== 採点 ====================
# cuts = (m2, m1, p1, p2): v≦m2 → −2, v≦m1 → −1, v≧p2 → +2, v≧p1 → +1。
# orient=−1 は「値が低いほど追い風（過熱）」の指標（原油・金利・VIX）。
# tilt は帯の中の位置を連続値にしたもの（±1 が帯の境目）。変化の向きの判定に使う。


def band(v, cuts, orient=1):
    if v is None:
        return None
    m2, m1, p1, p2 = cuts
    s = -2 if v <= m2 else -1 if v <= m1 else 2 if v >= p2 else 1 if v >= p1 else 0
    mid = (m1 + p1) / 2
    half = (p1 - m1) / 2 or 1
    tilt = max(-3.0, min(3.0, (v - mid) / half))
    return {"score": s * orient, "tilt": round(tilt * orient, 3)}


def _round_half_away(x: float) -> int:
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def combine(subs: list[dict]) -> tuple[int, float] | None:
    subs = [s for s in subs if s and s.get("score") is not None]
    if not subs:
        return None
    sc = max(-2, min(2, _round_half_away(mean(s["score"] for s in subs))))
    return sc, round(mean(s["tilt"] for s in subs), 3)


def _sub(label, value, text, cuts, orient=1):
    b = band(value, cuts, orient)
    if not b:
        return None
    return {"label": label, "value": r2(value), "text": text, **b}


FACTORS = [
    ("earnings",  "業績"),
    ("momentum",  "モメンタム"),
    ("heat",      "過熱感"),
    ("oil",       "原油"),
    ("rates",     "金利"),
    ("fx",        "為替"),
    ("us",        "米国株"),
    ("sentiment", "センチメント"),
]
FACTOR_NOTE = {
    "earnings":  "日経平均の予想EPS（指数÷PER）の20日変化と、適時開示の上方／下方修正の比",
    "momentum":  "日経平均の60日騰落と75日線からの位置",
    "heat":      "日経平均の RSI(14)・25日線乖離・225採用銘柄の騰落レシオ(25日)",
    "oil":       "WTI の20日変化。原油高は輸入国の日本に向かい風",
    "rates":     "米10年債・日本10年債の20日変化。金利上昇は株価の割高感を強める",
    "fx":        "ドル円の20日変化。円安は輸出企業の追い風",
    "us":        "S&P500 と SOX 半導体指数の20日騰落",
    "sentiment": "VIX・日経VI の水準と、株探・市況記事の見出しの論調（3営業日）",
}


def market_factors(m: dict, asof: str | None = None, strict_foreign: bool = False,
                   extras: dict | None = None) -> list[dict]:
    """8軸の採点。

    m: {key: [(date, close)]}（nikkei, nkvi, jp10y, spx, sox, vix, usdjpy, us10y, wti）
    asof: この日までのデータで計算する（None=全部）。strict_foreign=True なら海外系列は asof の前日まで
          （バックテストで先読みを避ける）。
    extras: 過去データが無い軸の材料。
       eps: [(date, eps)]            … 日経平均の予想EPS
       revisions: [(date, up, down)] … 業績修正の件数（方向が表題で分かるもの）
       news: [(date, pos, neg)]      … 見出しの論調
       breadth: [(date, adv, dec)]   … 225採用銘柄の値上がり／値下がり数
    """
    extras = extras or {}
    J = lambda k: upto(m.get(k) or [], asof)                              # noqa: E731
    F = lambda k: upto(m.get(k) or [], asof, strict=strict_foreign)       # noqa: E731
    out = []

    # --- 業績 ---
    subs = []
    eps = [(d, v) for d, v in (extras.get("eps") or []) if asof is None or d <= asof]
    if len(eps) >= 2:
        last_d, last_v = eps[-1]
        # 20営業日前に最も近い値（最低10営業日は離す）
        idx = max(0, len(eps) - 21)
        base_d, base_v = eps[idx]
        span = len(eps) - 1 - idx
        if span >= 10 and base_v:
            ch = pct(last_v, base_v)
            subs.append(_sub("予想EPS", ch, f"予想EPS {base_v:,.0f}→{last_v:,.0f}（{span}営業日で {ch:+.1f}%）",
                             (-3, -1, 1, 3)))
    rev = [(d, u, dn) for d, u, dn in (extras.get("revisions") or []) if asof is None or d <= asof][-20:]
    up = sum(u for _, u, _ in rev)
    dn = sum(x for _, _, x in rev)
    if up + dn >= 6:
        share = up / (up + dn)
        subs.append(_sub("業績修正", share, f"上方 {up}件／下方 {dn}件（直近{len(rev)}営業日・表題で方向が分かるもの）",
                         (0.25, 0.38, 0.62, 0.75)))
    out.append(("earnings", subs))

    # --- モメンタム ---
    nk = J("nikkei")
    subs = [
        _sub("60日騰落", ret(nk, 60), f"日経平均 60日 {ret(nk, 60):+.1f}%" if ret(nk, 60) is not None else "",
             (-12, -4, 4, 12)),
        _sub("75日線", dev(nk, 75), f"75日線から {dev(nk, 75):+.1f}%" if dev(nk, 75) is not None else "",
             (-8, -3, 3, 8)),
    ]
    out.append(("momentum", subs))

    # --- 過熱感 ---
    subs = [
        _sub("RSI", rsi(nk), f"RSI(14) {rsi(nk):.0f}" if rsi(nk) is not None else "", (25, 35, 65, 75)),
        _sub("25日線乖離", dev(nk, 25), f"25日線乖離 {dev(nk, 25):+.1f}%" if dev(nk, 25) is not None else "",
             (-6, -3, 3, 6)),
    ]
    br = [(d, a, b) for d, a, b in (extras.get("breadth") or []) if asof is None or d <= asof][-25:]
    if len(br) >= 20:
        a = sum(x for _, x, _ in br)
        b = sum(x for _, _, x in br) or 1
        ratio = a / b * 100
        subs.append(_sub("騰落レシオ", ratio, f"騰落レシオ(25日・225採用) {ratio:.0f}", (70, 85, 115, 130)))
    out.append(("heat", subs))

    # --- 原油 ---
    wti = F("wti")
    ch = ret(wti, 20)
    out.append(("oil", [_sub("WTI 20日", ch, f"WTI {wti[-1]:.1f}ドル（20日 {ch:+.1f}%）" if ch is not None else "",
                             (-12, -5, 5, 12), -1)]))

    # --- 金利 ---
    subs = []
    for key, label in (("us10y", "米10年"), ("jp10y", "日本10年")):
        xs = F(key)
        if len(xs) > 20:
            bp = (xs[-1] - xs[-21]) * 100
            subs.append(_sub(label, bp, f"{label} {xs[-1]:.2f}%（20日 {bp:+.0f}bp）", (-30, -12, 12, 30), -1))
    out.append(("rates", subs))

    # --- 為替 ---
    fx = F("usdjpy")
    ch = ret(fx, 20)
    out.append(("fx", [_sub("ドル円 20日", ch, f"ドル円 {fx[-1]:.2f}（20日 {ch:+.1f}%、+は円安）" if ch is not None else "",
                            (-3, -1.2, 1.2, 3))]))

    # --- 米国株 ---
    spx, sox = F("spx"), F("sox")
    subs = [
        _sub("S&P500 20日", ret(spx, 20), f"S&P500 20日 {ret(spx, 20):+.1f}%" if ret(spx, 20) is not None else "",
             (-6, -2.5, 2.5, 6)),
        _sub("SOX 20日", ret(sox, 20), f"SOX 20日 {ret(sox, 20):+.1f}%" if ret(sox, 20) is not None else "",
             (-12, -5, 5, 12)),
    ]
    out.append(("us", subs))

    # --- センチメント ---
    vix, nkvi = F("vix"), J("nkvi")
    subs = [
        _sub("VIX", vix[-1] if vix else None, f"VIX {vix[-1]:.1f}" if vix else "", (13, 16, 22, 30), -1),
        _sub("日経VI", nkvi[-1] if nkvi else None, f"日経VI {nkvi[-1]:.1f}" if nkvi else "", (18, 22, 28, 35), -1),
    ]
    news = [(d, p, n) for d, p, n in (extras.get("news") or []) if asof is None or d <= asof][-3:]
    pos = sum(p for _, p, _ in news)
    neg = sum(n for _, _, n in news)
    if pos + neg >= 10:
        share = pos / (pos + neg)
        subs.append(_sub("見出しの論調", share, f"見出し 強気 {pos}／弱気 {neg}（{len(news)}営業日）",
                         (0.25, 0.38, 0.62, 0.75)))
    out.append(("sentiment", subs))

    res = []
    for key, subs in out:
        subs = [s for s in subs if s]
        c = combine(subs)
        label = dict(FACTORS)[key]
        if c is None:
            res.append({"key": key, "label": label, "score": None, "tilt": None, "subs": [],
                        "note": FACTOR_NOTE[key], "available": False})
            continue
        res.append({"key": key, "label": label, "score": c[0], "tilt": c[1], "subs": subs,
                    "note": FACTOR_NOTE[key], "available": True})
    return res


ZONES = [
    # (下限, 名前, トーン, 読み方)
    (80, "過熱", "hot",
     "一般に天井圏で見られる形。新規の買いは見送るか小さく、保有株は一部の利益確定を検討する局面。"
     "上がった日の成行買い（高値掴み）に最も注意"),
    (62, "楽観", "warm",
     "買うなら押し目を待つ局面。急騰した銘柄を追いかけるより、25日線まで下がるのを待つ"),
    (39, "中立", "neutral",
     "全体の方向感は中立。いま効いている型に当てはまる個別の候補を選んで拾う局面"),
    (21, "悲観", "cool",
     "悲観に傾いている。狼狽売りは避け、分割で拾い始める候補を探す局面"),
    (-1, "総悲観", "cold",
     "一般に大底圏で見られる形。中長期の分割買いを検討する局面。一度に買わず3〜4回に分ける"),
]


def zone_of(temp: float) -> dict:
    for lo, name, tone, text in ZONES:
        if temp >= lo:
            return {"zone": name, "tone": tone, "guide": text}
    return {"zone": ZONES[-1][1], "tone": ZONES[-1][2], "guide": ZONES[-1][3]}


def temperature(factors: list[dict], min_factors: int = 5) -> dict | None:
    avail = [f for f in factors if f.get("available")]
    if len(avail) < min_factors:
        return None
    sc = [f["score"] for f in avail]
    temp = round(50 + 25 * mean(sc))
    tail = sum(1 for s in sc if s > 0)
    head = sum(1 for s in sc if s < 0)
    n = len(sc)
    consensus = None
    if head == 0 and tail >= n - 1:
        consensus = "全面追い風"
    elif tail == 0 and head >= n - 1:
        consensus = "全面向かい風"
    return {"temp": temp, "tailwind": tail, "headwind": head, "neutral": n - tail - head,
            "n": n, "consensus": consensus, **zone_of(temp)}


def direction(now: list[dict], before: list[dict], eps: float = 0.25) -> dict:
    """10営業日前と比べて、各軸が改善（追い風の側へ）したか悪化したか。"""
    prev = {f["key"]: f for f in before if f.get("available")}
    improving, worsening = [], []
    for f in now:
        p = prev.get(f["key"])
        if not f.get("available") or not p:
            f["change"] = None
            continue
        d = f["tilt"] - p["tilt"]
        f["change"] = "改善" if d > eps else "悪化" if d < -eps else "横ばい"
        f["score_before"] = p["score"]
        (improving if d > eps else worsening if d < -eps else []).append(f["label"])
    return {"improving": improving, "worsening": worsening}


def turning_signal(t: dict, d: dict) -> str | None:
    if not t:
        return None
    imp, wor = len(d["improving"]), len(d["worsening"])
    if t["temp"] <= 38 and imp >= 2 and imp > wor:
        return "底打ちの兆し（低温のまま、改善し始めた軸が増えている）"
    if t["temp"] >= 62 and wor >= 2 and wor > imp:
        return "天井打ちの兆し（高温のまま、悪化し始めた軸が増えている）"
    return None


def market_thermo(m: dict, asof: str | None = None, extras: dict | None = None,
                  lookback: int = 10) -> dict | None:
    """今日の温度計。10営業日前の温度計と比べて変化の向きも出す。"""
    nk_dates = [d for d, _ in (m.get("nikkei") or [])]
    if asof is not None:
        nk_dates = [d for d in nk_dates if d <= asof]
    if len(nk_dates) < 80:
        return None
    now = market_factors(m, asof, extras=extras)
    t = temperature(now)
    if not t:
        return {"factors": now, "temp": None, "zone": "材料不足", "tone": "neutral",
                "guide": "採点できた軸が5つ未満のため、温度は出しません"}
    past_asof = nk_dates[-1 - lookback] if len(nk_dates) > lookback else None
    before = market_factors(m, past_asof, extras=extras) if past_asof else []
    tb = temperature(before) if before else None
    d = direction(now, before)
    return {**t, "factors": now, "asof": nk_dates[-1], "temp_before": tb["temp"] if tb else None,
            "before_asof": past_asof, "improving": d["improving"], "worsening": d["worsening"],
            "turning": turning_signal(t, d)}


# ==================== バックテスト ====================


def backtest(m: dict, horizons=(20, 60), warmup: int = 80, breadth=None) -> dict | None:
    """温度計を過去の毎日に当て、温度帯ごとに「その後の日経平均」を集計する。

    過去データが無い軸（業績・見出しの論調）は使わない。海外系列は前日までの値を使い、
    その日の東証の引けの時点で分かっていた材料だけで判定する（先読みしない）。
    """
    nk = m.get("nikkei") or []
    if len(nk) < warmup + max(horizons) + 20:
        return None
    closes = [c for _, c in nk]
    extras = {"breadth": breadth} if breadth else None
    rows = []
    for i in range(warmup, len(nk)):
        d = nk[i][0]
        f = market_factors(m, d, strict_foreign=True, extras=extras)
        t = temperature(f)
        if not t:
            continue
        fwd = {h: pct(closes[i + h], closes[i]) if i + h < len(closes) else None for h in horizons}
        rows.append({"date": d, "temp": t["temp"], "zone": t["zone"], "consensus": t["consensus"], "fwd": fwd})
    if not rows:
        return None

    def stats(sel: list[dict]) -> dict:
        out = {"days": len(sel)}
        # 連続した日をひとかたまりと数えた「局面の数」（標本が見た目より少ないことを示す）
        dates = [r["date"] for r in sel]
        idx = {d: i for i, d in enumerate(r["date"] for r in rows)}
        runs, prev = 0, None
        for d in dates:
            if prev is None or idx[d] - idx[prev] > 1:
                runs += 1
            prev = d
        out["episodes"] = runs
        for h in horizons:
            v = [r["fwd"][h] for r in sel if r["fwd"][h] is not None]
            out[f"n{h}"] = len(v)
            out[f"median{h}"] = r2(median(v)) if v else None
            out[f"up{h}"] = round(sum(1 for x in v if x > 0) / len(v) * 100) if v else None
        return out

    zones = []
    for lo, name, tone, _ in ZONES:
        sel = [r for r in rows if r["zone"] == name]
        zones.append({"zone": name, "tone": tone, **stats(sel)})
    cons = []
    for c in ("全面追い風", "全面向かい風"):
        sel = [r for r in rows if r["consensus"] == c]
        cons.append({"consensus": c, **stats(sel)})
    base = stats(rows)
    return {"from": rows[0]["date"], "to": rows[-1]["date"], "all": base, "zones": zones,
            "consensus": cons, "horizons": list(horizons),
            "series": [[r["date"], r["temp"]] for r in rows[-250:]],
            "note": "価格から計算できる軸（業績・見出しの論調を除く）で毎日の温度を再現し、"
                    "その日の終値から n 営業日後の日経平均の騰落を集計した。期間が重なるため、"
                    "「日数」より「局面」の数が実質の標本数に近い。"}


# ==================== 225採用銘柄の騰落（騰落レシオの材料） ====================


def breadth_series(dates: list[str], stocks: dict[str, list]) -> list[tuple[str, int, int]]:
    out = []
    arrs = list(stocks.values())
    for i in range(1, len(dates)):
        a = dcount = 0
        for arr in arrs:
            p, c = arr[i - 1], arr[i]
            if p is None or c is None:
                continue
            if c > p:
                a += 1
            elif c < p:
                dcount += 1
        if a + dcount >= 50:
            out.append((dates[i], a, dcount))
    return out


# ==================== 見出しの論調 ====================
POS_WORDS = ["最高値", "上場来高値", "年初来高値", "高値更新", "急騰", "急伸", "大幅高", "ストップ高", "続伸",
             "上方修正", "増配", "最高益", "増益", "好調", "好決算", "上振れ", "買い優勢", "反発", "上昇", "買われ",
             "大幅反発", "底堅", "期待"]
NEG_WORDS = ["急落", "暴落", "大幅安", "ストップ安", "続落", "下方修正", "減配", "減益", "赤字", "懸念", "警戒",
             "売り優勢", "反落", "下落", "売られ", "安値", "ショック", "失望", "下振れ", "不安", "急反落", "軟調"]
THEME_ALIASES = {
    "半導体": ["半導体", "SOX", "エヌビディア", "NVIDIA", "キオクシア", "アドテスト", "東エレク"],
    "AI": ["AI", "生成AI", "人工知能"],
    "データセンター": ["データセンター", "ＤＣ"],
    "防衛": ["防衛"],
    "銀行": ["銀行", "メガバンク", "地銀"],
    "自動車": ["自動車", "トヨタ"],
    "電線": ["電線", "フジクラ"],
    "原子力": ["原子力", "原発"],
    "造船": ["造船"],
    "商社": ["商社"],
}


def headline_tone(titles: list[str], theme_names: list[str] | None = None) -> dict:
    """見出しの強気／弱気の語を数える。1見出しにつき強気・弱気はそれぞれ最大1回。"""
    pos = neg = 0
    themes: dict[str, list[int]] = {}
    keys = {t: THEME_ALIASES.get(t, [t]) for t in (theme_names or []) if len(t) >= 2}
    for k, v in THEME_ALIASES.items():
        keys.setdefault(k, v)
    for t in titles:
        if not t:
            continue
        p = any(w in t for w in POS_WORDS)
        n = any(w in t for w in NEG_WORDS)
        pos += p
        neg += n
        if not (p or n):
            continue
        for th, words in keys.items():
            if any(w in t for w in words):
                cnt = themes.setdefault(th, [0, 0])
                cnt[0] += p
                cnt[1] += n
    return {"pos": pos, "neg": neg, "themes": themes, "n": len([t for t in titles if t])}


# ==================== 開示の方向 ====================
UP_RE = re.compile(r"上方|増配|増額|上回")
DOWN_RE = re.compile(r"下方|減配|減額|無配|下回")


def disclosure_dir(r: dict) -> str | None:
    """業績・配当の修正の方向。表題で分からない・両方書いてあるものは None。"""
    if r.get("category") not in ("業績予想の修正", "配当予想の修正"):
        return None
    t = r.get("title") or ""
    u, d = bool(UP_RE.search(t)), bool(DOWN_RE.search(t))
    if u and not d:
        return "up"
    if d and not u:
        return "down"
    return None


def material_events(rows: list[dict], date_str: str) -> list[dict]:
    out = []
    seen = set()
    for r in rows or []:
        d = disclosure_dir(r)
        code = str(r.get("code") or "")
        if not d or not code or (code, d) in seen:
            continue
        seen.add((code, d))
        t = r.get("time") or ""
        mm = re.match(r"(\d{1,2}):(\d{2})", t)
        after = bool(mm and int(mm.group(1)) * 60 + int(mm.group(2)) >= 15 * 60 + 30)
        out.append({"date": date_str, "code": code, "name": r.get("name"), "dir": d, "after": after,
                    "title": (r.get("title") or "")[:80]})
    return out


# ==================== 個別株の指標 ====================


def px(v):
    """株価の丸め（100円以上は円単位、未満は0.1円）。"""
    if v is None:
        return None
    return round(v) if v >= 100 else r1(v)


def stock_metrics(xs: list[float]) -> dict | None:
    xs = [x for x in xs if x is not None]
    if len(xs) < 30:
        return None
    ma25, ma75 = sma(xs, 25), sma(xs, 75)
    ma25_before = sma(xs[:-5], 25)
    hi = max(xs[-120:])
    return {"price": xs[-1], "d1": r2(ret(xs, 1)), "r5": r1(ret(xs, 5)), "r20": r1(ret(xs, 20)),
            "r60": r1(ret(xs, 60)), "rsi": r1(rsi(xs)), "dev25": r1(dev(xs, 25)),
            "ma25": px(ma25), "ma75": px(ma75),
            # 25日線の向き（5営業日前の25日線からの変化率）。上向きに変わったかを見る
            "slope25": r2(pct(ma25, ma25_before)) if ma25 and ma25_before else None,
            "from_hi": r1(pct(xs[-1], hi)), "vol": r2(daily_vol(xs)),
            "lo20": px(min(xs[-20:])), "hi60": px(max(xs[-60:])), "hi120": px(hi)}


# ==================== 相対力 ====================
LEADER_RS = 80          # 60日騰落がユニバースの上位20%
LEADER_FROM_HI = -8.0   # 120日高値から −8% 以内


def rs_ranks(r60: dict[str, float | None]) -> dict[str, int]:
    """60日騰落のユニバース内の順位（0〜100。100 が最も強い）。"""
    vals = sorted((v, c) for c, v in r60.items() if v is not None)
    n = len(vals)
    if n < 20:
        return {}
    return {c: round(i / (n - 1) * 100) for i, (_, c) in enumerate(vals)}


def is_leader(mt: dict, rs: int | None) -> bool:
    """相対力リーダー: 60日で上位20%、株価 > 25日線 > 75日線、120日高値の近く。"""
    p, ma25, ma75, fh = mt.get("price"), mt.get("ma25"), mt.get("ma75"), mt.get("from_hi")
    return bool(rs is not None and rs >= LEADER_RS and p and ma25 and ma75 and p > ma25 > ma75
                and fh is not None and fh >= LEADER_FROM_HI)


# 2026-09-26 に足した型（DESIGN.md 11章）。どちらも毎日の検証（setup_backtest）で効き目を測り直す。
#   深押し   : 上昇トレンド（60日+5%以上・25日線≧75日線）の中で、5日で −7% 以上の急落
#   上向き転換: 25日線が上を向き（5営業日前より上）、株価が25日線の上。ただし25日線はまだ75日線の下
#               （下落トレンドからの転換の初動）。25日線から離れすぎたもの（+8%超）は追いかけになるので外す
DEEP_DIP_R5 = -7.0
TURN_MAX_DEV = 8.0


def stock_class(mt: dict, rs_rank: int | None = None) -> list[str]:
    """個別株の分類（複数可）。順序は表示の優先度。"""
    out = []
    rs, dv, r5, r60 = mt.get("rsi"), mt.get("dev25"), mt.get("r5"), mt.get("r60")
    d1 = mt.get("d1")
    ma25, ma75 = mt.get("ma25"), mt.get("ma75")
    if (rs is not None and rs >= 75) or (dv is not None and dv >= 15) or (r5 is not None and r5 >= 15):
        out.append("高値掴み注意")
    if (r60 is not None and r60 >= 5 and ma25 and ma75 and ma25 >= ma75
            and dv is not None and -6 <= dv <= 1 and rs is not None and 30 <= rs <= 50):
        out.append("押し目")
    if ((rs is not None and rs <= 30) or (dv is not None and dv <= -10)) and d1 is not None and d1 > 0:
        out.append("売られすぎ・下げ止まり")
    elif (rs is not None and rs <= 30) or (dv is not None and dv <= -10):
        out.append("売られすぎ（下げ継続）")
    if ma25 and ma75 and ma25 < ma75 and mt.get("r20") is not None and mt["r20"] < 0:
        out.append("下落トレンド")
    if is_leader(mt, rs_rank):
        out.append("相対力リーダー")
    if (r5 is not None and r5 <= DEEP_DIP_R5 and r60 is not None and r60 >= 5
            and ma25 and ma75 and ma25 >= ma75):
        out.append("深押し")
    p, slope = mt.get("price"), mt.get("slope25")
    if (p and ma25 and ma75 and slope is not None and slope > 0 and p > ma25 and ma25 < ma75
            and dv is not None and dv <= TURN_MAX_DEV):
        out.append("上向き転換")
    return out


# ==================== 売買計画（買う目安・損切り・利確の目安） ====================
# すべて終値と日々の値幅から機械的に出す「計算上の目安」。予測ではない。
#   損切り: 直近20日の安値の1%下。ただし日々の値幅（60日の標準偏差）の2.5〜3.5倍の範囲に収める
#           （近すぎるとふだんの揺れで掛かり、遠すぎると1回の損が大きくなるため）。
#           2026-09-26 に 1.5〜3倍 から広げた: 1.5倍は1日の揺れの範囲で、損切りの直後に戻される形が多かった
#           （389銘柄×約50営業日の再現で、押し目の勝率 45%→53%、平均Rは +0.27→+0.28。DESIGN.md 11章）
#   利確  : 押し目・リーダーは60日高値（直近の戻り高値）、売られすぎ・出尽くしは25日線（平均への戻り）。
#           ただし20営業日のふだんの値幅（日々の値幅×√20）の1.5倍までに抑える。届きそうにない利確で
#           R倍を大きく見せないため
#   R倍   : (利確 − 買い) ÷ (買い − 損切り)。1回の損を1としたときの、狙える幅
STOP_MIN_VOL = 2.5
STOP_MAX_VOL = 3.5
TARGET_MAX_SIGMA = 1.5
HOLD_DAYS = 20
PLAN_KINDS = ("dip", "oversold", "leader", "price")


def plan_kind(cls: list[str], ev: dict | None = None) -> str:
    """銘柄の分類から、どの計画の型を当てるか。"""
    if "押し目" in cls or "高値掴み注意" in cls:
        return "dip"                     # 25日線まで待つ
    if "売られすぎ・下げ止まり" in cls or (ev and str(ev.get("label", "")).startswith("悪材料出尽くし")):
        return "oversold"
    if "深押し" in cls:
        return "oversold"                # 急落の戻り（25日線まで）を狙う
    if "相対力リーダー" in cls:
        return "leader"
    return "price"


def trade_plan(mt: dict, kind: str = "price") -> dict | None:
    p, vol = mt.get("price"), mt.get("vol")
    if not p or not vol:
        return None
    ma25, lo20, hi = mt.get("ma25"), mt.get("lo20"), mt.get("hi60")
    if kind == "dip" and ma25 and p > ma25:
        entry, entry_by = ma25, "ma25"            # 25日線まで待つ（指値）
    else:
        entry, entry_by = p, "price"
    v = vol / 100
    far, near = entry * (1 - STOP_MAX_VOL * v), entry * (1 - STOP_MIN_VOL * v)
    struct = lo20 * 0.99 if lo20 else None
    if struct is None or struct > near:
        stop, stop_by = near, "vol_min"           # 20日安値が近すぎる（または上にある）
    elif struct < far:
        stop, stop_by = far, "vol_max"            # 20日安値が遠すぎる
    else:
        stop, stop_by = struct, "lo20"
    risk = entry - stop
    if kind == "oversold":
        target, target_by = (ma25, "ma25") if ma25 and ma25 > entry else (None, None)
    else:
        target, target_by = (hi, "hi60") if hi and hi > entry + 0.5 * risk else (None, None)
    cap = entry * (1 + TARGET_MAX_SIGMA * v * HOLD_DAYS ** 0.5)
    if target and target > cap:
        target, target_by = cap, "cap"            # 20営業日で届く距離に抑える
    rr = (target - entry) / risk if target and risk > 0 else None
    return {"kind": kind, "entry": px(entry), "entry_by": entry_by, "to_entry": r1(pct(entry, p)),
            "stop": px(stop), "stop_by": stop_by, "stop_pct": r1(pct(stop, entry)),
            "target": px(target), "target_by": target_by, "target_pct": r1(pct(target, entry)) if target else None,
            "rr": r1(rr)}


def simulate_plan(closes: list[float | None], plan: dict, fill_days: int = 5, hold: int = 20) -> dict | None:
    """計画どおりに売買したら、を終値だけで再現する（場中の高安は見ない）。

    closes: 計画を立てた日の「翌日以降」の終値。指値（買う目安が現値より下）は fill_days 以内に
    終値が目安以下になったら目安の価格で約定したとみなす。約定後、終値が損切りを割るか利確に届くか、
    hold 営業日たったら終わる。結果は R（1回の損を1とした損益）で返す。
    """
    entry, stop, target = plan["entry"], plan["stop"], plan.get("target")
    risk = entry - stop
    if risk <= 0:
        return None
    start = 0
    if plan["entry_by"] != "price":
        for k, c in enumerate(closes[:fill_days]):
            if c is not None and c <= entry:
                start = k
                break
        else:
            return {"filled": False} if len(closes) >= fill_days else None
    held = 0
    last = entry
    for c in closes[start:]:
        if c is None:
            continue
        held += 1
        last = c
        if c <= stop:
            return {"filled": True, "exit": "stop", "r": round((c - entry) / risk, 2)}
        if target and c >= target:
            return {"filled": True, "exit": "target", "r": round((c - entry) / risk, 2)}
        if held >= hold:
            return {"filled": True, "exit": "time", "r": round((c - entry) / risk, 2)}
    return None                                    # まだ結果が出ていない


# ==================== 型ごとの過去成績（銘柄レベルのバックテスト） ====================
# 各営業日に、その日までの終値だけで分類をやり直し（先読みしない）、その型に「初めて入った日」を
# 1件として数える（同じ銘柄が何日も同じ型にいると件数が水増しされるため）。
# 比べる相手は同じ日の全銘柄の中央値（相場全体の上げ下げを差し引いた、銘柄選びの効き目）。
SETUPS = [
    # (型, 期待する向き, 計画の型)  expect=down は「警告」の型（その後に劣後すれば警告どおり）
    ("押し目", "up", "dip"),
    ("売られすぎ・下げ止まり", "up", "oversold"),
    ("相対力リーダー", "up", "leader"),
    ("深押し", "up", "oversold"),
    ("上向き転換", "up", "price"),
    ("高値掴み注意", "down", None),
    ("下落トレンド", "down", None),
]


def _median_or_none(v):
    return r2(median(v)) if v else None


def _share(v, cond=lambda x: x > 0):
    return round(sum(1 for x in v if cond(x)) / len(v) * 100) if v else None


def setup_verdict(expect: str, n: int, x: float | None, beat: int | None, min_n: int = 30) -> tuple[str, str]:
    """全銘柄比（x）と勝ち越し率（beat）から、その型がこの期間に効いたかを言う。"""
    if n < min_n or x is None:
        return "件数不足", "info"
    if expect == "up":
        if x >= 0.5 and (beat or 0) >= 52:
            return "効いている", "chance"
        if x <= -0.5 and (beat or 100) <= 48:
            return "効いていない", "warn"
        return "はっきりしない", "info"
    if x <= -0.5 and (beat or 100) <= 48:
        return "警告どおり（その後に劣後）", "chance"
    if x >= 0.5 and (beat or 0) >= 52:
        return "警告が外れている（その後も強い）", "warn"
    return "はっきりしない", "info"


def setup_backtest(dates: list[str], stocks: dict[str, list], horizons=(5, 20), warmup: int = 80,
                   recent: int = 20) -> dict | None:
    """型ごとに「その後 n 営業日の騰落・全銘柄比・計画どおりに売買した場合の R」を集計する。

    stocks: {code: [終値 or None]}（dates と同じ長さ）。当日の場中値は入れない（確定値だけで測る）。
    recent: 直近 recent 営業日に点灯したものだけの5日後も別に出す（効く型の入れ替わりを早く見る）。
    """
    n_days = len(dates)
    if n_days < warmup + max(horizons) // 2 or len(stocks) < 50:
        return None
    # 1) その日までの値だけで指標を作る
    mts: dict[int, dict[str, dict]] = {}
    for code, arr in stocks.items():
        xs: list[float] = []
        for i, c in enumerate(arr):
            if c is None:
                continue
            xs.append(c)
            if i < warmup:
                continue
            mt = stock_metrics(xs)
            if mt:
                mts.setdefault(i, {})[code] = mt
    # 2) 同じ日の全銘柄の先行きの中央値（比べる相手）
    def fwd(arr, i, h):
        if i + h >= n_days or arr[i] is None or arr[i + h] is None or not arr[i]:
            return None
        return pct(arr[i + h], arr[i])
    bench = {}
    for i in mts:
        for h in horizons:
            v = [f for f in (fwd(a, i, h) for a in stocks.values()) if f is not None]
            bench[(i, h)] = median(v) if len(v) >= 20 else None
    # 3) 型に初めて入った日を拾い、先行きと計画の結果を集める
    acc = {name: {"events": 0, **{f"d{h}": [] for h in horizons}, **{f"x{h}": [] for h in horizons},
                  "recent": [], "sim": [], "unfilled": 0}
           for name, _, _ in SETUPS}
    base = {**{f"d{h}": [] for h in horizons}, **{f"x{h}": [] for h in horizons}}
    prev: dict[str, set] = {}
    last_signal = n_days - 1
    for i in sorted(mts):
        day = mts[i]
        ranks = rs_ranks({c: mt.get("r60") for c, mt in day.items()})
        for code, mt in day.items():
            arr = stocks[code]
            cur = set(stock_class(mt, ranks.get(code)))
            new = cur - prev.get(code, set())
            prev[code] = cur
            f = {h: fwd(arr, i, h) for h in horizons}
            for h in horizons:
                if f[h] is not None and bench.get((i, h)) is not None:
                    base[f"d{h}"].append(f[h])
                    base[f"x{h}"].append(f[h] - bench[(i, h)])
            for name, _, kind in SETUPS:
                if name not in new:
                    continue
                a = acc[name]
                a["events"] += 1
                for h in horizons:
                    if f[h] is not None and bench.get((i, h)) is not None:
                        a[f"d{h}"].append(f[h])
                        a[f"x{h}"].append(f[h] - bench[(i, h)])
                        if h == horizons[0] and i >= last_signal - recent - h:
                            a["recent"].append(f[h] - bench[(i, h)])
                if kind:
                    plan = trade_plan(mt, kind)
                    if plan:
                        res = simulate_plan(arr[i + 1:], plan)
                        if res and res["filled"]:
                            a["sim"].append(res)
                        elif res:
                            a["unfilled"] += 1
        # 分類できなかった銘柄は「型から外れた」とみなす
        for code in list(prev):
            if code not in day:
                prev[code] = set()

    h1, h2 = horizons[0], horizons[-1]
    rows = []
    for name, expect, kind in SETUPS:
        a = acc[name]
        xh = a[f"x{h2}"] if len(a[f"x{h2}"]) >= 30 else a[f"x{h1}"]
        verdict, tone = setup_verdict(expect, len(xh), _median_or_none(xh), _share(xh))
        sims = a["sim"]
        row = {"setup": name, "expect": expect, "plan": kind, "events": a["events"],
               "verdict": verdict, "tone": tone, "judged_on": h2 if xh is a[f"x{h2}"] else h1}
        for h in horizons:
            row[f"n{h}"] = len(a[f"d{h}"])
            row[f"d{h}"] = _median_or_none(a[f"d{h}"])
            row[f"x{h}"] = _median_or_none(a[f"x{h}"])
            row[f"beat{h}"] = _share(a[f"x{h}"])
        row["recent_n"] = len(a["recent"])
        row["recent_x"] = _median_or_none(a["recent"])
        if kind:
            rs_ = [s["r"] for s in sims]
            row.update({"trades": len(sims), "unfilled": a["unfilled"],
                        "avg_r": r2(mean(rs_)) if rs_ else None, "win": _share(rs_),
                        "stops": sum(1 for s in sims if s["exit"] == "stop"),
                        "targets": sum(1 for s in sims if s["exit"] == "target"),
                        "timeouts": sum(1 for s in sims if s["exit"] == "time")})
        rows.append(row)
    first = min(mts) if mts else 0
    return {"from": dates[first], "to": dates[-1], "universe": len(stocks), "horizons": list(horizons),
            "base": {**{f"d{h}": _median_or_none(base[f"d{h}"]) for h in horizons},
                     **{f"n{h}": len(base[f"d{h}"]) for h in horizons}},
            "setups": rows,
            "note": "各営業日にその日までの終値だけで分類をやり直し、型に初めて入った日を1件として、"
                    "その後の騰落を同じ日の全銘柄の中央値と比べた。計画の結果（R）は終値だけで再現しており、"
                    "場中の高安・売買コストは含まない。効く型は相場によって入れ替わる。"}


JUMP_OTHER = 12.0    # 開示からこれ以上動いたら「出尽くし」ではなく別の材料とみなす（%）


def exhaustion(events: list[dict], price_on, today: str) -> list[dict]:
    """開示のあとの値動きの反応で「出尽くし」を判定する。

    events: material_events（直近のもの）。price_on(code, date, before) -> 終値
      before=True は「その日の前営業日の終値」（場中の開示の基準）。
    """
    out = []
    for e in events:
        base = price_on(e["code"], e["date"], not e["after"])
        now = price_on(e["code"], today, False)
        if not base or not now or e["date"] >= today:
            continue
        ch = pct(now, base)
        label = None
        if abs(ch) >= JUMP_OTHER:
            # 修正の方向と関係なく大きく動いたものは、TOB・増資など別の材料の可能性が高い。出尽くしとは言わない
            label, tone = "開示後に急変（別の材料の可能性）", "info"
        elif e["dir"] == "up" and ch <= -2:
            label, tone = "好材料出尽くし", "warn"
        elif e["dir"] == "up" and ch >= 3:
            label, tone = "好材料を素直に評価", "info"
        elif e["dir"] == "down" and ch >= 0:
            label, tone = "悪材料出尽くし（アク抜け）", "chance"
        elif e["dir"] == "down" and ch <= -5:
            label, tone = "悪材料を消化中（下げ継続）", "info"
        if label:
            out.append({**e, "since_pct": r1(ch), "label": label, "tone": tone})
    return out


# ==================== 業種 ====================


def sector_index(members: list[list], n_dates: int) -> list[float]:
    """等ウェイトの業種指数（日次騰落の平均を積み上げ、初日=100）。"""
    idx = [100.0]
    for i in range(1, n_dates):
        rs = [pct(a[i], a[i - 1]) for a in members if a[i] is not None and a[i - 1]]
        idx.append(idx[-1] * (1 + (mean(rs) if rs else 0) / 100))
    return idx


DRIVER_SCALE = {"us10y": ("bp", 20), "jp10y": ("bp", 15), "yen": ("pct", 2),
                "oil": ("pct", 8), "sox": ("pct", 8), "spx": ("pct", 4)}
DRIVER_LABEL = {"us10y": "米金利", "jp10y": "国内金利", "yen": "円安", "oil": "原油", "sox": "米半導体", "spx": "米株"}
DRIVER_SRC = {"us10y": "us10y", "jp10y": "jp10y", "yen": "usdjpy", "oil": "wti", "sox": "sox", "spx": "spx"}


def macro_drivers(m: dict, n: int = 20) -> dict:
    """20日のマクロの動きを −1.5〜+1.5 に正規化する（金利は bp、ほかは %）。"""
    out = {}
    for k, (unit, scale) in DRIVER_SCALE.items():
        xs = [c for _, c in (m.get(DRIVER_SRC[k]) or [])]
        if len(xs) <= n:
            continue
        raw = (xs[-1] - xs[-1 - n]) * 100 if unit == "bp" else pct(xs[-1], xs[-1 - n])
        out[k] = {"raw": r1(raw), "unit": unit, "norm": max(-1.5, min(1.5, raw / scale))}
    return out


def macro_fit(sector: str, drivers: dict) -> dict | None:
    sens = SECTOR_MACRO_SENS.get(sector)
    if not sens or not drivers:
        return None
    parts = []
    for k, w in sens.items():
        dv = drivers.get(k)
        if dv is None:
            continue
        parts.append({"driver": DRIVER_LABEL[k], "value": round(w * dv["norm"], 2),
                      "raw": dv["raw"], "unit": dv["unit"]})
    if not parts:
        return None
    parts.sort(key=lambda p: -abs(p["value"]))
    score = round(sum(p["value"] for p in parts), 2)
    return {"score": score, "label": "追い風" if score >= 0.3 else "向かい風" if score <= -0.3 else "中立",
            "parts": parts[:3]}


def sector_class(mt: dict) -> str:
    rs, dv, z5 = mt.get("rsi"), mt.get("dev25"), mt.get("heat")
    r20, r60, d1 = mt.get("r20"), mt.get("r60"), mt.get("d1")
    if (rs is not None and rs >= 70) or (z5 is not None and z5 >= 2) or (dv is not None and dv >= 8):
        return "過熱"
    if (r60 is not None and r60 > 3 and mt.get("dev75") is not None and mt["dev75"] > 0
            and ((z5 is not None and z5 <= -1) or (dv is not None and dv <= -2))):
        return "押し目"
    if ((rs is not None and rs <= 35) or (dv is not None and dv <= -6)) and d1 is not None and d1 > 0:
        return "売られすぎ・下げ止まり"
    if r60 is not None and r20 is not None and r60 < 0 and r20 < 0:
        return "下落トレンド"
    if r60 is not None and r20 is not None and r60 > 0 and r20 > 0:
        return "上昇トレンド"
    return "中立"


CLASS_PICK = {"押し目": 2.0, "売られすぎ・下げ止まり": 1.5, "上昇トレンド": 0.5, "中立": 0.0,
              "下落トレンド": -1.0, "過熱": -2.0}
CLASS_TEXT = {
    "押し目": "中期は上昇、足元で調整。ざら場で拾う候補（25日線付近まで待つ）",
    "売られすぎ・下げ止まり": "売られすぎの水準で下げ止まり。逆張りの候補（分割で）",
    "上昇トレンド": "素直な上昇。追いかけるより押しを待つ",
    "中立": "方向感なし",
    "下落トレンド": "下落の途中。下げ止まりを確認するまで様子見",
    "過熱": "短期で上がりすぎ。ここからの買いは高値掴みになりやすい",
}


def sector_board(dates: list[str], stocks: dict[str, list], members: dict[str, dict],
                 drivers: dict, events: list[dict] | None = None) -> list[dict]:
    """業種ごとの温度と分類。members: {code: {"sector", "name"}}。
    当日の場中値は呼び出し側（thermo_run）が stocks の末尾に差し込んでから渡す。"""
    arrs = stocks
    by_sector: dict[str, list[str]] = {}
    for code, info in members.items():
        if info.get("sector") and code in arrs:
            by_sector.setdefault(info["sector"], []).append(code)

    rev: dict[str, list[int]] = {}
    for e in events or []:
        s = (members.get(e["code"]) or {}).get("sector")
        if s:
            r = rev.setdefault(s, [0, 0])
            r[0 if e["dir"] == "up" else 1] += 1

    out = []
    for sector, codes in by_sector.items():
        if len(codes) < 2:
            continue
        idx = sector_index([arrs[c] for c in codes], len(dates))
        mt = {"d1": r2(ret(idx, 1)), "r5": r1(ret(idx, 5)), "r20": r1(ret(idx, 20)), "r60": r1(ret(idx, 60)),
              "rsi": r1(rsi(idx)), "dev25": r1(dev(idx, 25)), "dev75": r1(dev(idx, 75))}
        vol = daily_vol(idx)
        mt["heat"] = r2(ret(idx, 5) / (vol * 5 ** 0.5)) if vol and ret(idx, 5) is not None else None
        cls = sector_class(mt)
        fit = macro_fit(sector, drivers)
        up, dn = rev.get(sector, [0, 0])
        rev_tilt = (up - dn) / max(3, up + dn)
        pick = CLASS_PICK[cls] + (max(-1.0, min(1.0, fit["score"])) if fit else 0) + 0.5 * rev_tilt
        # 業種の中の代表（押し目・下げ止まり向き: 5日で下げた順／過熱: 上げた順）
        mem = []
        for c in codes:
            xs = [x for x in arrs[c] if x is not None]
            if len(xs) > 5:
                mem.append((c, ret(xs, 5)))
        mem.sort(key=lambda x: (x[1] if x[1] is not None else 0), reverse=(cls == "過熱"))
        out.append({"sector": sector, "count": len(codes), **mt, "class": cls, "text": CLASS_TEXT[cls],
                    "macro": fit, "revisions": {"up": up, "down": dn}, "pick": round(pick, 2),
                    "sample": [{"code": c, "name": (members.get(c) or {}).get("name"), "r5": r1(v)} for c, v in mem[:3]]})
    out.sort(key=lambda s: -s["pick"])
    return out


# ==================== テーマ（見出しの論調 × 値動き） ====================


def theme_board(dates: list[str], stocks: dict[str, list], themes: dict, tone_days: list[dict],
                min_members: int = 3) -> list[dict]:
    """テーマ単位の出尽くし判定。tone_days: 直近の日ごとの headline_tone（新しい順でも古い順でもよい）。"""
    stock_map = (themes or {}).get("stocks") or {}
    by_theme: dict[str, list[str]] = {}
    for code, entry in stock_map.items():
        if code not in stocks:
            continue
        for t in entry.get("themes") or []:
            by_theme.setdefault(t, []).append(code)
    tone: dict[str, list[int]] = {}
    for day in tone_days or []:
        for t, (p, n) in (day.get("themes") or {}).items():
            x = tone.setdefault(t, [0, 0])
            x[0] += p
            x[1] += n
    out = []
    for t, codes in by_theme.items():
        if len(codes) < min_members:
            continue
        idx = sector_index([stocks[c] for c in codes], len(dates))
        mt = {"d1": r2(ret(idx, 1)), "r3": r1(ret(idx, 3)), "r5": r1(ret(idx, 5)), "r20": r1(ret(idx, 20)),
              "rsi": r1(rsi(idx)), "dev25": r1(dev(idx, 25))}
        p, n = tone.get(t, [0, 0])
        label = tone_cls = None
        if p + n >= 5 and p / (p + n) >= 0.7 and ((mt["rsi"] or 0) >= 65 or (mt["r5"] or 0) >= 5):
            label, tone_cls = "好材料一色で過熱（出尽くしに注意）", "warn"
        elif p + n >= 4 and n / (p + n) >= 0.6 and (mt["r20"] or 0) <= -5 and \
                ((mt["d1"] or 0) > 0 or (mt["r3"] or 0) > 0):
            label, tone_cls = "悪材料が続くが下げ止まり（出尽くしの兆し）", "chance"
        elif p + n >= 4 and n / (p + n) >= 0.6 and (mt["r5"] or 0) < 0:
            label, tone_cls = "悪材料を消化中", "info"
        elif (mt["rsi"] or 0) >= 75:
            label, tone_cls = "過熱", "warn"
        out.append({"theme": t, "count": len(codes), **mt, "news_pos": p, "news_neg": n,
                    "label": label, "tone": tone_cls})
    out.sort(key=lambda x: (x["label"] is None, -(x["r5"] or 0)))
    return out


# ==================== 提案の記録と成績 ====================


def track_update(track: dict, today: str, picks: list[dict], ret_of, nikkei_now: float | None,
                 horizons=(5, 20), cooldown: int = 20, keep: int = 800) -> dict:
    """提案（kind・key・name・price）を記録し、過去の提案の 5日／20日後を埋める。

    ret_of(entry) -> 記録した日から今日までの騰落率（%）。銘柄は株価、業種は構成銘柄の平均。
    同じ提案（kind・key）は cooldown 営業日のあいだ記録し直さない（期間の重なりで成績を水増ししない）。
    """
    entries = list(track.get("entries") or [])
    # 営業日の数え方: この関数が呼ばれた日（大引の実行日）の一覧を track に残して数える
    dates = sorted(set(track.get("dates") or []) | {e["date"] for e in entries} | {today})
    since = dates[-cooldown] if len(dates) >= cooldown else dates[0]
    recent = {(e["kind"], e["key"]) for e in entries if e["date"] >= since}
    for p in picks:
        if (p["kind"], p["key"]) in recent:
            continue
        entries.append({"date": today, "kind": p["kind"], "key": p["key"], "name": p.get("name"),
                        "price": p.get("price"), "nk": nikkei_now, **{f"d{h}": None for h in horizons},
                        **{f"x{h}": None for h in horizons}, "days": 0})
        recent.add((p["kind"], p["key"]))
    for e in entries:
        if e["date"] >= today:
            continue
        days = len([d for d in dates if e["date"] < d <= today])
        e["days"] = days
        if all(e.get(f"d{h}") is not None for h in horizons):
            continue
        rr = ret_of(e)
        if rr is None:
            continue
        nk = pct(nikkei_now, e.get("nk")) if nikkei_now and e.get("nk") else None
        for h in horizons:
            if e.get(f"d{h}") is None and days >= h:
                e[f"d{h}"] = r2(rr)
                e[f"x{h}"] = r2(rr - nk) if nk is not None else None
    entries = entries[-keep:]
    stats = []
    for kind in sorted({e["kind"] for e in entries}):
        sel = [e for e in entries if e["kind"] == kind]
        row = {"kind": kind, "count": len(sel)}
        for h in horizons:
            v = [e[f"d{h}"] for e in sel if e.get(f"d{h}") is not None]
            x = [e[f"x{h}"] for e in sel if e.get(f"x{h}") is not None]
            row[f"n{h}"] = len(v)
            row[f"d{h}_median"] = r2(median(v)) if v else None
            row[f"x{h}_median"] = r2(median(x)) if x else None
            row[f"beat{h}"] = round(sum(1 for y in x if y > 0) / len(x) * 100) if x else None
        stats.append(row)
    return {"updated_at": today, "dates": dates[-400:], "entries": entries, "stats": stats}


# ==================== 作戦ボード（今日、計画を立てられる銘柄） ====================
BOARD_MIN_RR = 1.5          # 利確の目安までが損切り幅の1.5倍に満たないものは載せない
BOARD_MAX_STOP = 12.0       # 損切りまで12%を超える（値動きが荒すぎる）ものは載せない
BOARD_SETUPS = ("押し目", "深押し", "上向き転換", "売られすぎ・下げ止まり", "相対力リーダー")
TONE_ORDER = {"chance": 0, "info": 1, "warn": 2}


def action_board(rows: dict[str, dict], setups: dict | None, ledger: dict[str, dict] | None = None,
                 limit: int = 8) -> list[dict]:
    """型に当てはまり、計画（買う目安・損切り・利確）が立つ銘柄を、型の直近成績の良い順に並べる。

    rows: thermo_run の stock_rows（cls・plan・ev を持つ）。ledger: 台帳で追跡中の {code: entry}。
    過熱（高値掴み注意）、利確までが近すぎるもの（R倍 < 1.5）、損切りまでが遠すぎるもの（12%超）は載せない。
    直近の検証で「効いていない」型だけに当てはまる銘柄も載せない（負けている型で入らない）。
    複数の型に当てはまるときは、成績の良い型で載せる。
    """
    verdict = {s["setup"]: s for s in (setups or {}).get("setups") or []}
    ledger = ledger or {}
    out = []
    for code, r in rows.items():
        cls = r.get("cls") or []
        if "高値掴み注意" in cls:
            continue
        live = [s for s in BOARD_SETUPS if s in cls and (verdict.get(s) or {}).get("verdict") != "効いていない"]
        live.sort(key=lambda s: (TONE_ORDER.get((verdict.get(s) or {}).get("tone"), 1),
                                 -((verdict.get(s) or {}).get("avg_r") or 0)))
        setup = live[0] if live else None
        ev = r.get("ev")
        if not setup and ev and str(ev.get("label", "")).startswith("悪材料出尽くし"):
            setup = "悪材料出尽くし"
        plan = r.get("plan")
        if not setup or not plan:
            continue
        rr = plan.get("rr")
        if rr is None and plan.get("kind") != "leader":
            continue
        if rr is not None and rr < BOARD_MIN_RR:
            continue
        if plan.get("stop_pct") is not None and plan["stop_pct"] < -BOARD_MAX_STOP:
            continue
        v = verdict.get(setup) or {}
        why = []
        led = ledger.get(code)
        if led:
            why.append("台帳: " + "・".join(led.get("signals") or []))
        if ev:
            why.append(f"{ev['label']}（{ev['date']} の開示から {ev['since']:+.1f}%）")
        out.append({"code": code, "name": r.get("n"), "sector": r.get("s"), "setup": setup,
                    "verdict": v.get("verdict"), "tone": v.get("tone") or "info",
                    "price": r.get("price"), "d1": r.get("d1"), "rsi": r.get("rsi"), "rs": r.get("rs"),
                    "plan": plan, "ledger": bool(led), "why": why})
    avg_r = {k: (v.get("avg_r") or 0) for k, v in verdict.items()}
    out.sort(key=lambda x: (TONE_ORDER.get(x["tone"], 1), not x["ledger"], -avg_r.get(x["setup"], 0),
                            -(x["plan"].get("rr") or BOARD_MIN_RR), x["code"]))
    return out[:limit]


# ==================== 今日のスタンス（攻め／選んで小さく／守り） ====================
# 画面の先頭に出す「今日どうするか」。地合い（日経平均の25日線・75日線）、いま効いている買いの型、
# 温度帯の過去の成績の3つを −1／0／+1 で数え、合計で決める。どの条件で何点かは reasons にそのまま出す。
# risk は「1回の損の上限」に掛ける倍率（負けやすい地合いでは株数を減らす）。予測ではなく、資金管理の目安。
STANCES = [
    # (合計の下限, キー, 名前, 1回の損に掛ける倍率, 読み方)
    (2, "attack", "攻め", 1.0, "効いている型の候補を、計画どおりの株数で。損切りの価格は先に決める"),
    (0, "select", "選んで小さく", 0.5, "効いている型の候補だけを、株数は通常の半分で。上がった銘柄の追いかけ買いはしない"),
    (-99, "defend", "守り", 0.25, "新規の買いは見送るか、通常の1/4の株数で試すだけ。保有株は損切りの価格を確かめる"),
]
STANCE_ZONE_EDGE = 1.0   # 温度帯の20日後の中央値が全期間よりこれ以上良い／悪いときに ±1


def market_stance(market: dict | None, setups: dict | None, nk_closes: list[float],
                  bt: dict | None = None) -> dict | None:
    """今日のスタンス。market: market_thermo、setups: setup_backtest、nk_closes: 日経平均の終値（古い順）。"""
    reasons = []
    score = 0
    if len(nk_closes) >= 75:
        p, m25, m75 = nk_closes[-1], sma(nk_closes, 25), sma(nk_closes, 75)
        where = f"日経平均 {p:,.0f}、25日線 {m25:,.0f}・75日線 {m75:,.0f}"
        if p > m25 > m75:
            score += 1
            reasons.append({"pt": 1, "text": f"{where}。両方の上で上昇基調"})
        elif p < m25 < m75:
            score -= 1
            reasons.append({"pt": -1, "text": f"{where}。両方の下で下落基調"})
        else:
            reasons.append({"pt": 0, "text": f"{where}。線がねじれていて方向感がはっきりしない"})
    rows = (setups or {}).get("setups") or []
    buy = [s for s in rows if s.get("expect") == "up"]
    works = [s["setup"] for s in buy if s.get("verdict") == "効いている"]
    fails = [s["setup"] for s in buy if s.get("verdict") == "効いていない"]
    if buy:
        if works:
            score += 1
            reasons.append({"pt": 1, "text": "直近の検証で全銘柄を上回っている買いの型: " + "・".join(works)})
        else:
            score -= 1
            reasons.append({"pt": -1, "text": "直近の検証で全銘柄を上回っている買いの型が無い"
                            + (f"（下回っている: {'・'.join(fails)}）" if fails else "")})
    if market and market.get("temp") is not None:
        zone = market.get("zone")
        zr = next((z for z in (bt or {}).get("zones") or [] if z.get("zone") == zone), None)
        base = ((bt or {}).get("all") or {}).get("median20")
        if zr and zr.get("median20") is not None and base is not None and (zr.get("n20") or 0) >= 20:
            diff = zr["median20"] - base
            txt = (f"温度 {market['temp']}（{zone}）。過去この帯の日の20日後の日経平均は中央値 {zr['median20']:+.1f}%"
                   f"（全期間 {base:+.1f}%）")
            pt = 1 if diff >= STANCE_ZONE_EDGE else -1 if diff <= -STANCE_ZONE_EDGE else 0
            score += pt
            reasons.append({"pt": pt, "text": txt})
        if zone == "過熱":
            score -= 1
            reasons.append({"pt": -1, "text": "温度が「過熱」。良い材料が出そろった形で、高値掴みになりやすい"})
    if not reasons:
        return None
    for lo, key, name, risk, text in STANCES:
        if score >= lo:
            return {"key": key, "label": name, "risk": risk, "text": text, "score": score, "reasons": reasons,
                    "works": works, "fails": fails}
    return None
