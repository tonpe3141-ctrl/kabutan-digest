"""市場の内部（25日線より上の銘柄の割合）が端まで偏った日のあとの日経平均（DESIGN.md 38章）。

使い方（自宅回線から。リポジトリの直下で）:
    python tools/research_crash.py ohlc4y --fetch    # 4年の四本値を CNBC から取る（研究_crash と同じ置き場所）
    python tools/research_breadth.py ohlc4y          # 表を出す

形は結果を見る前に決めた: 25日線より上の割合が LOW% 以下に入った日・HIGH% 以上に入った日（10営業日以内の続きは同じ出来事）。
比べる相手は同じ期間のすべての日。前半・後半は期間の真ん中で分ける。今の採用銘柄だけ（生存者の偏り）。
結果（2026-11-23）は dashboard/technical.py の BREADTH_4Y に写した。"""
import glob, json, os, statistics, sys

S = sys.argv[1] if len(sys.argv) > 1 else 'ohlc4y'
LOW, HIGH, GAP, H = 20, 80, 10, 20


def main():
    rows = {os.path.basename(p)[:-5]: json.load(open(p)) for p in glob.glob(S + '/ohlc/*.json')}
    nk = rows.pop('N225')
    days = [r[0] for r in nk]
    idx = {d: i for i, d in enumerate(days)}
    nkc = [r[4] for r in nk]
    above, cnt = [0] * len(days), [0] * len(days)
    for code, rs in rows.items():
        cs = [(r[0], r[4]) for r in rs if r[4]]
        for k in range(24, len(cs)):
            d, c = cs[k]
            if d not in idx:
                continue
            m = sum(x for _, x in cs[k - 24:k + 1]) / 25
            cnt[idx[d]] += 1
            above[idx[d]] += c > m
    p = [round(100 * a / n) if n >= 300 else None for a, n in zip(above, cnt)]
    start = next(i for i, v in enumerate(p) if v is not None)
    mid = days[(start + len(days) - H) // 2]

    def fwd(i):
        return (nkc[i + H] / nkc[i] - 1) * 100 if i + H < len(days) else None

    def events(test):
        out, last = [], -99
        for i in range(start + 1, len(days) - H):
            if p[i] is not None and p[i - 1] is not None and test(p[i]) and not test(p[i - 1]) and i - last >= GAP:
                out.append(i)
                last = i
        return out

    def one(ix):
        v = [fwd(i) for i in ix]
        return {'n': len(v), 'r20': round(statistics.fmean(v), 2), 'p20': round(100 * sum(1 for x in v if x > 0) / len(v))} if v else None
    base = list(range(start, len(days) - H))
    res = {'from': days[start], 'to': days[len(days) - H - 1], 'split': mid, 'low': LOW, 'high': HIGH}
    for k, ix in (('base', base), ('lo', events(lambda v: v <= LOW)), ('hi', events(lambda v: v >= HIGH))):
        res[k] = {'all': one(ix), 'first': one([i for i in ix if days[i] < mid]), 'second': one([i for i in ix if days[i] >= mid])}
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
