"""データ源の疎通診断ツール。

取得が全滅したときに、原因が「相手サイトの遮断」なのか
「こちらのパーサの不具合」なのかを切り分けるために使う。
Actions から「データ源の疎通診断」ワークフローを手動実行する。

分かっていること:
  🚫 株探 / stooq / Yahoo Finance(米) … クラウドIPを拒否
  ✅ CNBC … 米指数・セクターETF・為替・金利・商品すべて取得可
  ✅ Yahoo!ファイナンス(日本) の値上がり率ランキング … table で取得可
  ✅ TDnet 適時開示 … 到達可（文字コードは Shift_JIS）
"""
import json
import re
import sys
import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}
CNBC = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        "?symbols={syms}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")


def get(url, timeout=15, tries=2, encoding=None):
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if encoding:
                r.encoding = encoding
            return r
        except Exception as e:
            if i == tries - 1:
                print(f"    例外: {type(e).__name__}: {str(e)[:90]}")
    return None


print("########## 1. CNBC: 日本の指数と先物 ##########")
r = get(CNBC.format(syms="|".join([".N225", ".TOPX", "@NIY.1", "@NKD.1", ".JPXNK400", ".N225F"])))
if r and r.status_code == 200:
    for q in r.json()["FormattedQuoteResult"]["FormattedQuote"]:
        print(f"  ✅ {q.get('symbol'):<12} {str(q.get('name'))[:30]:<32} "
              f"last={q.get('last')!s:>12} chg={q.get('change')!s:>10} "
              f"chg%={q.get('change_pct')!s:>9} time={q.get('last_time')}")
else:
    print(f"  ❌ status={r.status_code if r else 'なし'}")

print("\n########## 2. CNBC: 1銘柄の全フィールド（項目名の確定） ##########")
for sym in (".SPX", "US10Y", "JPY="):
    r = get(CNBC.format(syms=sym))
    if r and r.status_code == 200:
        q = r.json()["FormattedQuoteResult"]["FormattedQuote"][0]
        keep = {k: v for k, v in q.items()
                if k in ("symbol", "last", "change", "change_pct", "previous_day_closing",
                         "open", "high", "low", "last_time", "last_time_msec",
                         "curmktstatus", "realTime", "provider", "timeZone")}
        print(f"  {sym}: {json.dumps(keep, ensure_ascii=False)}")

print("\n########## 3. Yahoo!ファイナンス ランキングの各種URL ##########")
RANK = [
    ("値上がり率", "https://finance.yahoo.co.jp/stocks/ranking/up?market=all&term=daily"),
    ("値下がり率", "https://finance.yahoo.co.jp/stocks/ranking/down?market=all&term=daily"),
    ("売買代金",   "https://finance.yahoo.co.jp/stocks/ranking/tradingValue?market=all&term=daily"),
    ("出来高",     "https://finance.yahoo.co.jp/stocks/ranking/volume?market=all&term=daily"),
]
for label, url in RANK:
    r = get(url, timeout=20, tries=2)
    if r is None or r.status_code != 200:
        print(f"  ❌ {label}: status={r.status_code if r else 'なし'}")
        continue
    soup = BeautifulSoup(r.text, "html.parser")
    tbl = soup.find("table")
    if not tbl:
        print(f"  ⚠️  {label}: table なし")
        continue
    trs = tbl.find_all("tr")
    print(f"  ✅ {label}: {len(trs)-1} 行")
    for tr in trs[:3]:
        cells = tr.find_all(["th", "td"])
        # セル内が連結されるので、子要素ごとに分解して見る
        detail = []
        for c in cells:
            parts = [s.strip() for s in c.stripped_strings]
            detail.append(parts if len(parts) > 1 else (parts[0] if parts else ""))
        print(f"       {detail}")
    a = tbl.find("a", href=re.compile(r"/quote/"))
    print(f"       コードリンク例: {a['href'] if a else 'なし'}")

print("\n########## 4. Yahoo!ファイナンス 指数ページから価格を拾えるか ##########")
for label, code in (("日経平均", "998407.O"), ("TOPIX", "998405.T"),
                    ("グロース250", "0000A0.T"), ("グロース250(別)", "998430.O")):
    r = get(f"https://finance.yahoo.co.jp/quote/{code}", timeout=20)
    if r is None or r.status_code != 200:
        print(f"  ❌ {label} ({code}): status={r.status_code if r else 'なし'}")
        continue
    soup = BeautifulSoup(r.text, "html.parser")
    t = soup.find("title")
    print(f"  ✅ {label} ({code}): {t.get_text(strip=True) if t else ''}")
    # 価格らしき要素を class 名から探す
    hits = 0
    for el in soup.find_all(attrs={"class": True}):
        cn = " ".join(el.get("class"))
        if re.search(r"(price|Price|StockPrice|number)", cn) and el.get_text(strip=True):
            txt = el.get_text(strip=True)[:24]
            if re.search(r"\d", txt):
                print(f"       class={cn[:44]:<46} text={txt}")
                hits += 1
                if hits >= 6:
                    break
    m = re.search(r'<meta name="description" content="([^"]{0,180})"', r.text)
    if m:
        print(f"       description: {m.group(1)}")

print("\n########## 5. TDnet 適時開示（Shift_JIS） ##########")
for d in ("20260904", "20260903"):
    url = f"https://www.release.tdnet.info/inbs/I_list_001_{d}.html"
    r = get(url, encoding="shift_jis")
    if r is None or r.status_code != 200:
        print(f"  ❌ {d}: status={r.status_code if r else 'なし'}")
        continue
    soup = BeautifulSoup(r.text, "html.parser")
    tbl = soup.find("table", id="main-list-table") or soup.find_all("table")[-1]
    trs = tbl.find_all("tr")
    print(f"  ✅ {d}: table id={tbl.get('id')} 行数={len(trs)}")
    for tr in trs[:5]:
        cells = [c.get_text(strip=True)[:26] for c in tr.find_all("td")]
        if cells:
            print(f"       {cells}")
    txt = r.text
    for w in ("決算短信", "業績予想", "修正"):
        print(f"       {'✅' if w in txt else '❌'} '{w}' を含む: {txt.count(w)} 箇所")
