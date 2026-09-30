"""マクロ環境: 金利・為替・商品の流れと、信頼できる報道で多かった話題を、決定的にまとめる（DESIGN.md 20章）。

市況の見立て（commentary.py）は「今日の相場がどう動いたか」を書く。ここは、その下にある外部環境を
1日ではなく 5日・20日・60日の流れで並べ、いまどんな局面にいるかを数字で示す:

  rows     … 米・日の金利（2年・10年・30年）、ドル円、原油・金・銅の水準と 前日／5日／20日／60日 の変化、
             1年のレンジの中の位置、取れている約3年での最高・最低、20日の流れ（公開の閾値を超えたら「局面」）
  spreads  … 米国の長短差（10年−2年）と日米の10年金利差。為替を金利差どおりに説明できるかを見る
  topics   … 報道・公的機関・株探の見出しを、マクロの話題（日銀・FRB・為替・原油・地政学…）ごとに数える。
             何媒体が報じたかを並べる（1社だけの話題と、各社がそろって報じる話題を分ける）
  commentary … 上の数字だけで組み立てる文章（LLM の見立てが無いときの土台。同じ数字からは同じ文章）

約束:
  - 予測はしない。「〜の形」「〜に読まれやすい」は教科書どおりの読み方で、断定しない
  - 温度計（thermo.py）の点数は付け直さない。ここは水準と流れの事実だけ
  - AI 生成の要約（news[]）は数えない。数えるのは config.PRESS_FEEDS の許可リストと株探・トレーダーズ・ウェブ
"""
import re
import unicodedata
from datetime import date

from .config import MACRO_RANGE_DAYS, MACRO_SPARK_DAYS, MACRO_TOPICS, MACRO_VIEW, SECTOR_MACRO_SENS

FAMILY_WORD = {
    "us_rate": ("米金利上昇", "米金利低下"), "jp_rate": ("国内金利上昇", "国内金利低下"),
    "fx": ("円安進行", "円高進行"), "oil": ("原油高", "原油安"), "gold": ("金高", "金安"), "copper": ("銅高", "銅安"),
}
TREND_WORD = {"rate": ("上昇", "低下"), "fx": ("円安", "円高"), "commo": ("上昇", "下落")}
# 業種の感応度（config.SECTOR_MACRO_SENS）のどのドライバーに当たるか
SENS_KEY = {"us10y": "us10y", "jp10y": "jp10y", "usdjpy": "yen", "wti": "oil"}
SENS_MIN = 0.5            # 感応度がこれ以上の業種だけを「追い風／重し」に挙げる
MINOR_ECON = re.compile(r"トルコ|南アフリカ|南ア|メキシコ|ブラジル|スイス|NZ|ニュージーランド|豪|カナダ|ノルウェー|スウェーデン|"
                        r"ロシア|インド|シンガポール|香港|韓国|台湾|MBA")


# ==================== 系列 ====================
def series(bars_macro: dict, key: str, live: dict | None = None) -> list[tuple[str, float]]:
    """日足キャッシュの系列に、収集した時点のクォート（live）を重ねる。同じ日付なら置き換え、新しければ足す。"""
    s = (bars_macro or {}).get(key) or {}
    xs = [(d, c) for d, c in zip(s.get("d") or [], s.get("c") or []) if c is not None]
    q = (live or {}).get(key) or {}
    if q.get("last") is not None and q.get("asof"):
        if xs and xs[-1][0] == q["asof"]:
            xs[-1] = (q["asof"], q["last"])
        elif not xs or q["asof"] > xs[-1][0]:
            xs.append((q["asof"], q["last"]))
    return xs


def _chg(group: str, now: float, before: float | None):
    if before is None:
        return None
    if group == "rate":
        return int(round((now - before) * 100))           # bp
    return round((now / before - 1) * 100, 2) if before else None


