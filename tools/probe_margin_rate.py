"""Yahoo の信用残の時系列を続けて読んだときに、どこで何が返るか（間隔ごと）。コミットしない診断。"""
import json
import sys
import time
from statistics import mean

import requests

sys.path.insert(0, ".")
from dashboard.http import HEADERS                  # noqa: E402
from dashboard.sources import margin as MS          # noqa: E402

o = json.load(open("docs/data/cache/ohlc.json"))


def tv(s):
    xs = [x[3] * x[4] * 100 / 1e8 for x in s[-60:] if x and x[3]]
    return mean(xs) if xs else 0.0


codes = sorted(o["stocks"], key=lambda c: -tv(o["stocks"][c]))[:80]
sess = requests.Session()
sess.headers.update(HEADERS)
for gap, part in ((0.5, codes[:40]), (1.5, codes[40:80])):
    print(f"\n=== 間隔 {gap}秒 ===")
    stat = {}
    for i, c in enumerate(part):
        t0 = time.time()
        try:
            r = sess.get(MS.URL.format(code=c), timeout=20)
            code, n = r.status_code, len(MS.parse_history(r.text)) if r.status_code == 200 else 0
            if r.status_code != 200 and stat.get(r.status_code, 0) == 0:
                print(f"    {c}: status {r.status_code} headers={dict(list(r.headers.items())[:8])} 先頭={r.text[:200]!r}")
            elif r.status_code == 200 and n == 0:
                print(f"    {c}: 200 だが表なし len={len(r.text)} 'テーブル' {r.text.count('信用残時系列のテーブル')} 先頭={r.text[:120]!r}")
        except Exception as e:                       # noqa: BLE001
            code, n = f"例外 {type(e).__name__}", 0
        stat[code] = stat.get(code, 0) + 1
        print(f"  {i:2d} {c} {code} 週{n} {time.time() - t0:.1f}s")
        time.sleep(gap)
    print(f"  集計: {stat}")
print("完了R")
