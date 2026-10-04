"""決算から読む: 決算・業績修正の開示から、業界の風向きと類似銘柄への連想の材料を集める（DESIGN.md 22章）。

入口は機械、理由は LLM。
  機械（ここ）… どの開示を読むか（業績・配当の修正を先に、次に追っている銘柄の決算短信）、会社の説明（TDnet の PDF の
                「修正の理由」「経営成績の概況」「業績予想の説明」）、株探の【決算速報】（数字の要約と「よく比較される銘柄」）、
                業種・テーマ、類似銘柄（株探の比較銘柄 → 同じテーマ → 日経225の同じ業種）の値動き・20日の騰落・次の決算予定・
                最近の決算の向き、直近20営業日に読んだ開示の向きを業種・テーマごとに数えた「風向き」
  LLM（Routine）… 何が分かったか・業界の風向き・類似銘柄への連想・次に確かめること（slots.{SLOT}.data.ai_earnings）

買う候補は出さない（買う候補は swing.py の2つのルールだけ）。類似銘柄は「連想の材料」で、注文の条件ではない。
取得に失敗した部分は欠損として扱い、例外を投げない。
"""
import json
import os
import re
from datetime import date, timedelta

from .config import (
    EARN_KINDS, EARN_LOG_DAYS, EARN_PEERS_MAX, EARN_PROVIDERS, EARN_READ_MAX, EARN_TEXT,
    EARN_WIND_DAYS, EARN_WIND_MIN,
)

EARN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "earnings.json")

DIR_LABEL = {"up": "上向き", "down": "下向き", "mixed": "強弱まちまち"}

# ==================== 向き ====================
# 上向き・下向きの語。同じ位置で長い語を先に当てる（「赤字縮小」を「赤字」と読まない、「赤字転落」を先に当てる）
_DIR_RE = re.compile(
    r"(?P<up>赤字縮小|上方修正|増額修正|上振れ|黒字浮上|黒字転換|黒字化|最高益|増益|増配|復配|増額)"
    r"|(?P<down>下方修正|減額修正|下振れ|赤字転落|赤字拡大|赤字|減益|減配|無配|減額)")
# この語が前にある（同じ句の中）か、語そのものが修正なら、会社の見通しの話として重く数える
_FORWARD = re.compile(r"今期|通期|来期|予想|見通し|計画")


def headline_dir(text: str | None) -> str | None:
    """見出し・表題の語から向きを読む。「前期配当増額も今期減配」のように割れたら、見通し（今期・通期・修正）に係る語を重く数える。"""
    if not text:
        return None
    up = down = 0
    for clause in re.split(r"[、・，,]", text):
        for m in _DIR_RE.finditer(clause):
            w = 2 if ("修正" in m.group(0) or _FORWARD.search(clause[:m.start()])) else 1
            if m.group("up"):
                up += w
            else:
                down += w
    if not (up or down):
        return None
    if up > down:
        return "up"
    if down > up:
        return "down"
    return "mixed"


# ==================== どの開示を読むか ====================
def _minutes(t: str | None) -> int:
    m = re.match(r"(\d{1,2}):(\d{2})", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


# 決算の中身ではないお知らせ（決算短信の訂正・開示の延期）
_NOT_RESULT = re.compile(r"訂正|延期|遅延|超過|超え")


def group_rows(rows: list[dict]) -> list[dict]:
    """決算・修正の開示を会社ごとにまとめる（同じ会社が決算短信と修正と配当を同時に出すことが多い）。"""
    by: dict[str, dict] = {}
    for r in rows or []:
        if r.get("category") not in EARN_KINDS or not r.get("code") or _NOT_RESULT.search(r.get("title") or ""):
            continue
        code = str(r["code"])
        g = by.setdefault(code, {"code": code, "name": r.get("name"), "time": r.get("time"),
                                 "kinds": [], "titles": [], "pdfs": {}})
        if r["category"] not in g["kinds"]:
            g["kinds"].append(r["category"])
        g["titles"].append(r.get("title") or "")
        if r.get("pdf"):
            g["pdfs"].setdefault(r["category"], r["pdf"])
        if _minutes(r.get("time")) > _minutes(g.get("time")):
            g["time"] = r.get("time")
    return list(by.values())


def prioritize(groups: list[dict], focus: set[str]) -> list[dict]:
    """業績予想の修正 → 決算短信 → 配当だけ、の順。同じ種類の中では追っている銘柄（日経225・テーマ辞書・ウォッチ・台帳）を先に。"""
    def key(g):
        kind = 0 if "業績予想の修正" in g["kinds"] else 1 if "決算短信" in g["kinds"] else 2
        return (kind, 0 if g["code"] in focus else 1, -_minutes(g.get("time")))
    return sorted(groups, key=key)


# ==================== 1社を読む ====================
def _same_day(time_txt: str | None, asof: str, run_date: str) -> bool:
    """銘柄ページの一覧の時刻（当日は 16:35、前日以前は 10/2）が、開示の日のものか。"""
    t = time_txt or ""
    if ":" in t:
        return asof == run_date
    m = re.match(r"(\d{1,2})/(\d{1,2})$", t)
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2))) == (int(asof[5:7]), int(asof[8:10]))


