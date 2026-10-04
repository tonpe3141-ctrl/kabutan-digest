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
_DATE_LINE = re.compile(r"^\[\d{4}年\d{1,2}月\d{1,2}日\]$")
_PAYWALL = ("続きをお読みいただくには", "この記事は有料会員", "VIP倶楽部の登録が必要")

# 本文を読む価値の高い記事の型（上ほど優先）。ランキング羅列系は後回し
ARTICLE_PRIORITY = [r"日経平均 大引け", r"マ[－ー]ケット日報", r"東京株式（大引け）", r"東京株式（前引け）",
                    r"日経平均 前引け", r"【業種】騰落ランキング", r"明日の株式相場", r"ストップ高／ストップ安",
                    r"投資部門別", r"上方修正|増額修正", r"決算速報", r"サプライズ決算|好悪材料|決算発表予定",
                    r"増資・売り出し", r"信用規制", r"PTS"]


def parse_yahoo_list(html: str, providers=(PROVIDER,)) -> list[dict]:
    """ニュース一覧から、指定した配信元の記事だけを {url, title, time, provider} で返す。

    リンクのテキストは「見出し 時刻(9:15 または 9/26) 配信元」の順に並ぶ。
    配信元は末尾で照合する（見出しの中に媒体名が出てきても取り違えない）。
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/news/detail/" not in href:
            continue
        block = a.get_text(" ", strip=True)
        provider = next((p for p in providers if block.endswith(p)), None)
        if not provider:
            continue
        title = block[: -len(provider)].strip()
        m = re.search(r"\s(\d{1,2}[:/]\d{1,2})\s*$", title)
        time_txt = m.group(1) if m else None
        title = re.sub(r"\s\d{1,2}[:/]\d{1,2}\s*$", "", title).strip()
        if href.startswith("/"):
            href = YAHOO_BASE + href
        out.append({"url": href, "title": title, "time": time_txt, "provider": provider})
    return out


def _list_yahoo(category: str, page: int, providers=(PROVIDER,)) -> list[dict]:
    html = get_text(YAHOO_LIST.format(category=category, page=page), timeout=20)
    if not html:
        return []
    return parse_yahoo_list(html, providers)


def parse_yahoo_article(html: str, url: str, provider: str = PROVIDER,
                        source: str | None = None) -> dict | None:
    """Yahoo!ファイナンスの記事ページから見出し・時刻・本文を取り出す。

    <article> のテキストは
      見出し / 時刻 / 配信 / 現在値 / (銘柄名・株価・前日比 の繰り返し) / 本文… /
      関連ニュース… / 最終更新: … / 配信元
    の順に並ぶ。株価の引用ブロックと末尾の定型部分を落として本文だけ残す。
    有料会員向けの記事（時事通信など）は「続きをお読みいただくには」で切り、partial=True を付ける。
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
    # 株探以外（トレーダーズ・ウェブの指標の一覧・FF 金利の織り込み度・要人発言など）は短い行の表が本文なので、
    # 「現在値」ブロックが無ければ「配信」の次の行から読む（長い行まで飛ばすと表の頭と発言者が欠ける）
    if provider != PROVIDER:
        sent = next((i for i, l in enumerate(lines[1:5], start=1) if l == "配信"), None)
        if sent is not None and "現在値" not in lines[sent + 1: sent + 3]:
            start = sent + 1
    body_lines = []
    partial = False
    for l in lines[start:]:
        if l.startswith(("関連ニュース", "最終更新", "【一緒によく見られる銘柄】")):
            break
        if l.startswith(_PAYWALL):
            partial = True
            break
        body_lines.append(l)
    # 末尾の定型（"[2026年9月17日]" / "株探ニュース（minkabu PRESS）" / "提供：〇〇社" / 配信元名）を落とす
    while body_lines and (body_lines[-1].startswith((provider, "提供：", "提供:"))
                          or _DATE_LINE.match(body_lines[-1])):
        body_lines.pop()
    # トレーダーズ・ウェブは末尾に担当者の名字だけの行（「越後」「岩間」）が付く
    if provider.startswith("トレーダーズ") and len(body_lines) > 1 and re.fullmatch(r"[一-鿿]{1,4}", body_lines[-1]):
        body_lines.pop()
    body = "\n".join(body_lines).strip()
    if not body:
        return None
    out = {"headline": headline, "timestamp": time_txt, "body": body, "url": url,
           "source": f"{source or provider}（Yahoo!ファイナンス配信）",
           "category": "株探" if provider == PROVIDER else provider}
    if partial:
        out["partial"] = True
    return out


