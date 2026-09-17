"""株探ニュースを、株探本体ではなく配信先から取得する。

株探（kabutan.jp）はクラウドIPを 405 で拒否するため、GitHub Actions からは
本体に届かない。実測で届いた配信先を2つ併用する:

  1. Google ニュース RSS（site:kabutan.jp）
       株探の全記事の「見出しと時刻」が取れる。本文は取れない（リンクは Google の
       リダイレクトで、遷移先は株探本体）。昼刊・夕刊・大引け・ストップ高安・
       レーティング日報などの見出しは、それ自体が要約になっている。
  2. Yahoo!ファイナンスのニュース一覧（/news/market, /news/stocks）
       株探ニュースとして配信された記事は本文まで読める。全記事ではないが、
       増資・売り出し、PTS、決算速報などが載る。

自宅 Mac は不要。ここで作るのは「素材」であり、解釈は後段の LLM が行う。
"""
import re
import warnings
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from ..http import get_text

try:  # RSS を html.parser で読むときの警告を黙らせる
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:  # 古い bs4
    pass

JST = timezone(timedelta(hours=9))

GOOGLE_RSS = "https://news.google.com/rss/search?q=site:kabutan.jp&hl=ja&gl=JP&ceid=JP:ja"
YAHOO_LIST = "https://finance.yahoo.co.jp/news/{category}?page={page}"
YAHOO_BASE = "https://finance.yahoo.co.jp"
PROVIDER = "株探ニュース"

# スロットごとに読みたい見出しの型。順序は表示の優先順
SLOT_PATTERNS = {
    "preopen": [r"話題株ピックアップ【夕刊】", r"大引け", r"今朝の注目ニュース", r"寄り付き直前",
                r"明日の", r"増資・売り出し", r"レーティング日報", r"ストップ高／ストップ安.*引け",
                r"決算速報", r"上方修正", r"PTS", r"注目"],
    "zenba":   [r"話題株ピックアップ【昼刊】", r"前場", r"ストップ高／ストップ安.*前場", r"後場に注目",
                r"寄前", r"今朝の注目ニュース", r"寄り付き直前", r"決算速報", r"上方修正"],
    "taibike": [r"話題株ピックアップ【夕刊】", r"大引け", r"ストップ高／ストップ安.*引け", r"明日の",
                r"増資・売り出し", r"レーティング日報", r"決算速報", r"上方修正", r"PTS", r"本日の"],
}


def _strip_source(title: str) -> str:
    return re.sub(r"\s*[-–]\s*株探\s*$", "", title or "").strip()


# ==================== 1. Google ニュース RSS（見出し） ====================
def fetch_headlines(limit: int = 100) -> list[dict]:
    """株探の記事見出しを新しい順に返す。{title, published, url}"""
    xml = get_text(GOOGLE_RSS, timeout=25)
    if not xml:
        print("    ⚠️  株探見出し(Google RSS): 取得できません")
        return []
    soup = BeautifulSoup(xml, "html.parser")
    out = []
    for it in soup.find_all("item"):
        t = it.find("title")
        if not t:
            continue
        pub = it.find("pubdate") or it.find("pubDate")
        published = None
        if pub:
            try:
                published = parsedate_to_datetime(pub.get_text(strip=True)).astimezone(JST)
            except (TypeError, ValueError):
                published = None
        link = it.find("link")
        url = ""
        if link:
            url = (link.get_text(strip=True) or (link.next_sibling or "")).strip()
        out.append({"title": _strip_source(t.get_text(strip=True)),
                    "published": published.isoformat(timespec="minutes") if published else None,
                    "url": url or None, "source": "株探（見出し）"})
    out.sort(key=lambda x: x["published"] or "", reverse=True)
    print(f"    {'✅' if out else '⚠️ '} 株探見出し(Google RSS): {len(out)} 件")
    return out[:limit]


