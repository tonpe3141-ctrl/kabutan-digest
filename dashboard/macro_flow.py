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


# ---------- 市場が見込む政策金利の目安・実質金利（DESIGN.md 38章） ----------
HIKE = 0.25                 # 1回の利上げ・利下げの幅の目安（%）


def _ser(bm: dict, key: str) -> list[tuple[str, float]]:
    s = (bm or {}).get(key) or {}
    return [(d, float(c)) for d, c in zip(s.get("d") or [], s.get("c") or []) if isinstance(c, (int, float))]


def _at(xs: list[tuple[str, float]], day: str) -> float | None:
    k = bisect.bisect_right([d for d, _ in xs], day) - 1
    return xs[k][1] if k >= 0 else None


def policy_view(bars_macro: dict | None, flows: dict | None) -> dict | None:
    """短期金利（6か月・2年）と政策金利の実勢（日銀の無担保コール・NY 連銀の実効 FF 金利）の差 = 市場が見込む先の政策金利の変化の目安。
    国債の金利には期間のプレミアムなども入るので、FF 先物や OIS のような「織り込み確率」ではない。20営業日前の差と並べる。"""
    bm, fl = bars_macro or {}, flows or {}
    out = []
    for key, label, base_key, base_label, legs in (
            ("jp", "日本", "call", "無担保コール（日銀）", (("jp6m", "6か月"), ("jp2y", "2年"))),
            ("us", "米国", "effr", "実効FF金利（NY連銀）", (("us6m", "6か月"), ("us2y", "2年")))):
        base = [(r["date"], float(r["rate"])) for r in fl.get(base_key) or [] if isinstance(r.get("rate"), (int, float))]
        if not base:
            continue
        now_d, now_b = base[-1]
        rows = []
        for k, lab in legs:
            xs = _ser(bm, k)
            if len(xs) < 22:
                continue
            last_d, last = xs[-1]
            b_now = _at(base, last_d) or now_b
            spread = last - b_now
            d20_day, v20 = xs[-21]
            b20 = _at(base, d20_day)
            sp20 = (v20 - b20) if b20 is not None else None
            rows.append({"leg": lab, "rate": round(last, 3), "spread": round(spread, 2), "hikes": round(spread / HIKE, 1),
                         "d20": None if sp20 is None else int(round((spread - sp20) * 100)), "asof": last_d})
        if not rows:
            continue
        tgt = fl.get("effr")[-1] if key == "us" and fl.get("effr") else {}
        out.append({"key": key, "label": label, "base": round(now_b, 3), "base_label": base_label, "base_date": now_d,
                    "target": [tgt.get("lo"), tgt.get("hi")] if tgt.get("lo") is not None else None, "rows": rows,
                    "base_since": base[0][0]})
    if not out:
        return None

    def word(r):
        h = r["hikes"]
        return "ほぼ据え置き" if abs(h) < 0.5 else f"約{abs(h):.0f}回分の{'利上げ' if h > 0 else '利下げ'}"
    bits = []
    for c in out:
        r2 = next((r for r in c["rows"] if r["leg"] == "2年"), c["rows"][-1])
        r6 = next((r for r in c["rows"] if r["leg"] == "6か月"), None)
        t = f"{c['label']}は2年で{word(r2)}" + (f"・半年で{word(r6).replace('約', '')}" if r6 else "")
        if r2.get("d20") is not None and abs(r2["d20"]) >= 5:
            t += f"（20日で見込みが{'強まった' if r2['d20'] > 0 else '弱まった'}）"
        bits.append(t)
    lines = ["短期の国債の金利から見ると、" + "、".join(bits) + "を見込む形。"]
    return {"rows": out, "text": "".join(lines), "hike": HIKE}


