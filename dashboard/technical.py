"""テクニカル: 日経平均がチャートの上でどこにいるか・節目・市場の内部と、各形のあとに過去36年で何が起きたか（DESIGN.md 37章）。

依頼（2026-11-23）: 市況分析・テクニカル分析・マクロ分析・一次情報取得を充実させて。

それまでのテクニカルは、温度計の軸（RSI(14)・25日線乖離・騰落レシオ）と局面（急落・伸びすぎ）と日経の位置の図（25日・75日線）だけで、
「移動平均の並び・MACD・ボリンジャーバンド・一目均衡表・節目（支持と抵抗）・市場の内部（何割の銘柄が線の上にいるか・新高値と新安値）」は無かった。

方針（CLAUDE.md の約束どおり）:
  - **事実の並べ替えと、同じ形の過去の分布だけ。** 買い・売りのシグナルとは呼ばない。形の定義は定数で公開し、
    件数・前半／後半・比べる相手（すべての日）を必ず並べる。20回未満は「少ない」。
  - 形は「その形に入った日」で数える（続いている日を毎日数えると同じ出来事を何十回も数えるため）。同じ形が GAP 日以内に
    また来たら同じ出来事。先読みしない（その日の引けまでの値だけで決める）。
  - 注文の条件・並べ方・損切り・保有の判定には使わない（買う候補を出すのは swing.py の2つのルールだけ）。
  - 節目は過去の値（線・バンド・雲・高値安値・千円の切りのいい値）の並べ替えで、そこで止まるとは言わない。
  - 市場の内部は約510銘柄（日足キャッシュ ohlc.json。売買代金の上位と日経225。今の顔ぶれだけ＝生存者の偏り）。

入力: docs/data/cache/index_long.json（日経の四本値 1990〜。phase.run が今日の分まで足したあと）、
      docs/data/cache/ohlc.json（約510銘柄・約2年）、docs/data/cache/bars.json の macro.topix（TOPIX の終値・約3年）。
"""
from __future__ import annotations

import json
import math
import statistics

from .phase import SPLIT

LONG_PATH = "docs/data/cache/index_long.json"
OHLC_PATH = "docs/data/cache/ohlc.json"

START = 260                 # 200日線・1年の高値・一目の雲（52+26日）がそろう日から数える
MAS = (5, 25, 75, 200)
RSI_N, RSI_LO, RSI_HI = 14, 30.0, 70.0
MACD_F, MACD_S, MACD_SIG = 12, 26, 9
BB_N, BB_K = 20, 2.0
ICHI_T, ICHI_K, ICHI_B = 9, 26, 52      # 転換線・基準線・先行スパンB の日数。先行スパンは 26日先、遅行スパンは 26日前に描く
SQUEEZE_PCT = 10            # バンド幅が直近1年で狭いほうから 10%以内 → 「バンドが縮んだ」
GAP = 10                    # 同じ形が10営業日以内にまた来たら同じ出来事
SMALL = 20                  # これ未満は「少ない」
SLOPE_DAYS = 5              # 線の向き: 5営業日前と比べる
SLOPE_FLAT = 0.1            # 5日で ±0.1% 未満は横ばい
CHART_DAYS = 130            # 図の日数
LEVEL_RANGE = 12.0          # 節目は今の値から ±12% の中だけ
LEVEL_MERGE = 0.25          # 0.25% より近い節目はまとめる
BREADTH_DAYS = 130          # 市場の内部の図の日数
NH_DAYS = 250               # 新高値・新安値: 直近250営業日の終値の最高・最低を更新

# 市場の内部の検証（tools/research_breadth.py。2026-11-23、510銘柄の4年の四本値。今の採用銘柄だけ＝生存者の偏り）。
# 25日線より上の銘柄の割合が 20%以下・80%以上に入った日（10営業日以内の続きは同じ出来事）から20営業日後の日経平均。閾値は結果を見る前に決めた
BREADTH_LOW, BREADTH_HIGH = 20, 80
BREADTH_4Y = {
    "from": "2021-10-07", "to": "2026-10-22", "split": "2024-04-12",
    "base": {"all": {"n": 1233, "r20": 1.38, "p20": 60}, "first": {"n": 616, "r20": 1.03, "p20": 56}, "second": {"n": 617, "r20": 1.72, "p20": 64}},
    "lo": {"all": {"n": 15, "r20": 4.0, "p20": 87}, "first": {"n": 10, "r20": 3.89, "p20": 90}, "second": {"n": 5, "r20": 4.22, "p20": 80}},
    "hi": {"all": {"n": 16, "r20": 0.52, "p20": 56}, "first": {"n": 10, "r20": 0.88, "p20": 50}, "second": {"n": 6, "r20": -0.08, "p20": 67}},
}

