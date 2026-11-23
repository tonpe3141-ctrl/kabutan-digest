"""マクロの流れ: 日経はいま何に連動しているか・海外投資家の売り買い（財務省・週次）・日銀と FOMC の日程（DESIGN.md 37章）。

  連動     … 日経平均の1日の騰落と、前夜の米国（S&P500・SOX・VIX・米10年・原油・ドル円）・同じ日の日本10年の変化の相関と感応度を、
              直近60営業日と1年（250営業日）で並べる。相関は「一緒に動いた度合い」で、原因とは言わない。米国の系列は東京の寄りの前に
              終わった日（前の日付）に合わせる（先読みしない）。
  海外勢   … 財務省「対外及び対内証券売買契約等の状況（週次）」の非居住者の株式（日本株）と中長期債（日本の債券）のネット、
              居住者の中長期債（日本の投資家の外債）のネット。2005年〜。4週の合計が直近3年の中で端（下から／上から10%）の週の後に、
              日経がどうだったかを全週と並べる（公表の木曜の引けから20営業日。前半 〜2015・後半 2016〜）。
  日程     … 日銀の金融政策決定会合（2日目が結果）と FOMC（米国の2日目の午後。日本は翌朝）の次の回と、あと何日か。

方針: 予測しない。注文の条件・並べ方には使わない。数字は材料にあるものだけ（Routine の ai_macro はこれを土台に書く）。
"""
from __future__ import annotations

import bisect
import statistics
from datetime import date, timedelta

LINK_SHORT, LINK_LONG = 60, 250
LINK_FACTORS = [  # (key, 名前, 単位, 前夜の米国か)
    ("spx", "S&P500（前夜）", "pct", True),
    ("sox", "SOX指数（前夜）", "pct", True),
    ("vix", "VIX（前夜）", "pct", True),
    ("usdjpy", "ドル円", "pct", True),
    ("us10y", "米10年金利（前夜）", "bp", True),
    ("wti", "WTI原油（前夜）", "pct", True),
    ("jp10y", "日本10年金利", "bp", False),
]
LINK_CHANGE = 0.2           # 60日と1年の相関の差がこれ以上なら「強まった／弱まった」

MOF_SPLIT = "2016-01-01"
MOF_WIN = 156               # 端を決める窓（直近3年の週）
MOF_EDGE = 10               # 下から／上から10%
MOF_H = 20                  # 公表の木曜の引けから20営業日
SMALL = 20


def _corr(x, y):
    if len(x) < 20:
        return None
    try:
        mx, my = statistics.fmean(x), statistics.fmean(y)
        sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
        sx = sum((a - mx) ** 2 for a in x) ** 0.5
        sy = sum((b - my) ** 2 for b in y) ** 0.5
        return sxy / (sx * sy) if sx and sy else None
    except statistics.StatisticsError:
        return None