# 銘柄ページに載るが、何銘柄もまとめた記事（その会社の話ではない）
_ROUNDUP = re.compile(r"^採れたて株価材料|コメント\s*No\.|^(前場|後場|寄り付き|大引け)コメント|注目銘柄|ランキング")


def read_company(g: dict, asof: str, run_date: str, fetch_news, fetch_article, fetch_pdf, extract) -> dict:
    """1社分の材料。どれが取れなくても、取れた分だけで返す。"""
    from .sources import kabutan_news as kn
    item = {"code": g["code"], "name": g.get("name"), "time": g.get("time"), "kinds": g["kinds"],
            "titles": g["titles"][:4], "pdfs": dict(g["pdfs"])}
    news = fetch_news(g["code"]) or {}
    item["industry"] = news.get("industry")
    rows = [r for r in news.get("items") or [] if _same_day(r.get("time"), asof, run_date)]
    flash = next((r for r in rows if r.get("provider") == kn.PROVIDER and "決算速報" in (r.get("title") or "")), None)
    peers = []
    if flash:
        art = fetch_article(flash["url"]) or {}
        body = art.get("body") or ""
        item["flash"] = {"headline": re.sub(r"^【決算速報】", "", flash["title"]),
                         "body": _cut(kn.flash_body(body), EARN_TEXT["flash"]), "url": flash["url"]}
        peers = kn.parse_peers(body)
    item["press"] = [{"title": r["title"], "provider": r["provider"], "url": r["url"]}
                     for r in rows if r.get("provider") != kn.PROVIDER and not _ROUNDUP.search(r.get("title") or "")][:3]
    # 会社の説明: 修正の PDF から「修正の理由」、決算短信の PDF から「経営成績の概況」「業績予想の説明」
    pdf_dir = None
    for kind in ("業績予想の修正", "配当予想の修正", "決算短信"):
        url = g["pdfs"].get(kind)
        if not url or (kind == "配当予想の修正" and item.get("reason")):
            continue
        text = fetch_pdf(url)
        if not text:
            continue
        ex = extract(text, EARN_TEXT)
        if kind == "決算短信":
            for k in ("overview", "outlook"):
                if ex.get(k):
                    item[k] = ex[k]
        else:
            if ex.get("reason") and not item.get("reason"):
                item["reason"] = ex["reason"]
                item["reason_pdf"] = url
            if kind == "業績予想の修正":
                pdf_dir = pdf_dir or ex.get("dir")
    item["dir"] = (headline_dir((item.get("flash") or {}).get("headline")) or pdf_dir
                   or headline_dir(" ".join(item["titles"])))
    item["peers_raw"] = peers
    return item


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = head.rfind("。")
    return head[:end + 1] if end >= limit // 2 else head + "…"


