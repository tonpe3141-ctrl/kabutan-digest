"""決算から読む: 決算・業績修正の開示から、業界の風向きと類似銘柄への連想の材料を集める（DESIGN.md 22章）。

入口は機械、理由は LLM。
  機械（ここ）… どの開示を読むか（業績・配当の修正を先に、次に追っている銘柄の決算短信）、会社の説明（TDnet の PDF の
                「修正の理由」「経営成績の概況」「業績予想の説明」）、株探の【決算速報】（数字の要約と「よく比較される銘柄」）、
                業種・テーマ、類似銘柄（株探の比較銘柄 → 同じテーマ → 日経225の同じ業種）の値動き・20日の騰落・次の決算予定・
                最近の決算の向き、直近20営業日に読んだ開示の向きを業種・テーマごとに数えた「風向き」
  LLM（Routine）… 何が分かったか・業界の風向き・類似銘柄への連想・次に確かめること（slots.{SLOT}.data.ai_earnings）

決算速報の数字（進捗率と過去の平均・修正率・累計と直近3か月の伸び・利益率・据え置いた計画での残りの期間の試算）は
flash_metrics が文型で読み、flash_notes が機械の印にする（DESIGN.md 25章）。印は「読みどころ」の目印で、先の値動きを確かめたものではない。
決算への株価の反応（自社と類似銘柄の、反応した日の市場との差）は大引で記録に残し（record_reactions）、たまったら向きごとに数える
（react_stats）。連想が実際に効くかは、この記録で測る（4年の日足では、同業の急騰に動かなかった銘柄が追いつくとは言えなかった。spill.py）。

買う候補は出さない（買う候補は swing.py の2つのルールだけ）。類似銘柄は「連想の材料」で、注文の条件ではない。
取得に失敗した部分は欠損として扱い、例外を投げない。
"""
import json
import os
import re
from datetime import date, timedelta
from statistics import mean

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


# ==================== 決算速報の数字（株探の定型文を読む） ====================
# 株探の【決算速報】は文型が決まっている（「前年同期比5.5％増の148億円」「進捗率は81.8％に達し、5年平均の69.0％も上回った」
# 「従来予想の151億円→159億円(前期は147億円)に4.9％上方修正」「当社が試算した9-11月期(4Q)の…前年同期比24.8％減」
# 「直近3ヵ月の実績である6-8月期(3Q)の…前年同期比2.2倍」「売上営業利益率は前年同期の4.1％→6.9％に改善」）。
_SENT = re.compile(r"[^。\n]+")
_YOY = re.compile(r"前(?:年同期|期|の期)比([\d.]+)％(増|減)|前(?:年同期|期|の期)比([\d.]+)倍")
_PROG = re.compile(r"進捗率[はが]([\d.]+)％")
_PROG_AFTER = re.compile(r"を(?:上回|下回)る([\d.]+)％")
_PROG_AVG = re.compile(r"(\d+)年平均の([\d.]+)％")
_REV = re.compile(r"([\d.]+)％(上方|下方)修正")
_OPM = re.compile(r"売上営業(?:利益|損益)率は前年同期の(-?[\d.]+)％→(-?[\d.]+)％")
_PROFIT = re.compile(r"(経常|最終|営業|税引き前)(?:利益|損益)")


def _yoy(text: str):
    m = _YOY.search(text or "")
    if not m:
        return None
    if m.group(3):
        return round((float(m.group(3)) - 1) * 100, 1)
    v = float(m.group(1))
    return v if m.group(2) == "増" else -v