# 形（その形に入った日）。key → (名前, 定義)
SIGNALS = {
    "rsi_lo": ("RSI が30を割った", f"RSI({RSI_N}) が {RSI_LO:.0f} 未満になった日（前日は {RSI_LO:.0f} 以上）"),
    "rsi_hi": ("RSI が70を超えた", f"RSI({RSI_N}) が {RSI_HI:.0f} 以上になった日（前日は未満）"),
    "bb_lo": ("−2σを下に抜けた", f"終値がボリンジャーバンド（{BB_N}日・±{BB_K:.0f}σ）の −2σ を下回った日"),
    "bb_hi": ("+2σを上に抜けた", f"終値が +{BB_K:.0f}σ を上回った日"),
    "macd_gc": ("MACD が上に交差", f"MACD（{MACD_F}・{MACD_S}・{MACD_SIG}）がシグナルを下から上に抜けた日"),
    "macd_dc": ("MACD が下に交差", "MACD がシグナルを上から下に抜けた日"),
    "kumo_up": ("雲の上に抜けた", f"終値が一目均衡表の雲（{ICHI_T}・{ICHI_K}・{ICHI_B}）の上限を上に抜けた日"),
    "kumo_dn": ("雲の下に抜けた", "終値が雲の下限を下に抜けた日"),
    "gc": ("25日線が75日線を上に", "25日線が75日線を下から上に抜けた日（ゴールデンクロス）"),
    "dc": ("25日線が75日線を下に", "25日線が75日線を上から下に抜けた日（デッドクロス）"),
    "ma200_up": ("200日線を上に", "終値が200日線を下から上に抜けた日"),
    "ma200_dn": ("200日線を下に", "終値が200日線を上から下に抜けた日"),
    "po_up": ("上昇の並び", "5日線 > 25日線 > 75日線 > 200日線 の並びになった日"),
    "po_dn": ("下落の並び", "5日線 < 25日線 < 75日線 < 200日線 の並びになった日"),
    "squeeze": ("バンドが縮んだ", f"バンド幅が直近1年で狭いほうから {SQUEEZE_PCT}%以内に入った日（向きは問わない。値幅を比べる）"),
}
ORDER = list(SIGNALS)


# ---------- 計算 ----------

def _sma(x: list[float], k: int) -> list[float | None]:
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= k:
            s -= x[i - k]
        if i >= k - 1:
            out[i] = s / k
    return out


def _ema(x: list[float | None], k: int) -> list[float | None]:
    out, a, prev = [None] * len(x), 2 / (k + 1), None
    for i, v in enumerate(x):
        if v is None:
            continue
        prev = v if prev is None else prev + a * (v - prev)
        out[i] = prev
    return out


def _rsi(c: list[float], n: int) -> list[float | None]:
    out = [None] * len(c)
    if len(c) <= n:
        return out
    up = sum(max(c[i] - c[i - 1], 0) for i in range(1, n + 1)) / n
    dn = sum(max(c[i - 1] - c[i], 0) for i in range(1, n + 1)) / n
    for i in range(n, len(c)):
        if i > n:
            ch = c[i] - c[i - 1]
            up = (up * (n - 1) + max(ch, 0)) / n
            dn = (dn * (n - 1) + max(-ch, 0)) / n
        out[i] = 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)
    return out


def _mid(h: list[float], lo: list[float], k: int) -> list[float | None]:
    out = [None] * len(h)
    for i in range(k - 1, len(h)):
        out[i] = (max(h[i - k + 1:i + 1]) + min(lo[i - k + 1:i + 1])) / 2
    return out


