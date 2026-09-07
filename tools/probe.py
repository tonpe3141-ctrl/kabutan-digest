"""データ源の疎通診断ツール。

取得が全滅したときに、原因が「相手サイトの遮断」なのか
「こちらのパーサの不具合」なのかを切り分けるために使う。
Actions から「データ源の疎通診断」ワークフローを手動実行する。
"""
import json
import re
import sys

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}
CNBC = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        "?symbols={syms}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")


def get(url, timeout=35):
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:
        print(f"    ❌ 例外: {type(e).__name__}: {str(e)[:90]}")
        return None


print(f"python {sys.version.split()[0]}")
print("########## 1. CNBC で日本の個別株が取れるか（ヒートマップ用） ##########")
for fmt in ("{c}.T-JP", "{c}-JP", "{c}.T", "{c}.TO", "{c}"):
    syms = [fmt.format(c=c) for c in ("7203", "9984", "8035", "285A")]
    r = get(CNBC.format(syms="|".join(syms)))
    if r is None or r.status_code != 200:
        print(f"  ❌ 形式 {fmt:<10} status={r.status_code if r else 'なし'}")
        continue
    try:
        qs = r.json()["FormattedQuoteResult"]["FormattedQuote"]
    except Exception as e:
        print(f"  ❌ 形式 {fmt:<10} 解析失敗 {e}")
        continue
    ok = [q for q in qs if q.get("last") not in (None, "")]
    print(f"  {'✅' if ok else '❌'} 形式 {fmt:<10} 取得 {len(ok)}/{len(syms)}")
    for q in qs[:4]:
        print(f"       {q.get('symbol'):<12} {str(q.get('name'))[:22]:<24} "
              f"last={q.get('last')!s:>10} chg%={q.get('change_pct')!s:>9} cur={q.get('currencyCode')}")

print("\n########## 2. Yahoo のランキング種別を一覧から拾う ##########")
r = get("https://finance.yahoo.co.jp/stocks/ranking/up?market=all&term=daily")
if r and r.status_code == 200:
    slugs = sorted(set(re.findall(r"/stocks/ranking/([A-Za-z]+)", r.text)))
    print(f"  見つかった種別 {len(slugs)}件: {slugs}")
else:
    print("  ❌ 一覧を取得できません")

print("\n########## 3. 日経のマーケット配下のページ一覧 ##########")
r = get("https://www.nikkei.com/markets/kabu/")
if r and r.status_code == 200:
    paths = sorted(set(re.findall(r"/markets/kabu/([a-z0-9_]+)/", r.text)))
    print(f"  {len(paths)}件: {paths}")
else:
    print(f"  status={r.status_code if r else 'なし'}")

print("\n########## 4. 東証33業種の候補 ##########")
for label, url in [
    ("日経 業種別株価", "https://www.nikkei.com/markets/kabu/gyoshubetsu/"),
    ("日経 東証業種別", "https://www.nikkei.com/markets/kabu/tosho33/"),
    ("JPX 指数一覧", "https://www.jpx.co.jp/markets/indices/line-up/index.html"),
]:
    r = get(url, timeout=25)
    print(f"  {'✅' if r is not None and r.status_code == 200 else '❌'} {label:<16} "
          f"status={r.status_code if r else 'なし'}"
          + (f" len={len(r.text)}" if r is not None else ""))
