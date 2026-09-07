"""データ源の疎通診断ツール。

取得が全滅したときに、原因が「相手サイトの遮断」なのか
「こちらのパーサの不具合」なのかを切り分けるために使う。
Actions から「データ源の疎通診断」ワークフローを手動実行する。

分かっていること:
  🚫 株探 / stooq / Yahoo Finance(米) / みんかぶ / トレーダーズ … 拒否 or 404
  ✅ CNBC / Yahoo!ファイナンス(日本) / TDnet / 日経新聞
  ⚠️ Yahoo の tradingValue は 400（そのランキング種別は存在しない）
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


def dump_all_tables(label, url, max_tables=14):
    """ページ内の全テーブルを、直前の見出しとあわせて出す。"""
    print(f"\n=== {label} ===\n    {url}")
    r = get(url)
    if r is None:
        return
    print(f"    status={r.status_code} len={len(r.text)}")
    if r.status_code != 200:
        return
    soup = BeautifulSoup(r.text, "html.parser")
    t = soup.find("title")
    print(f"    title: {t.get_text(strip=True) if t else '—'}")

    for i, tbl in enumerate(soup.find_all("table")[:max_tables]):
        # 直前の見出しを遡って探す
        heading = ""
        for prev in tbl.find_all_previous(["h1", "h2", "h3", "h4", "caption", "span"], limit=25):
            txt = prev.get_text(strip=True)
            if txt and 2 <= len(txt) <= 24:
                heading = txt
                break
        rows = tbl.find_all("tr")[:3]
        print(f"    ── table{i}  見出し候補: 「{heading}」  行数={len(tbl.find_all('tr'))}")
        for tr in rows:
            cells = []
            for c in tr.find_all(["th", "td"]):
                parts = [x.strip() for x in c.stripped_strings]
                cells.append(parts if len(parts) > 1 else (parts[0] if parts else ""))
            print(f"         {str(cells)[:190]}")


print(f"python {sys.version.split()[0]}")
print("########## 1. 日経のランキングページ 全テーブル ##########")
dump_all_tables("日経 ランキングトップ", "https://www.nikkei.com/markets/ranking/")
dump_all_tables("日経 売買代金(推測URL)", "https://www.nikkei.com/markets/ranking/page/?bd=baibaidaikin")

print("\n########## 2. Yahoo のランキング種別スラッグ探索 ##########")
for slug in ("turnover", "amount", "tradingvalue", "tradingValue", "value",
             "dealValue", "tradingValueHigh", "volume", "up"):
    url = f"https://finance.yahoo.co.jp/stocks/ranking/{slug}?market=all&term=daily"
    r = get(url, timeout=25)
    if r is None:
        continue
    title = ""
    if r.status_code == 200:
        m = re.search(r"<title>([^<]+)</title>", r.text)
        title = m.group(1) if m else ""
    print(f"  {'✅' if r.status_code == 200 else '❌'} {slug:<18} status={r.status_code}  {title}")
