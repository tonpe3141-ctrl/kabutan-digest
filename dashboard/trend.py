"""時間軸: 業種・テーマの 5日／20日の推移を履歴から組み立てる。

「今日強い業種は 20日でも強いか（トレンド）、逆か（リバウンド）」を1行で言うための層。
履歴には各日の業種平均（sectors）とテーマ上位（theme_top）を残してあり、
その日次の値を単純に累積する（複利ではなく合計。日次の平均騰落率を積んだ近似値）。
"""


def _daily_sector_map(session: dict) -> dict[str, float]:
    """履歴1日分から {業種: 平均騰落率} を取る。大引が無ければ前場を使う。"""
    for slot in ("taibike", "zenba"):
        rows = (session.get(slot) or {}).get("sectors")
        if rows:
            return {r["sector"]: r["avg_pct"] for r in rows if r.get("avg_pct") is not None}
    return {}


def sector_trend(today_sectors: list[dict] | None, history_sessions: list[dict],
                 short: int = 5, long: int = 20) -> dict | None:
    """業種ごとに 当日 / 5日累積 / 20日累積 と、その組み合わせの読みを返す。

    history_sessions は新しい順。当日の値は today_sectors（analyze.sector_performance の出力）。
    """
    if not today_sectors:
        return None
    today = {r["sector"]: r.get("avg_pct") for r in today_sectors if r.get("avg_pct") is not None}
    past = [_daily_sector_map(s) for s in history_sessions]
    past = [p for p in past if p]
    if len(past) < 2:
        return None

    def cum(sector: str, n: int) -> float | None:
        vals = [p[sector] for p in past[:n - 1] if sector in p]
        if not vals:
            return None
        return round(today.get(sector, 0.0) + sum(vals), 2)

    rows = []
    for sector, d0 in today.items():
        c5 = cum(sector, short)
        c20 = cum(sector, long)
        label = None
        if c5 is not None and c20 is not None:
            if d0 > 0 and c5 > 0 and c20 > 0:
                label = "続伸（トレンド）"
            elif d0 > 0 and c20 < 0:
                label = "反発（リバウンド）"
            elif d0 < 0 and c20 > 0:
                label = "押し目（上昇トレンド中の調整）"
            elif d0 < 0 and c5 < 0 and c20 < 0:
                label = "続落"
        rows.append({"sector": sector, "d0": round(d0, 2), "d5": c5, "d20": c20, "label": label})
    rows.sort(key=lambda r: -(r["d5"] if r["d5"] is not None else -999))
    days_available = min(len(past) + 1, long)
    return {"rows": rows, "days": days_available,
            "note": f"直近 {days_available} 営業日の履歴から日次の業種平均を累積した近似値。"
                    "時価総額加重の業種指数とは一致しない。"}


def index_trend(history_sessions: list[dict], today_close: float | None) -> dict | None:
    """日経平均の終値系列（古い順）と 5日／20日の変化率。"""
    closes = []
    for s in reversed(history_sessions):
        nk = ((s.get("taibike") or {}).get("indices") or {}).get("nikkei")
        if nk and nk.get("close") is not None:
            closes.append(nk["close"])
    if today_close is not None:
        closes.append(today_close)
    if len(closes) < 3:
        return None
    last = closes[-1]

    def chg(n):
        if len(closes) <= n:
            return None
        return round((last / closes[-1 - n] - 1) * 100, 2)

    return {"series": closes[-20:], "d5": chg(5), "d20": chg(20)}
