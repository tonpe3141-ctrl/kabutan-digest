"""相場温度計の試運転（GitHub Actions 上で実データを取り、結果を表示するだけ。コミットはしない）。

  python tools/probe_thermo.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import store, thermo_run   # noqa: E402

latest = store.load_latest()
slots = latest.get("slots") or {}
slot_src = next((s for s in ("taibike", "zenba") if s in slots), None)
payload = json.loads(json.dumps((slots.get(slot_src) or {}).get("data") or {}))
today = store.now_jst().date()
t0 = time.time()
res = thermo_run.run("taibike", today, payload, store.previous_sessions(today, thermo_run.HIST_DAYS))
print(f"\n所要 {time.time() - t0:.0f} 秒（元データ: latest.json の {slot_src}）")
th = json.load(open("docs/data/thermo.json", encoding="utf-8"))
for p in ("docs/data/thermo.json", "docs/data/cache/bars.json", "docs/data/cache/nikkei_per.json"):
    print(f"  {p}: {os.path.getsize(p) / 1024:.0f} KB")
mk = th.get("market") or {}
print(f"\n=== 相場温度 {mk.get('temp')}（{mk.get('zone')}）追い風 {mk.get('tailwind')} 向かい風 {mk.get('headwind')} "
      f"/{mk.get('n')}軸 一致度={mk.get('consensus')} 10日前={mk.get('temp_before')} 兆し={mk.get('turning')}")
for f in mk.get("factors") or []:
    print(f"  {f['label']:>6} {str(f['score']):>4} {f.get('change') or '':>4}  "
          + " / ".join(s['text'] for s in f.get('subs') or []))
bt = th.get("backtest") or {}
print(f"\n=== バックテスト {bt.get('from')}〜{bt.get('to')} 全期間 {bt.get('all')}")
for z in (bt.get("zones") or []) + (bt.get("consensus") or []):
    print("  ", {k: z.get(k) for k in ("zone", "consensus", "days", "episodes", "median20", "up20", "median60", "up60") if k in z})
print("\n=== 業種（上位・過熱）")
for s in (th.get("sectors") or [])[:6]:
    print(f"  {s['sector']:<8} {s['class']:<12} pick={s['pick']} r5={s['r5']} r20={s['r20']} rsi={s['rsi']} macro={(s.get('macro') or {}).get('label')}")
print("  過熱:", [s["sector"] for s in th.get("sectors") or [] if s["class"] == "過熱"])
for k, v in (th.get("lists") or {}).items():
    print(f"\n=== {k} {len(v)}件")
    for r in v[:6]:
        print("  ", {x: r.get(x) for x in ("code", "name", "price", "rsi", "dev25", "r5", "r60", "ma25", "to_ma25", "label", "since")})
print("\n=== テーマ", [(t["theme"], t["label"]) for t in th.get("themes") or [] if t.get("label")])
print("=== 対象", th.get("coverage"))
print("=== 要約", json.dumps(res["summary"], ensure_ascii=False)[:1500])