class Ind:
    """日経の四本値と指標の系列。cloud_top/bot[i] は i の日に描かれる雲（26日前に計算した先行スパン）。"""

    def __init__(self, rows: list):
        rows = sorted((r for r in rows if len(r) >= 5 and all(isinstance(v, (int, float)) and v > 0 for v in r[1:5])),
                      key=lambda r: r[0])
        self.d = [r[0] for r in rows]
        self.o = [float(r[1]) for r in rows]
        self.h = [float(r[2]) for r in rows]
        self.l = [float(r[3]) for r in rows]
        self.c = [float(r[4]) for r in rows]
        n = self.n = len(rows)
        c = self.c
        self.ma = {k: _sma(c, k) for k in MAS}
        self.rsi = _rsi(c, RSI_N)
        ef, es = _ema(c, MACD_F), _ema(c, MACD_S)
        self.macd = [None if a is None or b is None or i < MACD_S - 1 else a - b for i, (a, b) in enumerate(zip(ef, es))]
        self.sig = _ema(self.macd, MACD_SIG)
        self.hist = [None if a is None or b is None else a - b for a, b in zip(self.macd, self.sig)]
        mid = _sma(c, BB_N)
        self.bb_mid, self.bb_up, self.bb_lo, self.bw = mid, [None] * n, [None] * n, [None] * n
        for i in range(BB_N - 1, n):
            sd = statistics.pstdev(c[i - BB_N + 1:i + 1])
            self.bb_up[i] = mid[i] + BB_K * sd
            self.bb_lo[i] = mid[i] - BB_K * sd
            self.bw[i] = (self.bb_up[i] - self.bb_lo[i]) / mid[i] * 100
        self.tenkan = _mid(self.h, self.l, ICHI_T)
        self.kijun = _mid(self.h, self.l, ICHI_K)
        self.span_a = [None if a is None or b is None else (a + b) / 2 for a, b in zip(self.tenkan, self.kijun)]
        self.span_b = _mid(self.h, self.l, ICHI_B)
        self.cloud_top, self.cloud_bot = [None] * n, [None] * n
        for i in range(ICHI_K, n):
            a, b = self.span_a[i - ICHI_K], self.span_b[i - ICHI_K]
            if a is not None and b is not None:
                self.cloud_top[i], self.cloud_bot[i] = max(a, b), min(a, b)
        tr = [self.h[0] - self.l[0]] + [max(self.h[i], c[i - 1]) - min(self.l[i], c[i - 1]) for i in range(1, n)]
        self.atr = _sma(tr, 14)

    # ---- 形の判定（その日の引けまでの値だけ） ----
    def _cross(self, i: int, a: list, b: list | float, up: bool) -> bool:
        a0, a1 = a[i - 1], a[i]
        b0 = b if isinstance(b, float) else b[i - 1]
        b1 = b if isinstance(b, float) else b[i]
        if None in (a0, a1, b0, b1):
            return False
        return (a1 > b1 and a0 <= b0) if up else (a1 < b1 and a0 >= b0)

    def order(self, i: int) -> str:
        v = [self.ma[k][i] for k in MAS]
        if None in v:
            return "mixed"
        if v[0] > v[1] > v[2] > v[3]:
            return "up"
        if v[0] < v[1] < v[2] < v[3]:
            return "down"
        return "mixed"

    def squeezed(self, i: int) -> bool:
        if i < START or self.bw[i] is None:
            return False
        past = sorted(x for x in self.bw[i - 249:i + 1] if x is not None)
        return bool(past) and self.bw[i] <= past[max(0, int(len(past) * SQUEEZE_PCT / 100) - 1)]

    def on(self, key: str, i: int) -> bool:
        c = self.c
        if key == "rsi_lo":
            return self.rsi[i] is not None and self.rsi[i - 1] is not None and self.rsi[i] < RSI_LO <= self.rsi[i - 1]
        if key == "rsi_hi":
            return self.rsi[i] is not None and self.rsi[i - 1] is not None and self.rsi[i] >= RSI_HI > self.rsi[i - 1]
        if key == "bb_lo":
            return self._cross(i, c, self.bb_lo, False)
        if key == "bb_hi":
            return self._cross(i, c, self.bb_up, True)
        if key == "macd_gc":
            return self._cross(i, self.hist, 0.0, True)
        if key == "macd_dc":
            return self._cross(i, self.hist, 0.0, False)
        if key == "kumo_up":
            return self._cross(i, c, self.cloud_top, True)
        if key == "kumo_dn":
            return self._cross(i, c, self.cloud_bot, False)
        if key == "gc":
            return self._cross(i, self.ma[25], self.ma[75], True)
        if key == "dc":
            return self._cross(i, self.ma[25], self.ma[75], False)
        if key == "ma200_up":
            return self._cross(i, c, self.ma[200], True)
        if key == "ma200_dn":
            return self._cross(i, c, self.ma[200], False)
        if key == "po_up":
            return self.order(i) == "up" and self.order(i - 1) != "up"
        if key == "po_dn":
            return self.order(i) == "down" and self.order(i - 1) != "down"
        if key == "squeeze":
            return self.squeezed(i) and not self.squeezed(i - 1)
        return False


def _mean(x):
    return round(statistics.mean(x), 2) if x else None


def _med(x):
    return round(statistics.median(x), 2) if x else None


def _pct(x, f):
    return round(100 * sum(1 for v in x if f(v)) / len(x)) if x else None


def events(S: Ind, key: str, end: int | None = None) -> list[int]:
    """形に入った日（GAP 日以内の続きは最初の日だけ）。20日先まで分かる日だけ。"""
    end = S.n - 21 if end is None else end
    out, last = [], -999
    for i in range(START, end):
        if i - last >= GAP and S.on(key, i):
            out.append(i)
            last = i
    return out


def _one(S: Ind, ev: list[int]) -> dict | None:
    if not ev:
        return None
    c, lo = S.c, S.l
    r5 = [(c[i + 5] / c[i] - 1) * 100 for i in ev]
    r20 = [(c[i + 20] / c[i] - 1) * 100 for i in ev]
    return {"n": len(ev), "r5": _mean(r5), "r20": _mean(r20), "r20_med": _med(r20), "p20": _pct(r20, lambda v: v > 0),
            "abs20": _mean([abs(v) for v in r20]),
            "min20_med": _med([(min(lo[i + 1:i + 21]) / c[i] - 1) * 100 for i in ev])}