def flash_metrics(text: str | None) -> dict:
    """決算速報の本文から数字を読む。読めた項目だけを返す（文型が違えば欠ける）。

    y=累計（または通期実績）の前年比、q=直近3か月の前年比、pg=進捗率、pa=過去の平均の進捗率（pn=何年平均か）、
    kp=通期計画を据え置いた、rv=修正率（上方は＋）、rest=据え置いた／修正後の計画から株探が試算した残りの期間の前年比、
    rest_loss=残りの期間が赤字の試算、om=[前年同期の売上営業利益率, 今期]、turn=一転（増益・減益・赤字・黒字）、record=最高益、kind=利益の種類"""
    out: dict = {}
    if not text:
        return out
    for sent in _SENT.findall(text):
        if "直近3ヵ月" in sent or "直近3カ月" in sent:
            if "q" not in out and _yoy(sent) is not None:
                out["q"] = _yoy(sent)
            m = _OPM.search(sent)
            if m:
                out["om"] = [float(m.group(1)), float(m.group(2))]
            continue
        if "試算した" in sent:
            if "rest" not in out and _yoy(sent) is not None:
                out["rest"] = _yoy(sent)
            if re.search(r"赤字(?:に|が|幅|計算|転落)", sent) and "黒字に浮上" not in sent:
                out["rest_loss"] = True
            if "据え置いた" in sent:
                out["kp"] = True
            continue
        if "進捗率" in sent:
            m = _PROG.search(sent) or _PROG_AFTER.search(sent)
            if m:
                out["pg"] = float(m.group(1))
            m = _PROG_AVG.search(sent)
            if m:
                out["pn"], out["pa"] = int(m.group(1)), float(m.group(2))
        m = _REV.search(sent)
        if m and "rv" not in out:
            out["rv"] = float(m.group(1)) * (1 if m.group(2) == "上方" else -1)
        if "据え置" in sent:
            out["kp"] = True
        if "y" not in out and _yoy(sent) is not None:
            out["y"] = _yoy(sent)
        if "kind" not in out:
            m = _PROFIT.search(sent)
            if m:
                out["kind"] = m.group(1)
        if "一転" in sent:
            t = re.search(r"一転(?:して)?(増益|減益|赤字|黒字)", sent)
            if t:
                out["turn"] = t.group(1)
        if "最高益" in sent:
            out["record"] = True
    return out


PROG_GAP = 5.0          # 進捗率が過去の平均を何ポイント上回る／下回れば印にするか
ACCEL_GAP = 10.0        # 直近3か月の伸びが累計の伸びを何ポイント上回る／下回れば「加速」「鈍化」か
OPM_GAP = 1.0           # 売上営業利益率が何ポイント動けば「改善」「悪化」か


def flash_notes(m: dict) -> list[dict]:
    """決算速報の数字から、読みどころの印（{t: 文, tone: up/down/warn}）。先の値動きを確かめたものではない。"""
    notes = []
    pg, pa = m.get("pg"), m.get("pa")
    if pg is not None and pa is not None:
        gap = pg - pa
        if gap >= PROG_GAP:
            notes.append({"t": f"進捗 {pg:g}%（{m.get('pn') or ''}年平均 {pa:g}%）を上回る" + ("・計画は据え置き" if m.get("kp") else ""),
                          "tone": "up", "k": "prog_hi"})
        elif gap <= -PROG_GAP:
            notes.append({"t": f"進捗 {pg:g}%（{m.get('pn') or ''}年平均 {pa:g}%）を下回る", "tone": "down", "k": "prog_lo"})
    y, rest = m.get("y"), m.get("rest")
    if m.get("kp") and ((y is not None and y > 0 and rest is not None and rest < 0) or (m.get("rest_loss") and (y or 0) > 0)):
        notes.append({"t": "据え置いた計画では残りの期間が" + ("赤字" if m.get("rest_loss") else f"減益（{rest:+g}%）") + "＝計画が控えめか、失速の想定か",
                      "tone": "warn", "k": "rest_down"})
    q = m.get("q")
    if q is not None and y is not None and abs(q - y) >= ACCEL_GAP:
        if q > y:
            word = "増益に転じた" if y < 0 <= q else "減益幅が縮小" if q < 0 else "伸びが加速"
        else:
            word = "減益に転じた" if q < 0 <= y else "減益幅が拡大" if y < 0 else "伸びが鈍化"
        notes.append({"t": f"直近3か月 {q:+g}%（累計 {y:+g}%）で{word}", "tone": "up" if q > y else "down",
                      "k": "accel" if q > y else "decel"})
    om = m.get("om")
    if om and abs(om[1] - om[0]) >= OPM_GAP:
        notes.append({"t": f"利益率 {om[0]:g}%→{om[1]:g}% に{'改善' if om[1] > om[0] else '悪化'}",
                      "tone": "up" if om[1] > om[0] else "down", "k": "opm"})
    if m.get("turn"):
        notes.append({"t": f"一転して{m['turn']}", "tone": "up" if m["turn"] in ("増益", "黒字") else "down", "k": "turn"})
    if m.get("record"):
        notes.append({"t": "最高益", "tone": "up", "k": "record"})
    return notes


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
    peers = []
    # 銘柄ページのニュース欄には他社の決算速報が載ることがある（薬王堂ＨＤの欄にクリエイトＳＤの速報が載った。2026-10-05）。
    # 本文の先頭の「社名<コード>」がこの会社のものだけを使う
    for flash in [r for r in rows if r.get("provider") == kn.PROVIDER and "決算速報" in (r.get("title") or "")][:3]:
        art = fetch_article(flash["url"]) or {}
        body = art.get("body") or ""
        if not _is_own_flash(body, g["code"]):
            continue
        full = kn.flash_body(body)
        item["flash"] = {"headline": re.sub(r"^【決算速報】", "", flash["title"]),
                         "body": _cut(full, EARN_TEXT["flash"]), "url": flash["url"]}
        item["metrics"] = flash_metrics(full)
        item["notes"] = flash_notes(item["metrics"])
        peers = kn.parse_peers(body)
        break
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


