"""夜間の値動きの材料が取れるかの実測（GitHub Actions 上で実行。コミットしない。DESIGN.md 30章）。

  python tools/probe_overnight.py

CNBC のクォート API（exthrs=1）が、先物の候補の表記（@ND.1・@NQ.1・@SP.1・@NK.1 …）と、米国の株・ETF の時間外
（ExtendedMktQuote）を返すか、時刻と前日比の基準がどうなっているかを生のまま出す。どの表記が返るかで
config.OVERNIGHT_FUTURES の候補の順を直す。AI・半導体の報道のフィード（tech・cnbc_tech）に届くかも出す。
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.config import OVERNIGHT_FUTURES, OVERNIGHT_NAMES, PRESS_FEEDS   # noqa: E402
from dashboard.http import get                                                  # noqa: E402
from dashboard.sources import cnbc, press                                       # noqa: E402

KEYS = ("symbol", "name", "last", "change", "change_pct", "previous_day_closing", "last_time", "curmktstatus")


def raw_quotes(symbols):
    res = get(cnbc.ENDPOINT.format(syms="|".join(symbols)))
    if res is None:
        print("    届かない")
        return []
    try:
        return res.json()["FormattedQuoteResult"]["FormattedQuote"]
    except (ValueError, KeyError, TypeError):
        print(f"    解釈できない: {res.text[:200]}")
        return []


print("=== 先物の候補（クォート API） ===")
syms = [s for f in OVERNIGHT_FUTURES for s in f["symbols"]]
for i in range(0, len(syms), cnbc.BATCH):
    for q in raw_quotes(syms[i:i + cnbc.BATCH]):
        print("  ", {k: q.get(k) for k in KEYS})

print("\n=== 時間外（ExtendedMktQuote） ===")
names = [n["symbol"] for n in OVERNIGHT_NAMES]
for i in range(0, len(names), cnbc.BATCH):
    for q in raw_quotes(names[i:i + cnbc.BATCH]):
        ext = q.get("ExtendedMktQuote")
        print("  ", q.get("symbol"), q.get("last"), q.get("last_time"), q.get("curmktstatus"),
              "| ext:", json.dumps(ext, ensure_ascii=False)[:300] if ext else "なし")

print("\n=== 日経平均先物の日足（bars API。最後の3本） ===")
end = date.today() + timedelta(days=1)
for sym in ("@NK.1", "@NKD.1", "@NIY.1"):
    bars = cnbc.fetch_ohlc(sym, end - timedelta(days=10), end)
    print(f"   {sym}: {bars[-3:] if bars else '取れず'}")

print("\n=== AI・半導体の報道 ===")
for feed in PRESS_FEEDS:
    if feed["key"] not in ("tech", "cnbc_tech"):
        continue
    res = get(feed["url"], timeout=25)
    items = press.parse_rss(res.content) if res is not None else []
    print(f"   {feed['label']}: {len(items)} 件")
    for it in items[:8]:
        print(f"     - {it['title'][:80]} | {it.get('source_host')} | {it.get('published')}")