def stats(S: Ind, key: str) -> dict:
    ev = events(S, key)
    return {"key": key, "name": SIGNALS[key][0], "def": SIGNALS[key][1],
            "all": _one(S, ev), "first": _one(S, [i for i in ev if S.d[i] < SPLIT]),
            "second": _one(S, [i for i in ev if S.d[i] >= SPLIT]),
            "last": S.d[ev[-1]] if ev else None}


def baseline(S: Ind) -> dict:
    ev = list(range(START, S.n - 21))
    return {"all": _one(S, ev), "first": _one(S, [i for i in ev if S.d[i] < SPLIT]),
            "second": _one(S, [i for i in ev if S.d[i] >= SPLIT])}


# ---------- いまの位置 ----------

def _r(x, d=0):
    return None if x is None else round(x, d) if d else int(round(x))


def _dev(c, m):
    return None if m is None else round((c / m - 1) * 100, 2)


def _slope(series: list, i: int) -> str | None:
    a, b = series[i], series[i - SLOPE_DAYS] if i >= SLOPE_DAYS else None
    if a is None or b is None:
        return None
    ch = (a / b - 1) * 100
    return "up" if ch >= SLOPE_FLAT else "down" if ch <= -SLOPE_FLAT else "flat"


def _since(S: Ind, i: int, test) -> int:
    """test(j) が続いている日数（今日を含む）。"""
    k = 0
    while i - k >= 1 and test(i - k):
        k += 1
    return k


def now_state(S: Ind) -> dict:
    i, c = S.n - 1, S.c[-1]
    mas = []
    for k in MAS:
        m = S.ma[k][i]
        mas.append({"n": k, "v": _r(m), "dev": _dev(c, m), "slope": _slope(S.ma[k], i), "above": m is not None and c > m})
    rsi = S.rsi[i]
    rsi_zone = "lo" if rsi is not None and rsi < RSI_LO else "hi" if rsi is not None and rsi >= RSI_HI else "mid"
    # MACD: 最後の交差
    cross = None
    for j in range(i, max(i - 120, 1), -1):
        if S.on("macd_gc", j) or S.on("macd_dc", j):
            cross = {"kind": "gc" if S.on("macd_gc", j) else "dc", "date": S.d[j], "days": i - j}
            break
    up, lo_, mid = S.bb_up[i], S.bb_lo[i], S.bb_mid[i]
    pb = None if up is None or up == lo_ else round((c - lo_) / (up - lo_) * 100)
    past_bw = sorted(x for x in S.bw[max(0, i - 249):i + 1] if x is not None)
    bw_rank = round(100 * sum(1 for x in past_bw if x < S.bw[i]) / len(past_bw)) if past_bw and S.bw[i] is not None else None
    top, bot = S.cloud_top[i], S.cloud_bot[i]
    kumo = None if top is None else "above" if c > top else "below" if c < bot else "in"
    chikou = None if i < ICHI_K else ("above" if c > S.c[i - ICHI_K] else "below")
    tk = None if S.tenkan[i] is None or S.kijun[i] is None else ("above" if S.tenkan[i] > S.kijun[i] else "below" if S.tenkan[i] < S.kijun[i] else "same")
    sanyaku = ("good" if kumo == "above" and tk == "above" and chikou == "above"
               else "bad" if kumo == "below" and tk == "below" and chikou == "below" else None)
    # 先の雲（今日から26日先まで。すでに計算できる）と、雲のねじれ（先行スパンA・Bの上下が入れ替わる日）
    ahead, twist = [], None
    for k in range(1, ICHI_K + 1):
        j = i + k - ICHI_K
        a, b = S.span_a[j], S.span_b[j]
        if a is None or b is None:
            continue
        ahead.append([_r(a), _r(b)])
        if twist is None and len(ahead) >= 2 and (ahead[-1][0] - ahead[-1][1]) * (ahead[-2][0] - ahead[-2][1]) < 0:
            twist = k
    hi52, lo52 = max(S.h[i - 249:i + 1]), min(S.l[i - 249:i + 1])
    hi_i = max(range(i - 249, i + 1), key=lambda j: S.h[j])
    atr = S.atr[i]
    return {
        "date": S.d[i], "close": round(c, 2), "chg": round((c / S.c[i - 1] - 1) * 100, 2),
        "mas": mas, "order": S.order(i), "order_days": _since(S, i, lambda j: S.order(j) == S.order(i)),
        "rsi": _r(rsi, 1), "rsi_zone": rsi_zone, "rsi_days": _since(S, i, lambda j: S.rsi[j] is not None and
                                                                     (S.rsi[j] < RSI_LO if rsi_zone == "lo" else S.rsi[j] >= RSI_HI if rsi_zone == "hi"
                                                                      else RSI_LO <= S.rsi[j] < RSI_HI)),
        "macd": _r(S.macd[i]), "signal": _r(S.sig[i]), "hist": _r(S.hist[i]), "macd_cross": cross,
        "bb": {"up": _r(up), "mid": _r(mid), "lo": _r(lo_), "pb": pb, "bw": _r(S.bw[i], 1), "bw_rank": bw_rank,
               "walk": _since(S, i, lambda j: S.bb_lo[j] is not None and S.c[j] < S.bb_mid[j] - (S.bb_mid[j] - S.bb_lo[j]) / 2)
               if c < (mid or c) else _since(S, i, lambda j: S.bb_up[j] is not None and S.c[j] > S.bb_mid[j] + (S.bb_up[j] - S.bb_mid[j]) / 2)},
        "ichimoku": {"tenkan": _r(S.tenkan[i]), "kijun": _r(S.kijun[i]), "top": _r(top), "bot": _r(bot), "kumo": kumo,
                     "chikou": chikou, "tk": tk, "sanyaku": sanyaku, "twist": twist,
                     "thick": None if top is None else round((top - bot) / c * 100, 2)},
        "hi52": round(hi52, 2), "lo52": round(lo52, 2), "hi52_date": S.d[hi_i],
        "from_hi52": round((c / hi52 - 1) * 100, 2), "from_lo52": round((c / lo52 - 1) * 100, 2),
        "pos52": round((c - lo52) / (hi52 - lo52) * 100) if hi52 > lo52 else None,
        "atr": _r(atr), "atr_pct": None if atr is None else round(atr / c * 100, 2),
        "ahead": ahead,
    }


