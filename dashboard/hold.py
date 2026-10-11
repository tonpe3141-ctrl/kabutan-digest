"""保有株の売り時: 持つか・売るか、売るならいつ・いくらで（DESIGN.md 18章・27章）。

押し目買いのルール（swing.py）で買った銘柄は、そのルールの計画（売り指値・損切り・期限）で手仕舞う。
ここは、それ以外に自分の判断で持っている銘柄のためのもの。数日〜数週間で売る使い方に合わせ、次の5・10・20営業日を見る。
売買の推奨ではなく、同じ形の銘柄が過去にどうなったかの並び。

4年の四本値（428銘柄、2022-07〜2026-10。前半 〜2024-12 で決め、後半 2025-01〜 で確かめた。DESIGN.md 27章）で分かったこと:

  1. 上（利確）は決めない。買値 +2〜3ATR の利確の指値は、勝率を 52→63〜67% に上げるが、20日の平均は下がった
     （勢いで買った銘柄の後半で −0.5〜−0.6pt）。勝ちを早く売る癖の裏返し。
  2. 下（撤退）は決める。買値 −3ATR の逆指値は、売った資金をすぐ平均的な銘柄に回すなら20日の平均を下げず（+0.0〜+0.2pt）、
     下から5%の負けを −12.6% → −10.6% に浅くした。現金で置いておくなら平均を 0.3〜0.4pt 下げる（保険料）。
  3. 負けている銘柄（20日 −5% 以下）が戻った日（2日RSI 90 以上）は売り時。次の5〜20日は平均的な銘柄より弱かった（後半 −0.9pt）。
     撤退ラインにこの売りを足すと、3つの買い方（でたらめ・勢い・押し目）× 前半・後半のすべてで「持ち続ける」以上だった。
  4. 強い銘柄（20日 +10% 以上）は持つ。5日線から +2ATR 以上に離れた日（伸びすぎ）の次の5日は一服する（後半 −0.6pt）が、
     20日では平均的な銘柄を上回った（+0.4〜+1.0pt）。伸びすぎを理由に売ると平均が下がった。近いうちに売ると決めているなら、
     伸びすぎの翌日の寄りが有利。
  5. 20日で −20% を超えて急落した銘柄は投げない。次の10日は騰落そのもので +3〜4% 戻った（平均的な銘柄との差は前半 +0.2・後半 +1.4pt）。
     相場ぐるみの急落の日（全銘柄の平均 −1.5% 以下）に5日線から −2ATR 以上崩れた銘柄も、次の10日で +5.4%／+3.1% 戻った
     （平均的な銘柄はもっと戻ったので差はマイナス。現金にして逃げると戻りを取り逃がす）。
  6. 売ると決めたなら、押した日（2日RSI < 10）の翌日の寄りで成行で売るより、毎朝「直近4日の終値の平均」に売り指値を
     置く（5日で届かなければ5日目の引け）ほうが高く売れた。戻った日・伸びすぎの日はどちらでも同じ（寄りでよい）。
  7. 場中の形は判定に使わない（後場の騰落は前場の形からは読めなかった。18章）。

判定に買値は使わない（買値は市場にとって意味がない。「買値まで戻ったら売る」は負けを長く持つ癖＝ディスポジション効果）。
買値を使うのは、撤退ライン（保険）と、画面の含み損益だけ。

すべて純関数。thermo_run.py が日足キャッシュ（約2年）から毎日数え直した検証（verify）を判定と並べて出す。
出口の規則の比較（EXIT_STATS）と後場（PM_STATS）は本番では数え直せないので、研究の値を定数で持つ。
"""
from statistics import mean

from .swing import r1, r2, tick_down, tick_up