def real_view(bars_macro: dict | None) -> dict | None:
    """米10年の実質金利（TIPS）と期待インフレ（名目−実質。ブレークイーブン）の水準・20日の変化・1年の位置。"""
    nom, real = _ser(bars_macro, "us10y"), _ser(bars_macro, "us10r")
    if len(real) < 260 or len(nom) < 260:
        return None
    rd = dict(real)
    pairs = [(d, n, rd[d]) for d, n in nom if d in rd]
    if len(pairs) < 260:
        return None
    be = [n - r for _, n, r in pairs]
    rr = [r for _, _, r in pairs]

    def pos(xs):
        w = xs[-250:]
        return round((xs[-1] - min(w)) / (max(w) - min(w)) * 100) if max(w) > min(w) else None
    out = {"asof": pairs[-1][0], "real": round(rr[-1], 3), "real_d20": int(round((rr[-1] - rr[-21]) * 100)), "real_pos": pos(rr),
           "be": round(be[-1], 3), "be_d20": int(round((be[-1] - be[-21]) * 100)), "be_pos": pos(be),
           "nom": round(pairs[-1][1], 3), "spark_real": [round(x, 3) for x in rr[-60:]], "spark_be": [round(x, 3) for x in be[-60:]]}
    drv = "実質金利" if abs(out["real_d20"]) >= abs(out["be_d20"]) else "期待インフレ"
    out["text"] = (f"米10年の20日の動きは{drv}が主（実質 {out['real_d20']:+d}bp・期待インフレ {out['be_d20']:+d}bp）。"
                   + (f"実質金利は1年で高いほう（{out['real_pos']}%の位置）。" if (out["real_pos"] or 0) >= 80 else
                      f"実質金利は1年で低いほう（{out['real_pos']}%の位置）。" if (out["real_pos"] or 100) <= 20 else ""))
    return out


# ---------- 規模別・スタイル別（DESIGN.md 38章） ----------
SIZE_KEYS = [("core30", "Core30（超大型）"), ("large70", "Large70（大型）"), ("mid400", "Mid400（中型）"), ("small", "Small（小型）"),
             ("value", "バリュー"), ("growth", "グロース（スタイル）"), ("mothers", "グロース市場"), ("reit", "東証REIT")]


def size_style(bars_macro: dict | None) -> dict | None:
    """TOPIX の規模別・スタイル別とグロース市場・REIT の 1日・5日・20日・60日の騰落と、TOPIX との差（20日・60日）。"""
    tp = _ser(bars_macro, "topix")
    if len(tp) < 70:
        return None
    td = dict(tp)

    def chg(xs, n):
        return round((xs[-1][1] / xs[-1 - n][1] - 1) * 100, 2) if len(xs) > n else None
    rows = []
    for k, label in SIZE_KEYS:
        xs = [x for x in _ser(bars_macro, k) if x[0] in td]
        if len(xs) < 70:
            continue
        base = [(d, td[d]) for d, _ in xs]
        r = {"key": k, "label": label, "asof": xs[-1][0], **{f"r{n}": chg(xs, n) for n in (1, 5, 20, 60)}}
        r["rel20"] = None if r["r20"] is None else round(r["r20"] - chg(base, 20), 2)
        r["rel60"] = None if r["r60"] is None else round(r["r60"] - chg(base, 60), 2)
        r["spark"] = [round(a / b * 100, 3) for (_, a), (_, b) in zip(xs[-60:], base[-60:])]     # TOPIX との比（60日）
        rows.append(r)
    if not rows:
        return None
    by = {r["key"]: r for r in rows}
    lines = []
    if "core30" in by and "small" in by and by["core30"]["rel20"] is not None:
        gap = by["core30"]["r20"] - by["small"]["r20"]
        lines.append(f"20日は超大型（Core30）{by['core30']['r20']:+.1f}%・小型（Small）{by['small']['r20']:+.1f}%で、"
                     + ("大型が強い" if gap >= 1 else "小型が強い" if gap <= -1 else "規模の差は小さい") + "。")
    if "value" in by and "growth" in by and by["value"]["r20"] is not None:
        gap = by["value"]["r20"] - by["growth"]["r20"]
        lines.append(f"バリュー {by['value']['r20']:+.1f}%・グロース {by['growth']['r20']:+.1f}%で、"
                     + ("バリューが優位" if gap >= 1 else "グロースが優位" if gap <= -1 else "スタイルの差は小さい") + "。")
    if "mothers" in by and by["mothers"]["rel20"] is not None:
        lines.append(f"グロース市場はTOPIXより20日で{by['mothers']['rel20']:+.1f}ポイント。")
    return {"rows": rows, "text": "".join(lines), "asof": max(r["asof"] for r in rows)}


# ---------- 空売り比率と売買代金（JPX の空売り集計。DESIGN.md 38章） ----------
SHORT_HIGH = 40.0           # 空売り比率がこれ以上の日（よく言われる「売りの偏り」の目安。結果を見る前に決めた）
SHORT_LOW = 35.0            # これ未満の日