def levels(S: Ind, st: dict) -> list[dict]:
    """今の値の上下にある節目（線・バンド・雲・高値安値・千円の切りのいい値）。そこで止まるとは言わない。"""
    i, c = S.n - 1, S.c[-1]
    cand = []
    for k in (25, 75, 200):
        if S.ma[k][i]:
            cand.append((S.ma[k][i], f"{k}日線", "ma"))
    if S.bb_up[i]:
        cand += [(S.bb_up[i], "+2σ", "bb"), (S.bb_lo[i], "−2σ", "bb")]
    if S.cloud_top[i]:
        cand += [(S.cloud_top[i], "雲の上限", "kumo"), (S.cloud_bot[i], "雲の下限", "kumo")]
    if S.kijun[i]:
        cand.append((S.kijun[i], "基準線", "kumo"))
    h60 = max(range(i - 59, i + 1), key=lambda j: S.h[j])
    l60 = min(range(i - 59, i + 1), key=lambda j: S.l[j])
    cand += [(S.h[h60], f"60日の高値（{S.d[h60][5:].replace('-', '/')}）", "hl"), (S.l[l60], f"60日の安値（{S.d[l60][5:].replace('-', '/')}）", "hl"),
             (st["hi52"], "1年の高値", "hl"), (st["lo52"], "1年の安値", "hl")]
    step = 1000 if c >= 10000 else 100
    base = math.floor(c / step) * step
    for r in (base - step, base, base + step, base + 2 * step):
        if r > 0 and abs(r / c - 1) > 0.001:
            cand.append((float(r), f"{r:,.0f}円（切りのいい値）", "round"))
    cand = [x for x in cand if 0.05 <= abs(x[0] / c - 1) * 100 <= LEVEL_RANGE]
    cand.sort(key=lambda x: -x[0])
    out = []
    for p, label, kind in cand:
        if out and abs(p / out[-1]["price"] - 1) * 100 < LEVEL_MERGE:
            if kind != "round":
                out[-1]["label"] += "・" + label
                if out[-1]["kind"] == "round":
                    out[-1]["kind"] = kind
            continue
        out.append({"price": _r(p), "label": label, "kind": kind, "dist": round((p / c - 1) * 100, 2), "above": p > c})
    return out


def chart(S: Ind) -> dict:
    a = max(0, S.n - CHART_DAYS)
    R = lambda xs: [_r(v) for v in xs[a:]]   # noqa: E731
    return {"d": S.d[a:], "o": R(S.o), "h": R(S.h), "l": R(S.l), "c": R(S.c),
            "ma25": R(S.ma[25]), "ma75": R(S.ma[75]), "ma200": R(S.ma[200]), "bb_up": R(S.bb_up), "bb_lo": R(S.bb_lo),
            "top": R(S.cloud_top), "bot": R(S.cloud_bot),
            "hist": [None if v is None else round(v) for v in S.hist[a:]], "rsi": [None if v is None else round(v, 1) for v in S.rsi[a:]]}


# ---------- TOPIX・NT倍率（終値だけ） ----------