def _is_own_flash(body: str, code: str) -> bool:
    """決算速報の本文がこの会社のものか（先頭の「社名<コード>」で見る。コードが本文に無い古い型はそのまま使う）。"""
    m = re.search(r"<([0-9]{3}[0-9A-Z])>", body or "")
    return m is None or m.group(1) == code


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
        # 引け後（15:30〜）の開示は、いま見えている値動きより後に出ている。開示への反応は翌営業日の値動きに出るので、
        # この値動きは「開示前」の動きで、決算の評価に使えない（after_close を画面と Routine の要約に渡す）
        it["after_close"] = _minutes(it.get("time")) >= SESSION_END
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


# ==================== 決算への株価の反応（記録） ====================
# 引け後の開示は翌営業日、場中の開示はその日に反応する。大引で、その日が反応の日に当たる記録に、自社と類似銘柄の
# 前日比と市場（日経225採用銘柄の前日比の中央値）との差を書き残す。連想が効くか（上向きの決算のあと類似銘柄が市場に勝つか）は
# この記録がたまってから数える。1日の反応で、その後の値動き（5日・20日）ではない。
SESSION_END = 15 * 60 + 30       # 東証の大引け（これ以降の開示は翌営業日に反応する）
REACT_BIG = 2.0                  # 市場との差（%）がこれ以上なら「反応した」とみなす

REACT_LABEL = {"good_up": "上向きの決算に素直に上昇", "good_down": "上向きの決算でも下落",
               "bad_up": "下向きの決算でも上昇（悪材料出尽くしの形）", "bad_down": "下向きの決算で下落",
               "up": "上昇", "down": "下落", "flat": f"反応は小さい（市場との差 ±{REACT_BIG:g}% 未満）"}


def reaction_due(e: dict, today: str, prev_day: str | None) -> bool:
    t = _minutes(e.get("time"))
    return (e.get("date") == today and t < SESSION_END) or (bool(prev_day) and e.get("date") == prev_day and t >= SESSION_END)


def due_codes(store: dict, today: str, prev_day: str | None) -> list[str]:
    out = []
    for e in store.get("log") or []:
        if not e.get("rx") and reaction_due(e, today, prev_day):
            out += [e["code"]] + list(e.get("peers") or [])
    return list(dict.fromkeys(out))


def react_class(direction: str | None, ex) -> str | None:
    if ex is None:
        return None
    if ex >= REACT_BIG:
        return {"up": "good_up", "down": "bad_up"}.get(direction or "", "up")
    if ex <= -REACT_BIG:
        return {"up": "good_down", "down": "bad_down"}.get(direction or "", "down")
    return "flat"


def record_reactions(store: dict, today: str, prev_day: str | None, quotes: dict, mkt) -> list[dict]:
    """今日が反応の日に当たる記録に、反応（rx）を書き足す。書いた記録を返す。休場日・市場の値が無い日は何もしない。"""
    if mkt is None:
        return []
    done = []
    for e in store.get("log") or []:
        if e.get("rx") or not reaction_due(e, today, prev_day):
            continue
        q = quotes.get(e["code"]) or {}
        r = q.get("change_pct")
        if r is None or (q.get("asof") and q["asof"] != today):
            continue
        ps = [(quotes.get(c) or {}).get("change_pct") for c in e.get("peers") or [] if c != e["code"]]
        ps = [x - mkt for x in ps if x is not None]
        e["rx"] = {"d": today, "r": round(r, 1), "ex": round(r - mkt, 1), "m": round(mkt, 2),
                   "p": round(mean(ps), 1) if ps else None, "pn": len(ps)}
        done.append(e)
    return done


def reaction_rows(log: list[dict], day: str) -> list[dict]:
    """その日に反応した決算（画面の「決算への反応」）。市場との差の大きい順。"""
    rows = []
    for e in log or []:
        rx = e.get("rx") or {}
        if rx.get("d") != day:
            continue
        rows.append({"code": e["code"], "name": e.get("name"), "date": e.get("date"), "time": e.get("time"),
                     "dir": e.get("dir"), "head": e.get("head"), "r": rx.get("r"), "ex": rx.get("ex"),
                     "p": rx.get("p"), "pn": rx.get("pn"), "cls": react_class(e.get("dir"), rx.get("ex"))})
    rows.sort(key=lambda x: -abs(x["ex"] or 0))
    return rows


