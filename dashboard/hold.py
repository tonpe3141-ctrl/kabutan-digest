"""保有株の判定: 持つか・減らすか、売るならどう売るか（DESIGN.md 18章）。

押し目買いのルール（swing.py）で買った銘柄は、そのルールの計画（売り指値・損切り・期限）で手仕舞う。
ここは、それ以外に自分の判断で持っている銘柄のためのもの。売買の推奨ではなく、同じ形の銘柄が過去にどうなったかの並び。

4年の四本値（約380銘柄、2022-11〜2026-09。前半 〜2024 で決め、後半 2025〜 で確かめた）と、
2年の1時間足（2024-10〜2026-09。前半 〜2025-09・後半 2025-10〜）で分かったこと:

  1. 値動きだけで売る規則は、平均では損をする。全銘柄を5日おきに買ったことにして60日持つと、平均 +3.0%／+8.3%
     （前半／後半）。損切り（買値 −3ATR）を置くと +1.4%／+5.9%、高値から −3ATR の追いかけ売りは +0.7%／+3.9%、
     25日線割れで売ると +0.0%／+1.5%。代わりに下げの深い5%（−22%前後）は −12〜−16% に浅くなる。損切りは保険で、保険料がかかる。
  2. 20日で +10% 以上上げた銘柄（勝ち）は、次の20日も日経を上回った（+0.4pt／+1.1pt）。20日で −5〜−20% の銘柄（負け）は
     下回り続けた（−0.3pt／−1.4pt）。−20% を超えて急落した銘柄は反発した（+1.7pt／+2.5pt）。前半・後半で同じ向き。
     ただし1銘柄ずつでは日経に勝つ割合は4〜5割で、差は平均の話（大きく動く銘柄に引っ張られる）。
  3. 売ると決めたなら、下げた日（2日RSI < 10）の翌日の寄りで成行で売るより、毎朝「直近4日の終値の平均」に売り指値を
     置く（5日で届かなければ5日目の引け）ほうが、平均 +0.35%／+0.46% 高く売れた（3回に2回は高く売れた）。
     戻った日（2日RSI > 70）はどちらでも同じ。
  4. 場中の形は判定に使わない。後場（後場寄り→大引）の騰落は、前場の形（前日比 −4% 以下〜+4% 以上、日経との差）に
     よらず平均 ±0.3% 以内だった。寄りの窓（前日比 ±5%）で寄りに売るか引けまで待つかは、年によって向きが逆になった
     （窓を開けて下げた日の寄り→大引: 2024年 −1.7%、2025年 +0.7%、2026年 +0.9%）。

判定に買値は使わない（買値は市場にとって意味がない。「買値まで戻ったら売る」は負けを長く持つ癖＝ディスポジション効果）。
買値を使うのは、自分で決める撤退ライン（保険）と、画面の含み損益だけ。

すべて純関数。thermo_run.py が日足キャッシュ（約2年）から毎日数え直した検証（verify）を判定と並べて出す。
1.（出口の規則の比較）と 4.（1時間足）は本番では数え直せないので、研究の値を定数（EXIT_STATS・PM_STATS）で持つ。
"""
from statistics import mean

from .swing import r1, r2, tick_up

R_N = 20                # 勝ち負けを測る日数
WIN_R = 10.0            # 20日で +10% 以上 → 勝ち
LOSE_R = -5.0           # 20日で −5% 以下 → 負け
CRASH_R = -20.0         # 20日で −20% 以下 → 急落
DIP_RSI = 10.0          # 2日RSI がこれ未満 → 急に押した日
BOUNCE_RSI = 70.0       # 2日RSI がこれより上 → 戻った日
EXIT_N = 4              # 売り指値 = 直近4日の終値の平均（swing.py と同じ）
EXEC_N = 5              # 売り指値が届かなければ5日目の引けで売る
GAP = 5.0               # 検証の参考に数える寄りの窓（前日比 ±5%）。判定には使わない
STOP_ATR = 3.0          # 撤退ラインの目安 = 買値 − 3×ATR（押し目買いの損切りと同じ距離）
H = 20                  # 検証: 次の20営業日の、日経との差