# ==================== 類似銘柄 ====================
def find_peers(item: dict, themes: dict, members: dict, limit: int = EARN_PEERS_MAX) -> list[dict]:
    """株探の「よく比較される銘柄」→ 同じテーマ（テーマ辞書。日経225を先に）→ 日経225の同じ業種、の順に集める。"""
    code = item["code"]
    out, seen = [], {code}

    def add(c, name, src):
        c = str(c)
        if c in seen or len(out) >= limit:
            return
        seen.add(c)
        out.append({"code": c, "name": name, "src": src})

    for p in item.get("peers_raw") or []:
        add(p["code"], p.get("name"), "株探の比較銘柄")
    stocks = themes.get("stocks") or {}
    mine = (stocks.get(code) or {}).get("themes") or []
    item["themes"] = mine
    for th in mine:
        same = [(c, e) for c, e in stocks.items() if th in (e.get("themes") or [])]
        same.sort(key=lambda ce: 0 if ce[0] in members else 1)
        for c, e in same[:3]:
            add(c, e.get("name"), f"テーマ: {th}")
    sector = (members.get(code) or {}).get("sector")
    if sector:
        for c, m in members.items():
            if m.get("sector") == sector:
                add(c, m.get("name"), f"日経225の同じ業種: {sector}")
    return out


def _r20(closes: dict[str, float]) -> float | None:
    ds = sorted(closes)
    if len(ds) < 21:
        return None
    a, b = closes[ds[-21]], closes[ds[-1]]
    return round((b / a - 1) * 100, 1) if a else None


def enrich_peers(items: list[dict], quotes: dict, closes_of, schedule: dict, log: list[dict], today: str) -> None:
    """類似銘柄に、直近の値動き・20日の騰落・次の決算予定・最近の決算の向き（自分の記録から）を添える。

    today は実行日（決算予定はこの日以降だけ）。同じ回に読んだ会社が類似銘柄に入っていれば、その向きを today に置く。"""
    recent: dict[str, dict] = {}
    for e in sorted(log or [], key=lambda x: x.get("date") or ""):
        recent[e["code"]] = e
    read_now = {it["code"]: it.get("dir") for it in items}
    for it in items:
        q = quotes.get(it["code"]) or {}
        it["move"] = {"pct": q.get("change_pct"), "price": q.get("last"), "asof": q.get("asof")}
        for p in it.get("peers") or []:
            q = quotes.get(p["code"]) or {}
            p["pct"] = q.get("change_pct")
            p["r20"] = _r20(closes_of(p["code"]) or {})
            if not p.get("name"):
                p["name"] = q.get("name") or p["code"]
            s = schedule.get(p["code"])
            if s and s.get("date", "") >= today:
                p["next"] = s["date"]
            if p["code"] in read_now:
                p["today"] = read_now[p["code"]] or "read"
            elif p["code"] in recent:
                e = recent[p["code"]]
                p["last"] = {"date": e["date"], "dir": e.get("dir"), "head": e.get("head")}


# ==================== 風向き（読んだ開示の向きを業種・テーマで数える） ====================
def wind(log: list[dict], today: str, days: int = EARN_WIND_DAYS, min_n: int = EARN_WIND_MIN) -> dict:
    dates = sorted({e["date"] for e in log or [] if e.get("date") and e["date"] <= today})[-days:]
    keep = [e for e in log or [] if e.get("date") in set(dates)]

    def table(key_of):
        agg: dict[str, dict] = {}
        for e in keep:
            for k in key_of(e):
                a = agg.setdefault(k, {"key": k, "up": 0, "down": 0, "mixed": 0, "none": 0, "names": []})
                a[e.get("dir") or "none"] += 1
                a["names"].append({"code": e["code"], "name": e.get("name"), "dir": e.get("dir"), "date": e["date"]})
        rows = []
        for a in agg.values():
            n = a["up"] + a["down"] + a["mixed"] + a["none"]
            if n < min_n:
                continue
            a["n"] = n
            a["names"] = sorted(a["names"], key=lambda x: x["date"], reverse=True)[:6]
            a["lean"] = ("up" if a["up"] >= 2 * max(a["down"], 1) else
                         "down" if a["down"] >= 2 * max(a["up"], 1) else None)
            rows.append(a)
        rows.sort(key=lambda a: (-(abs(a["up"] - a["down"])), -a["n"]))
        return rows

    return {"days": len(dates), "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
            "n": len(keep), "min_n": min_n,
            "industries": table(lambda e: [e["industry"]] if e.get("industry") else []),
            "themes": table(lambda e: e.get("themes") or [])}


# ==================== 記録（docs/data/earnings.json） ====================
def load(path: str = EARN_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict, path: str = EARN_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, separators=(",", ":"))
        f.write("\n")


