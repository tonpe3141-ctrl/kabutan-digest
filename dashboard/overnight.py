"""夜間の値動き: 米国の引け後〜東京の寄りの前に動いた先物・時間外と、その日本株への響き（DESIGN.md 30章）。

依頼（2026-10-18）: 夜間のディープシークの新モデル発表で、時間外の半導体や日経平均・ナスダックがさらに下げた。
こういう値動きのある情報もキャッチして、株価への影響と投資行動に役立てたい。

寄り前（07:10）の収集で、4つを出す:
  1. 何が動いたか … 米国の先物（NASDAQ100・S&P500・ダウ・日経平均）と、時間外の ETF（SPY・QQQ・SMH）・主な銘柄（AI・半導体・大型テック）。
     引け後の動き（post）は ETF の時間外（通常取引の終値との差）を先に使い、古ければ先物から出す（collect）
  2. 何が出たか … 前営業日の東京の引け（15:30）から今までの見出しのうち、動いた銘柄の社名・話題（AI・半導体・輸出規制・関税…）に当たるもの。
     機械は照合だけ。どの見出しが理由かの読みは Routine（ai_commentary）。社名が出ただけで理由とは限らない（headlines）
  3. 日本株への響き … 銘柄ごとに、過去2年の朝の窓（寄り ÷ 前日の引け）を米国の夜（S&P500 と、SOX と S&P500 の差）で回帰した係数で、
     今朝の窓の目安を出す（米国の引けまでの分と、時間外を足した分）。予測ではなく、過去の連動の強さから出した目安（fit・estimate）
  4. 対処 … 米国の大きな下げ（SOX −3%以下か S&P500 −1.5%以下）の翌朝に、(a) 連動の強い銘柄を寄りで売った場合と持ち続けた場合、
     (b) 押し目買いの指値が約定した注文（swing.simulate）の成績を、ふだんの朝と前半・後半で並べ、その向きに沿った考え方だけを書く（research・playbook）。
     注文の条件・候補・並び順は変えない（買う候補を出すのは swing.py の2つのルールだけ）

研究（2026-10-18、2024-09〜2026-10 の日足 512銘柄で数えた値。30章の表。前半 〜2025-10-02／後半 2025-10-03〜）:
  窓の目安（前半の係数で後半を出した）: 連動の強い178銘柄・米国が大きく動いた夜で、実際の窓との相関 0.62、平均の誤差 1.61%
  （目安なし＝0% なら 2.05%）、向きの一致 75%。
  大きな下げの夜（58回）の翌朝、連動の強い銘柄: 窓 −1.7%、寄りから引け +0.28%（上げ 55%。全部の朝 +0.01%・48%）、
  寄りから5日後 +2.0%（全部 +0.8%）。前半 +0.31%／後半 +0.26%。
  押し目買いの指値が大きな下げの翌朝に約定した注文: 1回平均 +3.34%（39回の朝・345件、最悪 −23.9%。ほかの朝 +1.17%）。
  前半 +1.70%／後半 +4.52%（ほか +1.41%／+0.89%）。大きな上げの翌朝は約定 21%（ほか 31%）。

検証の限界: 米国は終値どうし（時間外・先物の日足は持っていない）。日本の個別株は今の採用銘柄の2年（生存者の偏り・上げ相場）。
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from statistics import correlation, mean

from . import swing
from .config import MACRO_TOPICS, OVERNIGHT_FUTURES, OVERNIGHT_NAMES, OVERNIGHT_TOPICS

JST = timezone(timedelta(hours=9))

# 引け後の動き（%）がこれ以上なら「大きく動いた」。その半分以上で「動いた」
BIG = {"ndq": 1.0, "spx": 0.8, "sox": 2.0, "nk": 1.5, "name": 5.0}
MOVE_FRAC = 0.5
NAME_MOVED = 2.0          # 個別の銘柄の時間外がこれ以上動いたら、見出しの照合で先に並べる
FRESH_H = 16              # 時間外・先物の約定がこれより古ければ使わない（時間）。週末明けの朝は金曜の時間外を使わない
NEW_SESSION_H = 1.5       # 先物の約定が現物の引け（16:00 ET）からこれ以上あと → 次の取引日のセッション（前日比の基準 ≒ 現物の引け）
NK_SANE = 15.0            # 日経平均先物が前日の終値から ±15% を超えていたら別の限月・通貨の取り違えとして使わない
JP_CLOSE = (15, 30)       # 東京の引け（見出しの窓の始まり）

# 研究（日本株への響き・対処の根拠）
SHOCK_SOX, SHOCK_SPX = -3.0, -1.5     # 米国の大きな下げの夜
SURGE_SOX, SURGE_SPX = 3.0, 1.5       # 大きな上げの夜
SENS_R2 = 0.3             # 連動の強い銘柄（前半の回帰の決定係数）
SHOW_R2 = 0.15            # 響きやすい銘柄の一覧に出す下限
EST_R2 = 0.1              # アプリ（保有・注文の行）に渡す下限
SHOW_N = 12
FIT_MIN = 120             # 回帰に使う朝の最小数
GAP_CAP = 25.0            # これより大きい窓は分割・権利落ちの取り違えとして捨てる
HOLD_N = 5                # 寄りから何営業日後の引けまで持ったかを見る
SMALL = 20                # これ未満の件数（朝の数）は「少ない」と書く


def r1(v):
    return None if v is None else round(v, 1)


def r2(v):
    return None if v is None else round(v, 2)


def _t(s) -> datetime | None:
    """CNBC の時刻（"2026-10-15T16:15:00.000-0400"）を時刻つきの datetime に。読めなければ None。"""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(str(s), fmt)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=JST)


def _fresh(t: datetime | None, now: datetime) -> bool:
    return t is not None and now - timedelta(hours=FRESH_H) <= t <= now + timedelta(hours=1)


def _hm(t: datetime | None) -> str | None:
    return t.astimezone(JST).strftime("%m/%d %H:%M") if t else None


# ==================== 1. 何が動いたか ====================
def pick_futures(quotes: dict) -> dict:
    """候補の表記のうち、最初に返ったものを {key: クォート} で返す。"""
    out = {}
    for spec in OVERNIGHT_FUTURES:
        for sym in spec["symbols"]:
            q = quotes.get(sym)
            if q and q.get("last") is not None:
                out[spec["key"]] = {**q, "key": spec["key"], "label": spec["label"], "cash": spec.get("cash")}
                break
    return out


def futures_post(fq: dict, cash_q: dict | None) -> float | None:
    """先物の前日比から、現物の引けのあとの動き（%）を出す。

    CME の株価指数先物は 16:00 ET に清算し、17:00〜18:00 ET に休んで次の取引日が始まる。
      - 約定が現物の引け（16:00 ET）から1.5時間以上あと（次の取引日。週明けの再開も）→ 前日比の基準はその日の清算値 ≒ 現物の引けなので、そのまま
      - 引けのあと〜休止まで → 基準は前の日の清算値なので、現物のその日の騰落を除く
    基準の取り違えがありうる目安。ETF の時間外が新しければそちらを先に使う（collect）。"""
    pct = fq.get("change_pct")
    t = _t(fq.get("last_time"))
    if pct is None or t is None:
        return None
    cash = (cash_q or {}).get("change_pct")
    ct = _t((cash_q or {}).get("last_time"))
    if ct is not None and ct.hour >= 16:      # 指数の最後の計算時刻は 16:00 より遅れることがあるので、引けの 16:00（現地）にそろえる
        ct = ct.replace(hour=16, minute=0, second=0, microsecond=0)
    if cash is None or ct is None or t >= ct + timedelta(hours=NEW_SESSION_H):
        return pct
    if t > ct:
        return ((1 + pct / 100) / (1 + cash / 100) - 1) * 100
    return None


def collect(fut_q: dict, name_q: dict, us: dict, now: datetime, prev_jp: str | None,
            prev_close: float | None) -> dict:
    """先物・時間外のクォートから、引け後の動きと米国の夜の合計を作る（純関数）。

    fut_q: pick_futures の出力。name_q: {シンボル: クォート}（OVERNIGHT_NAMES）。us: 米国の指数（spx/ndq/sox/dji）。
    prev_jp: 前営業日の東京の日付。米国の終値の騰落は、その日の東京の引けより後に終わったもの（米国の日付 ≥ prev_jp）だけ数える
    （米国が休場なら、前の営業日の東京がもう織り込んだ分なので 0 にする）。"""
    futures = []
    for key, fq in fut_q.items():
        t = _t(fq.get("last_time"))
        row = {"key": key, "label": fq["label"], "symbol": fq.get("symbol"), "last": fq.get("last"),
               "chg": r2(fq.get("change_pct")), "time": _hm(t), "fresh": _fresh(t, now)}
        if fq.get("cash"):
            row["post"] = r2(futures_post(fq, us.get(fq["cash"])))
        futures.append(row)

    names, post = [], {}
    for spec in OVERNIGHT_NAMES:
        q = name_q.get(spec["symbol"])
        if not q:
            continue
        ext = q.get("ext") or {}
        t = _t(ext.get("last_time"))
        fresh = bool(ext) and _fresh(t, now)
        row = {"key": spec["key"], "symbol": spec["symbol"], "label": spec["label"], "close": r2(q.get("last")),
               "chg": r2(q.get("change_pct")), "ext": r2(ext.get("pct")) if fresh else None,
               "ext_last": r2(ext.get("last")) if fresh else None, "time": _hm(t) if fresh else None,
               "role": spec.get("role")}
        names.append(row)
        if spec.get("role") and fresh and ext.get("pct") is not None:
            post[spec["role"]] = {"pct": r2(ext["pct"]), "src": f"{spec['label']}（{spec['symbol']}）の時間外", "time": _hm(t)}
    for f in futures:
        cash = (fut_q.get(f["key"]) or {}).get("cash")
        if cash in ("spx", "ndq") and cash not in post and f["fresh"] and f.get("post") is not None:
            post[cash] = {"pct": f["post"], "src": f"{f['label']}（{f['symbol']}）", "time": f["time"]}

    # 日経平均先物（取れて、前日の東京の引けより後の約定で、値が前日の終値の近くにあるときだけ）
    nk = None
    nq = fut_q.get("nk")
    if nq and prev_close and nq.get("last"):
        t = _t(nq.get("last_time"))
        after_close = prev_jp is None or t is None or t >= datetime(
            *map(int, prev_jp.split("-")), *JP_CLOSE, tzinfo=JST)
        gap = (nq["last"] / prev_close - 1) * 100
        if _fresh(t, now) and after_close and abs(gap) <= NK_SANE:
            nk = {"last": nq["last"], "symbol": nq.get("symbol"), "gap_pct": r2(gap), "time": _hm(t),
                  "asof": (nq.get("last_time") or "")[:10] or None}

    # 米国の終値の騰落（東京の前の引けより後に終わった分だけ）
    cash = {}
    for k in ("spx", "ndq", "sox"):
        q = us.get(k) or {}
        counts = q.get("change_pct") is not None and (prev_jp is None or (q.get("asof") or "") >= prev_jp)
        cash[k] = r2(q["change_pct"]) if counts else 0.0
    total = {k: r2((cash.get(k) or 0) + ((post.get(k) or {}).get("pct") or 0)) for k in ("spx", "ndq", "sox")}

    moved = [n for n in names if n.get("ext") is not None and not n.get("role") and abs(n["ext"]) >= NAME_MOVED]
    moved.sort(key=lambda n: -abs(n["ext"]))
    score, parts = 0.0, []
    for k, label in (("ndq", "NASDAQ"), ("sox", "米半導体"), ("spx", "S&P500")):
        p = (post.get(k) or {}).get("pct")
        if p is not None:
            score = max(score, abs(p) / BIG[k])
            parts.append((abs(p) / BIG[k], f"{label} {p:+.1f}%", p))
    if nk:
        score = max(score, abs(nk["gap_pct"]) / BIG["nk"])
        parts.append((abs(nk["gap_pct"]) / BIG["nk"], f"日経平均先物 {nk['gap_pct']:+.1f}%", nk["gap_pct"]))
    for n in moved[:3]:
        score = max(score, abs(n["ext"]) / BIG["name"])
        parts.append((abs(n["ext"]) / BIG["name"], f"{n['label']} {n['ext']:+.1f}%", n["ext"] / 3))
    if not post and not nk:
        level, direction = "none", 0
        headline = "米国の引け後の値段（時間外・先物）が取れていません（週末・休場・取得失敗）"
    else:
        level = "big" if score >= 1 else "move" if score >= MOVE_FRAC else "quiet"
        lead = sorted(parts, key=lambda x: -x[0])
        sgn = sum(x[2] for x in lead[:3])
        direction = -1 if sgn < 0 else 1 if sgn > 0 else 0
        txt = "、".join(x[1] for x in lead[:3])
        if level == "quiet":
            headline = f"米国の引け後は大きな動きなし（{txt}）"
        else:
            headline = f"米国の引け後に{'下げ' if direction < 0 else '上げ'}{'が大きい' if level == 'big' else ''}（{txt}）"
    return {"asof": now.isoformat(timespec="minutes"), "level": level, "dir": direction, "score": r2(score),
            "headline": headline, "futures": futures, "names": names, "post": post, "nk": nk,
            "cash": cash, "total": total, "moved": [n["key"] for n in moved],
            "defs": {"big": {k: v for k, v in BIG.items()}, "move_frac": MOVE_FRAC, "fresh_h": FRESH_H}}


# ==================== 2. 何が出たか ====================
_TOPICS = [(t["key"], t["label"], re.compile(t["pattern"], re.I)) for t in OVERNIGHT_TOPICS + MACRO_TOPICS]
_NAMES = [(n["key"], n["label"], re.compile(n["pattern"])) for n in OVERNIGHT_NAMES if n.get("pattern")]
_LEAD = ("ai", "export", "trade", "futures", "results", "geo", "fed")     # 先に並べる話題
HEADLINES_MAX = 15


def _pub(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=JST)


def match_headlines(groups: list[tuple[str, list[dict]]], since: datetime, now: datetime,
                    moved: list[str]) -> list[dict]:
    """since〜now の見出しのうち、時間外の銘柄の社名か夜間の話題に当たるものを選ぶ。

    並び: 時間外で動いた銘柄の社名 → AI・輸出規制・関税・先物などの話題 → そのほか、の順に、それぞれ新しい順。
    照合は NFKC で半角にそろえてから。社名が出ただけで値動きの理由とは限らない（読みは Routine）。"""
    seen, out = set(), []
    for grp, rows in groups:
        for r in rows or []:
            title = str(r.get("title") or r.get("headline") or "").strip()
            pub = _pub(r.get("published"))
            if not title or pub is None or pub < since or pub > now + timedelta(hours=1):
                continue
            norm = unicodedata.normalize("NFKC", title)
            k = re.sub(r"\s+", "", norm)[:40]
            if k in seen:
                continue
            names = [key for key, _, p in _NAMES if p.search(norm)]
            topics = [key for key, _, p in _TOPICS if p.search(norm)]
            if not names and not topics:
                continue
            seen.add(k)
            rank = 0 if set(names) & set(moved) else 1 if names or set(topics) & set(_LEAD) else 2
            out.append({"title": title, "published": pub.isoformat(timespec="minutes"), "url": r.get("url"),
                        "source": r.get("source") or grp, "names": names,
                        "topics": [lab for key, lab, _ in _TOPICS if key in topics], "_rank": rank, "_t": pub})
    out.sort(key=lambda x: (x["_rank"], -x["_t"].timestamp()))
    for x in out:
        x.pop("_rank")
        x.pop("_t")
    return out[:HEADLINES_MAX]


def since_of(prev_jp: str | None, now: datetime) -> datetime:
    """見出しの窓の始まり: 前営業日の東京の引け（15:30）。分からなければ16時間前。"""
    if prev_jp:
        try:
            y, m, d = map(int, prev_jp.split("-"))
            return datetime(y, m, d, *JP_CLOSE, tzinfo=JST)
        except ValueError:
            pass
    return now - timedelta(hours=16)


# ==================== 3. 日本株への響き ====================
def _last_before(ds: list[str], cs: list[float], day: str):
    k = bisect.bisect_left(ds, day) - 1
    return cs[k] if k >= 0 else None


def us_nights(jp_dates: list[str], spx: dict, sox: dict) -> dict[str, tuple[float, float]]:
    """東京の日付 → その朝の寄りの前に終わった米国の夜（S&P500・SOX の騰落 %）。

    東京の j 日の寄りが知っている米国の終値は、米国の日付が j より前のもの。前の日（j−1）の引けが知っていたのは j−1 より前のもの。
    その間の騰落を数える（米国が休場なら 0）。spx・sox は bars.json の macro 系列（{"d": [...], "c": [...]}）。"""
    out = {}
    sd, sc = (spx or {}).get("d") or [], (spx or {}).get("c") or []
    xd, xc = (sox or {}).get("d") or [], (sox or {}).get("c") or []
    for j in range(1, len(jp_dates)):
        a, b = _last_before(sd, sc, jp_dates[j - 1]), _last_before(sd, sc, jp_dates[j])
        x, y = _last_before(xd, xc, jp_dates[j - 1]), _last_before(xd, xc, jp_dates[j])
        if a and b and x and y:
            out[jp_dates[j]] = ((b / a - 1) * 100, (y / x - 1) * 100)
    return out


def gaps(dates: list[str], rows: list) -> list[tuple[str, float]]:
    """[(日付, 朝の窓 %)]。寄りか前日の引けが無い日、窓が GAP_CAP を超える日は捨てる。"""
    out = []
    for j in range(1, len(dates)):
        x, p = (rows[j] if j < len(rows) else None), (rows[j - 1] if j - 1 < len(rows) else None)
        if not x or not p or x[0] is None or p[3] is None or x[0] <= 0 or p[3] <= 0:
            continue
        g = (x[0] / p[3] - 1) * 100
        if abs(g) <= GAP_CAP:
            out.append((dates[j], g))
    return out


def fit(pts: list[tuple[float, float, float]]) -> dict | None:
    """窓 = a + bm×S&P500 + bs×(SOX − S&P500) の最小二乗。pts は (S&P500, SOX, 窓)。"""
    n = len(pts)
    if n < FIT_MIN:
        return None
    x1 = [p[0] for p in pts]
    x2 = [p[1] - p[0] for p in pts]
    y = [p[2] for p in pts]
    m1, m2, my = mean(x1), mean(x2), mean(y)
    s11 = sum((a - m1) ** 2 for a in x1)
    s22 = sum((b - m2) ** 2 for b in x2)
    s12 = sum((a - m1) * (b - m2) for a, b in zip(x1, x2))
    s1y = sum((a - m1) * (c - my) for a, c in zip(x1, y))
    s2y = sum((b - m2) * (c - my) for b, c in zip(x2, y))
    det = s11 * s22 - s12 * s12
    if det <= 0:
        return None
    bm = (s1y * s22 - s2y * s12) / det
    bs = (s2y * s11 - s1y * s12) / det
    a = my - bm * m1 - bs * m2
    sst = sum((c - my) ** 2 for c in y)
    sse = sum((c - a - bm * p - bs * q) ** 2 for p, q, c in zip(x1, x2, y))
    return {"a": a, "bm": bm, "bs": bs, "r2": 1 - sse / sst if sst else 0.0, "n": n}


def estimate(m: dict, spx: float, sox: float) -> float:
    return m["a"] + m["bm"] * spx + m["bs"] * (sox - spx)


def models(ohlc: dict, nights: dict, end: str | None = None) -> dict[str, dict]:
    """銘柄ごとの係数（end より前の朝だけで）。"""
    dates = ohlc.get("dates") or []
    out = {}
    for code, rows in (ohlc.get("stocks") or {}).items():
        pts = [(*nights[d], g) for d, g in gaps(dates, rows) if d in nights and (end is None or d < end)]
        m = fit(pts)
        if m:
            out[code] = m
    return out


# ==================== 4. 対処の根拠 ====================
def _is_shock(n) -> bool:
    return n is not None and (n[1] <= SHOCK_SOX or n[0] <= SHOCK_SPX)


def _is_surge(n) -> bool:
    return n is not None and (n[1] >= SURGE_SOX or n[0] >= SURGE_SPX)


def _hold_stats(rows: list[tuple]) -> dict:
    """rows: (日付, 窓, 寄り→引け, 寄り→5日後の引け)。"""
    if not rows:
        return {"n": 0}
    return {"n": len(rows), "nights": len({r[0] for r in rows}), "gap": r2(mean(r[1] for r in rows)),
            "oc": r2(mean(r[2] for r in rows)), "oc_up": round(sum(r[2] > 0 for r in rows) / len(rows) * 100),
            "o5": r2(mean(r[3] for r in rows))}


def _order_stats(rows: list[dict]) -> dict:
    filled = [t for t in rows if t["filled"]]
    done = [t for t in filled if t.get("ret") is not None]
    out = {"signals": len(rows), "nights": len({t["d"] for t in rows}),
           "fill": round(len(filled) / len(rows) * 100) if rows else None, "n": len(done)}
    if done:
        out.update({"avg": r2(mean(t["ret"] for t in done)), "win": round(sum(t["ret"] > 0 for t in done) / len(done) * 100),
                    "stops": round(sum(t["why"] == "stop" for t in done) / len(done) * 100),
                    "worst": r1(min(t["ret"] for t in done))})
    return out


def research(ohlc: dict, nights: dict, inds: dict | None = None) -> dict | None:
    """窓の目安の確かめ・寄りで売るか持つか・押し目買いの注文が大きな下げの朝にどうだったか（前半・後半）。

    前半（日足の前半の日付）で係数と「連動の強い銘柄」を決め、窓の目安は後半で確かめる。寄りで売るか・注文の成績は両方の半分で並べる
    （連動の強い銘柄の選び方は前半のデータだけで決めた）。"""
    dates = ohlc.get("dates") or []
    stocks = ohlc.get("stocks") or {}
    if len(dates) < 2 * FIT_MIN or not nights:
        return None
    half = dates[len(dates) // 2]
    first = models(ohlc, nights, end=half)
    sens = {c for c, m in first.items() if m["r2"] >= SENS_R2}

    # (1) 窓の目安（前半の係数で後半の朝）
    test = []
    for code in sens:
        m = first[code]
        for d, g in gaps(dates, stocks[code]):
            n = nights.get(d)
            if d >= half and n and (abs(n[1]) >= SURGE_SOX or abs(n[0]) >= SURGE_SPX):
                test.append((estimate(m, *n), g))
    gm = None
    if len(test) >= 30:
        gm = {"n": len(test), "corr": r2(correlation([t[0] for t in test], [t[1] for t in test])),
              "mae": r2(mean(abs(t[1] - t[0]) for t in test)), "mae0": r2(mean(abs(t[1]) for t in test)),
              "hit": round(sum((t[0] > 0) == (t[1] > 0) for t in test) / len(test) * 100), "from": half}

    # (2) 連動の強い銘柄を、大きな下げの朝に寄りで売った場合と持ち続けた場合
    hold = {"shock": [], "all": []}
    idx = {d: i for i, d in enumerate(dates)}
    for code in sens:
        rows = stocks[code]
        for d, g in gaps(dates, rows):
            j = idx[d]
            if j + HOLD_N >= len(rows) or not rows[j + HOLD_N] or rows[j][3] is None or rows[j + HOLD_N][3] is None:
                continue
            o = rows[j][0]
            rec = (d, g, (rows[j][3] / o - 1) * 100, (rows[j + HOLD_N][3] / o - 1) * 100)
            hold["all"].append(rec)
            if _is_shock(nights.get(d)):
                hold["shock"].append(rec)
    hold_out = {k: {"all": _hold_stats(v), "first": _hold_stats([r for r in v if r[0] < half]),
                    "second": _hold_stats([r for r in v if r[0] >= half])} for k, v in hold.items()}

    # (3) 押し目買いの注文（短期のルールの入口がそろった全銘柄。swing.simulate と同じ再現）
    trades = []
    for code, rows in stocks.items():
        ind = (inds or {}).get(code) or swing.indicators(swing.compact(dates, rows))
        for i in range(swing.WARMUP - 1, len(ind["c"]) - 1):
            if not swing.is_signal(ind, i):
                continue
            res = swing.simulate(ind, i)
            n = nights.get(ind["d"][i + 1])
            if res is None or n is None:
                continue
            trades.append({"d": ind["d"][i + 1], "filled": res["filled"], "ret": res.get("ret"), "why": res.get("why"),
                           "kind": "shock" if _is_shock(n) else "surge" if _is_surge(n) else "other"})
    orders = None
    if trades:
        mid = sorted(t["d"] for t in trades)[len(trades) // 2]
        orders = {"split": mid, "from": min(t["d"] for t in trades), "rows": {}}
        for k in ("shock", "surge", "other"):
            ts = [t for t in trades if t["kind"] == k]
            orders["rows"][k] = {"all": _order_stats(ts), "first": _order_stats([t for t in ts if t["d"] < mid]),
                                 "second": _order_stats([t for t in ts if t["d"] >= mid])}
    return {"half": half, "to": dates[-1], "sens_n": len(sens), "gap_model": gm, "hold": hold_out, "orders": orders,
            "defs": {"shock": f"SOX {SHOCK_SOX:.0f}%以下か S&P500 {SHOCK_SPX}%以下の夜",
                     "surge": f"SOX +{SURGE_SOX:.0f}%以上か S&P500 +{SURGE_SPX}%以上の夜",
                     "sens": f"前半の朝の窓を米国の夜で説明できた割合（決定係数）が {SENS_R2} 以上の銘柄",
                     "hold_n": HOLD_N}}


def _p(v, d=1):
    return "—" if v is None else f"{v:+.{d}f}%"


def _few(n):
    return "（少ない）" if n is not None and n < SMALL else ""


def playbook(ov: dict, rs: dict | None) -> list[dict]:
    """対処の行（考え方と根拠の数字）。向きが下げのとき・上げのときで出す。注文・候補・並び順は変えない。"""
    if ov.get("level") in ("quiet", "none"):
        return []
    out = []
    hold = ((rs or {}).get("hold") or {})
    od = (((rs or {}).get("orders") or {}).get("rows") or {})
    if ov.get("dir", 0) < 0:
        h, b = hold.get("shock") or {}, hold.get("all") or {}
        ha, hf, hs, ba = h.get("all") or {}, h.get("first") or {}, h.get("second") or {}, b.get("all") or {}
        if ha.get("n"):
            if (hf.get("oc") or 0) >= 0 and (hs.get("oc") or 0) >= 0:
                out.append({"who": "持ち株", "text": (
                    f"寄りの成行で慌てて売らない。過去の大きな下げの翌朝（{ha['nights']}回{_few(ha['nights'])}）、米国と連動の強い銘柄は"
                    f"窓 {_p(ha['gap'])} のあと寄りから引け {_p(ha['oc'], 2)}（上げ {ha['oc_up']}%。全部の朝 {_p(ba.get('oc'), 2)}）、"
                    f"寄りから{HOLD_N}日後 {_p(ha['o5'])}（全部 {_p(ba.get('o5'))}）。前半 {_p(hf.get('oc'), 2)}／後半 {_p(hs.get('oc'), 2)}。"
                    "売るかは引けの判定と、決めてある撤退の逆指値で")})
            else:
                out.append({"who": "持ち株", "text": (
                    f"過去の大きな下げの翌朝は、寄りのあとも下げやすかった（寄りから引け {_p(ha['oc'], 2)}、前半 {_p(hf.get('oc'), 2)}／"
                    f"後半 {_p(hs.get('oc'), 2)}）。撤退の逆指値の位置を寄りの前に確かめる")})
        s, o = od.get("shock") or {}, od.get("other") or {}
        sa, sf, ss, oa = s.get("all") or {}, s.get("first") or {}, s.get("second") or {}, o.get("all") or {}
        of, os_ = o.get("first") or {}, o.get("second") or {}
        if sa.get("n"):
            better = all(x.get("avg") is not None and y.get("avg") is not None and x["avg"] >= y["avg"]
                         for x, y in ((sf, of), (ss, os_)))
            nums = (f"大きな下げの翌朝に約定した押し目買いの注文は1回平均 {_p(sa['avg'], 2)}（{sa['nights']}回の朝{_few(sa['nights'])}・"
                    f"{sa['n']}件、勝ち {sa['win']}%・最悪 {_p(sa.get('worst'))}。ほかの朝 {_p(oa.get('avg'), 2)}）。前半 {_p(sf.get('avg'), 2)}／後半 {_p(ss.get('avg'), 2)}"
                    f"（ほか {_p(of.get('avg'), 2)}／{_p(os_.get('avg'), 2)}）")
            if better:
                out.append({"who": "注文", "text": "押し目買いのルールの指値は取り消さない。" + nums +
                            "。寄りが指値より下なら寄りで約定し、損切りは約定値から引き直す（約定値 −3ATR）"})
            else:
                out.append({"who": "注文", "text": nums + "。注文は変えていない（ルールの変更は前半で決めて後半で確かめてから）"})
        out.append({"who": "買い", "text": "候補・並び順は変えていない。夜間の下げで新しい入口は作らない（買う候補は押し目買いの2つのルールだけ）"})
    else:
        s, o = od.get("surge") or {}, od.get("other") or {}
        sa, oa = s.get("all") or {}, o.get("all") or {}
        if sa.get("signals"):
            out.append({"who": "注文", "text": (
                f"上げの夜の翌朝は買いの指値に届きにくい（約定 {sa.get('fill')}%、ほかの朝 {oa.get('fill')}%。{sa['nights']}回の朝"
                f"{_few(sa['nights'])}）。約定した注文は1回平均 {_p(sa.get('avg'), 2)}（ほか {_p(oa.get('avg'), 2)}）。"
                "約定しなければ資金はその日遊ぶ")})
        out.append({"who": "買い", "text": "寄りの高いところを追いかけない。候補・並び順は変えていない（買う候補は押し目買いの2つのルールだけ）"})
    return out


def exposure(ov: dict, mods: dict, names: dict, inds: dict | None = None) -> tuple[list[dict], dict]:
    """今朝の窓の目安（米国の引けまで・時間外を足した分）。響きやすい銘柄の一覧と、アプリに渡す {code: [目安, 決定係数]}。"""
    cash, total = ov.get("cash") or {}, ov.get("total") or {}
    rows, est = [], {}
    for code, m in mods.items():
        e_tot = estimate(m, total.get("spx") or 0, total.get("sox") or 0)
        if m["r2"] >= EST_R2:
            est[code] = [r1(e_tot), r2(m["r2"])]
        if m["r2"] < SHOW_R2:
            continue
        ind = (inds or {}).get(code)
        tv = (ind or {}).get("tv") or []
        if ind is not None and (not tv or tv[-1] is None or tv[-1] < swing.LIQ_MIN):
            continue                      # 売買代金が小さい銘柄は一覧に出さない（保有・注文の行には est で渡す）
        rows.append({"code": code, "name": names.get(code) or code, "est": r1(e_tot),
                     "est_close": r1(estimate(m, cash.get("spx") or 0, cash.get("sox") or 0)),
                     "bm": r2(m["bm"]), "bs": r2(m["bs"]), "r2": r2(m["r2"])})
    sign = -1 if ov.get("dir", 0) < 0 else 1
    rows.sort(key=lambda r: (-sign * r["est"]) if ov.get("dir") else -abs(r["est"]))
    return rows[:SHOW_N], est


def finish(ov: dict, ohlc: dict, macro_bars: dict, names: dict, groups: list[tuple[str, list[dict]]],
           prev_jp: str | None, now: datetime) -> dict:
    """collect の出力に、見出し・日本株への響き・対処の根拠と行を足す。"""
    ov = dict(ov)
    ov["headlines"] = match_headlines(groups, since_of(prev_jp, now), now, ov.get("moved") or [])
    ov["since"] = since_of(prev_jp, now).isoformat(timespec="minutes")
    dates = (ohlc or {}).get("dates") or []
    nights = us_nights(dates, (macro_bars or {}).get("spx"), (macro_bars or {}).get("sox"))
    inds = {code: swing.indicators(swing.compact(dates, rows)) for code, rows in ((ohlc or {}).get("stocks") or {}).items()}
    mods = models(ohlc or {}, nights)
    ov["jp"], ov["est"] = exposure(ov, mods, names, inds)
    ov["research"] = research(ohlc or {}, nights, inds)
    ov["playbook"] = playbook(ov, ov["research"])
    ov["n_models"] = len(mods)
    return ov
