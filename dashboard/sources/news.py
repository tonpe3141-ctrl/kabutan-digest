"""Yahoo!ファイナンス「マーケットAIトピックス」から相場振り返り記事を取得する。

株探の話題株ピックアップに相当するものとして、Yahoo!ファイナンスの
"今日の市場はどう動いた？" 記事群（/news/ai-market/detail/{id}）を使う。
見出し・更新時刻・影響銘柄・本文が <article> 内にそのままテキストで
入っており、JSON埋め込みではないので単純な抽出で済む。

ここで作るのは「素材」であり、解釈や示唆はここでは書かない
（それは AI_ANALYSIS_TASK.md に従う後段のLLMの仕事）。
"""
import re

from bs4 import BeautifulSoup

from ..http import get_text

TOP_URL = "https://finance.yahoo.co.jp/"
DETAIL_URL = "https://finance.yahoo.co.jp/news/ai-market/detail/{id}"
ID_RE = re.compile(r"/news/ai-market/detail/(\d+)")


def _list_ids(limit: int) -> list[str]:
    html = get_text(TOP_URL, timeout=20)
    if not html:
        return []
    ids = list(dict.fromkeys(ID_RE.findall(html)))   # 順序を保って重複除去
    return ids[:limit]


def _parse_article(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if not article:
        return None
    lines = [l.strip() for l in article.get_text("\n", strip=True).split("\n") if l.strip()]
    if lines and lines[0] == "マーケットAIトピックス":
        lines = lines[1:]
    if len(lines) < 3:
        return None

    category = lines[0]
    headline = lines[1]
    timestamp = lines[2] if "更新" in lines[2] else None
    body_lines = lines[3:] if timestamp else lines[2:]
    body = "\n".join(body_lines)   # 影響銘柄タグと本文をまとめて渡す（後段のLLMが読む）

    return {"category": category, "headline": headline, "timestamp": timestamp,
            "body": body, "url": url, "source": "Yahoo!ファイナンス マーケットAIトピックス"}


def fetch_market_topics(limit: int = 6) -> list[dict]:
    """直近の「今日の市場はどう動いた？」記事を取得する。"""
    ids = _list_ids(limit)
    if not ids:
        print("    ⚠️  マーケットAIトピックス: 一覧を取得できません")
        return []

    articles = []
    for aid in ids:
        url = DETAIL_URL.format(id=aid)
        html = get_text(url, timeout=20)
        if not html:
            continue
        art = _parse_article(html, url)
        if art:
            articles.append(art)

    print(f"    {'✅' if articles else '⚠️ '} マーケットAIトピックス: {len(articles)} 件"
          + (f"（最新: {articles[0]['headline']}）" if articles else ""))
    return articles
