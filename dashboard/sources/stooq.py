"""stooq.com から日足 CSV を取得して、直近値・前日比・スパークライン用系列を作る。

日足履歴を取る理由:
  スナップショット API と違い、前日終値・数日分のトレンド・スパークラインを
  1 リクエストで同時に賄えるため。レート的にも安い。
"""
from datetime import date, timedelta

from ..http import get_text

CSV_URL = "https://stooq.com/q/d/l/?s={symbol}&d1={d1}&d2={d2}&i=d"
LOOKBACK_DAYS = 45          # 直近 ~30 営業日ぶんを確保する
SERIES_POINTS = 20          # スパークラインに使う点数


def _parse_csv(text: str) -> list[dict]:
    rows = []
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2 or not lines[0].lower().startswith("date"):
        return rows
    header = [h.strip().lower() for h in lines[0].split(",")]
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != len(header):
            continue
        rec = dict(zip(header, parts))
        try:
            rows.append({"date": rec["date"], "close": float(rec["close"])})
        except (KeyError, ValueError):
            continue
    return rows


def fetch_symbol(symbol: str, today: date | None = None) -> dict | None:
    """1 シンボルの日足を取得する。失敗時 None。"""
    today = today or date.today()
    # 週末や休場をまたいでも直近営業日が拾えるよう、終端は数日先まで取る
    d1 = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    d2 = (today + timedelta(days=2)).strftime("%Y%m%d")
    text = get_text(CSV_URL.format(symbol=symbol, d1=d1, d2=d2))
    if not text:
        return None
    rows = _parse_csv(text)
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r["date"])
    last, prev = rows[-1], rows[-2]
    change = last["close"] - prev["close"]
    change_pct = (change / prev["close"] * 100) if prev["close"] else None
    series = [r["close"] for r in rows[-SERIES_POINTS:]]
    return {
        "symbol": symbol,
        "last": last["close"],
        "prev": prev["close"],
        "change": change,
        "change_pct": change_pct,
        "asof": last["date"],
        "series": series,
    }


def fetch_many(specs: list[dict], today: date | None = None) -> dict:
    """config の定義リストを受け取り {key: 値} を返す。取れなかったキーは入らない。"""
    out = {}
    for spec in specs:
        data = fetch_symbol(spec["symbol"], today)
        if data is None:
            print(f"    ⚠️  {spec['label']} ({spec['symbol']}) 取得失敗")
            continue
        out[spec["key"]] = {**{k: v for k, v in spec.items() if k != "symbol"}, **data}
        print(f"    ✅ {spec['label']}: {data['last']:,.2f} ({data['change_pct']:+.2f}%)")
    return out


def fetch_first_available(symbols: list[str], today: date | None = None) -> dict | None:
    """候補シンボルを順に試し、最初に取れたものを返す（日経先物の呼称ゆれ対策）。"""
    for sym in symbols:
        data = fetch_symbol(sym, today)
        if data is not None:
            return data
    return None