R_N = 20                # 勝ち負けを測る日数
WIN_R = 10.0            # 20日で +10% 以上 → 勝ち
LOSE_R = -5.0           # 20日で −5% 以下 → 負け
CRASH_R = -20.0         # 20日で −20% 以下 → 急落
DIP_RSI = 10.0          # 2日RSI がこれ未満 → 急に押した日
BOUNCE_RSI = 90.0       # 2日RSI がこれ以上 → 戻った日（負けの銘柄の売り時。70 から変えた。27章）
HOT_Z = 2.0             # (終値 − 5日線) ÷ ATR がこれ以上 → 伸びすぎ
BRK_Z = -2.0            # これ以下 → 5日線から崩れた
PANIC_MK = -1.5         # 全銘柄の平均（等ウェイト）の前日比がこれ以下の日 → 相場ぐるみの急落
EXIT_N = 4              # 売り指値 = 直近4日の終値の平均（swing.py と同じ）
EXEC_N = 5              # 売り指値が届かなければ5日目の引けで売る
GAP = 5.0               # 検証の参考に数える寄りの窓（前日比 ±5%）。判定には使わない
STOP_ATR = 3.0          # 撤退ライン = 買値 − 3×ATR（押し目買いの損切りと同じ距離）
HS = (5, 10, 20)        # 検証: 次の5・10・20営業日の、平均的な銘柄との差
H = 20                  # 検証の最長
MIN_PRICE, MIN_TV = 300.0, 3.0

# 引けの形。判定と一言は画面にそのまま出す
VERDICTS = {
    "hold": {"short": "持つ", "label": "持つ", "tone": "ok", "act": "hold",
             "text": "20日で強い銘柄。次の5〜20日も平均的な銘柄を上回りやすかった"},
    "hot": {"short": "持つ", "label": "持つ（伸びすぎ）", "tone": "ok", "act": "hold",
            "text": "強い銘柄が5日線から大きく上に離れた日。次の5日は一服しやすいが、20日では平均的な銘柄を上回った。"
                    "伸びすぎを理由に売ると平均が下がった"},
    "wait": {"short": "売らない", "label": "今は売らない", "tone": "accent", "act": "hold",
             "text": "20日で −20% を超えて急落した銘柄。その後の10日は大きく戻りやすかった。投げると戻りを取り逃がしやすい"},
    "trim_now": {"short": "売り時", "label": "売り時（戻った日）", "tone": "warn", "act": "sell",
                 "text": "20日で負けている銘柄が戻った日（2日RSI 90以上）。その後も平均的な銘柄より弱かった"},
    "trim_limit": {"short": "減らす", "label": "減らす（売り指値で）", "tone": "warn", "act": "trim",
                   "text": "20日で負けている銘柄が押した日。翌日の寄りの成行より、直近4日の平均の売り指値のほうが高く売れた"},
    "trim": {"short": "減らす候補", "label": "減らす候補", "tone": "warn", "act": "trim",
             "text": "20日で負けている銘柄。後半（2025〜）は平均的な銘柄より弱い状態が続いた（前半は差が小さかった）"},
    "flat": {"short": "様子見", "label": "様子見", "tone": "", "act": "hold",
             "text": "値動きの形からは、持つ・売るに差が出ていない"},
}
CLASSES = {"win": "勝ち（20日 +10%以上）", "flat": "中立", "lose": "負け（20日 −5〜−20%）",
           "crash": "急落（20日 −20%超）"}