def topix_view(bars_macro: dict | None, S: Ind) -> dict | None:
    tp = (bars_macro or {}).get("topix") or {}
    d, c = tp.get("d") or [], tp.get("c") or []
    pairs = [(x, float(y)) for x, y in zip(d, c) if isinstance(y, (int, float)) and y > 0]
    if len(pairs) < 210:
        return None
    td = [x for x, _ in pairs]
    tc = [y for _, y in pairs]
    i = len(tc) - 1
    ma = {k: _sma(tc, k) for k in (25, 75, 200)}
    rsi = _rsi(tc, RSI_N)
    hi, lo = max(tc[-250:]), min(tc[-250:])
    nkmap = dict(zip(S.d, S.c))
    nt = [(x, nkmap[x] / y) for x, y in pairs if x in nkmap]
    ntv = [v for _, v in nt]
    nt_now = ntv[-1] if ntv else None
    nt20 = ntv[-21] if len(ntv) > 21 else None
    nt_1y = ntv[-250:]
    return {
        "date": td[-1], "close": round(tc[-1], 2), "chg": round((tc[-1] / tc[-2] - 1) * 100, 2),
        "mas": [{"n": k, "dev": _dev(tc[-1], ma[k][i]), "slope": _slope(ma[k], i)} for k in (25, 75, 200)],
        "rsi": _r(rsi[i], 1), "from_hi52": round((tc[-1] / hi - 1) * 100, 2), "pos52": round((tc[-1] - lo) / (hi - lo) * 100) if hi > lo else None,
        "nt": None if nt_now is None else {
            "now": round(nt_now, 2), "d20": None if nt20 is None else round(nt_now - nt20, 2),
            "hi1y": round(max(nt_1y), 2), "lo1y": round(min(nt_1y), 2),
            "pos1y": round((nt_now - min(nt_1y)) / (max(nt_1y) - min(nt_1y)) * 100) if max(nt_1y) > min(nt_1y) else None,
            "spark": [round(v, 2) for v in ntv[-CHART_DAYS:]],
        },
    }


# ---------- 市場の内部（約510銘柄） ----------

def breadth(ohlc: dict | None) -> dict | None:
    """日足キャッシュの銘柄で、25日線・200日線より上の割合、新高値・新安値の数、騰落レシオ(25日)を日ごとに数える。"""
    dates = (ohlc or {}).get("dates") or []
    stocks = (ohlc or {}).get("stocks") or {}
    n = len(dates)
    if n < 230 or len(stocks) < 50:
        return None
    a25, n25, a200, n200, nh, nl, adv, dec = ([0] * n for _ in range(8))
    for rows in stocks.values():
        c = [r[3] if r and isinstance(r[3], (int, float)) and r[3] > 0 else None for r in rows[:n]]
        c += [None] * (n - len(c))
        s, k, last = 0.0, 0, None
        win: list[float] = []
        for i in range(n):
            v = c[i]
            if v is None:
                continue
            if last is not None:
                if v > last:
                    adv[i] += 1
                elif v < last:
                    dec[i] += 1
            last = v
            win.append(v)
            if len(win) >= 25:
                m25 = sum(win[-25:]) / 25
                n25[i] += 1
                a25[i] += v > m25
            if len(win) >= 200:
                m200 = sum(win[-200:]) / 200
                n200[i] += 1
                a200[i] += v > m200
            if len(win) > NH_DAYS:
                prev = win[-NH_DAYS - 1:-1]
                nh[i] += v > max(prev)
                nl[i] += v < min(prev)
    a = max(0, n - BREADTH_DAYS)
    p25 = [round(100 * a25[i] / n25[i]) if n25[i] >= 50 else None for i in range(n)]
    p200 = [round(100 * a200[i] / n200[i]) if n200[i] >= 50 else None for i in range(n)]
    ratio = [None] * n
    for i in range(25, n):
        u, dn = sum(adv[i - 24:i + 1]), sum(dec[i - 24:i + 1])
        ratio[i] = round(100 * u / dn) if dn else None

    def rank(xs, i):
        past = [x for x in xs[max(0, i - 249):i + 1] if x is not None]
        return round(100 * sum(1 for x in past if x < xs[i]) / len(past)) if past and xs[i] is not None else None

    i = n - 1
    zone = "lo" if p25[i] is not None and p25[i] <= BREADTH_LOW else "hi" if p25[i] is not None and p25[i] >= BREADTH_HIGH else None
    return {
        "date": dates[-1], "n": len(stocks), "zone": zone, "research": {**BREADTH_4Y, "low": BREADTH_LOW, "high": BREADTH_HIGH, "small": SMALL},
        "p25": p25[i], "p25_rank": rank(p25, i), "p200": p200[i], "p200_rank": rank(p200, i),
        "nh": nh[i], "nl": nl[i], "nh5": sum(nh[i - 4:i + 1]), "nl5": sum(nl[i - 4:i + 1]),
        "adv": adv[i], "dec": dec[i], "ratio": ratio[i], "ratio_rank": rank(ratio, i),
        "series": {"d": dates[a:], "p25": p25[a:], "p200": p200[a:], "nh": nh[a:], "nl": nl[a:], "ratio": ratio[a:]},
    }


# ---------- 個別株（銘柄シートの「チャートの位置」） ----------

