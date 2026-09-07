"""データ源の疎通診断ツール。

取得が全滅したときに、原因が「相手サイトの遮断」なのか
「こちらのパーサの不具合」なのかを切り分けるために使う。
Actions から「データ源の疎通診断」ワークフローを手動実行する。

分かっていること:
  🚫 株探 / stooq / Yahoo Finance(米) / みんかぶ / トレーダーズ … 拒否 or 404
  ✅ CNBC / Yahoo!ファイナンス(日本) / TDnet / 日経新聞 / 日経インデックス
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}


def get(url, timeout=40):
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:
        print(f"    ❌ 例外: {type(e).__name__}: {str(e)[:100]}")
        return None


def dump(label, url, max_tables=8, rows=4):
    print(f"\n=== {label} ===\n    {url}")
    r = get(url)
    if r is None:
        return None
    body = r.text or ""
    blocked = any(x in body[:2500] for x in ("Human Verification", "verify your browser"))
    print(f"    status={r.status_code} len={len(body)}" + ("  ※bot対策" if blocked else ""))
    if r.status_code != 200 or blocked:
        print(f"    先頭: {body[:120]}")
        return None
    soup = BeautifulSoup(body, "html.parser")
    t = soup.find("title")
    print(f"    title: {t.get_text(strip=True) if t else '—'}")
    tables = soup.find_all("table")
    print(f"    table 数: {len(tables)}")
    for i, tbl in enumerate(tables[:max_tables]):
        heading = ""
        for prev in tbl.find_all_previous(["h1", "h2", "h3", "h4", "caption"], limit=12):
            txt = prev.get_text(strip=True)
            if txt and 2 <= len(txt) <= 26:
                heading = txt
                break
        trs = tbl.find_all("tr")
        print(f"    ── table{i} 「{heading}」 行数={len(trs)}")
        for tr in trs[:rows]:
            cells = []
            for c in tr.find_all(["th", "td"]):
                parts = [x.strip() for x in c.stripped_strings]
                cells.append(parts if len(parts) > 1 else (parts[0] if parts else ""))
            print(f"         {str(cells)[:185]}")
    codes = re.findall(r"[/=](\d{3}[0-9A-Z])(?:\.T|\b)", body)
    if codes:
        u = list(dict.fromkeys(codes))
        print(f"    コードらしき文字列: {len(u)}種 例 {u[:12]}")
    return body


print(f"python {sys.version.split()[0]}")

print("\n########## 1. 日経225の構成銘柄（ヒートマップ用） ##########")
dump("日経公式 構成銘柄", "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225", rows=5)
dump("日経公式 寄与度", "https://indexes.nikkei.co.jp/nkave/topic/contribution?idx=nk225")
dump("日経 銘柄一覧", "https://www.nikkei.com/markets/kabu/nidxprice/")

print("\n########## 2. 業種別の騰落率 ##########")
for label, url in [
    ("Yahoo 業種一覧", "https://finance.yahoo.co.jp/stocks/sectors"),
    ("Yahoo 業種別ランキング",
     "https://finance.yahoo.co.jp/stocks/ranking/industry?market=all&term=daily"),
    ("日経 業種別", "https://www.nikkei.com/markets/kabu/gyoshu/"),
    ("日経 東証業種別指数", "https://www.nikkei.com/markets/kabu/japanidx/"),
]:
    dump(label, url, max_tables=4, rows=5)
