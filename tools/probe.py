"""データ源の疎通診断ツール。

取得が全滅したときに、原因が「相手サイトの遮断」なのか
「こちらのパーサの不具合」なのかを切り分けるために使う。
Actions から「データ源の疎通診断」ワークフローを手動実行する。

分かっていること:
  🚫 株探 / stooq / Yahoo Finance(米) / みんかぶ … クラウドIPを拒否
  ✅ CNBC / Yahoo!ファイナンス(日本) / TDnet
"""
import re
import sys

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}

# 売買代金ランキングの取得元候補
TURNOVER = [
    ("Yahoo tradingValue",
     "https://finance.yahoo.co.jp/stocks/ranking/tradingValue?market=all&term=daily"),
    ("Yahoo tradingValue(パラメータなし)",
     "https://finance.yahoo.co.jp/stocks/ranking/tradingValue"),
    ("Yahoo tradingValue(プライム)",
     "https://finance.yahoo.co.jp/stocks/ranking/tradingValue?market=tokyoPrime&term=daily"),
    ("Yahoo 出来高(比較用)",
     "https://finance.yahoo.co.jp/stocks/ranking/volume?market=all&term=daily"),
    ("日経 売買代金",
     "https://www.nikkei.com/markets/ranking/page/?bd=uriagedaka"),
    ("トレーダーズ 売買代金",
     "https://www.traders.co.jp/market_jp/ranking/trading_value"),
    ("みんかぶ 売買代金",
     "https://minkabu.jp/financial_item_ranking/trading_value"),
    ("かぶたん(参考)",
     "https://kabutan.jp/warning/trading_value_ranking"),
]


def show(label, url, timeout=45):
    print(f"\n=== {label} ===\n    {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:
        print(f"    ❌ 例外: {type(e).__name__}: {str(e)[:110]}")
        return
    body = r.text or ""
    blocked = any(x in body[:2500] for x in
                  ("Human Verification", "requires JavaScript to verify", "Just a moment"))
    print(f"    status={r.status_code} len={len(body)}" + ("  ※bot対策" if blocked else ""))
    if r.status_code != 200 or blocked:
        print(f"    先頭: {body[:130]}")
        return
    soup = BeautifulSoup(body, "html.parser")
    t = soup.find("title")
    print(f"    title: {t.get_text(strip=True) if t else '—'}")
    for i, tbl in enumerate(soup.find_all("table")[:2]):
        for j, tr in enumerate(tbl.find_all("tr")[:4]):
            cells = tr.find_all(["th", "td"])
            detail = []
            for c in cells:
                parts = [x.strip() for x in c.stripped_strings]
                detail.append(parts if len(parts) > 1 else (parts[0] if parts else ""))
            print(f"       t{i}r{j}: {str(detail)[:200]}")
    codes = re.findall(r"/quote/(\d{3}[0-9A-Z])\.T", body)
    if codes:
        print(f"    銘柄リンク: {list(dict.fromkeys(codes))[:10]}")


if __name__ == "__main__":
    print(f"python {sys.version.split()[0]}\n########## 売買代金ランキングの取得元 ##########")
    for label, url in TURNOVER:
        show(label, url)