# 引けの形。判定と一言は画面にそのまま出す
VERDICTS = {
    "hold": {"label": "持つ", "tone": "ok",
             "text": "20日で大きく上げた銘柄（勝ち）。勝ちを早く売らない"},
    "wait": {"label": "今は売らない", "tone": "accent",
             "text": "20日で急落した銘柄。ここで投げると反発を取り逃がしやすい。減らすなら戻ってから"},
    "trim_limit": {"label": "減らす候補（売り指値で）", "tone": "warn",
                   "text": "20日で負けている銘柄で、今日は急に押した日。翌日の寄りで投げず、売り指値で戻りを待つ"},
    "trim_now": {"label": "減らす候補（戻った今）", "tone": "warn",
                 "text": "20日で負けている銘柄が戻った日。減らすなら、戻ったところで"},
    "trim": {"label": "減らす候補", "tone": "warn",
             "text": "20日で負けている銘柄。日経より弱い状態が続きやすい。減らすなら売り指値で"},
    "flat": {"label": "形の差なし", "tone": "",
             "text": "値動きの形からは、持つ・売るに差が出ていない。撤退ラインと、業績の前提で決める"},
}
CLASSES = {"win": "勝ち（20日 +10%以上）", "flat": "中立", "lose": "負け（20日 −5〜−20%）",
           "crash": "急落（20日 −20%超）"}

# 研究の値（本番の日足キャッシュでは数え直せないもの）。[前半, 後半]
# 出口の規則: 全銘柄を5日おきに翌日の寄りで買ったことにして、最長60営業日。平均と、下から5%の値（%、往復0.1%のコスト込み）
EXIT_STATS = {
    "period": ["2023-07〜2024-12", "2025-01〜2026-06"], "n": [21309, 22211], "days": 60,
    "rows": [
        {"k": "none", "label": "60日持つ", "avg": [3.0, 8.3], "p5": [-22.3, -22.8]},
        {"k": "stop3", "label": "損切り（買値 −3ATR）", "avg": [1.4, 5.9], "p5": [-14.8, -16.5]},
        {"k": "trail3", "label": "高値から −3ATR で売る", "avg": [0.7, 3.9], "p5": [-11.9, -13.0]},
        {"k": "ma25", "label": "25日線を割ったら売る", "avg": [0.0, 1.5], "p5": [-7.1, -7.8]},
    ],
}
# 形の判定を決めた4年の研究（hold.verify を研究用の4年の四本値に当てた値。前半 〜2024-12 で決め、後半 2025-01〜 で確かめた）。
# 次の20営業日の日経との差（pt）の平均。本番の検証（日足キャッシュ約2年）と並べて出す
RESEARCH = {
    "period": ["2022-11〜2024-12", "2025-01〜2026-08"], "stocks": 382,
    "cls": {"win": [0.42, 1.13], "flat": [-0.11, -0.16], "lose": [-0.34, -1.35], "crash": [1.69, 2.53],
            "all": [-0.05, -0.06]},
    "exec_dip": [0.35, 0.46],   # 押した日の売り指値 − 翌日の寄りの成行（%）
}
# 後場: Yahoo!ファイナンスの1時間足（377銘柄）。前場の騰落（前日比）ごとの、後場寄り→大引の平均（%）と上げた割合
PM_STATS = {
    "period": ["2024-10〜2025-09", "2025-10〜2026-09"], "stocks": 377,
    "rows": [
        {"k": "≤−4%", "n": [2435, 4451], "pm": [-0.10, 0.25], "win": [47, 54]},
        {"k": "−4〜−1%", "n": [17707, 19613], "pm": [0.06, 0.03], "win": [51, 50]},
        {"k": "−1〜+1%", "n": [46721, 37103], "pm": [0.01, 0.03], "win": [48, 49]},
        {"k": "+1〜+4%", "n": [18137, 20723], "pm": [-0.05, 0.04], "win": [44, 50]},
        {"k": "≥+4%", "n": [3146, 4878], "pm": [-0.03, -0.05], "win": [47, 49]},
    ],
    "sd": [1.05, 1.22],      # 後場の騰落の標準偏差（1銘柄1日）
}


