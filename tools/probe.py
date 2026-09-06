"""データ源の疎通診断。GitHub Actions のランナー上から実行して、
どのエンドポイントが使えるかを実測するための使い捨てスクリプト。

第1回の診断で、株探・stooq・Yahoo Finance がいずれも
クラウドIPからのアクセスを拒否することが判明した。
第2回では「クラウドから使える代替ソース」を洗い出す。
"""
import json
import sys
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}

TARGETS = [
    # ---- 日本株 ----
    ("株探 トップ",            "https://kabutan.jp/", None),
    ("日経公式 日経平均",       "https://indexes.nikkei.co.jp/nkave/index/profile?idx=nk225", None),
    ("日経公式 アーカイブ",     "https://indexes.nikkei.co.jp/nkave/archives/data", None),
    ("Yahoo!ファイナンス 日経", "https://finance.yahoo.co.jp/quote/998407.O", None),
    ("Yahoo!ファイナンス 個別", "https://finance.yahoo.co.jp/quote/7203.T", None),
    ("みんかぶ 個別",          "https://minkabu.jp/stock/7203", None),
    ("JPX トップ",             "https://www.jpx.co.jp/", None),
    ("トレーダーズ・ウェブ",    "https://www.traders.co.jp/market_jp/indices", None),

    # ---- 米国・グローバル（キー不要） ----
    ("CNBC クォート",
     "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
     "?symbols=.SPX|.IXIC|.DJI|.SOX|.VIX&requestMethod=itv&noform=1&partnerId=2"
     "&fund=1&exthrs=1&output=json", "cnbc"),
    ("Frankfurter 為替(ECB)",  "https://api.frankfurter.app/latest?from=USD&to=JPY", "json"),
    ("exchangerate.host",      "https://api.exchangerate.host/latest?base=USD&symbols=JPY", "json"),
    ("Twelve Data デモ",
     "https://api.twelvedata.com/time_series?symbol=SPX&interval=1day&outputsize=5&apikey=demo", "json"),
    ("Alpha Vantage デモ",
     "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=IBM&apikey=demo", "json"),
    ("FRED(キーなし=到達性確認)",
     "https://api.stlouisfed.org/fred/series/observations?series_id=SP500&file_type=json", None),
    ("Stooq(再確認)",          "https://stooq.com/q/d/l/?s=^spx&i=d", None),
]


def probe(label: str, url: str, kind) -> None:
    try:
        res = requests.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        print(f"  ❌ {label}\n       例外: {type(e).__name__}: {e}\n")
        return

    body = res.text or ""
    # bot 対策ページを本文から判定する（200 でも中身が検証ページのことがある）
    blocked = any(s in body[:2000] for s in
                  ("Human Verification", "requires JavaScript to verify",
                   "Access Denied", "Just a moment"))
    ok = res.status_code == 200 and not blocked
    mark = "✅" if ok else ("🚫" if blocked else "❌")
    print(f"  {mark} {label}")
    print(f"       status={res.status_code} len={len(body)} type={res.headers.get('content-type','')}"
          + ("  ※bot対策ページ" if blocked else ""))
    print(f"       先頭: {body[:170].replace(chr(10), ' | ')}")

    if ok and kind == "json":
        try:
            print(f"       → JSON: {json.dumps(json.loads(body), ensure_ascii=False)[:260]}")
        except Exception as e:
            print(f"       → JSON 解析失敗: {e}")
    if ok and kind == "cnbc":
        try:
            d = json.loads(body)
            qs = d["FormattedQuoteResult"]["FormattedQuote"]
            for q in qs:
                print(f"       → {q.get('symbol'):<8} {q.get('last'):>12} "
                      f"{q.get('change_pct')}  ({q.get('last_time')})")
        except Exception as e:
            print(f"       → 解析失敗: {type(e).__name__}: {e}")
    print()


if __name__ == "__main__":
    print(f"requests {requests.__version__} / python {sys.version.split()[0]}\n")
    for t in TARGETS:
        probe(*t)
