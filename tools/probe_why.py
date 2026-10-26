"""値動きの分解と経済指標の材料が取れるかの実測（GitHub Actions 上で実行。コミットしない。DESIGN.md 31章）。

  python tools/probe_why.py

1. 日経公式のウエートの CSV（構成銘柄のページは Cloudflare の確認画面。CSV は直接取れるか）
2. CNBC の5分足（日経平均・日経平均先物・S&P500先物・ドル円・米10年・アジアの指数）
3. アジア・欧州・規模別の指数のクォート
4. トレーダーズ・ウェブの指標の短信（一覧・本文の配信時刻）と、その解析
5. ロイター（Google ニュースの配信元が www.reuters.com に移ったか）
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import indicators                                     # noqa: E402
from dashboard.config import INTRADAY_SYMBOLS, PRESS_FEEDS, WORLD_INDICES  # noqa: E402
from dashboard.http import get                                       # noqa: E402
from dashboard.sources import cnbc, nikkei225, press                 # noqa: E402

JST = timezone(timedelta(hours=9))
now = datetime.now(JST)

print("=== 1. 日経平均のウエート（CSV） ===")
w = nikkei225.fetch_weights()
if w:
    top = sorted(w["rows"], key=lambda r: -r["weight"])[:5]
    print(f"   {w['date']} 時点・{len(w['rows'])}銘柄。上位: " + "、".join(f"{r['name']} {r['weight']}%" for r in top))

print("\n=== 2. 5分足 ===")
for key, sym in INTRADAY_SYMBOLS.items():
    bars = cnbc.fetch_intraday(sym, (now - timedelta(days=3)).date(), now.date())
    if not bars:
        print(f"   {key} {sym}: 取れず")
        continue
    first = datetime.fromtimestamp(bars[0][0], JST).strftime("%m/%d %H:%M")
    last = datetime.fromtimestamp(bars[-1][0], JST).strftime("%m/%d %H:%M")
    print(f"   {key} {sym}: {len(bars)}本 {first}〜{last} 最後の終値 {bars[-1][4]}")

print("\n=== 3. 海外・規模別のクォート ===")
q = cnbc.fetch_symbols([s["symbol"] for s in WORLD_INDICES])
for s in WORLD_INDICES:
    x = q.get(s["symbol"])
    print(f"   {s['label']} {s['symbol']}: " + (f"{x['last']} {x['change_pct']:+.2f}% {x.get('last_time')}" if x and x.get("change_pct") is not None else "取れず"))

print("\n=== 4. 指標の短信 ===")
found = press.list_rows()
wire = press.pick_wire(found, now)
kinds = {}
for x in wire:
    kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
print(f"   一覧 {len(found)} 行・短信 {len(wire)} 本 {kinds}")
bodies = press.fetch_wire_bodies(wire[:12])
for b in bodies[:6]:
    rows = indicators.parse_result_wire(b) if b["kind"] == "result" else indicators.parse_schedule_wire(b)
    print(f"   {b['published_at']} {b['title'][:50]} → {len(rows)}本: " +
          "、".join(f"{r.get('name')} {r.get('actual') or ''}/{r.get('forecast') or ''}" for r in rows[:3]))

print("\n=== 5. ロイター ===")
for feed in PRESS_FEEDS:
    if feed["key"] != "reuters":
        continue
    res = get(feed["url"], timeout=25)
    items = press.parse_rss(res.content) if res is not None else []
    picked = press.select_feed_items(items, feed, now)
    hosts = {}
    for it in items:
        hosts[it.get("source_host")] = hosts.get(it.get("source_host"), 0) + 1
    print(f"   取得 {len(items)}・採用 {len(picked)} 配信元 {hosts}")
    for p in picked[:5]:
        print(f"     {p['published'][5:16]} {p['title'][:60]}")