def view_row(spec: dict, xs: list[tuple[str, float]]) -> dict | None:
    if len(xs) < 2:
        return None
    cs = [c for _, c in xs]
    last, g = cs[-1], spec["group"]
    back = {n: _chg(g, last, cs[-1 - n] if len(cs) > n else None) for n in (1, 5, 20, 60)}
    rng = cs[-MACRO_RANGE_DAYS:]
    lo, hi = min(rng), max(rng)
    prior = cs[:-1]
    years = round((date.fromisoformat(xs[-1][0]) - date.fromisoformat(xs[0][0])).days / 365.25)
    record = None
    if len(prior) >= MACRO_RANGE_DAYS:             # 1年に満たない系列では「最高」と言わない
        record = "high" if last >= max(prior) else "low" if last <= min(prior) else None
    d20 = back[20]
    norm = round(d20 / spec["trend"], 2) if d20 is not None else None
    trend = None
    if norm is not None and abs(norm) >= 1:
        trend = TREND_WORD[g][0 if norm > 0 else 1]
    return {"key": spec["key"], "label": spec["label"], "group": g, "family": spec["family"],
            "unit": spec["unit"], "digits": spec["digits"], "last": last, "asof": xs[-1][0],
            "d1": back[1], "d5": back[5], "d20": d20, "d60": back[60],
            "lo": lo, "hi": hi, "pos": round((last - lo) / (hi - lo) * 100) if hi > lo else None,
            "record": record, "years": years, "norm": norm, "trend": trend, "threshold": spec["trend"],
            "spark": [round(c, 4) for c in cs[-MACRO_SPARK_DAYS:]]}


def spreads(bars_macro: dict, live: dict | None) -> list[dict]:
    """米国の長短差（10年−2年）と日米の10年金利差。20日の変化は bp。"""
    def pair(a, b):
        xa, xb = series(bars_macro, a, live), series(bars_macro, b, live)
        if len(xa) < 21 or len(xb) < 21:
            return None
        now = xa[-1][1] - xb[-1][1]
        before = xa[-21][1] - xb[-21][1]
        return {"last": round(now, 3), "d20": int(round((now - before) * 100))}
    out = []
    for key, label, a, b in (("us_curve", "米国の長短差（10年−2年）", "us10y", "us2y"),
                             ("jpus10", "日米の10年金利差（米−日）", "us10y", "jp10y")):
        p = pair(a, b)
        if p:
            out.append({"key": key, "label": label, **p})
    return out


# ==================== ニュースの論点 ====================
def _norm_title(t: str) -> str:
    return unicodedata.normalize("NFKC", t or "")


def source_items(press: dict | None, kabutan: dict | None) -> list[dict]:
    """数える見出し。公的機関・報道・海外・短信・株探。見出しが同じものは1本にする。"""
    press, kabutan = press or {}, kabutan or {}
    rows = []
    for x in press.get("official") or []:
        rows.append({"title": x.get("title"), "source": x.get("source"), "url": x.get("url"),
                     "published": x.get("published"), "official": True})
    for x in press.get("headlines") or []:
        rows.append({"title": x.get("title"), "source": x.get("source"), "url": x.get("url"),
                     "published": x.get("published")})
    for x in press.get("overseas") or []:
        rows.append({"title": x.get("title"), "source": "CNBC", "url": x.get("url"), "published": x.get("published")})
    for x in (press.get("articles") or []) + (press.get("macro_articles") or []):
        rows.append({"title": x.get("headline"), "source": x.get("provider") or x.get("category"), "url": x.get("url")})
    for x in press.get("wire") or []:
        rows.append({"title": x.get("title"), "source": "トレーダーズ・ウェブ", "url": x.get("url"),
                     "published": x.get("published")})
    for x in kabutan.get("headlines") or []:
        rows.append({"title": x.get("title"), "source": "株探", "url": x.get("url"), "published": x.get("published")})
    for x in kabutan.get("articles") or []:
        rows.append({"title": x.get("headline"), "source": "株探", "url": x.get("url")})
    seen, out = set(), []
    for r in rows:
        k = re.sub(r"\s", "", _norm_title(r["title"]))[:40]
        if not r["title"] or not r["source"] or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def topics(items: list[dict], limit_items: int = 4) -> list[dict]:
    """マクロの話題ごとに、何媒体・何本が報じたか。媒体の数 → 本数の順。"""
    out = []
    for t in MACRO_TOPICS:
        pat = re.compile(t["pattern"], re.IGNORECASE)
        hit = [x for x in items if pat.search(_norm_title(x["title"]))]
        if not hit:
            continue
        media: dict[str, int] = {}
        for x in hit:
            media[x["source"]] = media.get(x["source"], 0) + 1
        # 公的機関（新しい順に2本まで先頭へ）→ 報道を新しい順 → 時刻の無いもの
        official = sorted([x for x in hit if x.get("official")], key=lambda x: x.get("published") or "", reverse=True)
        dated = sorted([x for x in hit if not x.get("official") and x.get("published")],
                       key=lambda x: x["published"], reverse=True)
        ordered = (official[:2] + dated + [x for x in hit if not x.get("official") and not x.get("published")]
                   + official[2:])
        out.append({"key": t["key"], "label": t["label"], "n": len(hit),
                    "media": [m for m, _ in sorted(media.items(), key=lambda kv: -kv[1])],
                    "official": sum(1 for x in hit if x.get("official")),
                    "items": ordered[:limit_items], "_all": ordered})
    out.sort(key=lambda t: (-len(t["media"]), -t["n"]))
    return out


