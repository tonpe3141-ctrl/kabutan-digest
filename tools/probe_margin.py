"""信用残・需給の材料が取れるかの実測（GitHub Actions 上で実行。コミットしない。DESIGN.md 26章）。

  python tools/probe_margin.py [コード ...]

Yahoo!ファイナンスの信用残の時系列（/quote/{code}.T/history?styl=margin）を読み、表の見出し・週の数・分割の補正を出す。
届かない（相手の遮断）のか、読めない（パーサ）のかを切り分ける。JPX の信用残・空売り・投資部門別の配布ファイルに届くかも出す。
財務省の週次（海外投資家の売り買い）と日銀・FOMC の日程（sources/official.py）が届いて読めるかも出す。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests                                     # noqa: E402
from bs4 import BeautifulSoup                       # noqa: E402

from dashboard import supply as SU                  # noqa: E402
from dashboard.http import HEADERS                  # noqa: E402
from dashboard.sources import margin as MS          # noqa: E402

CODES = sys.argv[1:] or ["5803", "285A", "7203"]


def get(url, timeout=30):
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:                          # noqa: BLE001
        print(f"    例外: {e}")
        return None


for code in CODES:
    url = MS.URL.format(code=code)
    r = get(url)
    print(f"\n=== 信用残の時系列 {code} ===\n    {url} status={getattr(r, 'status_code', None)} len={len(r.text) if r is not None else 0}")
    if r is None or r.status_code != 200:
        continue
    soup = BeautifulSoup(r.text, "html.parser")
    tb = soup.find("table", attrs={"aria-label": re.compile("信用残")})
    print(f"    見出し: {[th.get_text(strip=True) for th in tb.find_all('th')] if tb else '表が無い'}")
    rows = MS.parse_history(r.text)
    print(f"    読めた週: {len(rows)}  新しい順の先頭: {rows[:2]}")
    if not rows and tb is not None:
        print("    ⚠️  表はあるが読めない（見出しの名前が変わった？ sources/margin.py の _COLS）")
    adj, splits = SU.adjust_splits(sorted(rows, key=lambda x: x["date"]))
    print(f"    分割の補正: {splits or 'なし'}")

for label, url in [("JPX 信用取引残高等", "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html"),
                   ("JPX 空売り残高", "https://www.jpx.co.jp/markets/public/short-selling/index.html"),
                   ("JPX 投資部門別", "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html")]:
    r = get(url)
    n = len(re.findall(r'href="[^"]+\.(?:xlsx?|csv|pdf)"', r.text)) if r is not None and r.status_code == 200 else 0
    print(f"\n=== {label} === status={getattr(r, 'status_code', None)} 配布ファイルのリンク {n}")

# 公的機関の一次情報（DESIGN.md 37章）: 財務省の週次・日銀と FOMC の日程。届くかと、読めるか
from dashboard.sources import official as OF       # noqa: E402
r = get(OF.MOF_WEEK)
rows = OF.parse_mof_week(r.content.decode("cp932", "replace")) if r is not None and r.status_code == 200 else []
print(f"\n=== 財務省 週次の対外・対内証券売買 === status={getattr(r, 'status_code', None)} 週 {len(rows)}"
      + (f"（最後 {rows[-1]['end']} 非居住者の株式 {rows[-1]['nr_stock']:+,.0f}億円）" if rows else "  ⚠️ 読めない（列の位置が変わった？ official.MOF_COLS）"))
for label, url, fn in [("日銀 会合の日程", OF.BOJ_SCHEDULE, OF.parse_boj), ("FOMC の日程", OF.FOMC_CALENDAR, OF.parse_fomc)]:
    r = get(url)
    xs = fn(r.text) if r is not None and r.status_code == 200 else []
    print(f"\n=== {label} === status={getattr(r, 'status_code', None)} 回 {len(xs)}" + (f"（最後 {xs[-1]['end']}）" if xs else "  ⚠️ 読めない"))
print("\n完了")
