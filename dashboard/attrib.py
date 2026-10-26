"""値動きの分解: その日の日経平均を「どの銘柄・業種が」「いつ」「何と一緒に」動かしたか（DESIGN.md 31章）。

依頼（2026-10-26）: なぜその値動きになったかの分析を厚く。それまでの課題: 寄与度は株探の記事の上位5銘柄ずつを Routine が引くだけで、
業種ぐるみの寄与・5日／20日の寄与は無かった。前場・後場のどこで動いたか、そのとき海外や為替が動いていたかは見ていなかった
（10/26 は寄り前の先物が +0.6% だったのに −0.4% で寄った。寄りの前の2時間に何が動いたかがどこにも無かった）。

1. 寄与度（contrib）… 日経平均の前日比を銘柄ごとの押し上げ・押し下げに分ける。
   日経平均 = Σ(株価 × 株価換算係数) ÷ 除数 なので、銘柄 i の寄与 = 前日の日経平均 × 係数_i × 前日比_i ÷ Σ(係数 × 前日の株価)。
   係数は日経公式のウエート（月末）÷ その日の分割調整済みの終値に比例する（除数を知らなくても比で足りる）。
   月中の入れ替え・キャップ調整で係数が変わるとずれる（10/26 のアドテストは推計 −306円・株探 −274円）ので、株探が公表する
   寄与度の上位（大引け・前引け・毎時の記事）をその銘柄の値に使い、推計との比を記録して（nk_calib.json）次から推計を直す。
   推計の合計と実際の前日比の差は「誤差」として出す（隠さない）。
2. 5日・20日の寄与（period）… 同じ係数で、日足キャッシュの終値から（日経平均の5日・20日の騰落を、どの銘柄が作ったか）
3. 時間の流れ（path）… 日経平均の5分足で、前日の引け → 寄り（窓）→ 前引け → 後場寄り → 引けと、高値・安値の時刻。
   寄り前の想定（先物）と実際の寄りの差と、寄りまでに先物・米株先物・ドル円がどう動いたか
4. 大きく動いた時間（swings）… 5分足で15〜30分に大きく動いた区間と、同じ時間のドル円・米株先物・アジアの株の動きと、
   同じ時間に出た見出し・開示・指標。関係は確かめていない（並べるだけ。理由の読みは Routine）
5. 海外と規模（world）… アジア（KOSPI・香港・上海・台湾）・欧州と、TOPIX Core30・グロース250・NT倍率
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from statistics import median

from .config import NK_CALIB_PATH
from .indicators import price_at

JST = timezone(timedelta(hours=9))

TOP_N = 8                 # 寄与度の上位・下位に出す銘柄の数
SECTOR_N = 6              # 業種の合計の上位・下位
CALIB_MIN_YEN = 3.0       # 推計の寄与がこれ未満の銘柄は、株探との比を記録しない（小さいと比がぶれる）
CALIB_RANGE = (0.5, 1.6)  # 株探との比がこの外なら取り違えとして記録しない
SWING_MIN = 0.3           # 15〜30分でこれ以上（%）動いた区間を「大きく動いた」とする
SWING_N = 4
EVENT_PAD = (10, 5)       # 区間の前10分〜後5分に出た見出し・開示・指標を並べる
SEMI_THEMES = ("半導体", "半導体製造装置", "半導体材料")
# 同じ時間の動きに並べる値段（5分足のキー → 表示）
SIDE = [("usdjpy", "ドル円", "円", 2, False), ("es", "米株先物", "%", 2, True), ("us10y", "米10年", "bp", 1, False),
        ("kospi", "韓国KOSPI", "%", 2, True), ("hsi", "香港", "%", 2, True), ("ssec", "上海", "%", 2, True)]


def r1(v):
    return None if v is None else round(v, 1)


def r2(v):
    return None if v is None else round(v, 2)


def _macro(bars: dict, key: str) -> list[tuple[str, float]]:
    """日足キャッシュのマクロ系列（{"d": [...], "c": [...]}）を [(日付, 終値)] に。"""
    m = (bars.get("macro") or {}).get(key) or {}
    if isinstance(m, dict):
        return list(zip(m.get("d") or [], m.get("c") or []))
    return [tuple(x) for x in m]


# ==================== 1. 寄与度 ====================
_KAB_HEAD = re.compile(r"日経平均\d{1,2}日(大引け|前引け)")


def kabutan_contrib(articles: list[dict], slot: str) -> dict | None:
    """株探の「日経平均26日大引け＝…」（大引）・「…前引け＝…」（前場）の本文から、寄与度の上位（円）を読む。{at, label, url, rows: {code: 円}}。

    毎時の記事（「15時の日経平均は…」）は引けと数字が違うので使わない。"""
    want = "大引け" if slot == "taibike" else "前引け"
    best = None
    for a in articles or []:
        m = _KAB_HEAD.search(unicodedata.normalize("NFKC", a.get("headline") or ""))
        if m and m.group(1) == want and (best is None or (a.get("timestamp") or "") > (best.get("timestamp") or "")):
            best = a
    if not best:
        return None
    rows = parse_contrib_text(best.get("body") or "")
    if not rows:
        return None
    return {"at": best.get("timestamp"), "label": best.get("headline"), "url": best.get("url"), "rows": rows}


_SEG = re.compile(r"(プラス|マイナス)寄与度")


def parse_contrib_text(body: str) -> dict:
    """「日経平均マイナス寄与度は274円の押し下げでアドテスト<6857>がトップ。以下、東エレク<8035>が175.97円、…。
    プラス寄与度トップは豊田通商<8015>で、日経平均を20.23円押し上げ。次いで三菱商<8058>が19.72円、…」→ {code: 円（符号つき）}。"""
    t = unicodedata.normalize("NFKC", body)
    out: dict[str, float] = {}
    marks = list(_SEG.finditer(t))
    for i, m in enumerate(marks):
        seg = t[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(t)]
        seg = seg.split("業種別")[0]
        sign = 1 if m.group(1) == "プラス" else -1
        head = re.search(r"は?([\d.]+)円の押し(?:上げ|下げ)で[^<。]*<([0-9]{3}[0-9A-Z])>", seg)
        if head:
            out[head.group(2)] = sign * float(head.group(1))
        head2 = re.search(r"<([0-9]{3}[0-9A-Z])>で[、,]?\s*日経平均を([\d.]+)円押し", seg)
        if head2:
            out[head2.group(1)] = sign * float(head2.group(2))
        for c, v in re.findall(r"<([0-9]{3}[0-9A-Z])>が([\d.]+)円", seg):
            out.setdefault(c, sign * float(v))
    return out


def factors(weights: dict | None, bars: dict) -> dict:
    """係数（比例）: ウエート ÷ ウエートの日の分割調整済みの終値。日足キャッシュに無い銘柄は外す。"""
    if not weights or not weights.get("rows"):
        return {}
    dates = bars.get("dates") or []
    wd = weights.get("date")
    if wd not in dates:
        # 月末が休場・取得漏れなら、その日以前でいちばん近い営業日
        older = [d for d in dates if d <= (wd or "")]
        if not older:
            return {}
        wd = older[-1]
    i = dates.index(wd)
    out = {}
    for r in weights["rows"]:
        arr = (bars.get("stocks") or {}).get(r["code"]) or []
        p = arr[i] if i < len(arr) else None
        if p:
            out[r["code"]] = r["weight"] / p
    return out


def load_calib() -> dict:
    try:
        with open(NK_CALIB_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_calib(c: dict) -> None:
    os.makedirs(os.path.dirname(NK_CALIB_PATH), exist_ok=True)
    tmp = NK_CALIB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, NK_CALIB_PATH)


def calibrated(f: dict, calib: dict, wdate: str | None) -> dict:
    """係数に株探との比を掛ける。比を記録した銘柄はその比、ほかは全体の比（記録した比の中央値）。

    全体の比が要るのは、月中の入れ替えでウエートの日の採用銘柄と今の採用銘柄が違い、すべての銘柄の推計が同じ割合でずれるため
    （10/26 は 3銘柄の入れ替えで約 0.6%）。キャップ調整のような銘柄ごとのずれは、その銘柄の比で直す。"""
    if not calib or calib.get("weights_date") != wdate:
        return dict(f)
    rs = calib.get("ratio") or {}
    g = (calib.get("global") or {}).get("r", 1.0)
    return {c: v * (rs.get(c) or {}).get("r", g) for c, v in f.items()}


def contrib(constituents: list[dict], index: dict, f: dict, kab: dict | None, sectors: dict | None = None,
            themes: dict | None = None) -> dict | None:
    """寄与度（純関数）。constituents: [{code,name,sector,price,change}]。index: {close, change}。f: 係数（補正済み）。
    kab: 株探の公表値（kabutan_contrib）。戻り値の est は推計（補正前の比を出すのに使う）。"""
    if not constituents or not index or index.get("change") is None or index.get("close") is None:
        return None
    prev_idx = index["close"] - index["change"]
    have = [c for c in constituents if c.get("code") in f and c.get("price") is not None and c.get("change") is not None]
    if len(have) < 150 or not prev_idx:
        return None
    s = sum(f[c["code"]] * (c["price"] - c["change"]) for c in have)
    if not s:
        return None
    kab_rows = (kab or {}).get("rows") or {}
    rows = []
    for c in have:
        est = prev_idx * f[c["code"]] * c["change"] / s
        val = kab_rows.get(c["code"])
        rows.append({"code": c["code"], "name": c.get("name"), "sector": (sectors or {}).get(c["code"]) or c.get("sector"),
                     "pct": r2(c.get("change_pct")), "yen": round(val if val is not None else est, 2),
                     "est": round(est, 2), "src": "kabutan" if val is not None else "est"})
    # 係数の無い構成銘柄（月中に入った銘柄）でも、株探の公表値があれば足す
    for code, val in kab_rows.items():
        if not any(r["code"] == code for r in rows):
            c = next((x for x in constituents if x.get("code") == code), {})
            rows.append({"code": code, "name": c.get("name"), "sector": c.get("sector"), "pct": r2(c.get("change_pct")),
                         "yen": round(val, 2), "est": None, "src": "kabutan"})
    total = sum(r["yen"] for r in rows)
    rows.sort(key=lambda r: r["yen"])
    neg = [r for r in rows if r["yen"] < 0][:TOP_N]
    pos = [r for r in sorted(rows, key=lambda r: -r["yen"]) if r["yen"] > 0][:TOP_N]
    by_sec: dict[str, dict] = {}
    for r in rows:
        g = by_sec.setdefault(r.get("sector") or "—", {"sector": r.get("sector") or "—", "yen": 0.0, "n": 0, "top": None})
        g["yen"] += r["yen"]
        g["n"] += 1
        if g["top"] is None or abs(r["yen"]) > abs(g["top"]["yen"]):
            g["top"] = {"code": r["code"], "name": r["name"], "yen": r["yen"]}
    secs = sorted(by_sec.values(), key=lambda g: g["yen"])
    for g in secs:
        g["yen"] = round(g["yen"], 1)
    semi = None
    if themes:
        codes = {c for c, x in (themes.get("stocks") or {}).items()
                 if any(t in SEMI_THEMES for t in ((x or {}).get("themes") if isinstance(x, dict) else x) or [])}
        mem = [r for r in rows if r["code"] in codes]
        if mem:
            semi = {"label": "半導体関連（テーマ辞書）", "yen": round(sum(r["yen"] for r in mem), 1), "n": len(mem),
                    "names": [r["name"] for r in sorted(mem, key=lambda r: -abs(r["yen"]))[:5]]}
    big = sorted(rows, key=lambda r: -abs(r["yen"]))
    top3 = big[:3]
    top3_yen = sum(r["yen"] for r in top3)
    actual = index["change"]
    pcts = [c.get("change_pct") for c in constituents if c.get("change_pct") is not None]
    return {
        "prev": round(prev_idx, 2), "actual": round(actual, 2), "sum": round(total, 1), "residual": round(actual - total, 1),
        "n": len(rows), "n_members": len(constituents), "kab_n": len(kab_rows),
        "neg": neg, "pos": pos,
        "sectors_neg": [g for g in secs if g["yen"] < 0][:SECTOR_N],
        "sectors_pos": [g for g in sorted(secs, key=lambda g: -g["yen"]) if g["yen"] > 0][:SECTOR_N],
        "semi": semi,
        "top3": {"names": [r["name"] for r in top3], "yen": round(top3_yen, 1),
                 "pct": r2(top3_yen / prev_idx * 100), "rest": round(actual - top3_yen, 1),
                 "rest_pct": r2((actual - top3_yen) / prev_idx * 100), "rest_n": len(constituents) - len(top3)},
        "median_pct": r2(median(pcts)) if pcts else None,
        "up": sum(1 for x in pcts if x > 0), "down": sum(1 for x in pcts if x < 0),
    }


def update_calib(calib: dict, rows_all: list[dict], wdate: str | None, today: str) -> dict:
    """株探の公表値と推計の比を記録する（次の推計を直す）。ウエートの日付が変われば作り直す。

    推計は補正後の係数で出しているので、新しい比 = 使った比 × 公表値 ÷ 推計。全体の比はその中央値（外れた銘柄に引きずられない）。"""
    c = dict(calib or {})
    if c.get("weights_date") != wdate:
        c = {"weights_date": wdate, "ratio": {}, "global": {"r": 1.0}}
    rs = dict(c.get("ratio") or {})
    g = (c.get("global") or {}).get("r", 1.0)
    for r in rows_all:
        if r.get("src") != "kabutan" or r.get("est") is None or abs(r["est"]) < CALIB_MIN_YEN:
            continue
        used = (rs.get(r["code"]) or {}).get("r", g)
        new = used * r["yen"] / r["est"]
        if CALIB_RANGE[0] <= new <= CALIB_RANGE[1]:
            rs[r["code"]] = {"r": round(new, 4), "asof": today}
    c["ratio"] = rs
    vals = [x["r"] for x in rs.values()]
    if len(vals) >= 5:
        c["global"] = {"r": round(median(vals), 4), "n": len(vals), "asof": today}
    return c


def period(bars: dict, f: dict, days: int, today: str, live: dict | None = None, names: dict | None = None,
           sectors: dict | None = None) -> dict | None:
    """days 営業日の寄与（日足の終値で）。今日の終値が日足キャッシュに無ければ、live（{code: 今の値段}）と日経平均の今の値で終わりにする。"""
    dates = bars.get("dates") or []
    nk = dict(_macro(bars, "nikkei"))
    if not dates or not f:
        return None
    if today in dates:
        end_i, end_px = dates.index(today), None
    else:
        end_i, end_px = len(dates), live or {}
    start_i = end_i - days
    if start_i < 0:
        return None
    d0 = dates[start_i]
    idx0 = nk.get(d0)
    if not idx0:
        return None
    st = bars.get("stocks") or {}
    rows, s = [], 0.0
    pairs = []
    for code, fc in f.items():
        arr = st.get(code) or []
        p0 = arr[start_i] if start_i < len(arr) else None
        p1 = (end_px or {}).get(code) if end_px is not None else (arr[end_i] if end_i < len(arr) else None)
        if not p0 or not p1:
            continue
        pairs.append((code, fc, p0, p1))
        s += fc * p0
    if len(pairs) < 150 or not s:
        return None
    for code, fc, p0, p1 in pairs:
        rows.append({"code": code, "name": (names or {}).get(code), "sector": (sectors or {}).get(code),
                     "yen": round(idx0 * fc * (p1 - p0) / s, 1), "pct": r1((p1 / p0 - 1) * 100)})
    rows.sort(key=lambda r: r["yen"])
    idx1 = nk.get(today) if today in dates else (live or {}).get("_index")
    by_sec: dict[str, float] = {}
    for r in rows:
        by_sec[r.get("sector") or "—"] = by_sec.get(r.get("sector") or "—", 0.0) + r["yen"]
    secs = sorted(({"sector": k, "yen": round(v, 1)} for k, v in by_sec.items()), key=lambda g: g["yen"])
    return {"days": days, "from": d0, "to": today, "index_from": idx0, "actual": r1(idx1 - idx0) if idx1 else None,
            "sum": round(sum(r["yen"] for r in rows), 1),
            "neg": rows[:5], "pos": sorted(rows, key=lambda r: -r["yen"])[:5],
            "sectors_neg": secs[:4], "sectors_pos": sorted(secs, key=lambda g: -g["yen"])[:4]}


# ==================== 2. 時間の流れ ====================
def _today(bars: list[tuple], day: str) -> list[tuple]:
    return [b for b in bars or [] if datetime.fromtimestamp(b[0], JST).date().isoformat() == day]


def _hm(t: int) -> str:
    return datetime.fromtimestamp(t, JST).strftime("%H:%M")


def _between(bars, h0: str, h1: str) -> list[tuple]:
    return [b for b in bars if h0 <= _hm(b[0]) < h1]


def session_path(nk5: list[tuple], day: str, prev_close: float | None, close: float | None, slot: str) -> dict | None:
    """日経平均の5分足から、窓・前場・昼休み・後場の動きと、高値・安値の時刻（純関数）。"""
    bars = _today(nk5, day)
    am = _between(bars, "09:00", "11:31")
    pm = _between(bars, "12:30", "15:31")
    if not am or not prev_close:
        return None
    open_ = am[0][1]
    am_close = am[-1][4]
    legs = [{"key": "gap", "label": "寄り（窓）", "from": prev_close, "to": open_},
            {"key": "am", "label": "前場（寄り→前引け）", "from": open_, "to": am_close}]
    if pm and slot == "taibike":
        pm_open = pm[0][1]
        last = close if close is not None else pm[-1][4]
        legs += [{"key": "lunch", "label": "昼休み（前引け→後場寄り）", "from": am_close, "to": pm_open},
                 {"key": "pm", "label": "後場（後場寄り→引け）", "from": pm_open, "to": last}]
    for g in legs:
        g["yen"] = round(g["to"] - g["from"], 1)
        g["pct"] = r2((g["to"] / g["from"] - 1) * 100) if g["from"] else None
    sess = am + (pm if slot == "taibike" else [])
    hi = max(sess, key=lambda b: b[2])
    lo = min(sess, key=lambda b: b[3])
    # 画面の小さな折れ線（5分ごとの終値。前日の終値を基準に % で）
    line = [{"t": _hm(b[0]), "v": r2((b[4] / prev_close - 1) * 100)} for b in sess]
    return {"open": open_, "am_close": am_close, "legs": legs,
            "high": {"v": hi[2], "t": _hm(hi[0]), "pct": r2((hi[2] / prev_close - 1) * 100)},
            "low": {"v": lo[3], "t": _hm(lo[0]), "pct": r2((lo[3] / prev_close - 1) * 100)},
            "line": line}


def premarket(series: dict, day: str, implied: dict | None, open_: float | None, prev_close: float | None) -> dict | None:
    """寄り前の想定（先物の値・時刻）と実際の寄りの差、その間（想定の時刻 → 8:59）の先物・米株先物・ドル円の動き。"""
    if not implied or open_ is None or not prev_close:
        return None
    out = {"implied_pct": implied.get("gap_pct"), "actual_pct": r2((open_ / prev_close - 1) * 100), "method": implied.get("method_label")}
    if out["implied_pct"] is not None:
        out["miss"] = r2(out["actual_pct"] - out["implied_pct"])
    t_from = None
    asof = implied.get("asof") or ""
    m = re.search(r"(\d{1,2}):(\d{2})", asof)
    if m:
        t_from = int(datetime.fromisoformat(f"{day}T{int(m.group(1)):02d}:{m.group(2)}").replace(tzinfo=JST).timestamp())
    t_to = int(datetime.fromisoformat(f"{day}T09:00").replace(tzinfo=JST).timestamp())
    if t_from and t_from < t_to:
        out["from"] = datetime.fromtimestamp(t_from, JST).strftime("%H:%M")
        moves = {}
        for key, label, unit, dg, pct in [("nk", "日経平均先物", "%", 2, True)] + SIDE[:3]:
            bars = series.get(key) or []
            starts = [b[0] for b in bars]
            p0, p1 = price_at(bars, starts, t_from), price_at(bars, starts, t_to)
            if p0 and p1:
                v = (p1 / p0 - 1) * 100 if pct else (p1 - p0) * (100 if key == "us10y" else 1)
                moves[key] = {"label": label, "unit": unit, "v": round(v, dg)}
        out["moves"] = moves
    return out


# ==================== 3. 大きく動いた時間 ====================
def swings(nk5: list[tuple], day: str, slot: str) -> list[dict]:
    """15分・30分の区間で、日経平均が SWING_MIN% 以上動いたところ（重ならないように大きい順に SWING_N まで）。前場・後場をまたがない。"""
    bars = _today(nk5, day)
    out = []
    for h0, h1 in (("09:00", "11:31"), ("12:30", "15:31")):
        if h0 == "12:30" and slot != "taibike":
            continue
        seg = _between(bars, h0, h1)
        for w in (3, 6):
            for i in range(len(seg)):
                j = i + w - 1
                if j >= len(seg):
                    break
                p0 = seg[i][1]
                p1 = seg[j][4]
                pct = (p1 / p0 - 1) * 100 if p0 else 0
                if abs(pct) >= SWING_MIN:
                    out.append({"t0": seg[i][0], "t1": seg[j][0] + 300, "from": p0, "to": p1, "pct": pct, "w": w})
    out.sort(key=lambda x: -abs(x["pct"]))
    picked = []
    for s in out:
        if any(not (s["t1"] <= p["t0"] or s["t0"] >= p["t1"]) for p in picked):
            continue
        picked.append(s)
        if len(picked) >= SWING_N:
            break
    picked.sort(key=lambda s: s["t0"])
    return [{"from_t": _hm(s["t0"]), "to_t": _hm(s["t1"]), "t0": s["t0"], "t1": s["t1"], "from": s["from"], "to": s["to"],
             "yen": round(s["to"] - s["from"], 1), "pct": r2(s["pct"])} for s in picked]


def side_moves(series: dict, t0: int, t1: int) -> list[dict]:
    out = []
    for key, label, unit, dg, pct in SIDE:
        bars = series.get(key) or []
        if not bars:
            continue
        starts = [b[0] for b in bars]
        p0, p1 = price_at(bars, starts, t0), price_at(bars, starts, t1)
        if p0 is None or p1 is None:
            continue
        v = (p1 / p0 - 1) * 100 if pct else (p1 - p0) * (100 if key == "us10y" else 1)
        out.append({"key": key, "label": label, "unit": unit, "v": round(v, dg)})
    return out


def _minute(s: str | None, day: str) -> int | None:
    """「2026-10-26T14:31+09:00」「14:31」→ epoch 秒（その日の JST）。日付が違えば None。"""
    if not s:
        return None
    s = str(s)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", s)
    if m:
        if m.group(1) != day:
            return None
        hm = f"{m.group(2)}:{m.group(3)}"
    else:
        m = re.match(r"^(\d{1,2}):(\d{2})$", s)
        if not m:
            return None
        hm = f"{int(m.group(1)):02d}:{m.group(2)}"
    return int(datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=JST).timestamp())


def events_for(day: str, press: dict, kabutan: dict, disclosures: list[dict], ind_rows: list[dict],
               members: set | None = None) -> list[dict]:
    """その日の時刻つきの出来事（見出し・短信・開示・指標）を {t, kind, text, src} の列に（純関数）。
    開示は日経225の採用銘柄と決算・修正だけ（全部並べると多すぎる）。"""
    ev = []
    for h in (press or {}).get("headlines") or []:
        t = _minute(h.get("published"), day)
        if t:
            ev.append({"t": t, "kind": "報道", "text": f"{h.get('source')}: {h.get('title')}", "url": h.get("url")})
    for h in (press or {}).get("wire") or []:
        t = _minute(h.get("published"), day)
        if t and h.get("kind") in ("result", "remarks"):
            ev.append({"t": t, "kind": "短信", "text": h.get("title"), "url": h.get("url")})
    for h in (kabutan or {}).get("headlines") or []:
        t = _minute(h.get("published"), day)
        if t:
            ev.append({"t": t, "kind": "株探", "text": h.get("title"), "url": h.get("url")})
    for d in disclosures or []:
        t = _minute(d.get("time"), day)
        if t and (not members or d.get("code") in members):
            ev.append({"t": t, "kind": "開示", "text": f"{d.get('name')}({d.get('code')}) {d.get('category') or d.get('title')}",
                       "url": d.get("pdf"), "code": d.get("code")})
    for r in ind_rows or []:
        t = _minute(r.get("at"), day)
        if t:
            ev.append({"t": t, "kind": "指標", "text": f"{r['country']} {r['name']} {r.get('actual') or ''}"
                       + (f"（予想 {r['forecast']}）" if r.get("forecast") else ""), "url": (r.get("src") or {}).get("url")})
    ev.sort(key=lambda e: e["t"])
    return ev


# 相場そのものを伝える定時の記事（「〔東京株式〕…」「円相場、…＝…現在」）。値動きの結果なので、材料の候補の後ろに回す
_ROUTINE = re.compile(r"〔東京株式|〔東京外為|〔NY|円相場[、,]|日経平均.{0,12}(?:寄り付|前引け|大引け|円高|円安|続落|続伸|反落|反発)|"
                      r"東証(?:前引け|大引け|寄り付き|後場寄り)|株価指数先物|ストップ高/ストップ安|ストップ高／ストップ安|騰落ランキング|"
                      r"の日経平均は|ETF売買|新興株\d|商品\d|債券\d|外為\d")


def attach_events(sw: list[dict], events: list[dict], limit: int = 5) -> None:
    for s in sw:
        lo, hi = s["t0"] - EVENT_PAD[0] * 60, s["t1"] + EVENT_PAD[1] * 60
        hits = [e for e in events if lo <= e["t"] <= hi]
        # 指標・開示を先に（時刻が確かな一次情報）、相場そのものを伝える定時の記事は後ろに
        hits.sort(key=lambda e: (5 if _ROUTINE.search(unicodedata.normalize("NFKC", e["text"])) else
                                 {"指標": 0, "開示": 1, "短信": 2, "報道": 3, "株探": 4}.get(e["kind"], 5), e["t"]))
        s["events"] = [{"time": _hm(e["t"]), "kind": e["kind"], "text": e["text"], "url": e.get("url")} for e in hits[:limit]]


# ==================== 4. 海外と規模 ====================
def world(quotes: dict, specs: list[dict], day: str) -> list[dict]:
    """アジア・欧州・規模別の指数。今日の値でないもの（休場・古い気配）は stale を付ける。"""
    out = []
    for s in specs:
        q = quotes.get(s["key"])
        if not q:
            continue
        asof = (q.get("last_time") or "")[:10] or q.get("asof")
        out.append({"key": s["key"], "label": s["label"], "region": s.get("region"), "last": q.get("last"),
                    "pct": r2(q.get("change_pct")), "asof": asof, "stale": bool(asof and asof != day and s.get("region") != "europe")})
    return out


def nt_ratio(indices: dict, bars: dict) -> dict | None:
    nk, tp = (indices or {}).get("nikkei") or {}, (indices or {}).get("topix") or {}
    if not nk.get("close") or not tp.get("close"):
        return None
    now = nk["close"] / tp["close"]
    n_, t_ = dict(_macro(bars, "nikkei")), dict(_macro(bars, "topix"))
    days = [d for d in (bars.get("dates") or []) if d in n_ and d in t_]
    prev20 = None
    if len(days) > 20:
        d = days[-21]
        prev20 = n_[d] / t_[d]
    return {"now": round(now, 2), "d20": round(now - prev20, 2) if prev20 else None}


# ==================== まとめ ====================
def headline(c: dict | None, path: dict | None, idx: dict | None) -> str | None:
    """機械の一文: 日経の前日比のうち、上位3銘柄でいくら、残りでいくら（225の中央値）。前場・後場のどちらで動いたか。"""
    if not c:
        return None
    t = c["top3"]
    names = "・".join(n or "—" for n in t["names"])
    s = (f"日経平均 {c['actual']:+,.0f}円のうち、{names}の3銘柄で {t['yen']:+,.0f}円、残り{t['rest_n']}銘柄で {t['rest']:+,.0f}円"
         + (f"（225の中央値 {c['median_pct']:+.1f}%）" if c.get("median_pct") is not None else ""))
    if path:
        legs = {g["key"]: g for g in path["legs"]}
        big = max(path["legs"], key=lambda g: abs(g["yen"]))
        s += f"。いちばん動いたのは{big['label'].split('（')[0]}（{big['yen']:+,.0f}円）"
        if "gap" in legs and big["key"] != "gap":
            s += f"、窓は{legs['gap']['yen']:+,.0f}円"
    return s


def build(slot: str, day: str, payload: dict, weights: dict | None, bars: dict, series: dict, calib: dict,
          themes: dict | None, implied: dict | None, world_q: dict, world_specs: list[dict],
          ind_rows: list[dict] | None = None) -> tuple[dict | None, dict]:
    """値動きの分解の全体（純関数に近い。取得は呼び出し側）。戻り値は (画面の分, 更新した補正)。"""
    idx = (payload.get("indices") or {}).get("nikkei") or {}
    if idx.get("stale"):
        return None, calib
    cons = payload.get("constituents") or []
    secs = {c["code"]: c.get("sector") for c in cons}
    names = {c["code"]: c.get("name") for c in cons}
    f_raw = factors(weights, bars)
    wdate = (weights or {}).get("date")
    f = calibrated(f_raw, calib, wdate)
    kab = kabutan_contrib((payload.get("kabutan") or {}).get("articles"), slot)
    c = contrib(cons, idx, f, kab, secs, themes) if f else None
    if c is None and kab:
        # ウエートが取れなかった日は、株探の公表値（上位）だけを出す
        rows = [{"code": k, "name": names.get(k), "sector": secs.get(k), "yen": v, "src": "kabutan", "est": None}
                for k, v in kab["rows"].items()]
        rows.sort(key=lambda r: r["yen"])
        c = {"actual": idx.get("change"), "neg": [r for r in rows if r["yen"] < 0], "pos": sorted([r for r in rows if r["yen"] > 0], key=lambda r: -r["yen"]),
             "sectors_neg": [], "sectors_pos": [], "only_kabutan": True, "kab_n": len(rows), "n": len(rows),
             "top3": None, "residual": None}
    new_calib = calib
    if c and not c.get("only_kabutan"):
        new_calib = update_calib(calib, c["neg"] + c["pos"], wdate, day)
    out = {"asof": day, "slot": slot, "weights_date": wdate, "kabutan": {"at": (kab or {}).get("at"), "label": (kab or {}).get("label"),
                                                                     "url": (kab or {}).get("url")} if kab else None,
           "contrib": c}
    if f:
        live = {x["code"]: x["price"] for x in cons if x.get("price")}
        live["_index"] = idx.get("close")
        out["period"] = [p for p in (period(bars, f, 5, day, live, names, secs), period(bars, f, 20, day, live, names, secs)) if p]
    nk5 = series.get("nikkei") or []
    prev_close = (idx.get("close") - idx.get("change")) if idx.get("close") is not None and idx.get("change") is not None else None
    path = session_path(nk5, day, prev_close, idx.get("close"), slot)
    if path:
        pm = premarket(series, day, implied, path["open"], prev_close)
        if pm:
            path["premarket"] = pm
    out["path"] = path
    sw = swings(nk5, day, slot)
    members = set(secs)
    disc = []
    for key in ("kessan_intraday", "kessan_after"):
        disc += ((payload.get("tables") or {}).get(key) or {}).get("rows") or []
    events = events_for(day, payload.get("press"), payload.get("kabutan"), disc, ind_rows, members)
    for s in sw:
        s["side"] = side_moves(series, s["t0"], s["t1"])
    attach_events(sw, events)
    for s in sw:
        s.pop("t0", None)
        s.pop("t1", None)
    out["swings"] = sw
    out["world"] = world(world_q, world_specs, day)
    out["nt"] = nt_ratio(payload.get("indices"), bars)
    tp = (payload.get("indices") or {}).get("topix") or {}
    if c and c.get("top3") and tp.get("change_pct") is not None and idx.get("change_pct") is not None:
        out["nt_gap"] = {"gap_pt": r2(idx["change_pct"] - tp["change_pct"]), "top3_pt": c["top3"]["pct"]}
    out["headline"] = headline(c if c and not c.get("only_kabutan") else None, path, idx)
    return out, new_calib


def lines_for_brief(w: dict, ref=None) -> list[str]:
    """ai_brief の行（数字は付け直さない）。ref(title, url) は参照番号（「[K3]」）を返す関数。"""
    ref = ref or (lambda title, url: "")
    out = []
    if w.get("headline"):
        kb = w.get("kabutan") or {}
        out.append(w["headline"] + (ref(kb.get("label"), kb.get("url")) if kb.get("url") else ""))
    c = w.get("contrib") or {}
    src = "株探の公表値" if c.get("only_kabutan") else f"日経公式のウエート（{w.get('weights_date')}）からの推計。株探の公表値がある{c.get('kab_n', 0)}銘柄はその値"
    if c:
        out.append(f"寄与度（{src}）: 押し下げ " + "、".join(f"{r['name']} {r['yen']:+.0f}円" for r in c.get("neg") or [])
                   + "／押し上げ " + "、".join(f"{r['name']} {r['yen']:+.0f}円" for r in c.get("pos") or []))
        if c.get("residual") is not None:
            out.append(f"  合計 {c['sum']:+.0f}円（実際 {c['actual']:+.0f}円、誤差 {c['residual']:+.0f}円）")
        if c.get("sectors_neg") or c.get("sectors_pos"):
            out.append("  業種の合計: " + "、".join(f"{g['sector']} {g['yen']:+.0f}円" for g in (c.get("sectors_neg") or [])[:4])
                       + "／" + "、".join(f"{g['sector']} {g['yen']:+.0f}円" for g in (c.get("sectors_pos") or [])[:4]))
        if c.get("semi"):
            out.append(f"  {c['semi']['label']}: {c['semi']['yen']:+.0f}円（{c['semi']['n']}銘柄）")
    if w.get("nt_gap"):
        g = w["nt_gap"]
        out.append(f"日経とTOPIXの騰落の差 {g['gap_pt']:+.2f}pt のうち、寄与の大きい3銘柄の分 {g['top3_pt']:+.2f}pt")
    if w.get("nt"):
        out.append(f"NT倍率 {w['nt']['now']}" + (f"（20日で {w['nt']['d20']:+.2f}）" if w["nt"].get("d20") is not None else ""))
    for p in w.get("period") or []:
        out.append(f"{p['days']}日の寄与（{p['from']}〜）: 日経 {p['actual']:+.0f}円 ／ 押し下げ "
                   + "、".join(f"{r['name']} {r['yen']:+.0f}" for r in p["neg"][:4]) + "／押し上げ "
                   + "、".join(f"{r['name']} {r['yen']:+.0f}" for r in p["pos"][:4])
                   + "｜業種 " + "、".join(f"{g['sector']} {g['yen']:+.0f}" for g in (p.get("sectors_neg") or [])[:2] + (p.get("sectors_pos") or [])[:2]))
    pth = w.get("path")
    if pth:
        out.append("時間の流れ: " + "、".join(f"{g['label']} {g['yen']:+,.0f}円" for g in pth["legs"])
                   + f"／高値 {pth['high']['v']:,.0f}（{pth['high']['t']}）・安値 {pth['low']['v']:,.0f}（{pth['low']['t']}）")
        pm = pth.get("premarket")
        if pm:
            s = f"寄り前の想定 {pm['implied_pct']:+.2f}%（{pm.get('method') or ''}）→ 実際の寄り {pm['actual_pct']:+.2f}%" if pm.get("implied_pct") is not None else ""
            if pm.get("moves"):
                s += f"｜{pm.get('from')}→9:00 に " + "、".join(f"{m['label']} {m['v']:+}{m['unit']}" for m in pm["moves"].values())
            if s:
                out.append(s)
    for s in w.get("swings") or []:
        line = f"大きく動いた時間 {s['from_t']}〜{s['to_t']} {s['yen']:+,.0f}円（{s['pct']:+.2f}%）"
        if s.get("side"):
            line += "｜同じ時間: " + "、".join(f"{m['label']} {m['v']:+}{m['unit']}" for m in s["side"])
        evs = [e for e in s.get("events") or [] if not _ROUTINE.search(unicodedata.normalize("NFKC", e["text"]))]
        if evs:
            # 相場そのものを伝える定時の記事は要約に載せない（値動きの結果で、理由の候補ではない。本文は「株探」「報道」の節にある）
            line += "｜同じ時間の見出し（関係は未確認）: " + "／".join(f"{e['time']} {e['kind']} {e['text'][:56]}" + ref(e["text"], e.get("url"))
                                                     for e in evs[:2])
        out.append(line)
    wl = w.get("world") or []
    if wl:
        out.append("海外・規模: " + "、".join(f"{x['label']} {x['pct']:+.2f}%" + ("（古い値）" if x.get("stale") else "")
                                         for x in wl if x.get("pct") is not None))
    return out
