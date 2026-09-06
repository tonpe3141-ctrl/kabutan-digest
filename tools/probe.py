"""データ源の疎通診断。GitHub Actions のランナー上から実行して、
どのエンドポイントが使えるかを実測するための使い捨てスクリプト。

  python tools/probe.py
"""
import json
import sys
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}

TARGETS = [
    ("stooq 日足CSV(日付指定)", "https://stooq.com/q/d/l/?s=^spx&d1=20260801&d2=20260908&i=d"),
    ("stooq 日足CSV(全期間)",   "https://stooq.com/q/d/l/?s=^spx&i=d"),
    ("stooq スナップショット",  "https://stooq.com/q/l/?s=^spx&f=sd2t2ohlcv&h&e=csv"),
    ("stooq com→pl",           "https://stooq.pl/q/d/l/?s=^spx&i=d"),
    ("Yahoo chart ^GSPC",      "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1mo&interval=1d"),
    ("Yahoo chart ^SOX",       "https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?range=1mo&interval=1d"),
    ("Yahoo chart NKD=F(日経先物)", "https://query1.finance.yahoo.com/v8/finance/chart/NKD%3DF?range=1mo&interval=1d"),
    ("Yahoo chart ^N225",      "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225?range=1mo&interval=1d"),
    ("Yahoo chart JPY=X",      "https://query1.finance.yahoo.com/v8/finance/chart/JPY%3DX?range=1mo&interval=1d"),
    ("Yahoo chart ^TNX",       "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=1mo&interval=1d"),
    ("Yahoo chart XLK",        "https://query1.finance.yahoo.com/v8/finance/chart/XLK?range=1mo&interval=1d"),
    ("Yahoo query2 ^GSPC",     "https://query2.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1mo&interval=1d"),
    ("株探 日経平均",           "https://kabutan.jp/stock/?code=0000"),
    ("株探 売買代金ランキング",  "https://kabutan.jp/warning/trading_value_ranking"),
    ("株探 ニュース一覧",       "https://kabutan.jp/news/marketnews/"),
]


def probe(label: str, url: str) -> None:
    try:
        res = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    except Exception as e:
        print(f"  ❌ {label}\n       例外: {type(e).__name__}: {e}")
        return

    body = res.text or ""
    ctype = res.headers.get("content-type", "")
    print(f"  {'✅' if res.status_code == 200 else '❌'} {label}")
    print(f"       status={res.status_code}  len={len(body)}  type={ctype}")
    if res.history:
        print(f"       リダイレクト: {' → '.join(str(h.status_code) for h in res.history)} → {res.url[:90]}")
    snippet = body[:220].replace("\n", " | ")
    print(f"       先頭: {snippet}")

    # Yahoo の JSON なら、実際に終値が取り出せるかまで確認する
    if "finance.yahoo.com" in url and res.status_code == 200:
        try:
            d = json.loads(body)
            r = d["chart"]["result"][0]
            closes = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
            ts = r["timestamp"]
            print(f"       → 終値 {len(closes)} 点 / 最新 {closes[-1]:.2f} / "
                  f"通貨 {r['meta'].get('currency')} / 取引所 {r['meta'].get('exchangeName')}")
        except Exception as e:
            print(f"       → JSON 解析に失敗: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"requests {requests.__version__} / python {sys.version.split()[0]}\n")
    for label, url in TARGETS:
        probe(label, url)
        print()
