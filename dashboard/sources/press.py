"""株探以外の、信頼できる情報源からニュースの素材を集める。

株探だけでは「株探が何を記事にしたか」に視点が偏る。ここでは性格の違う3系統を足す:

  official … 一次情報。日本銀行・財務省・日本取引所グループ・FRB の公式 RSS（表題と時刻）
  press    … 報道。ロイター・ブルームバーグ・日本経済新聞・時事通信・NHK の見出し
             （NHK 以外は Google ニュース RSS 経由。本文なし）と、Yahoo!ファイナンスに本文付きで
             配信される 時事通信・トレーダーズ・ウェブ（DZH）・ウエルスアドバイザー の記事
  overseas … 海外報道。CNBC の Markets / Economy / Earnings（英語。見出しと要約）

信頼性は絶対条件なので、配信元は取得時に必ず照合する:
  - Google ニュースは各記事の配信元ドメイン（<source url>）が config の hosts にあるものだけを採る
    （同じ話題を転載した別媒体・プレスリリースの転載・銘柄の株価ページが混ざるのを落とす）
  - Yahoo!ファイナンスは一覧の配信元表記が許可リストにあるものだけを採る
許可リスト（config.PRESS_FEEDS / PRESS_YAHOO_PROVIDERS）に無い媒体は、たとえ届いても使わない。

ここで作るのは「素材」であり、解釈はしない（後段の LLM の仕事）。
取得に失敗した情報源は空で返し、どれが取れなかったかを `status` に残す。
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from ..config import (
    PRESS_ARTICLE_LIMIT, PRESS_ARTICLE_PRIORITY, PRESS_BODY_MAX, PRESS_EXCLUDE, PRESS_FEEDS,
    PRESS_WINDOW_HOURS, PRESS_YAHOO_CATEGORIES, PRESS_YAHOO_PROVIDERS,
)
from ..http import get, get_text
from .kabutan_news import _list_yahoo, parse_yahoo_article

JST = timezone(timedelta(hours=9))
_STOCK_PAGE = re.compile(r"Stock Price|Quote\b|株価・チャート")
_EXCLUDE = re.compile(PRESS_EXCLUDE)
_JA = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")   # 日本語の報道なのに日本語が無い表題は銘柄・ファンドのページ
_HALF_KANA_ONLY = re.compile(r"^[\uff61-\uff9f\s・]+$")   # ﾌﾞﾙｰﾑﾊﾞｰｸﾞの銘柄ページの表題


# ==================== RSS ====================
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el, name: str) -> str:
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def _parse_date(txt: str) -> datetime | None:
    if not txt:
        return None
    try:
        d = parsedate_to_datetime(txt)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(JST)


def parse_rss(xml: str | bytes) -> list[dict]:
    """RSS 2.0 / RSS 1.0(RDF) / Atom を {title, url, published(datetime|None), summary, source_host,
    source_name} の列にする。source_* は Google ニュースの <source url="...">媒体名</source>。"""
    if not xml:
        return []
    # バイト列で受けて XML 宣言の文字コードに任せる（requests の推定だと BOM 付きの FRB が化ける）
    if isinstance(xml, bytes):
        xml = xml.lstrip(b"\xef\xbb\xbf \r\n\t")
    else:
        xml = xml.lstrip("\ufeff \r\n\t")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _child_text(el, "title")
        if not title:
            continue
        url = _child_text(el, "link")
        if not url:  # Atom は <link href="...">
            for c in el:
                if _local(c.tag) == "link" and c.get("href"):
                    url = c.get("href")
                    break
        pub = (_child_text(el, "pubdate") or _child_text(el, "date")
               or _child_text(el, "updated") or _child_text(el, "published"))
        summary = _child_text(el, "description") or _child_text(el, "summary")
        if "<" in summary:  # Google ニュースの description はリンクの HTML なので捨てる
            summary = ""
        src_host = src_name = None
        for c in el:
            if _local(c.tag) == "source":
                src_host = urlparse(c.get("url") or "").netloc.lower() or None
                src_name = (c.text or "").strip() or None
                break
        out.append({"title": title, "url": url or None, "published": _parse_date(pub),
                    "summary": summary[:300], "source_host": src_host, "source_name": src_name})
    return out


def _match_source(it: dict, hosts: list[str] | None) -> str | None:
    """配信元ドメインを照合して、見出しから末尾の「 - 媒体名」を外して返す。一致しなければ None。

    hosts が無いフィード（媒体の公式 RSS）は照合不要。Google ニュースのように配信元が
    混ざるフィードは hosts 必須で、<source> が無い記事も落とす。
    """
    title = it["title"]
    if not hosts:
        return title
    if (it.get("source_host") or "") not in hosts:
        return None
    name = it.get("source_name")
    if name and title.endswith(" - " + name):
        title = title[: -len(name) - 3]
    return title.strip()


def _clean_title(title: str) -> str:
    title = re.sub(r"[：:]\s*時事ドットコム$", "", title)      # 本文側にも媒体名が付くことがある
    title = re.sub(r"^[◎○●☆★◇◆]+", "", title)               # 時事の速報記号
    title = re.sub(r"[☆★]?差替$", "", title)
    return title.strip()


def window_hours(kind: str, now: datetime) -> int:
    h = PRESS_WINDOW_HOURS.get(kind, 30)
    return h + 48 if now.weekday() == 0 else h


def select_feed_items(items: list[dict], feed: dict, now: datetime) -> list[dict]:
    """1つのフィードから、配信元の照合・表題の絞り込み・鮮度で残すものを選ぶ。新しい順。"""
    cutoff = now - timedelta(hours=window_hours(feed["kind"], now))
    inc = re.compile(feed["include"]) if feed.get("include") else None
    exc = re.compile(feed["exclude"]) if feed.get("exclude") else None
    out = []
    for it in items:
        title = _match_source(it, feed.get("hosts"))
        if not title or _STOCK_PAGE.search(title) or _HALF_KANA_ONLY.match(title):
            continue
        title = _clean_title(title)
        if feed["kind"] == "press" and not _JA.search(title):
            continue
        if len(title) < 8 or _EXCLUDE.search(title) or (inc and not inc.search(title)) \
                or (exc and exc.search(title)):
            continue
        pub = it.get("published")
        if pub is None or pub < cutoff or pub > now + timedelta(hours=1):
            continue
        row = {"title": title, "published": pub.isoformat(timespec="minutes"),
               "url": it.get("url"), "source": feed["label"], "source_key": feed["key"]}
        if it.get("summary") and feed["kind"] == "overseas":
            row["summary"] = it["summary"]
        out.append(row)
    out.sort(key=lambda r: r["published"], reverse=True)
    return out[: feed.get("max", 12)]


def _norm(title: str) -> str:
    return re.sub(r"[\s　「」『』（）()【】\[\]、。,.・－\-–—:：]", "", title)[:40]


def _dedupe(rows: list[dict]) -> list[dict]:
    """同じ出来事を複数の媒体が報じても、見出しが同一のものだけを落とす（別媒体の別見出しは残す）。"""
    seen, out = set(), []
    for r in rows:
        k = _norm(r["title"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# ==================== Yahoo!ファイナンス配信の他社記事（本文） ====================
def _article_prio(title: str) -> int:
    return next((i for i, p in enumerate(PRESS_ARTICLE_PRIORITY) if re.search(p, title)),
                len(PRESS_ARTICLE_PRIORITY))


def pick_articles(rows: list[dict], limit: int) -> list[dict]:
    """一覧の行を、型の優先順 → 一覧の順（新しい順）で選ぶ。1つの配信元に偏りすぎないよう
    同じ配信元は limit の半分までにする。"""
    ranked = sorted(enumerate(rows), key=lambda x: (_article_prio(x[1]["title"]), x[0]))
    cap = max(2, limit // 2)
    per: dict[str, int] = {}
    out = []
    for _, r in ranked:
        if per.get(r["provider"], 0) >= cap:
            continue
        per[r["provider"]] = per.get(r["provider"], 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def fetch_articles(limit: int = PRESS_ARTICLE_LIMIT) -> tuple[list[dict], dict]:
    providers = tuple(PRESS_YAHOO_PROVIDERS)
    found, seen = [], set()
    for cat in PRESS_YAHOO_CATEGORIES:
        for page in (1, 2):
            rows = _list_yahoo(cat, page, providers)
            for r in rows:
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                found.append(r)
    articles = []
    for r in pick_articles(found, limit):
        html = get_text(r["url"], timeout=20)
        if not html:
            continue
        art = parse_yahoo_article(html, r["url"], provider=r["provider"],
                                  source=PRESS_YAHOO_PROVIDERS.get(r["provider"]))
        if not art:
            continue
        if len(art["body"]) > PRESS_BODY_MAX:
            art["body"] = art["body"][:PRESS_BODY_MAX] + "…"
            art["partial"] = True
        if not art.get("timestamp") and r.get("time"):
            art["timestamp"] = r["time"]
        art["provider"] = r["provider"]
        art["headline"] = _clean_title(art["headline"])
        if any(_norm(x["headline"]) == _norm(art["headline"]) for x in articles):
            continue   # 差替で同じ記事が2本載ることがある
        articles.append(art)
    counts: dict[str, int] = {}
    for r in found:
        counts[r["provider"]] = counts.get(r["provider"], 0) + 1
    print(f"    {'✅' if articles else '⚠️ '} 他社配信記事(Yahoo): 一覧 {len(found)} 件 {counts} / 本文 {len(articles)} 件")
    return articles, counts


# ==================== まとめ ====================
def fetch_all(now: datetime | None = None) -> dict:
    """報道・公的機関・海外の素材をまとめて返す。どれが落ちても残りは返す。"""
    now = now or datetime.now(JST)
    groups: dict[str, list[dict]] = {"official": [], "press": [], "overseas": []}
    status = []
    for feed in PRESS_FEEDS:
        res = get(feed["url"], timeout=25)
        items = parse_rss(res.content) if res is not None else []
        picked = select_feed_items(items, feed, now)
        ok = bool(items)
        status.append({"key": feed["key"], "label": feed["label"], "kind": feed["kind"],
                       "ok": ok, "fetched": len(items), "kept": len(picked)})
        mark = "✅" if ok else "⚠️ "
        print(f"    {mark} {feed['label']}: 取得 {len(items)} / 採用 {len(picked)}")
        groups[feed["kind"]].extend(picked)
    for k in groups:
        groups[k] = _dedupe(sorted(groups[k], key=lambda r: r["published"], reverse=True))

    articles, counts = fetch_articles()
    status.append({"key": "yahoo_press", "label": "他社配信記事（Yahoo!ファイナンス）", "kind": "press",
                   "ok": bool(articles), "fetched": sum(counts.values()), "kept": len(articles)})
    return {
        "official": groups["official"],
        "headlines": groups["press"],
        "overseas": groups["overseas"],
        "articles": articles,
        "status": status,
        "ok": any(s["ok"] for s in status),
    }


def tone_titles(press: dict) -> list[str]:
    """温度計の「見出しの論調」に数える見出し。日本語の報道だけ（公的機関の表題と英語は数えない）。"""
    tone_keys = {f["key"] for f in PRESS_FEEDS if f.get("tone")}
    titles = [h["title"] for h in (press or {}).get("headlines") or [] if h.get("source_key") in tone_keys]
    titles += [a.get("headline") for a in (press or {}).get("articles") or []]
    return [t for t in titles if t]