def stock_tech(rows: list) -> dict | None:
    """日足キャッシュの1銘柄（[始, 高, 安, 終, 出来高] の並び。欠けは None）→ 線の並び・75/200日線から・%b・雲・1年の位置。"""
    xs = [["", r[0], r[1], r[2], r[3]] for r in rows or [] if r and all(isinstance(v, (int, float)) and v > 0 for v in r[:4])]
    if len(xs) < 80:
        return None
    S = Ind(xs)
    i, c = S.n - 1, S.c[-1]
    up, lo_ = S.bb_up[i], S.bb_lo[i]
    top, bot = S.cloud_top[i], S.cloud_bot[i]
    w = min(250, S.n)
    hi52, lo52 = max(S.h[-w:]), min(S.l[-w:])
    return {
        "dev75": _dev(c, S.ma[75][i]), "dev200": _dev(c, S.ma[200][i]) if S.n >= 200 else None,
        "order": S.order(i) if S.n >= 200 else None,
        "pb": None if up is None or up == lo_ else round((c - lo_) / (up - lo_) * 100),
        "kumo": None if top is None else "above" if c > top else "below" if c < bot else "in",
        "pos52": round((c - lo52) / (hi52 - lo52) * 100) if hi52 > lo52 else None,
        "from_hi52": round((c / hi52 - 1) * 100, 1), "macd": None if S.hist[i] is None else ("up" if S.hist[i] >= 0 else "down"),
    }


# ---------- 見出しと読み（ルールベース。判定ではなく事実の並べ替え） ----------

ORDER_WORD = {"up": "上昇の並び（5日線>25日線>75日線>200日線）", "down": "下落の並び（5日線<25日線<75日線<200日線）", "mixed": "線の並びは混在"}
KUMO_WORD = {"above": "雲の上", "in": "雲の中", "below": "雲の下"}


def _f(v, d=1, unit="%"):
    return "—" if v is None else f"{v:+.{d}f}{unit}"


def active(S: Ind, sx: dict, base: dict, look: int = 5) -> list[dict]:
    """直近 look 営業日に入った形（新しい順）と、その形のあとの分布。"""
    i = S.n - 1
    out = []
    for key in ORDER:
        for j in range(i, i - look, -1):
            if S.on(key, j):
                s = sx[key]
                out.append({"key": key, "name": s["name"], "def": s["def"], "date": S.d[j], "days": i - j,
                            "all": s["all"], "first": s["first"], "second": s["second"], "few": ((s["all"] or {}).get("n") or 0) < SMALL})
                break
    out.sort(key=lambda x: x["days"])
    return out


def headline(st: dict) -> str:
    below = [m["n"] for m in st["mas"] if m["v"] is not None and not m["above"]]
    above = [m["n"] for m in st["mas"] if m["v"] is not None and m["above"]]
    if not below:
        pos = "すべての移動平均の上"
    elif not above:
        pos = "すべての移動平均の下"
    else:
        pos = "・".join(f"{n}日" for n in below) + "線の下" if len(below) <= 2 else "・".join(f"{n}日" for n in above) + "線の上"
    kumo = KUMO_WORD.get((st["ichimoku"] or {}).get("kumo") or "", "")
    rsi = st["rsi"]
    rz = f"RSI {rsi:.0f}（売られすぎの域）" if st["rsi_zone"] == "lo" else f"RSI {rsi:.0f}（買われすぎの域）" if st["rsi_zone"] == "hi" else f"RSI {rsi:.0f}"
    return f"日経は{pos}・{kumo}。{rz}" if kumo else f"日経は{pos}。{rz}"


