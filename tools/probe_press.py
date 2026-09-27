"""株探以外の報道・公的機関の情報源が、GitHub Actions のランナーから届くかを実測する。

  python tools/probe_press.py

dashboard/sources/press.py をそのまま動かし、情報源ごとの「取得件数 / 採用件数」と
採用した見出しの先頭を出す。取得 0 は遮断か URL の変更、取得あり・採用 0 は
配信元の照合（hosts）や鮮度の窓で落ちている。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.sources import press  # noqa: E402


def main() -> int:
    p = press.fetch_all()
    print("\n=== 情報源ごとの結果 ===")
    for s in p["status"]:
        print(f"  {'✅' if s['ok'] else '❌'} {s['label']:<28} 取得 {s['fetched']:>3} / 採用 {s['kept']:>3}")
    for key, label in (("official", "公的機関"), ("headlines", "報道の見出し"), ("overseas", "海外")):
        rows = p[key]
        print(f"\n=== {label}: {len(rows)} 件 ===")
        for r in rows[:12]:
            print(f"  {r['published'][5:16]} [{r['source']}] {r['title'][:70]}")
    print(f"\n=== 配信記事（本文）: {len(p['articles'])} 本 ===")
    for a in p["articles"]:
        print(f"  [{a.get('provider')}] {a['headline'][:60]}  本文 {len(a['body'])} 字{'（冒頭のみ）' if a.get('partial') else ''}")
    print(f"\n論調に数える見出し: {len(press.tone_titles(p))} 本")
    return 0


if __name__ == "__main__":
    sys.exit(main())
