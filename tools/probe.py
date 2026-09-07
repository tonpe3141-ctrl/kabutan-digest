"""データ源の疎通診断ツール。

取得が全滅したときに、原因が「相手サイトの遮断」なのか
「こちらのパーサの不具合」なのかを切り分けるために使う。
Actions から「データ源の疎通診断」ワークフローを手動実行する。

分かっていること:
  🚫 株探 / stooq / Yahoo Finance(米) / みんかぶ / トレーダーズ … 拒否 or 404
  ✅ CNBC / Yahoo!ファイナンス(日本) / TDnet / 日経新聞
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}


def get(url, timeout=35):
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:
        print(f"    ❌ 例外: {type(e).__name__}: {str(e)[:100]}")
        return None


def dump(label, url, max_len=800):
    print(f"\n=== {label} ===\n    {url}")
    r = get(url)
    if r is None:
        return None
    body = r.text or ""
    blocked = any(x in body[:2500] for x in
                  ("Human Verification", "verify your browser", "Access Denied", "Just a moment"))
    print(f"    status={r.status_code} len={len(body)}" + ("  ※bot対策" if blocked else ""))
    if r.status_code != 200 or blocked:
        print(f"    先頭: {body[:150]}")
        return None
    soup = BeautifulSoup(body, "html.parser")
    t = soup.find("title")
    print(f"    title: {t.get_text(strip=True) if t else '—'}")

    # 記事一覧らしきリンクを拾う
    links = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if 8 <= len(text) <= 60:
            links.append((text, a["href"]))
    uniq = []
    seen = set()
    for text, href in links:
        if text in seen:
            continue
        seen.add(text)
        uniq.append((text, href))
    print(f"    リンク候補 {len(uniq)}件（先頭10）:")
    for text, href in uniq[:10]:
        print(f"      ・{text}  → {href[:70]}")

    # 本文らしき段落
    paras = [p.get_text(strip=True) for p in soup.find_all("p")]
    paras = [p for p in paras if len(p) > 40]
    if paras:
        print(f"    本文段落 {len(paras)}件。先頭: {paras[0][:120]}")
    return body


print(f"python {sys.version.split()[0]}")
print("########## 日本株の市況・相場振り返り記事の取得元 ##########")

dump("Yahoo!ニュース 経済", "https://news.yahoo.co.jp/categories/business")
dump("Yahoo!ファイナンス 市況記事一覧",
     "https://finance.yahoo.co.jp/news/marketnews")
dump("Yahoo!ファイナンス トップ", "https://finance.yahoo.co.jp/")
dump("NHK 経済カテゴリ", "https://www3.nhk.or.jp/news/catnew.html")
dump("NHK ビジネス特集トップ", "https://www3.nhk.or.jp/news/word/0000378.html")
dump("Reuters Japan マーケット", "https://jp.reuters.com/markets/japan")
dump("Bloomberg Japan マーケット", "https://www.bloomberg.co.jp/markets")
dump("日経 マーケット速報", "https://www.nikkei.com/markets/kabu/")
dump("共同通信 経済", "https://www.kyodo.co.jp/economy/")
dump("時事通信 経済", "https://www.jiji.com/jc/list?g=eco")
