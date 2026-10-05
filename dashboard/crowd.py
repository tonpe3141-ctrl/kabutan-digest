"""急騰して混み合った銘柄の印と、主役の入れ替わり（DESIGN.md 24章）。

2026-10-05、日経平均が +2.4% で半導体（アドバンテスト・東エレク・ディスコ）が +4〜6% 上げた日に、保有していた
フジクラ（5803）だけが −0.6% で終わった。前営業日（10/2）のフジクラは、日経が −0.9% の中で +4.3%
（3日で +13.5%、出来高は20日平均の1.6倍、上ヒゲ2%）と、資金が集中していた。

ここで確かめたこと（約2年の日足・417銘柄。前半・後半に分け、「翌日の、その日の全銘柄の中央値との差」で数えた）:

  1. 天井のサインは取れない。「3日で +10% 以上＋出来高1.5倍」「上ヒゲ」「高値から −25% の戻り」「2日RSI 95 超」の
     どれも、翌日・5日後の平均は市場を下回らなかった（翌日の平均 +0.3%。5日後の中央値は −0.5〜−2% だが平均は +）。
     過去2年は上げ相場で、急騰した銘柄は平均では続伸した。「過熱だから売る」は検証に通らない。
  2. 変わるのは「振れ幅」。急騰して混み合った銘柄は、翌日に市場より3%以上弱くなる割合が 25〜27%（通常 5〜8%）、
     3%以上強くなる割合も 22〜24%（通常 7〜9%）。向きは五分で、両方に振れやすい。前半・後半で同じ。
  3. 「売買代金が上位10位に連続」は印にしない。フジクラは70営業日ずっと上位で、常に混んでいる銘柄には情報が無く、
     前半・後半で振れ幅の差もそろわなかった（弱くなる割合 10.5% → 20.3%）。

だから出すのは予測ではなく「明日は市場と違う動きをしやすい銘柄」の印と、主役が入れ替わった事実。
注文・売買の条件には使わない。検証の数字は thermo.json の crowd.verify に毎日数え直して並べる。

すべて純関数。入力は ohlc.json（dates と stocks: {コード: [[始,高,安,終,出来高(100株)] か null, ...]}）。
"""
from statistics import mean, median

HOT_R3 = 10.0        # 3日の騰落がこれ以上（%）
HOT_VR = 1.5         # 当日の出来高が直前20日平均のこの倍数以上
BIG = 3.0            # 「市場と違う動き」の大きさ（翌日の対市場、%）
WARMUP = 25          # 指標が出る最初の日
MIN_TV = 20.0        # 主役の入れ替わりに出す銘柄の流動性（20日平均の売買代金、億円）
LEAD = 3.0           # 前の日に市場を上回った大きさ（%）。主役
LAG = -1.0           # 今日の対市場がこれ以下 → 置いていかれた
UP = 3.0             # 今日の対市場がこれ以上 → 買われた
ROT_N = 8            # 入れ替わりに出す件数
UNIT = 100           # ohlc の出来高は 100株単位

FLAG_TEXT = f"3日で +{HOT_R3:.0f}% 以上・出来高が20日平均の {HOT_VR} 倍以上"


def market_returns(dates: list, stocks: dict) -> list:
    """各日の全銘柄の前日比の中央値（%。市場の代わり）。先頭は 0。"""
    n = len(dates)
    out = [0.0] * n
    for t in range(1, n):
        r = [(s[t][3] / s[t - 1][3] - 1) * 100 for s in stocks.values()
             if s[t] and s[t - 1] and s[t][3] and s[t - 1][3]]
        out[t] = median(r) if len(r) >= 20 else 0.0
    return out


def features(s: list, t: int):
    """t 日目の引けまでで決まる特徴。足りなければ None。"""
    if t < WARMUP or not s[t] or not s[t - 1] or not s[t - 3] or not s[t][3] or not s[t - 1][3] or not s[t - 3][3]:
        return None
    win = [s[t - k] for k in range(1, 21)]
    if sum(1 for x in win if x) < 15:
        return None
    va = mean((x[4] or 0) for x in win if x)
    if not va:
        return None
    pc = s[t - 1][3]
    return {"r3": (s[t][3] / s[t - 3][3] - 1) * 100, "vr": (s[t][4] or 0) / va,
            "wick": (s[t][1] - s[t][3]) / pc * 100, "d1": (s[t][3] / pc - 1) * 100}


def is_hot(f: dict) -> bool:
    return f["r3"] >= HOT_R3 and f["vr"] >= HOT_VR


def today_rows(dates: list, stocks: dict) -> dict:
    """最新の日に印が付いた銘柄 {コード: {asof, r3, vr, wick, d1}}。"""
    if not dates:
        return {}
    t = len(dates) - 1
    out = {}
    for code, s in stocks.items():
        f = features(s, t)
        if f and is_hot(f):
            out[code] = {"asof": dates[t], "r3": round(f["r3"], 1), "vr": round(f["vr"], 2),
                         "wick": round(f["wick"], 1), "d1": round(f["d1"], 1)}
    return out