# ==================== 文章 ====================
def _level(r: dict) -> str:
    if r["group"] == "rate":
        return f"{r['last']:.2f}%"
    if r["group"] == "fx":
        return f"{r['last']:.2f}円"
    return f"{r['last']:,.{min(r['digits'], 2)}f}ドル"


def _move(r: dict, key: str) -> str:
    v = r.get(key)
    if v is None:
        return "—"
    if r["group"] == "rate":
        return f"{v:+d}bp" if v else "0bp"
    v = round(v, 1)
    return f"{v:+.1f}%" if v else "0.0%"


def _quote(r: dict) -> str:
    return f"{r['label']} {_level(r)}（前日比 {_move(r, 'd1')}・20日 {_move(r, 'd20')}）"


def _record(r: dict) -> str | None:
    if r.get("record") == "high":
        return f"{r['label']}は取れている約{r['years']}年で最も高い"
    if r.get("record") == "low":
        return f"{r['label']}は取れている約{r['years']}年で最も低い"
    return None


def _sens_sectors(driver: str, rising: bool) -> tuple[list[str], list[str]]:
    """その向きの動きで追い風になりやすい業種・重しになりやすい業種（公開係数の符号から）。"""
    k = SENS_KEY[driver]
    plus = [s for s, w in SECTOR_MACRO_SENS.items() if w.get(k, 0) >= SENS_MIN]
    minus = [s for s, w in SECTOR_MACRO_SENS.items() if w.get(k, 0) <= -SENS_MIN]
    return (plus, minus) if rising else (minus, plus)


def _sector_line(driver: str, r: dict | None, what: str) -> str | None:
    if not r or not r.get("trend"):
        return None
    good, bad = _sens_sectors(driver, (r["norm"] or 0) > 0)
    parts = []
    if good:
        parts.append(f"{'・'.join(good[:4])}に追い風")
    if bad:
        parts.append(f"{'・'.join(bad[:4])}に重し")
    if not parts:
        return None
    return f"業種の感応度（公開係数）では、{what}は" + "、".join(parts) + "になりやすい"


def _curve_read(d2: int | None, d10: int | None) -> str | None:
    """2年と10年の20日の動き方の差。教科書どおりの読み方にとどめる。"""
    if d2 is None or d10 is None or max(abs(d2), abs(d10)) < 10 or abs(d2 - d10) < 8:
        return None
    if d2 > 0 and d10 > 0:
        return ("2年が10年より大きく上がるベア・フラット化。利上げ観測（または利下げ観測の後退）を織り込む動きに読まれやすい形"
                if d2 > d10 else
                "10年が2年より大きく上がるベア・スティープ化。長い金利に上乗せ（財政・インフレへの警戒）が乗る形")
    if d2 < 0 and d10 < 0:
        return ("2年が大きく下がるブル・スティープ化。利下げ観測の強まりを織り込む形" if d2 < d10 else
                "10年が大きく下がるブル・フラット化。景気の先行きへの慎重さを映しやすい形")
    return "短い金利と長い金利が逆向きに動いている（曲線の形が変わる途中）"