def update_log(store: dict, items: list[dict], asof: str, today: date) -> None:
    """読んだ開示を記録する（同じ日・同じ会社は上書き）。古いものは捨てる。"""
    log = [e for e in store.get("log") or [] if not (e.get("date") == asof and e.get("code") in {i["code"] for i in items})]
    for it in items:
        log.append({"date": asof, "code": it["code"], "name": it.get("name"), "industry": it.get("industry"),
                    "themes": it.get("themes") or [], "dir": it.get("dir"), "kinds": it.get("kinds"),
                    "head": (it.get("flash") or {}).get("headline") or (it.get("titles") or [""])[0]})
    cutoff = (today - timedelta(days=EARN_LOG_DAYS)).isoformat()
    store["log"] = sorted([e for e in log if (e.get("date") or "") >= cutoff], key=lambda e: (e["date"], e["code"]))


def update_schedule(store: dict, articles: list[dict], today: date) -> int:
    from .sources import kabutan_news as kn
    sched = {c: s for c, s in (store.get("schedule") or {}).items() if s.get("date", "") >= today.isoformat()}
    added = 0
    for a in articles or []:
        h = a.get("headline") or ""
        if not re.search(r"決算発表予定|サプライズ決算", h):
            continue
        for code, s in kn.parse_schedule(a.get("body") or "", today).items():
            if s["date"] >= today.isoformat():
                added += code not in sched
                sched[code] = s
    store["schedule"] = dict(sorted(sched.items()))
    return added


# ==================== まとめ ====================
def build(slot: str, target_date: date, rows: list[dict], asof: str, scope: str, *, focus: set[str],
          themes: dict, members: dict, articles: list[dict], quotes_of, closes_of,
          fetch_news=None, fetch_article=None, fetch_pdf=None, extract=None, names_of=None,
          path: str = EARN_PATH) -> dict | None:
    """決算・修正の開示を読んで、画面と Routine に渡す材料を作る。rows は TDnet の開示（tdnet.fetch_disclosures の行）。"""
    from .sources import kabutan_news as kn, tdnet
    fetch_news = fetch_news or (lambda c: kn.fetch_stock_news(c, EARN_PROVIDERS))
    fetch_article = fetch_article or kn.fetch_article
    fetch_pdf = fetch_pdf or tdnet.fetch_pdf_text
    extract = extract or tdnet.extract_explanations
    today = target_date.isoformat()

    store = load(path)
    update_schedule(store, articles, target_date)
    groups = prioritize(group_rows(rows), focus)
    picked = groups[:EARN_READ_MAX.get(slot, 10)]
    items = []
    for g in picked:
        try:
            items.append(read_company(g, asof, today, fetch_news, fetch_article, fetch_pdf, extract))
        except Exception as e:              # noqa: BLE001  1社の失敗で全体を止めない
            print(f"    ⚠️  決算を読む: {g['code']} で例外: {e}")
    jp = names_of() if names_of else {}
    for it in items:
        it["peers"] = find_peers(it, themes, members)
        it.pop("peers_raw", None)
        for p in it["peers"]:
            if jp.get(p["code"]) and not re.search(r"[^\x00-\x7f]", p.get("name") or ""):
                p["name"] = jp[p["code"]]
    codes = list(dict.fromkeys([it["code"] for it in items] + [p["code"] for it in items for p in it["peers"]]))
    quotes = quotes_of(codes) if codes else {}
    if items:
        update_log(store, items, asof, target_date)
    enrich_peers(items, quotes or {}, closes_of, store.get("schedule") or {}, store.get("log") or [], today)
    store["updated_at"] = today
    save(store, path)
    n_flash = sum(1 for it in items if it.get("flash"))
    n_text = sum(1 for it in items if it.get("reason") or it.get("overview"))
    print(f"    {'✅' if items else '・'} 決算を読む: {len(items)}/{len(groups)} 社（決算速報 {n_flash}・会社の説明 {n_text}）"
          f"、決算予定 {len(store.get('schedule') or {})} 社")
    if not groups:
        return None
    return {"asof": asof, "scope": scope, "n_total": len(groups), "n_read": len(items),
            "items": items, "wind": wind(store.get("log") or [], asof),
            "more": [{"code": g["code"], "name": g.get("name"), "kinds": g["kinds"]} for g in groups[len(picked):]][:60]}