def react_stats(log: list[dict]) -> dict | None:
    """記録した反応を、決算の向きごとに数える（自社の反応と、類似銘柄の平均の反応。どちらも市場との差）。"""
    by: dict[str, dict] = {}
    days = set()
    for e in log or []:
        rx = e.get("rx")
        if not rx or e.get("dir") not in ("up", "down"):
            continue
        days.add(rx.get("d"))
        a = by.setdefault(e["dir"], {"n": 0, "ex": [], "p": []})
        a["n"] += 1
        a["ex"].append(rx["ex"])
        if rx.get("p") is not None:
            a["p"].append(rx["p"])
    if not by:
        return None

    def agg(xs):
        return {"n": len(xs), "avg": round(mean(xs), 2), "pos": round(100 * sum(1 for x in xs if x > 0) / len(xs))} if xs else None
    return {"days": len(days), "from": min(days), "to": max(days),
            **{k: {"n": a["n"], "self": agg(a["ex"]), "peers": agg(a["p"])} for k, a in by.items()}}


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
    codes = {i["code"] for i in items}
    old = {e["code"]: e for e in store.get("log") or [] if e.get("date") == asof and e.get("code") in codes}
    log = [e for e in store.get("log") or [] if not (e.get("date") == asof and e.get("code") in codes)]
    for it in items:
        e = {"date": asof, "time": it.get("time"), "code": it["code"], "name": it.get("name"), "industry": it.get("industry"),
             "themes": it.get("themes") or [], "dir": it.get("dir"), "kinds": it.get("kinds"),
             "head": (it.get("flash") or {}).get("headline") or (it.get("titles") or [""])[0],
             # 類似銘柄（売買タブで「この銘柄を類似に挙げた決算」を引く。picks.py）
             "peers": [p["code"] for p in it.get("peers") or []]}
        m = {k: v for k, v in (it.get("metrics") or {}).items() if k in ("pg", "pa", "kp", "y", "q", "rv", "rest")}
        if m:
            e["m"] = m
        if (old.get(it["code"]) or {}).get("rx"):
            e["rx"] = old[it["code"]]["rx"]           # 反応は記録済みなら残す（寄り前に読み直しても消さない）
        log.append(e)
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
def build(slot: str, target_date: date, rows: list[dict], asof: str | None, scope: str, *, focus: set[str],
          themes: dict, members: dict, articles: list[dict], quotes_of, closes_of,
          fetch_news=None, fetch_article=None, fetch_pdf=None, extract=None, names_of=None,
          prev_day: str | None = None, mkt=None, path: str = EARN_PATH) -> dict | None:
    """決算・修正の開示を読んで、画面と Routine に渡す材料を作る。rows は TDnet の開示（tdnet.fetch_disclosures の行）。

    大引（mkt に今日の市場の前日比を渡したとき）は、今日が反応の日に当たる記録に株価の反応を書き足す
    （前営業日 prev_day の引け後の開示と、今日の場中の開示）。"""
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
    if items:
        update_log(store, items, asof, target_date)
    react_codes = due_codes(store, today, prev_day) if mkt is not None else []
    codes = list(dict.fromkeys([it["code"] for it in items] + [p["code"] for it in items for p in it["peers"]] + react_codes))
    quotes = (quotes_of(codes) if codes else {}) or {}
    enrich_peers(items, quotes, closes_of, store.get("schedule") or {}, store.get("log") or [], today)
    reacted = record_reactions(store, today, prev_day, quotes, mkt)
    store["updated_at"] = today
    save(store, path)
    n_flash = sum(1 for it in items if it.get("flash"))
    n_text = sum(1 for it in items if it.get("reason") or it.get("overview"))
    print(f"    {'✅' if items else '・'} 決算を読む: {len(items)}/{len(groups)} 社（決算速報 {n_flash}・会社の説明 {n_text}）"
          f"、決算予定 {len(store.get('schedule') or {})} 社" + (f"、反応を記録 {len(reacted)} 社" if mkt is not None else ""))
    log = store.get("log") or []
    reactions = reaction_rows(log, today) if mkt is not None else []
    if not groups and not reactions:
        return None
    return {"asof": asof or today, "scope": scope, "n_total": len(groups), "n_read": len(items),
            "items": items, "wind": wind(log, asof or today),
            "more": [{"code": g["code"], "name": g.get("name"), "kinds": g["kinds"]} for g in groups[len(picked):]][:60],
            "reactions": reactions, "react": react_stats(log), "react_big": REACT_BIG}