# 研究の値（本番の日足キャッシュでは数え直せないもの）。[前半, 後半]
# 出口の規則: 3つの買い方で翌日の寄りに買ったことにして、最長20営業日。往復0.1%のコスト込み。
#   fair = 売ったあと20日目までを全銘柄の等ウェイト平均に乗り換えた場合、raw = 売ったあとは現金
#   diff = 同じ入口で20日持った場合との差（fair どうし）、p5 = 下から5%（fair）
EXIT_STATS = {
    "period": ["2022-07〜2024-12", "2025-01〜2026-09"], "days": 20, "stocks": 428,
    "cohorts": {"all": "でたらめに買った（全銘柄を5日おき）", "chase": "勢いで買った（20日高値・5日 +5%以上）",
                "dip": "押し目で買った（200日線の上・2日RSI < 10）"},
    "n": {"all": [27507, 20098], "chase": [3850, 3720], "dip": [4054, 2942]},
    "rows": [
        {"k": "hold", "short": "20日持つ", "label": "20日持つ", "adopt": False,
         "diff": {"all": [0.0, 0.0], "chase": [0.0, 0.0], "dip": [0.0, 0.0]},
         "raw": {"all": [1.01, 2.51], "chase": [0.28, 2.69], "dip": [1.42, 3.43]},
         "win": {"all": [52, 57], "chase": [49, 54], "dip": [53, 58]},
         "p5": {"all": [-12.6, -15.3], "chase": [-15.7, -18.5], "dip": [-13.1, -13.8]}},
        {"k": "tp2", "short": "利確 +2ATR", "label": "利確の指値（買値 +2ATR）", "adopt": False,
         "diff": {"all": [-0.03, -0.12], "chase": [-0.02, -0.59], "dip": [-0.09, -0.33]},
         "raw": {"all": [0.71, 1.51], "chase": [0.16, 1.34], "dip": [0.96, 2.07]},
         "win": {"all": [63, 67], "chase": [61, 67], "dip": [63, 66]},
         "p5": {"all": [-12.0, -14.3], "chase": [-15.0, -17.0], "dip": [-12.5, -13.3]}},
        {"k": "tp3", "short": "利確 +3ATR", "label": "利確の指値（買値 +3ATR）", "adopt": False,
         "diff": {"all": [-0.06, -0.09], "chase": [0.01, -0.49], "dip": [-0.14, -0.18]},
         "raw": {"all": [0.78, 1.90], "chase": [0.25, 1.73], "dip": [1.08, 2.62]},
         "win": {"all": [56, 61], "chase": [54, 60], "dip": [57, 62]},
         "p5": {"all": [-12.4, -14.7], "chase": [-15.4, -17.7], "dip": [-12.9, -13.5]}},
        {"k": "str", "short": "伸びすぎで売る", "label": "伸びすぎで売る（20日 +10%・5日線 +2ATR）", "adopt": False,
         "diff": {"all": [-0.01, -0.06], "chase": [0.02, -0.34], "dip": [-0.09, -0.18]},
         "raw": {"all": [0.94, 2.23], "chase": [0.19, 1.86], "dip": [1.31, 3.06]},
         "win": {"all": [54, 58], "chase": [53, 59], "dip": [53, 59]},
         "p5": {"all": [-12.5, -14.9], "chase": [-15.1, -17.3], "dip": [-13.0, -13.8]}},
        {"k": "trail", "short": "追いかけ売り", "label": "撤退＋利益が出たら追いかけ売り（+2ATR から高値 −3ATR）", "adopt": False,
         "diff": {"all": [0.11, 0.04], "chase": [0.12, -0.26], "dip": [0.21, 0.01]},
         "raw": {"all": [0.67, 1.87], "chase": [0.17, 1.72], "dip": [0.93, 2.56]},
         "win": {"all": [48, 53], "chase": [45, 50], "dip": [48, 54]},
         "p5": {"all": [-10.4, -13.0], "chase": [-12.6, -14.5], "dip": [-10.5, -12.3]}},
        {"k": "time10", "short": "10日で見切る", "label": "10日たってプラスでなければ売る", "adopt": False,
         "diff": {"all": [0.08, 0.10], "chase": [0.02, -0.03], "dip": [0.13, 0.02]},
         "raw": {"all": [0.71, 1.93], "chase": [0.13, 2.11], "dip": [1.04, 2.67]},
         "win": {"all": [42, 46], "chase": [39, 43], "dip": [44, 48]},
         "p5": {"all": [-10.3, -12.4], "chase": [-12.9, -15.2], "dip": [-10.4, -11.1]}},
        {"k": "stop", "short": "撤退 −3ATR", "label": "撤退ライン（買値 −3ATR の逆指値）", "adopt": True,
         "diff": {"all": [0.14, 0.09], "chase": [0.17, 0.02], "dip": [0.20, 0.03]},
         "raw": {"all": [0.74, 2.09], "chase": [0.25, 2.21], "dip": [0.92, 2.77]},
         "win": {"all": [49, 53], "chase": [46, 50], "dip": [49, 55]},
         "p5": {"all": [-10.6, -13.4], "chase": [-13.0, -15.3], "dip": [-10.7, -12.6]}},
        {"k": "plan", "short": "撤退＋戻り売り", "label": "撤退ライン＋負けの戻りで売る（このアプリの売り時）", "adopt": True,
         "diff": {"all": [0.17, 0.18], "chase": [0.17, 0.02], "dip": [0.14, 0.10]},
         "raw": {"all": [0.72, 2.06], "chase": [0.25, 2.21], "dip": [0.81, 2.78]},
         "win": {"all": [50, 55], "chase": [46, 50], "dip": [50, 56]},
         "p5": {"all": [-10.4, -13.0], "chase": [-13.0, -15.3], "dip": [-10.4, -12.3]}},
    ],
}
# 判定の形を決めた4年の研究（verify を研究用の4年の四本値に当てた値。前半 〜2024-12、後半 2025-01〜）。
# 翌日の寄りで売らずに持った場合の、次の h 営業日の平均的な銘柄との差（pt）。本番の検証（約2年）と並べて出す
RESEARCH = {
    "period": ["2021-10〜2024-12", "2025-01〜2026-09"], "stocks": 428,
    "v": {
        "hold": {"5": [0.07, 0.17], "10": [0.17, 0.54], "20": [0.26, 1.25]},
        "hot": {"5": [-0.16, -0.76], "10": [0.16, -0.16], "20": [0.43, 0.99]},
        "wait": {"5": [-0.01, 0.25], "10": [0.18, 1.36], "20": [-0.49, 2.16]},
        "trim_now": {"5": [-0.22, -0.98], "10": [-0.14, -1.07], "20": [-0.28, -1.01]},
        "trim_limit": {"5": [0.02, -0.01], "10": [0.06, -0.35], "20": [0.20, -0.63]},
        "trim": {"5": [-0.11, -0.29], "10": [-0.09, -0.37], "20": [0.03, -0.73]},
        "flat": {"5": [-0.07, -0.18], "10": [-0.10, -0.33], "20": [-0.10, -0.48]},
        "panic": {"5": [-1.69, -0.81], "10": [-1.65, -1.38], "20": [-1.80, -0.41]},
        "all": {"5": [-0.06, -0.13], "10": [-0.05, -0.13], "20": [-0.02, -0.11]},
    },
    # 騰落そのもの（翌日の寄り→h 日目の引け、%）。急落・相場ぐるみの急落の日の崩れは、平均的な銘柄との差がマイナスでも、それ自体は大きく戻った
    "abs": {
        "hold": {"5": [-0.01, 0.59], "10": [0.18, 1.53], "20": [0.65, 3.52]},
        "hot": {"5": [0.02, 0.06], "10": [0.56, 1.33], "20": [1.26, 3.34]},
        "wait": {"5": [2.14, 2.10], "10": [3.45, 4.37], "20": [2.92, 6.92]},
        "trim_now": {"5": [0.64, -0.01], "10": [0.99, 0.44], "20": [0.28, 1.40]},
        "panic": {"5": [2.59, 1.48], "10": [5.41, 3.05], "20": [7.61, 8.21]},
        "all": {"5": [0.20, 0.57], "10": [0.44, 1.28], "20": [0.91, 2.69]},
    },
    "exec_dip": [0.27, 0.51],   # 押した日の売り指値 − 翌日の寄りの成行（%）
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


def z5(ind: dict, i: int):
    """(終値 − 5日線) ÷ ATR。5日線からどれだけ離れたか。"""
    atr = ind["atr"][i]
    if i < 4 or not atr:
        return None
    return (ind["c"][i] - sum(ind["c"][i - 4:i + 1]) / 5) / atr


def classify(r20, rsi2, z=None) -> tuple[str | None, str | None]:
    """(形, 判定)。20日の騰落・2日RSI・5日線からの離れ（z）だけで決まる（買値は使わない）。"""
    if r20 is None:
        return None, None
    if r20 <= CRASH_R:
        return "crash", "wait"
    if r20 <= LOSE_R:
        if rsi2 is not None and rsi2 >= BOUNCE_RSI:
            return "lose", "trim_now"
        if rsi2 is not None and rsi2 < DIP_RSI:
            return "lose", "trim_limit"
        return "lose", "trim"
    if r20 >= WIN_R:
        return "win", "hot" if z is not None and z >= HOT_Z else "hold"
    return "flat", "flat"


def sell_limit(ind: dict, i: int):
    """i 日目の引けのあと、次の営業日に置く売り指値（直近4日＝i−3〜i 日目の終値の平均、呼値で切り上げ）。"""
    if i < EXIT_N - 1:
        return None
    return tick_up(sum(ind["c"][i - EXIT_N + 1:i + 1]) / EXIT_N)


def rsi_price(ind: dict, i: int, level: float):
    """次の営業日の終値がいくらなら 2日RSI がちょうど level になるか（それ以上で引けると level 以上）。

    swing.trigger_price と同じ Wilder の漸化式を、境目を level にして解いたもの（level=10 なら同じ値）。"""
    if i < 1 or ind["ag"][i] is None:
        return None
    a = 1 / 2
    r = level / (100 - level)
    c, ag, al = ind["c"][i], ind["ag"][i], ind["al"][i]
    if ag - r * al > 0:
        return c - (1 - a) * (ag - r * al) / (r * a)
    return c + (1 - a) * (r * al - ag) / a


def z5_price(ind: dict, i: int, z: float):
    """次の営業日の終値がいくらなら (終値 − 5日線) ÷ ATR が z になるか（ATR は今日の値で近似）。
    5日線 = (直近4日の終値の和 + p) ÷ 5 なので、p − (S4 + p)/5 = z·ATR を解く。"""
    atr = ind["atr"][i]
    if i < 3 or not atr:
        return None
    s4 = sum(ind["c"][i - 3:i + 1])
    return s4 / 4 + 1.25 * z * atr


def market_moves(inds: dict, min_price: float = MIN_PRICE, min_tv: float = MIN_TV) -> dict[str, float]:
    """全銘柄の等ウェイト平均の前日比（%）。前日に株価・売買代金の条件を満たした銘柄だけ。30銘柄未満の日は出さない。"""
    acc: dict[str, list] = {}
    for ind in inds.values():
        if not ind:
            continue
        c, tv, d = ind["c"], ind["tv"], ind["d"]
        for i in range(1, len(c)):
            if c[i - 1] >= min_price and tv[i - 1] is not None and tv[i - 1] >= min_tv:
                r = c[i] / c[i - 1] - 1
                if abs(r) < 0.5:
                    a = acc.setdefault(d[i], [0.0, 0])
                    a[0] += r
                    a[1] += 1
    return {d: s / m * 100 for d, (s, m) in acc.items() if m >= 30}


def market_level(moves: dict[str, float]) -> dict[str, float]:
    lvl, out = 1.0, {}
    for d in sorted(moves):
        lvl *= 1 + moves[d] / 100
        out[d] = lvl
    return out


def today_row(ind: dict, mk: float | None = None) -> dict | None:
    """最新の日の引けでの形・判定と、次の営業日の値段の目安。mk は最新の日の全銘柄の平均の前日比（%）。

    lines: 次の営業日の終値がこの値段を超える（下回る）と判定が変わる線
      hot    … 強い銘柄が伸びすぎになる終値（以上）
      bounce … 2日RSI が 90 以上になる終値（以上）。負けの銘柄なら売り時
      brk    … 5日線から −2ATR 崩れる終値（以下）"""
    n = len(ind["c"])
    if n < R_N + 2:
        return None
    i = n - 1
    c, atr, rsi = ind["c"][i], ind["atr"][i], ind["rsi"][i]
    r20 = ret_n(ind, i)
    z = z5(ind, i)
    cls, v = classify(r20, rsi, z)
    if not cls:
        return None
    hi60 = max(ind["c"][max(0, i - 59):i + 1])
    up = lambda p: tick_up(p) if p is not None and p > 0 else None       # noqa: E731  以上で引けたら → 呼値で切り上げ
    dn = lambda p: tick_down(p) if p is not None and p > 0 else None     # noqa: E731  以下で引けたら → 切り下げ
    return {"asof": ind["d"][i], "c": c, "cls": cls, "v": v, "r20": r1(r20), "rsi2": r1(rsi),
            "r5": r1((c / ind["c"][i - 5] - 1) * 100), "atr": r2(atr), "dd60": r1((c / hi60 - 1) * 100),
            "z5": r2(z), "mk": r2(mk),
            # 相場ぐるみの急落の日に5日線から崩れた（投げない）
            "panic": bool(mk is not None and mk <= PANIC_MK and z is not None and z <= BRK_Z),
            # 次の営業日に置く売り指値（減らすと決めたとき）
            "sell": sell_limit(ind, i),
            "lines": {"hot": up(z5_price(ind, i, HOT_Z)), "bounce": up(rsi_price(ind, i, BOUNCE_RSI)),
                      "brk": dn(z5_price(ind, i, BRK_Z))}}


def intraday(prev: float | None, open_: float | None, last: float | None,
             nk_prev: float | None = None, nk_last: float | None = None) -> dict | None:
    """場中（前場の更新）の値動き。prev は前の営業日の終値、open_ は今日の寄り、last はその時点の値。
    判定は変えない（18章）。撤退ライン・売り指値に届いたかはアプリが保有の記録と突き合わせる。"""
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


def verify(stocks: dict, moves: dict | None = None, split: str | None = None, min_price: float = MIN_PRICE,
           min_tv: float = MIN_TV) -> dict | None:
    """日足（stocks = {コード: swing.indicators の結果}）に毎日当てた、判定ごとのその後。先読みしない。

    moves = {日付: 全銘柄の等ウェイト平均の前日比%}（無ければ stocks から作る）。split（'YYYY-MM-DD'）より前を前半。
      判定  : i 日目の引けの判定ごとに、翌日の寄りで売らずに持った場合の、次の h 営業日の騰落（翌日の寄り→h 日目の引け）
              − 平均的な銘柄の同じ期間の騰落（i 日目の引け→h 日目の引け）。h = 5・10・20。abs は騰落そのもの
              panic は相場ぐるみの急落の日に5日線から崩れた日（判定とは別に数える）
      寄り  : 前日比 ±5% の窓を開けた日の、寄り→大引（参考で、判定には使わない）
      売り方: 2日RSI < 10 の日の翌日から、直近4日の平均の売り指値（5日目の引けまで）と、翌日の寄りで成行の差
    商いの少ない銘柄（20日平均の売買代金 3億円未満）と 300円未満は数えない。"""
    moves = moves if moves is not None else market_moves(stocks, min_price, min_tv)
    lvl = market_level(moves)
    keys = list(VERDICTS) + list(CLASSES) + ["panic", "all"]
    rows = {k: {h: [] for h in HS} for k in keys}
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
            if i + EXEC_N < n and i >= EXIT_N and rsi[i] is not None and (rsi[i] < DIP_RSI or rsi[i] >= BOUNCE_RSI):
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
            # 判定 → 次の h 日の、平均的な銘柄との差
            if d[i] not in lvl or not o[i + 1]:
                continue
            z = z5(ind, i)
            cls, v = classify(ret_n(ind, i), rsi[i], z)
            if not cls:
                continue
            ks = [v, cls, "all"]
            if moves.get(d[i], 0) <= PANIC_MK and z is not None and z <= BRK_Z:
                ks.append("panic")
            for hh in HS:
                if i + hh >= n or d[i + hh] not in lvl:
                    break
                a = (c[i + hh] / o[i + 1] - 1) * 100
                x = a - (lvl[d[i + hh]] / lvl[d[i]] - 1) * 100
                if hh == H:
                    days.add(d[i])
                for k in dict.fromkeys(ks):        # 中立は形と判定が同じ名前なので1回だけ
                    rows[k][hh].append((d[i], x, a))
    if not rows["all"][H]:
        return None
    ds = sorted(days)
    split = split or ds[len(ds) // 2]
    return {
        "from": ds[0], "to": ds[-1], "split": split, "hs": list(HS),
        "v": {k: {str(hh): _half_stats(rows[k][hh], split) for hh in HS} for k in keys},
        "gap": {k: _half_stats(v, split) for k, v in gap_rows.items()},
        "exec": {k: _half_stats(v, split) for k, v in ex_rows.items()},
    }