def fetch_articles(categories=("market", "stocks"), max_pages: int = 3,
                   limit: int = 15) -> list[dict]:
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
    # 読む価値の高い型を先に、同じ型なら一覧の順（新しい順）で
    def _prio(r):
        return next((i for i, p in enumerate(ARTICLE_PRIORITY) if re.search(p, r["title"])), len(ARTICLE_PRIORITY))
    found.sort(key=_prio)
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


# ==================== 3. 東証33業種の騰落（株探「【業種】騰落ランキング」記事） ====================
SECTORS33 = [
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学", "医薬品",
    "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "機械",
    "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業", "陸運業", "海運業",
    "空運業", "倉庫・運輸関連業", "情報・通信業", "卸売業", "小売業", "銀行業",
    "証券、商品先物取引業", "保険業", "その他金融業", "不動産業", "サービス業",
]
_SECTOR_ALT = "|".join(re.escape(n).replace("、", "[、・]") for n in
                       sorted(SECTORS33, key=len, reverse=True)) + r"|証券業|倉庫・運輸業"
# 上位銘柄の名前には符号付きの数値（次の業種の率）を含めない。行が連結されているため
_SECTOR_ROW = re.compile(r"(" + _SECTOR_ALT + r")[\s　]+([+\-−]?\d+\.\d+)[\s　]*"
                         r"((?:[^<\n+\-−]{1,20}<[0-9]{3}[0-9A-Z]>、?)*)")
_STOCK_TAG = re.compile(r"([^<、\n+\-−]{1,20})<([0-9]{3}[0-9A-Z])>")
_SUMMARY = re.compile(r"値上がり[：:]\s*(\d+)\s*業種[\s　]+値下がり[：:]\s*(\d+)\s*業種")
_PRIME = re.compile(r"東証プライム[：:]\s*(\d+)銘柄[\s　]+値上がり[：:]\s*(\d+)\s*銘柄[\s　]+値下がり[：:]\s*(\d+)")


def parse_sector_ranking(body: str) -> dict | None:
    """「本日の【業種】騰落ランキング」の本文から東証33業種の前日比率を取り出す。

    行の形: 海運業　　+3.09　商船三井<9104>、川崎汽<9107>、郵船<9101>その他製品　+2.88　…
    業種名の直後に率、その後に上位3銘柄が続く。行が連結されているので業種名で区切る。
    """
    if not body:
        return None
    rows = []
    for m in _SECTOR_ROW.finditer(body):
        name = m.group(1).replace("、", "・")
        if name == "証券業":
            name = "証券・商品先物取引業"
        if name == "倉庫・運輸業":
            name = "倉庫・運輸関連業"
        try:
            pct = float(m.group(2).replace("−", "-"))
        except ValueError:
            continue
        leaders = [{"name": n.strip("　 "), "code": c} for n, c in _STOCK_TAG.findall(m.group(3) or "")]
        rows.append({"sector": name, "change_pct": pct, "leaders": leaders[:3]})
    if len(rows) < 20:
        return None
    seen, uniq = set(), []
    for r in rows:
        if r["sector"] in seen:
            continue
        seen.add(r["sector"])
        uniq.append(r)
    uniq.sort(key=lambda r: r["change_pct"], reverse=True)
    out = {"rows": uniq, "source": "株探ニュース（東証33業種、Yahoo!ファイナンス配信）"}
    m = _SUMMARY.search(body)
    if m:
        out["up"], out["down"] = int(m.group(1)), int(m.group(2))
    m = _PRIME.search(body)
    if m:
        out["prime"] = {"total": int(m.group(1)), "up": int(m.group(2)), "down": int(m.group(3))}
    return out