# ==================== 形 ====================


def ret_n(ind: dict, i: int, n: int = R_N):
    if i < n or not ind["c"][i - n]:
        return None
    return (ind["c"][i] / ind["c"][i - n] - 1) * 100


def classify(r20, rsi2) -> tuple[str | None, str | None]:
    """(形, 判定)。20日の騰落と2日RSI だけで決まる（買値は使わない）。"""
    if r20 is None:
        return None, None
    if r20 <= CRASH_R:
        return "crash", "wait"
    if r20 <= LOSE_R:
        if rsi2 is not None and rsi2 < DIP_RSI:
            return "lose", "trim_limit"
        if rsi2 is not None and rsi2 > BOUNCE_RSI:
            return "lose", "trim_now"
        return "lose", "trim"
    if r20 >= WIN_R:
        return "win", "hold"
    return "flat", "flat"


def sell_limit(ind: dict, i: int):
    """i 日目の引けのあと、次の営業日に置く売り指値（直近4日＝i−3〜i 日目の終値の平均、呼値で切り上げ）。"""
    if i < EXIT_N - 1:
        return None
    return tick_up(sum(ind["c"][i - EXIT_N + 1:i + 1]) / EXIT_N)


def today_row(ind: dict) -> dict | None:
    """最新の日の引けでの形・判定と、次の営業日の値段の目安（寄りの窓・売り指値）。"""
    n = len(ind["c"])
    if n < R_N + 2:
        return None
    i = n - 1
    c, atr, rsi = ind["c"][i], ind["atr"][i], ind["rsi"][i]
    r20 = ret_n(ind, i)
    cls, v = classify(r20, rsi)
    if not cls:
        return None
    hi60 = max(ind["c"][max(0, i - 59):i + 1])
    return {"asof": ind["d"][i], "c": c, "cls": cls, "v": v, "r20": r1(r20), "rsi2": r1(rsi),
            "r5": r1((c / ind["c"][i - 5] - 1) * 100), "atr": r2(atr), "dd60": r1((c / hi60 - 1) * 100),
            # 次の営業日に置く売り指値（減らすと決めたとき）
            "sell": sell_limit(ind, i)}


def intraday(prev: float | None, open_: float | None, last: float | None,
             nk_prev: float | None = None, nk_last: float | None = None) -> dict | None:
    """場中（前場の更新）の値動き。prev は前の営業日の終値、open_ は今日の寄り、last はその時点の値。
    判定は変えない（4.）。撤退ライン・売り指値に届いたかはアプリが保有の記録と突き合わせる。"""
    if not prev or not last or prev <= 0:
        return None
    nk = (nk_last / nk_prev - 1) * 100 if nk_prev and nk_last else None
    move = (last / prev - 1) * 100
    return {"prev": prev, "open": open_, "last": last, "gap": r2((open_ / prev - 1) * 100) if open_ else None,
            "move": r2(move), "nk": r2(nk), "ex": r2(move - nk) if nk is not None else None}


# ==================== 検証 ====================


def _half_stats(rows: list[tuple], split: str) -> list[dict]:
    """rows = [(日付, 値[, 騰落そのもの])]。split より前と後ろの平均・勝率（値 > 0 の割合）・件数。"""
    out = []
    for part in ([r for r in rows if r[0] < split], [r for r in rows if r[0] >= split]):
        s = {"n": len(part), "avg": r2(mean(r[1] for r in part)) if part else None,
             "win": round(sum(1 for r in part if r[1] > 0) / len(part) * 100) if part else None}
        if part and len(part[0]) > 2:
            s["abs"] = r2(mean(r[2] for r in part))
        out.append(s)
    return out


