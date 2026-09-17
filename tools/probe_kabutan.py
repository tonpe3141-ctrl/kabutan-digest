"""株探ニュースの配信先取得と追加ランキングの実測（GitHub Actions 上で実行）。

  python tools/probe_kabutan.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.sources import kabutan_news, yahoojp   # noqa: E402

print("=== 株探 見出し（Google RSS） ===")
heads = kabutan_news.fetch_headlines()
for slot in ("preopen", "zenba", "taibike"):
    picked = kabutan_news.select_headlines(heads, slot)
    print(f"\n--- {slot}: {len(picked)} 件 ---")
    for h in picked[:12]:
        print(f"   {h['published']}  {h['title']}")

print("\n=== 株探 記事本文（Yahoo!ファイナンス配信） ===")
arts = kabutan_news.fetch_articles(max_pages=3, limit=10)
for a in arts:
    print(f"\n--- {a['timestamp']} {a['headline']}\n    {a['url']}\n    本文 {len(a['body'])} 字: {a['body'][:400]!r}")

print("\n=== 追加ランキング ===")
for page in [
    {"key": "ytd_high", "label": "年初来高値更新", "max_rows": 30,
     "urls": [{"url": "https://finance.yahoo.co.jp/stocks/ranking/yearToDateHigh?market=all&term=daily", "label": "年初来高値更新"}]},
    {"key": "vol_surge", "label": "出来高急増", "max_rows": 30,
     "urls": [{"url": "https://finance.yahoo.co.jp/stocks/ranking/volumeIncrease?market=all&term=daily", "label": "出来高急増"}]},
    {"key": "hot", "label": "注目", "max_rows": 30,
     "urls": [{"url": "https://finance.yahoo.co.jp/stocks/ranking/hot?market=all&term=daily", "label": "注目"}]},
]:
    t = yahoojp.fetch_ranking(page)
    for r in t["rows"][:6]:
        print("   ", json.dumps(r, ensure_ascii=False))
    # 生の列構造も見る（パーサの位置読みが合っているか）
    from dashboard.http import get_text
    from bs4 import BeautifulSoup
    html = get_text(page["urls"][0]["url"], timeout=20)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if table:
            ths = [th.get_text(" ", strip=True) for th in table.find_all("th")]
            print("    ヘッダ:", ths)
            trs = table.find_all("tr")
            for tr in trs[1:3]:
                print("    行:", [[t for t in c.stripped_strings] for c in tr.find_all(["th", "td"])])
