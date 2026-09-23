"""日足の長期履歴が取れるかの実測（GitHub Actions 上で実行）。

相場温度計（25日線乖離・騰落レシオ・52週位置・逆張りの検証）には
数か月分の日足が要る。自前の履歴は溜まるまで時間がかかるので、
取れる公開エンドポイントがあるかを確かめる。

  python tools/probe_history.py
"""
import json
import re
import sys
from datetime import datetime, timedelta

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.9"}


def get(url):
    try:
        return requests.get(url, headers=H, timeout=25)
    except Exception as e:
        print(f"    例外: {e}")
        return None


def show(label, url, parse=None):
    print(f"\n=== {label} ===\n    {url}")
    r = get(url)
    if r is None:
        return
    print(f"    status={r.status_code} len={len(r.text)} type={r.headers.get('content-type')}")
    if r.status_code != 200:
        print(f"    先頭: {r.text[:200]!r}")
        return
    if parse:
        try:
            parse(r)
        except Exception as e:
            print(f"    解析失敗: {e}; 先頭: {r.text[:300]!r}")
    else:
        print(f"    先頭: {r.text[:400]!r}")


def cnbc_bars(r):
    data = r.json()
    bars = (data.get("barData") or {}).get("priceBars") or []
    print(f"    本数={len(bars)}")
    for b in bars[:2] + bars[-2:]:
        print(f"      {b}")


def yahoo_hist(r):
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", r.text, re.S)
    if not m:
        print("    __PRELOADED_STATE__ なし")
        return
    st = json.loads(m.group(1))
    print(f"    トップレベル: {list(st.keys())[:30]}")

    def walk(o, path="", depth=0):
        if depth > 5:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and \
                        any(x in v[0] for x in ("closePrice", "close", "baseDatetime", "date")):
                    print(f"    候補 {path}.{k}: {len(v)} 行 先頭={json.dumps(v[0], ensure_ascii=False)[:300]}")
                walk(v, f"{path}.{k}", depth + 1)
    walk(st)


end = datetime.utcnow()
start = end - timedelta(days=400)
s, e = start.strftime("%Y%m%d000000"), end.strftime("%Y%m%d000000")
print(f"python {sys.version.split()[0]}")
for sym in (".N225", ".TOPX", "JPY=", "US10Y", "@CL.1", ".SOX", ".VIX", "7203.T"):
    show(f"CNBC bars {sym}",
         f"https://ts-api.cnbc.com/harmony/app/bars/{sym}/1D/{s}/{e}/adjusted/EST5EDT.json", cnbc_bars)
show("CNBC charts 1Y .N225", "https://ts-api.cnbc.com/harmony/app/charts/1Y.json?symbol=.N225", cnbc_bars)
show("Yahoo!ファイナンス 日経平均 時系列", "https://finance.yahoo.co.jp/quote/998407.O/history", yahoo_hist)
show("Yahoo!ファイナンス トヨタ 時系列", "https://finance.yahoo.co.jp/quote/7203.T/history", yahoo_hist)


def nikkei_per(r):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup.find_all("table")[:3]:
        rows = [[c.get_text(strip=True) for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]
        print(f"    表 {len(rows)} 行: {rows[:3]} … {rows[-2:]}")


# 業績の軸: 日経平均の PER（→ 予想EPS = 指数 / PER）
show("日経 PER（当月）", "https://indexes.nikkei.co.jp/nkave/archives/data?list=per", nikkei_per)
show("日経 PER（指定月）", "https://indexes.nikkei.co.jp/nkave/archives/data?list=per&year=2026&month=6", nikkei_per)
# 国内金利・恐怖指数の候補
for sym in ("JP10Y", "JP2Y", ".NKVI", ".JNIV", "@NK.1", "NIY.1", ".TOPX.ELEC"):
    show(f"CNBC bars {sym}",
         f"https://ts-api.cnbc.com/harmony/app/bars/{sym}/1D/{s}/{e}/adjusted/EST5EDT.json", cnbc_bars)
# 取れる期間の上限
show("CNBC bars .N225 3年", f"https://ts-api.cnbc.com/harmony/app/bars/.N225/1D/"
     f"{(end - timedelta(days=1100)).strftime('%Y%m%d000000')}/{e}/adjusted/EST5EDT.json", cnbc_bars)