def reading(st: dict, act: list[dict], base: dict, br: dict | None, tp: dict | None) -> list[dict]:
    """節ごとの読み（読み → 根拠 → だから の形の、機械の文章）。Routine の ai_review / ai_commentary はこれを材料にする。"""
    out = []
    ich = st["ichimoku"]
    ma25 = next((m for m in st["mas"] if m["n"] == 25), {})
    ma200 = next((m for m in st["mas"] if m["n"] == 200), {})
    slope_w = {"up": "上向き", "down": "下向き", "flat": "横ばい"}
    t = [f"{ORDER_WORD[st['order']]}が{st['order_days']}日目。"]
    def where(m):
        if m.get("v") is None or m.get("dev") is None:
            return "—"
        gap = abs(m["v"] / st["close"] - 1) * 100
        return f"{m['v']:,}円で{slope_w.get(m.get('slope'), '—')}（今の値より{gap:.1f}%{'上' if m['v'] > st['close'] else '下'}）"
    t.append(f"25日線は{where(ma25)}、200日線は{where(ma200)}。")
    if ich.get("kumo"):
        t.append(f"一目均衡表は{KUMO_WORD[ich['kumo']]}" + ("（三役好転）" if ich["sanyaku"] == "good" else "（三役逆転）" if ich["sanyaku"] == "bad" else "")
                 + (f"、雲は{ich['twist']}日先でねじれる" if ich.get("twist") else "") + "。")
    if st["order"] == "up":
        t.append("だから大きな流れは上向きのままで、線の上での押しは流れの中の押し。")
    elif st["order"] == "down":
        t.append("だから大きな流れは下向きで、戻りは線（25日線・雲）で止まりやすい位置関係にある（止まるとは限らない）。")
    else:
        t.append("だから長い線と短い線の向きがそろっておらず、流れが入れ替わる途中にある。")
    out.append({"title": "流れ（移動平均と一目均衡表）", "text": "".join(t)})
    bb = st["bb"]
    t = [f"RSI(14) は {st['rsi']:.0f}（{st['rsi_days']}日目）、ボリンジャーバンドの %b は {bb['pb']}（0が−2σ、100が+2σ）、"
         f"バンド幅は1年で狭いほうから{bb['bw_rank']}%の位置。"]
    mc = st.get("macd_cross")
    if mc:
        t.append(f"MACD は {mc['date'][5:].replace('-', '/')} に{'上' if mc['kind'] == 'gc' else '下'}に交差（{mc['days']}日前）。")
    if st["rsi_zone"] == "lo" or (bb["pb"] is not None and bb["pb"] < 0):
        t.append("だから短い目では売られた位置にあるが、過去の同じ形の後は「戻る」と「さらに下げる」の両方があり、下の分布で確かめる。")
    elif st["rsi_zone"] == "hi" or (bb["pb"] is not None and bb["pb"] > 100):
        t.append("だから短い目では買われた位置。過去の同じ形の後の分布で、一服の大きさを確かめる。")
    else:
        t.append("だから短い目の勢いは中立の範囲。")
    out.append({"title": "勢い（RSI・MACD・バンド）", "text": "".join(t)})
    if br:
        t = [f"約{br['n']}銘柄のうち25日線より上は{br['p25']}%（1年で低いほうから{br['p25_rank']}%）、200日線より上は{br['p200']}%。"
             f"新高値 {br['nh']}・新安値 {br['nl']}（5日で {br['nh5']}・{br['nl5']}）。"]
        nk_above25 = ma25.get("above")
        if nk_above25 and br["p25"] is not None and br["p25"] < 50:
            t.append("だから日経は25日線の上でも、半分以上の銘柄は線の下にいる（上げが一部の銘柄に偏っている）。")
        elif (not nk_above25) and br["p25"] is not None and br["p25"] >= 50:
            t.append("だから日経は25日線の下でも、半分以上の銘柄は線の上にいる（下げが一部の値がさ株に偏っている）。")
        else:
            t.append("だから指数と銘柄の広がりは同じ向き。")
        out.append({"title": "市場の内部（何割の銘柄が線の上か）", "text": "".join(t)})
    if tp and tp.get("nt"):
        nt = tp["nt"]
        t = [f"NT倍率（日経÷TOPIX）は {nt['now']:.2f}倍で、20日で{_f(nt['d20'], 2, '')}、1年の範囲（{nt['lo1y']:.2f}〜{nt['hi1y']:.2f}）の{nt['pos1y']}%の位置。"]
        if nt["d20"] is not None and nt["d20"] <= -0.3:
            t.append("だから日経（値がさ・半導体の比重が大きい）よりTOPIX（銀行・内需の比重が大きい）が強い流れ。")
        elif nt["d20"] is not None and nt["d20"] >= 0.3:
            t.append("だからTOPIXより日経が強い（値がさ・半導体が相場を引っ張る）流れ。")
        else:
            t.append("だから日経とTOPIXの強さに大きな差はない。")
        out.append({"title": "日経とTOPIX", "text": "".join(t)})
    return out


# ---------- 入口 ----------

def _read(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def build(long: dict, ohlc: dict | None = None, bars_macro: dict | None = None, slot: str = "", today: str = "") -> dict | None:
    S = Ind((long or {}).get("n225") or [])
    if S.n < START + 60:
        return None
    sx = {k: stats(S, k) for k in ORDER}
    base = baseline(S)
    st = now_state(S)
    act = active(S, sx, base)
    br = breadth(ohlc)
    tp = topix_view(bars_macro, S)
    return {
        "asof": S.d[-1], "slot": slot, "stale": bool(slot == "taibike" and today and S.d[-1] != today),
        "headline": headline(st), "state": st, "levels": levels(S, st), "active": act,
        "reading": reading(st, act, base, br, tp), "topix": tp, "breadth": br, "chart": chart(S),
        "research": {"from": S.d[START], "to": S.d[-21], "split": SPLIT, "gap": GAP, "small": SMALL, "base": base,
                     "signals": [sx[k] for k in ORDER]},
        "rules": {"mas": list(MAS), "rsi": [RSI_N, RSI_LO, RSI_HI], "macd": [MACD_F, MACD_S, MACD_SIG], "bb": [BB_N, BB_K],
                  "ichimoku": [ICHI_T, ICHI_K, ICHI_B], "squeeze": SQUEEZE_PCT, "nh_days": NH_DAYS},
    }


def run(slot: str, today: str, bars_macro: dict | None = None) -> dict | None:
    """収集から呼ぶ入口（phase.run が index_long.json を今日まで更新したあと）。"""
    return build(_read(LONG_PATH, {}) or {}, _read(OHLC_PATH, None), bars_macro, slot, today)
