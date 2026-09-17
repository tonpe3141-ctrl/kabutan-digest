"""候補データ源の疎通診断（GitHub Actions のランナーから実行する）。

目的:
  1. 株探ニュースの配信先（Yahoo!ファイナンス / Yahoo!ニュース / minkabu /
     Google ニュース RSS）のうち、クラウドから届き、見出しと本文が読めるものを見つける
  2. Yahoo!ファイナンスのランキング一覧から、使えるスラッグ（年初来高値・出来高急増・
     業種別など）を洗い出す

  python tools/probe_sources.py
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}


def get(url, timeout=30):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        return r
    except Exception as e:
        print(f"    例外: {e}")
        return None


def show(label, url, link_pat=None, text_pat=None, max_links=40, body_tag=None):
    print(f"\n=== {label} ===\n    {url}")
    r = get(url)
    if r is None:
        return None
    print(f"    status={r.status_code} len={len(r.text)} ctype={r.headers.get('content-type','')[:40]}")
    if r.status_code != 200:
        print(f"    先頭200字: {r.text[:200]!r}")
        return None
    html = r.text
    if "xml" in r.headers.get("content-type", "") or html.lstrip().startswith("<?xml"):
        soup = BeautifulSoup(html, "html.parser")
        items = soup.find_all("item")
        print(f"    RSS item 数: {len(items)}")
        for it in items[:15]:
            t = it.find("title"); l = it.find("link"); d = it.find("pubdate") or it.find("pubDate")
            print(f"      - {t.get_text(strip=True) if t else ''} | {d.get_text(strip=True) if d else ''} | "
                  f"{(l.get_text(strip=True) if l and l.get_text(strip=True) else (l.next_sibling or '')).strip()[:90]}")
        return soup
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    print(f"    title: {title.get_text(strip=True)[:80] if title else '-'}")
    if text_pat:
        n = len(re.findall(text_pat, html))
        print(f"    '{text_pat}' の出現: {n}")
    if link_pat:
        seen = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(link_pat, href) and href not in seen:
                seen.append(href)
                txt = a.get_text(" ", strip=True)[:60]
                print(f"      {href[:100]}  |  {txt}")
                if len(seen) >= max_links:
                    break
        print(f"    リンク {len(seen)} 件（上限 {max_links}）")
    if body_tag:
        el = soup.find(body_tag)
        if el:
            text = el.get_text("\n", strip=True)
            print(f"    <{body_tag}> 文字数={len(text)}\n    ---\n" + text[:700] + "\n    ---")
        else:
            print(f"    <{body_tag}> なし")
    return soup


print(f"python {sys.version.split()[0]}")

# ---- 1. 株探本体（遮断の再確認） ----
show("株探 本体", "https://kabutan.jp/news/marketnews/")

# ---- 2. Yahoo!ファイナンス ニュース一覧: 株探配信の記事を探す ----
soup = show("Yahoo!ファイナンス ニュース一覧", "https://finance.yahoo.co.jp/news/",
            link_pat=r"/news/(detail|[a-z\-]+)/", text_pat="株探", max_links=60)
for cat in ("stocks", "market", "brand", "column"):
    show(f"Yahoo!ファイナンス ニュース/{cat}", f"https://finance.yahoo.co.jp/news/{cat}",
         link_pat=r"/news/detail/", text_pat="株探", max_links=30)

# 一覧から株探らしい記事を1本開く
kabutan_links = []
for s_url in ("https://finance.yahoo.co.jp/news/stocks", "https://finance.yahoo.co.jp/news/market",
              "https://finance.yahoo.co.jp/news/"):
    r = get(s_url)
    if r is None or r.status_code != 200:
        continue
    sp = BeautifulSoup(r.text, "html.parser")
    for a in sp.find_all("a", href=True):
        block = a.get_text(" ", strip=True)
        if "/news/detail/" in a["href"] and ("株探" in block or "【" in block):
            kabutan_links.append((a["href"], block[:80]))
print(f"\n株探らしいリンク候補: {len(kabutan_links)}")
for href, txt in kabutan_links[:10]:
    print(f"   {href}  |  {txt}")
if kabutan_links:
    href = kabutan_links[0][0]
    if href.startswith("/"):
        href = "https://finance.yahoo.co.jp" + href
    show("Yahoo!ファイナンス 記事本文", href, body_tag="article", text_pat="株探")

# ---- 3. Yahoo!ニュース 媒体ページ ----
show("Yahoo!ニュース 株探ニュース媒体ページ", "https://news.yahoo.co.jp/media/kabutan",
     link_pat=r"/articles/", text_pat="株探", max_links=20)

# ---- 4. minkabu ----
show("minkabu ニュース一覧", "https://minkabu.jp/news", link_pat=r"/news/\d+", text_pat="株探", max_links=30)
show("minkabu 株探カテゴリ推定", "https://minkabu.jp/news/kabutan", link_pat=r"/news/\d+", text_pat="株探", max_links=20)
show("minkabu 市況", "https://minkabu.jp/news/search?category=market", link_pat=r"/news/\d+", text_pat="株探", max_links=20)

# ---- 5. Google ニュース RSS（見出しだけでも取れるか） ----
show("Google ニュース RSS site:kabutan.jp", "https://news.google.com/rss/search?q=site:kabutan.jp&hl=ja&gl=JP&ceid=JP:ja")
show("Google ニュース RSS 株探 夕刊", "https://news.google.com/rss/search?q=%E6%A0%AA%E6%8E%A2+%E5%A4%95%E5%88%8A&hl=ja&gl=JP&ceid=JP:ja")

# ---- 6. Yahoo!ファイナンス ランキングのスラッグ一覧 ----
show("Yahoo!ファイナンス ランキング目次", "https://finance.yahoo.co.jp/stocks/ranking/",
     link_pat=r"/stocks/ranking/[A-Za-z]+", max_links=80)
show("Yahoo!ファイナンス ランキング(上昇率)ページ内リンク", "https://finance.yahoo.co.jp/stocks/ranking/up?market=all&term=daily",
     link_pat=r"/stocks/ranking/[A-Za-z]+", max_links=80)
for slug in ("yearToDateHigh", "highPriceUpdate", "volumeIncrease", "tradingValueIncrease",
             "volumeRatio", "newHigh", "highPrice52w", "yearHigh"):
    r = get(f"https://finance.yahoo.co.jp/stocks/ranking/{slug}?market=all&term=daily")
    print(f"    slug {slug}: status={r.status_code if r else 'なし'}")

# ---- 7. 業種別（東証33業種） ----
show("Yahoo!ファイナンス 業種別", "https://finance.yahoo.co.jp/stocks/industry/",
     link_pat=r"/stocks/(industry|ranking)", max_links=40, text_pat="業種")
show("Yahoo!ファイナンス 業種別ランキング推定", "https://finance.yahoo.co.jp/stocks/ranking/industry?market=all&term=daily",
     link_pat=r"/stocks/", max_links=10)
show("JPX 業種別指数", "https://www.jpx.co.jp/markets/indices/line-up/index.html", text_pat="業種")
show("日経 業種別（日経公式）", "https://indexes.nikkei.co.jp/nkave/index/profile?idx=nk225", text_pat="業種")