def _agg(xs: list):
    """翌日の対市場（%）の一覧から: 件数・平均・プラスの割合・3%以上弱い／強い割合。"""
    if len(xs) < 30:
        return None
    n = len(xs)
    return {"n": n, "avg": round(mean(xs), 2), "up": round(100 * sum(1 for x in xs if x > 0) / n),
            "weak": round(100 * sum(1 for x in xs if x <= -BIG) / n, 1),
            "strong": round(100 * sum(1 for x in xs if x >= BIG) / n, 1)}


def verify(dates: list, stocks: dict, mk: list | None = None):
    """印が付いた日の翌日、その銘柄が市場（全銘柄の中央値）よりどれだけ違う動きをしたか。

    先読みしない（印は t 日目の引けまでで決め、結果は t+1 日目）。前半・後半に分けて並べる。
    今の採用銘柄だけで測るので、上げ続けた銘柄に偏る（画面に書く）。"""
    n = len(dates)
    if n < WARMUP + 30:
        return None
    mk = mk if mk is not None else market_returns(dates, stocks)
    mid = n // 2
    groups = {"all": ([], []), "hot": ([], [])}
    for s in stocks.values():
        for t in range(WARMUP, n - 1):
            if not s[t + 1] or not s[t + 1][3] or not s[t] or not s[t][3]:
                continue
            f = features(s, t)
            if not f:
                continue
            ex = (s[t + 1][3] / s[t][3] - 1) * 100 - mk[t + 1]
            half = 0 if t < mid else 1
            groups["all"][half].append(ex)
            if is_hot(f):
                groups["hot"][half].append(ex)
    return {"from": dates[WARMUP], "to": dates[n - 2], "split": dates[mid], "big": BIG,
            "cls": {k: [_agg(a), _agg(b)] for k, (a, b) in groups.items()}}


# ==================== 主役の入れ替わり ====================


def rotation(dates: list, stocks: dict, names: dict, theme_stocks: dict, mk: list | None = None):
    """前の営業日に市場を大きく上回った銘柄（主役）のうち、今日は市場を下回った銘柄と、その逆。事実の並べ替え。

    相場全体が上げた日に、どこへ資金が移ったかを説明するためのもの。今日の動きから明日を読むものではない。"""
    n = len(dates)
    if n < WARMUP + 2:
        return None
    mk = mk if mk is not None else market_returns(dates, stocks)
    t = n - 1
    rows = []
    for code, s in stocks.items():
        if not (s[t] and s[t - 1] and s[t - 2] and s[t][3] and s[t - 1][3] and s[t - 2][3]):
            continue
        tvs = [s[k][3] * (s[k][4] or 0) * UNIT for k in range(t - 19, t + 1) if s[k] and s[k][3]]
        if len(tvs) < 15 or mean(tvs) / 1e8 < MIN_TV:
            continue
        prev = (s[t - 1][3] / s[t - 2][3] - 1) * 100 - mk[t - 1]
        now = (s[t][3] / s[t - 1][3] - 1) * 100 - mk[t]
        th = ((theme_stocks.get(code) or {}).get("themes") or [])[:3]
        rows.append({"code": code, "name": names.get(code) or code, "th": th, "prev": round(prev, 1),
                     "now": round(now, 1), "d1": round((s[t][3] / s[t - 1][3] - 1) * 100, 1)})
    out_ = sorted((r for r in rows if r["prev"] >= LEAD and r["now"] <= LAG), key=lambda r: r["now"] - r["prev"])
    into = sorted((r for r in rows if r["prev"] <= 0 and r["now"] >= UP), key=lambda r: r["prev"] - r["now"])
    themes = {}
    for key, lst in (("out", out_), ("into", into)):
        for r in lst:
            for th in r["th"][:1]:
                themes.setdefault(th, {"theme": th, "out": 0, "into": 0})[key] += 1
    th_rows = sorted(themes.values(), key=lambda x: (-(x["out"] + x["into"]), x["theme"]))[:6]
    return {"asof": dates[t], "prev": dates[t - 1], "mkt": [round(mk[t - 1], 1), round(mk[t], 1)],
            "lead": LEAD, "lag": LAG, "up": UP, "min_tv": MIN_TV, "n_out": len(out_), "n_into": len(into),
            "out": out_[:ROT_N], "into": into[:ROT_N], "themes": th_rows}


def block(dates: list, stocks: dict, names: dict, theme_stocks: dict) -> tuple:
    """thermo.json の crowd と、銘柄ごとの印（today_rows）。"""
    mk = market_returns(dates, stocks)
    rows = today_rows(dates, stocks)
    try:
        ver = verify(dates, stocks, mk)
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  混み合いの検証で例外: {e}")
        ver = None
    try:
        rot = rotation(dates, stocks, names, theme_stocks, mk)
    except Exception as e:                      # noqa: BLE001
        print(f"    ⚠️  主役の入れ替わりで例外: {e}")
        rot = None
    return {"rules": {"r3": HOT_R3, "vr": HOT_VR, "big": BIG, "text": FLAG_TEXT},
            "verify": ver, "rotation": rot, "n": len(rows)}, rows