def _beta(x, y):
    """y（日経 %）の x 1単位あたりの動き。"""
    if len(x) < 20:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    vx = sum((a - mx) ** 2 for a in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / vx if vx else None


def _chg_series(d: list[str], c: list, unit: str) -> tuple[list[str], list[float]]:
    out_d, out_v = [], []
    for i in range(1, len(c)):
        a, b = c[i - 1], c[i]
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or a <= 0:
            continue
        out_d.append(d[i])
        out_v.append((b - a) * 100 if unit == "bp" else (b / a - 1) * 100)
    return out_d, out_v


def linkage(bars_macro: dict | None, nk_rows: list | None = None) -> dict | None:
    bm = bars_macro or {}
    nk = bm.get("nikkei") or {}
    nd, nc = list(nk.get("d") or []), list(nk.get("c") or [])
    if nk_rows:                                    # 36年の四本値のほうが新しければ足す（大引の今日の分）
        for r in nk_rows[-5:]:
            if nd and r[0] > nd[-1]:
                nd.append(r[0])
                nc.append(r[4])
    if len(nc) < LINK_LONG + 10:
        return None
    ndd, nr = _chg_series(nd, nc, "pct")
    rows = []
    for key, label, unit, lag in LINK_FACTORS:
        s = bm.get(key) or {}
        fd, fv = _chg_series(list(s.get("d") or []), list(s.get("c") or []), unit)
        if len(fv) < LINK_LONG:
            continue
        xs, ys = [], []
        for t, y in zip(ndd, nr):
            k = bisect.bisect_left(fd, t) - 1 if lag else bisect.bisect_left(fd, t)
            if lag:
                if k < 0:
                    continue
            elif k >= len(fd) or fd[k] != t:
                continue
            xs.append(fv[k])
            ys.append(y)
        if len(xs) < LINK_LONG:
            continue
        c60, c250 = _corr(xs[-LINK_SHORT:], ys[-LINK_SHORT:]), _corr(xs[-LINK_LONG:], ys[-LINK_LONG:])
        b60 = _beta(xs[-LINK_SHORT:], ys[-LINK_SHORT:])
        if c60 is None or c250 is None:
            continue
        per = 10 if unit == "bp" else 1          # 金利は 10bp、ほかは 1% あたり
        rows.append({"key": key, "label": label, "unit": unit, "c60": round(c60, 2), "c250": round(c250, 2),
                     "beta60": None if b60 is None else round(b60 * per, 2), "per": "10bp" if unit == "bp" else "1%",
                     "shift": ("強まった" if abs(c60) - abs(c250) >= LINK_CHANGE else "弱まった" if abs(c250) - abs(c60) >= LINK_CHANGE else None)})
    if not rows:
        return None
    rows.sort(key=lambda r: -abs(r["c60"]))
    top = rows[0]
    moved = [r for r in rows if r["shift"]]
    head = f"日経にいちばん連動しているのは{top['label']}（60日の相関 {top['c60']:+.2f}・1年 {top['c250']:+.2f}）"
    lines = [head + "。"]
    if top["beta60"] is not None:
        lines.append(f"直近60日は{top['label'].replace('（前夜）', '')}が{top['per']}動くと日経が平均{top['beta60']:+.2f}%動いた。")
    for r in moved[:2]:
        lines.append(f"{r['label']}との連動は1年の{r['c250']:+.2f}から60日の{r['c60']:+.2f}へ{r['shift']}。")
    return {"rows": rows, "headline": head, "text": "".join(lines), "asof": ndd[-1],
            "rules": {"short": LINK_SHORT, "long": LINK_LONG, "change": LINK_CHANGE}}


# ---------- 海外投資家（財務省・週次） ----------

def _oku(v: float, sign: bool = False) -> str:
    """億円の数 → 「2,241億円」「1.1兆円」。"""
    sg = ("+" if v > 0 else "−" if v < 0 else "") if sign else ("−" if v < 0 else "")
    a = abs(v)
    return f"{sg}{a / 10000:.1f}兆円" if a >= 10000 else f"{sg}{a:,.0f}億円"


def _nk_after(nk_rows: list, day: str, h: int) -> float | None:
    d = [r[0] for r in nk_rows]
    k = bisect.bisect_right(d, day) - 1             # その日（休場なら直前）の引け
    if k < 0 or k + h >= len(nk_rows):
        return None
    return (nk_rows[k + h][4] / nk_rows[k][4] - 1) * 100


def _pub_day(end: str) -> str:
    """週（日〜土）の公表日 = 翌週の木曜。"""
    return (date.fromisoformat(end) + timedelta(days=5)).isoformat()


def mof_view(mof: list[dict] | None, nk_rows: list | None) -> dict | None:
    rows = [r for r in (mof or []) if isinstance(r.get("nr_stock"), (int, float))]
    if len(rows) < MOF_WIN + 10:
        return None
    nr = [r["nr_stock"] for r in rows]
    s4 = [None] * 3 + [sum(nr[i - 3:i + 1]) for i in range(3, len(nr))]
    ev_lo, ev_hi, base = [], [], []
    for i in range(MOF_WIN, len(rows)):
        past = sorted(x for x in s4[i - MOF_WIN + 1:i + 1] if x is not None)
        lo_cut = past[max(0, int(len(past) * MOF_EDGE / 100) - 1)]
        hi_cut = past[min(len(past) - 1, len(past) - int(len(past) * MOF_EDGE / 100))]
        r = _nk_after(nk_rows or [], _pub_day(rows[i]["end"]), MOF_H)
        if r is None:
            continue
        base.append((rows[i]["end"], r))
        if s4[i] <= lo_cut:
            ev_lo.append((rows[i]["end"], r))
        elif s4[i] >= hi_cut:
            ev_hi.append((rows[i]["end"], r))

    def one(xs):
        v = [r for _, r in xs]
        return {"n": len(v), "r20": round(statistics.fmean(v), 2), "p20": round(100 * sum(1 for x in v if x > 0) / len(v))} if v else None

    def split(xs):
        return {"all": one(xs), "first": one([x for x in xs if x[0] < MOF_SPLIT]), "second": one([x for x in xs if x[0] >= MOF_SPLIT])}

    i = len(rows) - 1
    past = sorted(x for x in s4[i - MOF_WIN + 1:i + 1] if x is not None)
    rank = round(100 * sum(1 for x in past if x < s4[i]) / len(past))
    last = rows[-1]
    yr = last["end"][:4]
    ytd = sum(r["nr_stock"] for r in rows if r["end"][:4] == yr)
    s13 = sum(nr[-13:])
    res_bond4 = sum(r.get("res_bond") or 0 for r in rows[-4:])
    nr_bond4 = sum(r.get("nr_bond") or 0 for r in rows[-4:])
    zone = "lo" if rank <= MOF_EDGE else "hi" if rank >= 100 - MOF_EDGE else "mid"
    st = {"lo": split(ev_lo), "hi": split(ev_hi), "base": split(base)}
    word = "売り越し" if last["nr_stock"] < 0 else "買い越し"
    head = f"海外投資家は日本株を{last['end'][5:].replace('-', '/')}までの週に{_oku(abs(last['nr_stock']))}の{word}（4週 {_oku(s4[i], True)}）"
    facts = (f"{head}。4週の合計は直近3年で低いほうから{rank}%の位置、年初から {_oku(ytd, True)}。"
             f"日本の投資家の外債（中長期債）は4週で{_oku(res_bond4, True)}（プラスは外債の買い越し＝円売りの側）、"
             f"海外投資家の日本の中長期債は{_oku(nr_bond4, True)}。")
    b = st["base"]["all"] or {}
    ev = []
    for z, word2 in (("lo", "売り"), ("hi", "買い")):
        a = st[z]["all"]
        if not a:
            continue
        f1, f2 = st[z]["first"] or {}, st[z]["second"] or {}
        ev.append(f"{word2}が偏った週{'（いま）' if zone == z else ''}のあと {a['r20']:+.1f}%（{a['n']}週・前半 {f1.get('r20', 0):+.1f}%・後半 {f2.get('r20', 0):+.1f}%）")
    bf, bs = st["base"]["first"] or {}, st["base"]["second"] or {}
    evidence = (f"4週の合計が直近3年の端（{MOF_EDGE}%）まで偏った週のあと、日経の{MOF_H}営業日: " + "、".join(ev) +
                f"。すべての週は {b.get('r20', 0):+.1f}%（前半 {bf.get('r20', 0):+.1f}%・後半 {bs.get('r20', 0):+.1f}%）。") if ev else ""
    lo = st["lo"]["all"]
    so = ("だから海外勢の売りが偏ったあとも日経は弱くなく、売り越しの数字を追って売る根拠は過去の数字には無い。"
          if lo and b and lo["r20"] >= b.get("r20", 0) else
          "だから海外勢の売りが偏ったあとは日経も弱めだった（前半・後半の向きは検証の表で確かめる）。" if lo else "")
    return {
        "asof": last["end"], "pub": _pub_day(last["end"]), "headline": head, "text": facts + evidence + so,
        "facts": facts, "evidence": evidence, "so": so, "zone": zone, "rank": rank,
        "last": last, "s4": round(s4[i]), "s13": round(s13), "ytd": round(ytd), "res_bond4": round(res_bond4), "nr_bond4": round(nr_bond4),
        "weeks": [{"end": r["end"], "v": r["nr_stock"]} for r in rows[-26:]],
        "research": {**st, "from": rows[MOF_WIN]["end"], "split": MOF_SPLIT, "win": MOF_WIN, "edge": MOF_EDGE, "h": MOF_H, "small": SMALL},
    }


# ---------- 日銀・FOMC の日程 ----------

def meetings_view(meet: dict | None, today: str) -> dict | None:
    m = meet or {}
    out = {}
    for key, label in (("boj", "日銀の金融政策決定会合"), ("fomc", "FOMC")):
        nxt = next((x for x in (m.get(key) or []) if x["end"] >= today), None)
        if not nxt:
            continue
        # FOMC は米国の2日目の午後2時 → 日本の翌朝（夏時間 3:00・冬時間 4:00）
        jp = nxt["end"] if key == "boj" else (date.fromisoformat(nxt["end"]) + timedelta(days=1)).isoformat()
        out[key] = {"label": label, "start": nxt["start"], "end": nxt["end"], "jp_result": jp,
                    "days": (date.fromisoformat(jp) - date.fromisoformat(today)).days, "sep": nxt.get("sep")}
    return out or None


def build(bars_macro: dict | None, flows: dict | None, nk_rows: list | None, today: str) -> dict:
    out = {}
    for k, fn in (("link", lambda: linkage(bars_macro, nk_rows)),
                  ("foreign", lambda: mof_view((flows or {}).get("mof"), nk_rows)),
                  ("meetings", lambda: meetings_view((flows or {}).get("meetings"), today))):
        try:
            v = fn()
        except Exception as e:                      # noqa: BLE001  収集は止めない
            print(f"    ⚠️ マクロの流れ（{k}）: {e}")
            v = None
        if v:
            out[k] = v
    return out


def refresh(flows: dict, today: str) -> str:
    """財務省の週次（新しい週が出ていそうなら）と、日銀・FOMC の日程（7日に1回）を取り直す。例外を投げない。"""
    from .sources import official
    msgs = []
    mof = flows.get("mof") or []
    last = mof[-1]["end"] if mof else "2000-01-01"
    if (date.fromisoformat(today) - date.fromisoformat(last)).days >= 12:      # 木曜に前の週の分（土曜まで）
        new = official.mof_weeks()
        if new and (not mof or new[-1]["end"] > last):
            flows["mof"] = new
            msgs.append(f"財務省の週次 {new[-1]['end']}まで（{len(new)}週）")
    mt = flows.get("meetings") or {}
    if not mt.get("fetched") or (date.fromisoformat(today) - date.fromisoformat(mt["fetched"])).days >= 7:
        got = official.meetings()
        if got.get("boj") or got.get("fomc"):
            cut = str(int(today[:4]) - 1)
            flows["meetings"] = {"fetched": today, **{k: ([x for x in got[k] if x["end"][:4] >= cut] if got.get(k) else mt.get(k) or [])
                                                      for k in ("boj", "fomc")}}
            msgs.append(f"会合の日程（日銀 {len(got.get('boj') or [])}・FOMC {len(got.get('fomc') or [])}）")
    return "・".join(msgs)
