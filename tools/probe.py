"""データ源の疎通・構造診断。GitHub Actions のランナー上で実行する。

診断1・2で判明したこと:
  🚫 株探 / stooq / Yahoo Finance(米) … クラウドIPを拒否
  ✅ CNBC クォートAPI / Yahoo!ファイナンス(日本) / 日経公式 / JPX

診断3では、実際にパーサを書くために必要な「中身の構造」を確認する。
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


def get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        return r
    except Exception as e:
        print(f"    例外: {type(e).__name__}: {e}")
        return None


def cnbc(label, symbols):
    print(f"\n=== CNBC: {label} ===")
    r = get(CNBC.format(syms="|".join(symbols)))
    if r is None or r.status_code != 200:
        print(f"    ❌ status={r.status_code if r else 'なし'}")
        return
    try:
        qs = r.json()["FormattedQuoteResult"]["FormattedQuote"]
    except Exception as e:
        print(f"    ❌ 解析失敗: {e} / {r.text[:200]}")
        return
    got = set()
    for q in qs:
        sym = q.get("symbol")
        got.add(sym)
        print(f"    ✅ {sym:<10} {str(q.get('name'))[:26]:<28} "
              f"last={q.get('last')!s:>12} chg%={q.get('change_pct')!s:>8} "
              f"time={q.get('last_time_msec') and q.get('last_time')} cur={q.get('currencyCode')}")
    for s in symbols:
        if s not in got:
            print(f"    ❌ {s:<10} 該当なし")


def yahoo_page(label, url, want):
    print(f"\n=== Yahoo!ファイナンス: {label} ===")
    print(f"    {url}")
    r = get(url)
    if r is None or r.status_code != 200:
        print(f"    ❌ status={r.status_code if r else 'なし'}")
        return
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title")
    print(f"    title: {title.get_text(strip=True) if title else '—'}")

    # 埋め込み JSON があれば、そこから取るのが最も安定する
    for key in ("__NEXT_DATA__", "__PRELOADED_STATE__", "PRELOADED_STATE"):
        if key in html:
            print(f"    ✅ 埋め込み JSON: {key} あり")
            m = re.search(re.escape(key) + r"\s*=\s*(\{.*?\});?\s*</script>", html, re.S)
            if not m:
                m = re.search(r'id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', html, re.S)
            if m:
                try:
                    d = json.loads(m.group(1))
                    print(f"       top keys: {list(d)[:12]}")
                except Exception as e:
                    print(f"       パース失敗: {e}")
            break
    else:
        print("    ― 埋め込み JSON なし（HTML から拾う必要あり）")

    # 探したい語がページに存在するか
    for w in want:
        print(f"    {'✅' if w in html else '❌'} 文字列 '{w}'")

    tables = soup.find_all("table")
    print(f"    table 数: {len(tables)}")
    for i, t in enumerate(tables[:2]):
        rows = t.find_all("tr")[:3]
        for j, tr in enumerate(rows):
            cells = [c.get_text(strip=True)[:16] for c in tr.find_all(["th", "td"])]
            print(f"       table{i} row{j}: {cells}")

    # リスト形式（table でない）の可能性に備えて、リンク付き銘柄を数える
    codes = re.findall(r'/quote/(\d{4}[A-Z0-9]?)\.T', html)
    if codes:
        uniq = list(dict.fromkeys(codes))
        print(f"    銘柄リンク: {len(uniq)} 件 例: {uniq[:8]}")


def plain(label, url, want=()):
    print(f"\n=== {label} ===")
    print(f"    {url}")
    r = get(url)
    if r is None:
        return
    print(f"    status={r.status_code} len={len(r.text)}")
    if r.status_code != 200:
        print(f"    先頭: {r.text[:150]}")
        return
    soup = BeautifulSoup(r.text, "html.parser")
    t = soup.find("title")
    print(f"    title: {t.get_text(strip=True) if t else '—'}")
    tables = soup.find_all("table")
    print(f"    table 数: {len(tables)}")
    for i, tb in enumerate(tables[:2]):
        for j, tr in enumerate(tb.find_all("tr")[:3]):
            cells = [c.get_text(strip=True)[:18] for c in tr.find_all(["th", "td"])]
            print(f"       table{i} row{j}: {cells}")
    for w in want:
        print(f"    {'✅' if w in r.text else '❌'} 文字列 '{w}'")


if __name__ == "__main__":
    print(f"python {sys.version.split()[0]}")

    cnbc("米国指数", [".SPX", ".IXIC", ".DJI", ".SOX", ".VIX", ".RUT", ".NDX"])
    cnbc("日本指数・先物", [".N225", ".TOPX", "@NIY.1", "@NKD.1", ".JPXNK400"])
    cnbc("セクターETF", ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC", "SMH"])
    cnbc("為替・金利・商品", ["JPY=", "EUR=", "US10Y", "US2Y", "@CL.1", "@GC.1", "DXY="])

    yahoo_page("日経平均", "https://finance.yahoo.co.jp/quote/998407.O", ["終値", "前日比"])
    yahoo_page("TOPIX", "https://finance.yahoo.co.jp/quote/998405.T", ["終値"])
    yahoo_page("グロース250", "https://finance.yahoo.co.jp/quote/998430.T", ["グロース"])
    yahoo_page("個別銘柄 7203", "https://finance.yahoo.co.jp/quote/7203.T", ["トヨタ", "業種"])
    yahoo_page("売買代金ランキング",
               "https://finance.yahoo.co.jp/stocks/ranking/tradingValue?market=all&term=daily", [])
    yahoo_page("値上がり率ランキング",
               "https://finance.yahoo.co.jp/stocks/ranking/up?market=all&term=daily", [])
    yahoo_page("業種別",  "https://finance.yahoo.co.jp/sectors", ["水産", "電気機器"])

    plain("TDnet 適時開示一覧", "https://www.release.tdnet.info/inbs/I_list_001_20260904.html",
          ["決算", "修正"])
    plain("日経公式 日経平均プロフィール",
          "https://indexes.nikkei.co.jp/nkave/index/profile?idx=nk225", ["終値"])