def sectors33_from_articles(articles: list[dict], session: str | None = None) -> dict | None:
    """記事一覧から業種騰落ランキング記事を探して解析する。session は "大引け"/"前引け" 等。"""
    for a in articles or []:
        h = a.get("headline") or ""
        if "【業種】騰落ランキング" not in h:
            continue
        if session and session not in h:
            continue
        parsed = parse_sector_ranking(a.get("body") or "")
        if parsed:
            parsed["headline"] = h
            parsed["url"] = a.get("url")
            parsed["timestamp"] = a.get("timestamp")
            return parsed
    return None


# ==================== 4. 銘柄ごとのニュース欄（決算から読む） ====================
# Yahoo!ファイナンスの銘柄ページのニュース欄には、その銘柄の株探【決算速報】（数字の要約と「よく比較される銘柄」）、
# 時事通信（DZH 個別株情報＝なぜ動いたか）、ウエルスアドバイザーの記事が新しい順に並ぶ。ページには東証33業種も載る。
STOCK_NEWS_URL = "https://finance.yahoo.co.jp/quote/{code}.T/news"
_INDUSTRY_RE = re.compile(r'industryName\\?"\s*:\s*\\?"([^"\\]{2,20})')
_PEERS_RE = re.compile(r"とよく比較される銘柄[：:](.+?)(?:※|\n|$)")
_PEER_TAG = re.compile(r"([^<、\s]{1,20})<([0-9]{3}[0-9A-Z])>")
_SCHED_DATE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日\s*(?:[―─－-]{2,}|の決算発表)")
_SCHED_CODE = re.compile(r"<([0-9]{3}[0-9A-Z])>\s*([^\s<\[［　]{1,20})")
_SCHED_END = re.compile(r"合計\s*\d+\s*社|●「株探」")


def parse_stock_news(html: str, providers) -> dict:
    """銘柄ページのニュース欄から、業種と、許可リストの配信元の記事一覧を返す。"""
    m = _INDUSTRY_RE.search(html or "")
    return {"industry": m.group(1) if m else None,
            "items": parse_yahoo_list(html or "", tuple(providers))}


def fetch_stock_news(code: str, providers) -> dict | None:
    html = get_text(STOCK_NEWS_URL.format(code=code), timeout=20)
    return parse_stock_news(html, providers) if html else None


def fetch_article(url: str, provider: str = PROVIDER, source: str | None = None) -> dict | None:
    html = get_text(url, timeout=20)
    return parse_yahoo_article(html, url, provider, source) if html else None


def parse_peers(body: str) -> list[dict]:
    """株探【決算速報】の末尾「〇〇とよく比較される銘柄：Ａ<1234>、Ｂ<5678>…」を読む。"""
    m = _PEERS_RE.search(body or "")
    if not m:
        return []
    return [{"name": n.strip("　 、"), "code": c} for n, c in _PEER_TAG.findall(m.group(1))]


def flash_body(body: str) -> str:
    """【決算速報】の本文から、数字の段落だけを残す（配信元の定型・比較銘柄・注意書きを落とす）。"""
    keep = []
    for line in (body or "").split("\n"):
        if line.startswith(("株探ニュース", "※")) or "とよく比較される銘柄" in line:
            break
        keep.append(line)
    return "\n".join(keep).strip()


def parse_schedule(body: str, today) -> dict[str, dict]:
    """株探の決算発表予定（「来週の決算発表予定」「サプライズ決算」の後半）から {コード: {date, name}} を読む。

    日付の見出し（「●10月 5日―――」「10月5日の決算発表銘柄（予定）」）の後ろに並ぶ <コード>銘柄名 を、その日の予定とする。
    日付の見出しより前（その日に発表済みの銘柄）は読まない。年は今日から決める（年末に翌年1月の予定が来れば翌年）。
    """
    out: dict[str, dict] = {}
    text = body or ""
    end = _SCHED_END.search(text)
    if end:
        text = text[:end.start()]
    marks = list(_SCHED_DATE.finditer(text))
    for i, m in enumerate(marks):
        month, day = int(m.group(1)), int(m.group(2))
        year = today.year + (1 if month < today.month - 6 else 0)
        try:
            d = today.replace(year=year, month=month, day=day).isoformat()
        except ValueError:
            continue
        seg = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        for code, name in _SCHED_CODE.findall(seg):
            out[code] = {"date": d, "name": name}
    return out