def _rates_section(by: dict, sp: dict) -> list[str]:
    out = []
    us = [by[k] for k in ("us10y", "us2y", "us30y") if k in by]
    if us:
        out.append("米国は " + "、".join(_quote(r) for r in us))
        rec = [_record(r) for r in us if _record(r)]
        if rec:
            out.append("、".join(rec))
    c = sp.get("us_curve")
    if c:
        out.append(f"長短差（10年−2年）は {c['last']:.2f}%（20日 {c['d20']:+d}bp）")
    read = _curve_read((by.get("us2y") or {}).get("d20"), (by.get("us10y") or {}).get("d20"))
    if read:
        out.append(read)
    jp = [by[k] for k in ("jp10y", "jp2y", "jp30y") if k in by]
    if jp:
        out.append("日本は " + "、".join(_quote(r) for r in jp))
        rec = [_record(r) for r in jp if _record(r)]
        if rec:
            out.append("、".join(rec))
    j2 = by.get("jp2y")
    if j2 and j2.get("d20") is not None and abs(j2["d20"]) >= 10:
        out.append("日本の2年金利は日銀の政策金利の見通しを映しやすく、20日で "
                   f"{j2['d20']:+d}bp は利上げ観測が{'強まる' if j2['d20'] > 0 else '後退する'}方向の動き")
    for key, what in (("jp10y", "国内金利の" + ((by.get("jp10y") or {}).get("trend") or "")),
                      ("us10y", "米金利の" + ((by.get("us10y") or {}).get("trend") or ""))):
        line = _sector_line(key, by.get(key), what)
        if line:
            out.append(line)
            break                  # 同じ業種を2回挙げない（国内金利を優先）
    return out


def _fx_section(by: dict, sp: dict, tp: dict) -> list[str]:
    r = by.get("usdjpy")
    if not r:
        return []
    out = [f"ドル円は {_level(r)}（前日比 {_move(r, 'd1')}・5日 {_move(r, 'd5')}・20日 {_move(r, 'd20')}）"
           + (f"。1年のレンジ（{r['lo']:.2f}〜{r['hi']:.2f}円）の {r['pos']}% の位置" if r.get("pos") is not None else "")]
    if _record(r):
        out.append(_record(r))
    d = sp.get("jpus10")
    if d:
        out.append(f"日米の10年金利差は {d['last']:.2f}%（20日 {d['d20']:+d}bp）")
        fx20 = r.get("d20")
        if fx20 is not None and abs(d["d20"]) >= 10 and abs(fx20) >= 0.5:
            same = (d["d20"] > 0) == (fx20 > 0)
            out.append("金利差の向きと為替の向きはそろっている（金利差どおりの動き）" if same else
                       f"金利差が{'開いた' if d['d20'] > 0 else '縮んだ'}のに{'円高' if fx20 < 0 else '円安'}。"
                       "金利差だけでは説明できない動きで、ほかの材料（当局の姿勢・需給など）が効いている可能性がある")
    line = _sector_line("usdjpy", r, r.get("trend") or "")
    if line:
        out.append(line)
    fx = tp.get("fx")
    if fx:
        iv = [x for x in fx["_all"] if "介入" in _norm_title(x["title"])]
        if iv:
            media = sorted({x["source"] for x in iv})
            out.append(f"報道では介入に触れた見出しが {len(iv)}本（{'・'.join(media)}）")
    return out


def _commo_section(by: dict) -> list[str]:
    rows = [by[k] for k in ("wti", "gold", "copper") if k in by]
    if not rows:
        return []
    out = ["、".join(_quote(r) for r in rows)]
    rec = [_record(r) for r in rows if _record(r)]
    if rec:
        out.append("、".join(rec))
    line = _sector_line("wti", by.get("wti"), "原油の" + ((by.get("wti") or {}).get("trend") or ""))
    if line:
        out.append(line)
    return out