def short_view(flows: dict | None, nk_rows: list | None) -> dict | None:
    sh = [r for r in (flows or {}).get("short") or [] if isinstance(r.get("ratio"), (int, float))]
    if len(sh) < 5:
        return None
    last = sh[-1]
    r5 = statistics.fmean(r["ratio"] for r in sh[-5:])
    tot = [r["total"] for r in sh if r.get("total")]
    t20 = statistics.fmean(tot[-21:-1]) if len(tot) > 20 else None
    rr = [r["ratio"] for r in sh[-250:]]
    rank = round(100 * sum(1 for x in rr if x < last["ratio"]) / len(rr))
    # その後の日経（1年だけ。比べる相手はこの期間のすべての日）
    ev_hi, ev_lo, base = [], [], []
    for r in sh:
        v = _nk_after(nk_rows or [], r["date"], 20)
        if v is None:
            continue
        base.append(v)
        (ev_hi if r["ratio"] >= SHORT_HIGH else ev_lo if r["ratio"] < SHORT_LOW else []).append(v)

    def one(v):
        return {"n": len(v), "r20": round(statistics.fmean(v), 2), "p20": round(100 * sum(1 for x in v if x > 0) / len(v))} if v else None
    out = {"date": last["date"], "ratio": last["ratio"], "reg": last.get("reg_pct"), "noreg": last.get("noreg_pct"), "r5": round(r5, 1),
           "rank": rank, "n_days": len(rr), "total": last.get("total"), "total_avg20": None if t20 is None else round(t20),
           "series": [{"d": r["date"], "ratio": r["ratio"], "total": r.get("total")} for r in sh[-130:]],
           "research": {"high": one(ev_hi), "low": one(ev_lo), "base": one(base), "from": sh[0]["date"], "th": [SHORT_HIGH, SHORT_LOW], "small": SMALL}}
    tv = f"東証全体の売買代金は {last['total'] / 1e6:.2f}兆円" + (f"（20日平均 {t20 / 1e6:.2f}兆円の{last['total'] / t20 * 100:.0f}%）" if t20 else "") if last.get("total") else ""
    out["text"] = (f"{last['date'][5:].replace('-', '/')}の空売り比率は {last['ratio']:.1f}%（5日平均 {r5:.1f}%・直近{len(rr)}日で低いほうから{rank}%）。{tv}。")
    return out


def holidays_view(flows: dict | None) -> dict | None:
    h = (flows or {}).get("holidays") or {}
    return h.get("days") or None


def build(bars_macro: dict | None, flows: dict | None, nk_rows: list | None, today: str) -> dict:
    out = {}
    for k, fn in (("link", lambda: linkage(bars_macro, nk_rows)),
                  ("foreign", lambda: mof_view((flows or {}).get("mof"), nk_rows)),
                  ("meetings", lambda: meetings_view((flows or {}).get("meetings"), today)),
                  ("policy", lambda: policy_view(bars_macro, flows)), ("real", lambda: real_view(bars_macro)),
                  ("size", lambda: size_style(bars_macro)), ("short", lambda: short_view(flows, nk_rows)),
                  ("holidays", lambda: holidays_view(flows))):
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
    # 空売り集計（JPX・日次の PDF）。大引の時点ではその日の分がまだのことが多い（前営業日までが入る）
    try:
        from .sources import jpx
        sh = flows.get("short") or []
        new = jpx.short_days({r["date"] for r in sh}, archives=1 if len(sh) < 5 else 0, limit=40)
        if new:
            flows["short"] = sorted({r["date"]: r for r in sh + new}.values(), key=lambda r: r["date"])[-800:]
            msgs.append(f"空売り集計 +{len(new)}日")
    except Exception as e:                  # noqa: BLE001  収集は止めない
        print(f"    ⚠️ 空売り集計: {e}")
    # 政策金利の実勢（日銀の無担保コール・NY 連銀の EFFR）。確報で速報を置き換える
    for key, fn in (("call", lambda have: official.boj_call(have)), ("effr", lambda have: official.effr(10 if have else 400))):
        try:
            old = flows.get(key) or []
            got = fn({r["date"] for r in old})
            if got:
                merged = {r["date"]: r for r in old}
                merged.update({r["date"]: r for r in got})
                flows[key] = sorted(merged.values(), key=lambda r: r["date"])[-800:]
                msgs.append(f"{'無担保コール' if key == 'call' else 'EFFR'} {flows[key][-1]['date']}")
        except Exception as e:              # noqa: BLE001
            print(f"    ⚠️ {key}: {e}")
    # 東証の休業日（JPX の一覧。7日に1回）
    hol = flows.get("holidays") or {}
    if not hol.get("fetched") or (date.fromisoformat(today) - date.fromisoformat(hol["fetched"])).days >= 7:
        days = official.jpx_holidays()
        if len(days) >= 10:
            flows["holidays"] = {"fetched": today, "days": days}
            msgs.append(f"休業日 {len(days)}日")
    return "・".join(msgs)
