"""決算から読む の材料が取れるかの実測（GitHub Actions 上で実行。コミットしない）。

  python tools/probe_earnings.py [YYYYMMDD]

TDnet の開示一覧 → 決算・修正の PDF の会社の説明 → Yahoo!ファイナンスの銘柄ページ（業種・株探の決算速報・比較銘柄）を、
3社だけ読んで中身を出す。PDF が読めない（pypdf・相手の遮断）のか、見出しで切り出せない（パーサ）のかを切り分ける。
"""
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import earnings                      # noqa: E402
from dashboard.config import EARN_PROVIDERS          # noqa: E402
from dashboard.sources import kabutan_news, tdnet   # noqa: E402

if len(sys.argv) > 1:
    day = datetime.strptime(sys.argv[1], "%Y%m%d").date()
else:   # 直近の平日
    day = date.today()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
print(f"=== TDnet 適時開示 {day} ===")
rows = tdnet.fetch_disclosures(day)["rows"]
groups = earnings.prioritize(earnings.group_rows(rows), set())
print(f"    決算・修正の会社: {len(groups)}  PDF の URL つき: {sum(1 for g in groups if g['pdfs'])}")
try:
    import pypdf
    print(f"    pypdf {pypdf.__version__}")
except ImportError:
    print("    ⚠️  pypdf が入っていません（requirements.txt）")

for g in groups[:3]:
    print(f"\n--- {g['code']} {g['name']} {g['time']} {g['kinds']}")
    for kind, url in g["pdfs"].items():
        text = tdnet.fetch_pdf_text(url)
        print(f"    PDF {kind}: {url}  文字数 {len(text or '')}")
        if text:
            ex = tdnet.extract_explanations(text)
            for k in ("reason", "overview", "outlook", "dir"):
                if ex.get(k):
                    print(f"      {k}: {str(ex[k])[:160]!r}")
            if not any(ex.get(k) for k in ("reason", "overview", "outlook")):
                print(f"      ⚠️  会社の説明を切り出せません。先頭: {tdnet.clean_pdf_text(text)[:300]!r}")
    news = kabutan_news.fetch_stock_news(g["code"], EARN_PROVIDERS)
    if not news:
        print("    ⚠️  銘柄ページが取れません")
        continue
    print(f"    業種: {news['industry']}  記事 {len(news['items'])} 本")
    for r in news["items"][:6]:
        print(f"      {r['time']} [{r['provider']}] {r['title']}")
    flash = next((r for r in news["items"] if "決算速報" in r["title"]), None)
    if flash:
        art = kabutan_news.fetch_article(flash["url"]) or {}
        print(f"    決算速報: {kabutan_news.flash_body(art.get('body'))[:200]!r}")
        print(f"    比較銘柄: {kabutan_news.parse_peers(art.get('body'))}")