def _news_section(tps: list[dict], press: dict | None) -> list[str]:
    out = []
    if tps:
        out.append("信頼できる報道・公的機関・株探の見出しで多かった話題は、" +
                   "、".join(f"{t['label']}（{len(t['media'])}媒体・{t['n']}本）" for t in tps[:4]) +
                   "。本数は話題の多さで、相場の向きではない")
    # 公的機関の発表は、マクロの話題に当たるものだけ（取引所のサービス案内などは挙げない）
    pats = [re.compile(t["pattern"], re.IGNORECASE) for t in MACRO_TOPICS]
    official = [x for x in (press or {}).get("official") or []
                if any(p.search(_norm_title(x.get("title"))) for p in pats)]
    official.sort(key=lambda x: x.get("published") or "", reverse=True)
    if official:
        out.append("公的機関の発表は " + "、".join(f"{x['source']}「{x['title']}」" for x in official[:3]))
    wire = (press or {}).get("wire") or []
    # 日米・中国・欧州の指標を先に（新興国・資源国の指標は後ろへ。一覧は新しい順のまま安定に並べ替える）
    res = sorted((x for x in wire if x.get("kind") == "result"), key=lambda x: bool(MINOR_ECON.search(x["title"])))
    if res:
        out.append("発表された経済指標（トレーダーズ・ウェブの短信）は " +
                   "、".join(re.sub(r"^【指標】|ほか$", "", x["title"]).strip() for x in res[:3]) +
                   (f" など{len(res)}本" if len(res) > 3 else ""))
    rem = [x for x in wire if x.get("kind") == "remarks"]
    if rem:
        out.append("要人発言は " + "、".join(re.sub(r"^【要人発言】", "", x["title"]) for x in rem[:2]))
    return out


def headline(rows: list[dict]) -> str:
    fam: dict[str, dict] = {}
    for r in rows:
        if r.get("norm") is None or abs(r["norm"]) < 1:
            continue
        f = fam.get(r["family"])
        if not f or abs(r["norm"]) > abs(f["norm"]):
            fam[r["family"]] = r
    if not fam:
        return "金利・為替・商品に大きな流れはない（20日）"
    top = sorted(fam.values(), key=lambda r: -abs(r["norm"]))[:3]
    words = [FAMILY_WORD[r["family"]][0 if r["norm"] > 0 else 1] for r in top]
    rec = next((r for r in top if r.get("record")), None) or next((r for r in rows if r.get("record") == "high"), None)
    tail = f"。{_record(rec)}" if rec else ""
    return "・".join(words) + "の流れ（20日）" + tail


def commentary(view: dict, tps_all: list[dict], press: dict | None) -> dict | None:
    by = {r["key"]: r for r in view["rows"]}
    sp = {s["key"]: s for s in view["spreads"]}
    tp = {t["key"]: t for t in tps_all}

    def sec(title, xs):
        body = "".join(s if s.endswith("。") else s + "。" for s in xs if s)
        return {"title": title, "body": body} if body else None

    sections = [s for s in (sec("金利", _rates_section(by, sp)), sec("為替", _fx_section(by, sp, tp)),
                            sec("商品", _commo_section(by)), sec("ニュースの論点", _news_section(tps_all, press))) if s]
    if not sections:
        return None
    return {"headline": view["headline"], "sections": sections, "method": "指標ベースの自動生成"}


# ==================== まとめ ====================
def build(bars_macro: dict, live: dict | None, press: dict | None, kabutan: dict | None) -> dict | None:
    rows = [r for r in (view_row(spec, series(bars_macro, spec["key"], live)) for spec in MACRO_VIEW) if r]
    tps_all = topics(source_items(press, kabutan))
    if not rows and not tps_all:
        return None
    view = {"asof": max((r["asof"] for r in rows), default=None), "rows": rows,
            "spreads": spreads(bars_macro, live), "headline": headline(rows),
            "rules": {"trend": {s["key"]: s["trend"] for s in MACRO_VIEW}, "range_days": MACRO_RANGE_DAYS}}
    view["topics"] = [{k: v for k, v in t.items() if k != "_all"} for t in tps_all[:8]]
    view["commentary"] = commentary(view, tps_all, press)
    return view