def verify(stocks: dict, nk: dict, split: str | None = None, min_price: float = 300.0,
           min_tv: float = 3.0) -> dict | None:
    """日足（stocks = {コード: swing.indicators の結果}）に毎日当てた、形ごとのその後。先読みしない。

    nk = {日付: 日経の終値}。split（'YYYY-MM-DD'）より前を前半、以降を後半にする。無ければ真ん中の日。
      形    : i 日目の引けの形ごとに、次の20営業日の騰落 − 日経の同じ期間の騰落（引け→引け）。abs は騰落そのもの
      寄り  : 前日比 ±5% の窓を開けた日の、寄り→大引（寄りで売らずに引けまで持った場合。参考で、判定には使わない）
      売り方: 2日RSI < 10 の日の翌日から、直近4日の平均の売り指値（5日目の引けまで）と、翌日の寄りで成行の差
    商いの少ない銘柄（20日平均の売買代金 3億円未満）と 300円未満は数えない。"""
    cls_rows = {k: [] for k in list(CLASSES) + list(VERDICTS) + ["all"]}
    gap_rows = {"dn": [], "up": [], "all": []}
    ex_rows = {"dip": [], "bounce": []}
    days = set()
    for ind in stocks.values():
        if not ind:
            continue
        d, o, h, c, rsi, tv = ind["d"], ind["o"], ind["h"], ind["c"], ind["rsi"], ind["tv"]
        n = len(c)
        for i in range(R_N, n - 1):
            if c[i] < min_price or tv[i] is None or tv[i] < min_tv:
                continue
            # 寄りの窓（翌日）
            g = (o[i + 1] / c[i] - 1) * 100
            if abs(g) < 25:
                oc = (c[i + 1] / o[i + 1] - 1) * 100
                gap_rows["all"].append((d[i + 1], oc))
                if g <= -GAP:
                    gap_rows["dn"].append((d[i + 1], oc))
                elif g >= GAP:
                    gap_rows["up"].append((d[i + 1], oc))
            # 売り方（翌日の寄りで成行 と 売り指値）
            if i + EXEC_N < n and i >= EXIT_N and rsi[i] is not None and (rsi[i] < DIP_RSI or rsi[i] > BOUNCE_RSI):
                base, got = o[i + 1], None
                for j in range(i + 1, i + 1 + EXEC_N):
                    lim = sum(c[j - EXIT_N:j]) / EXIT_N
                    if o[j] >= lim:
                        got = o[j]
                        break
                    if h[j] >= lim:
                        got = lim
                        break
                got = got if got is not None else c[i + EXEC_N]
                ex_rows["dip" if rsi[i] < DIP_RSI else "bounce"].append((d[i], (got / base - 1) * 100))
            # 形 → 次の20日の日経との差
            if i + H < n:
                r20 = ret_n(ind, i)
                cls, v = classify(r20, rsi[i])
                n0, n1 = nk.get(d[i]), nk.get(d[i + H])
                if cls and n0 and n1:
                    x = ((c[i + H] / c[i]) - (n1 / n0)) * 100
                    days.add(d[i])
                    a = (c[i + H] / c[i] - 1) * 100
                    cls_rows[cls].append((d[i], x, a))
                    if v != cls:
                        cls_rows[v].append((d[i], x, a))
                    cls_rows["all"].append((d[i], x, a))
    if not cls_rows["all"]:
        return None
    ds = sorted(days)
    split = split or ds[len(ds) // 2]
    return {
        "from": ds[0], "to": ds[-1], "split": split, "h": H,
        "cls": {k: _half_stats(v, split) for k, v in cls_rows.items()},
        "gap": {k: _half_stats(v, split) for k, v in gap_rows.items()},
        "exec": {k: _half_stats(v, split) for k, v in ex_rows.items()},
    }