def select_headlines(items: list[dict], slot: str, since_hours: int = 30,
                     now: datetime | None = None, limit: int = 20) -> list[dict]:
    """スロットに関係する見出しだけを、型の優先順 → 新しい順で返す。"""
    now = now or datetime.now(JST)
    cutoff = now - timedelta(hours=since_hours)
    pats = [re.compile(p) for p in SLOT_PATTERNS.get(slot, [])]
    picked = []
    for it in items:
        if it.get("published"):
            try:
                if datetime.fromisoformat(it["published"]) < cutoff:
                    continue
            except ValueError:
                pass
        rank = next((i for i, p in enumerate(pats) if p.search(it["title"])), None)
        if rank is None:
            continue
        picked.append((rank, it))
    picked.sort(key=lambda x: (x[0], -(x[1].get("published") and 1 or 0)))
    return [it for _, it in picked][:limit]


# ==================== 2. Yahoo!ファイナンス配信の株探記事（本文） ====================
_CODE_LINES = re.compile(r"\n<\n([0-9]{3}[0-9A-Z])\n>\n")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _list_yahoo(category: str, page: int) -> list[dict]:
    html = get_text(YAHOO_LIST.format(category=category, page=page), timeout=20)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/news/detail/" not in href:
            continue
        block = a.get_text(" ", strip=True)
        if PROVIDER not in block:
            continue
        title = block.split(PROVIDER)[0]
        m = re.search(r"\s(\d{1,2}:\d{2})\s*$", title)
        time_txt = m.group(1) if m else None
        title = re.sub(r"\s\d{1,2}:\d{2}\s*$", "", title).strip()
        if href.startswith("/"):
            href = YAHOO_BASE + href
        out.append({"url": href, "title": title, "time": time_txt})
    return out


def parse_yahoo_article(html: str, url: str) -> dict | None:
    """Yahoo!ファイナンスの記事ページから見出し・時刻・本文を取り出す。

    <article> のテキストは
      見出し / 時刻 / 配信 / 現在値 / (銘柄名・株価・前日比 の繰り返し) / 本文… /
      関連ニュース… / 最終更新: … / 配信元
    の順に並ぶ。株価の引用ブロックと末尾の定型部分を落として本文だけ残す。
    """
    soup = BeautifulSoup(html, "html.parser")
    art = soup.find("article")
    if not art:
        return None
    text = art.get_text("\n", strip=True)
    text = _CODE_LINES.sub(r"<\1>", "\n" + text + "\n").strip("\n")
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return None
    headline = lines[0]
    time_txt = next((l for l in lines[1:4] if _TIME_RE.match(l)), None)

    # 本文の開始: 「現在値」ブロック（短い行の連続）を抜けた最初の長い行
    start = 1
    for i, l in enumerate(lines[1:], start=1):
        if len(l) >= 30 or "。" in l:
            start = i
            break
    body_lines = []
    for l in lines[start:]:
        if l.startswith(("関連ニュース", "最終更新", "【一緒によく見られる銘柄】")):
            break
        body_lines.append(l)
    body = "\n".join(body_lines).strip()
    if not body:
        return None
    return {"headline": headline, "timestamp": time_txt, "body": body, "url": url,
            "source": f"{PROVIDER}（Yahoo!ファイナンス配信）", "category": "株探"}


def fetch_articles(categories=("market", "stocks"), max_pages: int = 3,
                   limit: int = 8) -> list[dict]:
    """Yahoo!ファイナンスに配信された株探ニュースの本文を新しい順に返す。"""
    found: list[dict] = []
    seen = set()
    for cat in categories:
        for page in range(1, max_pages + 1):
            rows = _list_yahoo(cat, page)
            if not rows:
                break
            for r in rows:
                if r["url"] in seen:
                    continue
                seen.add(r["url"])
                found.append(r)
    articles = []
    for r in found[:limit]:
        html = get_text(r["url"], timeout=20)
        if not html:
            continue
        art = parse_yahoo_article(html, r["url"])
        if art:
            if not art.get("timestamp") and r.get("time"):
                art["timestamp"] = r["time"]
            articles.append(art)
    print(f"    {'✅' if articles else '⚠️ '} 株探記事(Yahoo配信): 一覧 {len(found)} 件 / 本文 {len(articles)} 件")
    return articles


def fetch_for_slot(slot: str) -> dict:
    """スロット向けの素材をまとめて返す。どちらが取れなくても片方は出す。"""
    headlines = fetch_headlines()
    picked = select_headlines(headlines, slot)
    articles = fetch_articles()
    return {"headlines": picked, "articles": articles,
            "headline_count": len(headlines), "ok": bool(picked or articles)}
