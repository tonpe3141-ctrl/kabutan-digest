"""信用残が先の値動きに効くかの研究（GitHub Actions 上で実行。コミットしない。DESIGN.md 26章）。

  python tools/research_margin.py [銘柄数] [ページ数]

Yahoo!ファイナンスの信用残の時系列（/quote/{code}.T/history?styl=margin）から、売買代金の大きい順に銘柄ごとの週の残高を取り、
日足キャッシュ（docs/data/cache/ohlc.json）と突き合わせて、公表日（残高の日付の翌週の火曜。先読みしない）の引けから
次の20営業日の対市場（全銘柄の中央値との差）を、信用残の形ごとに数える。前半・後半に分ける。境目は先に決めて動かさない
（supply.py の DAYS_HEAVY・RATIO_LONG・RATIO_SHORT・JUMP と、4週の買残 ±10%・株価 −5%）。
"""
import json
import os
import sys
from datetime import date, timedelta
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import http as HTTP                  # noqa: E402
from dashboard import supply as SU                  # noqa: E402
from dashboard.sources import margin as MS          # noqa: E402

GAP = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
HTTP._MIN_INTERVAL["finance.yahoo.co.jp"] = GAP     # 続けて100回ほど読むと届かなくなったので、間隔を空ける

N_STOCKS = int(sys.argv[1]) if len(sys.argv) > 1 else 150
PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 5
H = 20

o = json.load(open("docs/data/cache/ohlc.json"))
dates = o["dates"]
stocks = o["stocks"]
idx = {d: i for i, d in enumerate(dates)}
mk = SU.market_fwd(dates, stocks, H)


def tv(s):
    xs = [x[3] * x[4] * 100 / 1e8 for x in s[-60:] if x and x[3]]
    return mean(xs) if xs else 0.0


codes = sorted(stocks, key=lambda c: -tv(stocks[c]))[:N_STOCKS]
print(f"銘柄 {len(codes)}（売買代金の大きい順）・ページ {PAGES}・日足 {dates[0]}〜{dates[-1]}")


def pub_index(d: str):
    """残高の日付 d（金曜）の公表日＝翌週の火曜（休場なら次の営業日）の、日足の位置。"""
    t = date.fromisoformat(d) + timedelta(days=4)
    for _ in range(7):
        if t.isoformat() in idx:
            return idx[t.isoformat()]
        t += timedelta(days=1)
    return None


groups: dict[str, list] = {}
n_weeks = n_ok = n_split = n_fail = 0
first_date = None
for k, code in enumerate(codes):
    got = MS.fetch_history(code, pages=PAGES)
    if got is None:
        n_fail += 1
        print(f"  {k}: {code} 届かない（{n_fail}回目）")
    raw = sorted(got or [], key=lambda x: x["date"])
    if not raw:
        continue
    hist, splits = SU.adjust_splits(raw)          # 分割の前の週を今の株数にそろえる（日足は分割を調整済み）
    n_ok += 1
    n_split += len(splits)
    s = stocks[code]
    first_date = min(first_date or hist[0]["date"], hist[0]["date"])
    for j, w in enumerate(hist):
        t = pub_index(w["date"])
        if t is None or t + H >= len(dates) or mk[t] is None or not s[t] or not s[t + H] or not s[t][3] or not s[t + H][3]:
            continue
        ex = (s[t + H][3] / s[t][3] - 1) * 100 - mk[t]
        vols = [x[4] * 100 for x in s[max(0, t - 19): t + 1] if x and x[4]]
        buy, sell = w.get("buy"), w.get("sell")
        if not buy or len(vols) < 15:
            continue
        n_weeks += 1
        days = buy / mean(vols)
        ratio = buy / sell if sell else None
        prev = hist[j - 1] if j else None
        jump = (buy / prev["buy"] - 1) * 100 if prev and prev.get("buy") else None
        base = hist[j - 4] if j >= 4 else None
        bw = (buy / base["buy"] - 1) * 100 if base and base.get("buy") else None
        tb = pub_index(base["date"]) if base else None
        pw = (s[t][3] / s[tb][3] - 1) * 100 if tb is not None and s[tb] and s[tb][3] else None
        tags = ["all"]
        tags.append("days>=5" if days >= SU.DAYS_HEAVY else "days1-5" if days >= 1 else "days<1")
        if ratio is not None:
            tags.append("ratio>=5" if ratio >= SU.RATIO_LONG else "ratio<1" if ratio < SU.RATIO_SHORT else "ratio1-5")
        if jump is not None:
            tags.append("jump>=30" if jump >= SU.JUMP else "jump<=-20" if jump <= -20 else "jump-mid")
        if bw is not None and pw is not None:
            if bw >= 10 and pw <= -5:
                tags.append("trap")
            elif bw <= -10 and pw <= -5:
                tags.append("flush")
            elif bw >= 10 and pw >= 5:
                tags.append("chase")
        half = w["date"]
        for g in tags:
            groups.setdefault(g, []).append((half, ex))
    if (k + 1) % 25 == 0:
        print(f"  {k + 1}銘柄 取れた {n_ok}・週 {n_weeks}")

allw = sorted(d for v in groups.get("all", []) for d in [v[0]])
split = allw[len(allw) // 2] if allw else None
print(f"\n取れた銘柄 {n_ok}（届かなかった {n_fail}）・週 {n_weeks}・分割の補正 {n_split}回・最古の残高 {first_date}・前半は {split} まで。次の{H}営業日の対市場（%）")


def agg(xs):
    if len(xs) < 20:
        return f"n{len(xs):5d}"
    xs = sorted(xs)
    return f"n{len(xs):5d} 平均{mean(xs):+6.2f} 中央{median(xs):+6.2f} 勝{100 * sum(1 for x in xs if x > 0) / len(xs):3.0f}%"


for g in ["all", "days<1", "days1-5", "days>=5", "ratio<1", "ratio1-5", "ratio>=5", "jump<=-20", "jump-mid", "jump>=30",
          "trap", "flush", "chase"]:
    xs = groups.get(g, [])
    a = [x for d, x in xs if d < split]
    b = [x for d, x in xs if d >= split]
    print(f"  {g:10s} 前半 {agg(a)} ｜ 後半 {agg(b)}")
print("\n完了")
