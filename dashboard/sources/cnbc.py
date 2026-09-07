"""CNBC のクォート API から相場データを取得する。

なぜ CNBC か:
  GitHub Actions のランナーからは stooq も Yahoo Finance も拒否される。
  実測の結果、CNBC のこのエンドポイントだけが米国指数・日本の指数・
  セクターETF・為替・米金利・商品をまとめて返してくれた。

返す値はすべて整形済み文字列（"7,718.60" "-0.38%" "UNCH"）なので、
数値化はこのモジュールで完結させ、上位には float だけを渡す。
"""
from ..http import get

ENDPOINT = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
            "?symbols={syms}&requestMethod=itv&noform=1&partnerId=2"
            "&fund=1&exthrs=1&output=json")

BATCH = 12          # 1 リクエストあたりのシンボル数


def _num(text) -> float | None:
    """"7,718.60" "+0.022" "-0.38%" "4.784%" → float。"UNCH"/None → None。"""
    if text is None:
        return None
    s = str(text).strip()
    if not s or s.upper() in ("UNCH", "N/A", "--", "---"):
        return None
    s = s.replace(",", "").replace("%", "").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def _quote(raw: dict) -> dict:
    last = _num(raw.get("last"))
    prev = _num(raw.get("previous_day_closing"))
    change = _num(raw.get("change"))
    change_pct = _num(raw.get("change_pct"))

    # 市場が閉じていると change が "UNCH" になる。前日終値から自分で出す。
    if change is None and last is not None and prev is not None:
        change = last - prev

    # CNBC の change_pct は銘柄によって基準がずれることがある（米2年債で実測）。
    # change と前日終値が揃っているときは、必ず自分で計算し直す。
    if change is not None and prev:
        change_pct = change / prev * 100

    return {
        "symbol": raw.get("symbol"),
        "name": raw.get("name"),
        "last": last,
        "prev": prev,
        "change": change,
        "change_pct": change_pct,
        "open": _num(raw.get("open")),
        "high": _num(raw.get("high")),
        "low": _num(raw.get("low")),
        "asof": (raw.get("last_time") or "")[:10] or None,
        "market_status": raw.get("curmktstatus"),
    }


def fetch_symbols(symbols: list[str]) -> dict:
    """シンボルの一覧を {symbol: 値} で返す。取れなかったものは入らない。"""
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        res = get(ENDPOINT.format(syms="|".join(chunk)))
        if res is None:
            print(f"    ⚠️  CNBC 取得失敗: {', '.join(chunk)}")
            continue
        try:
            quotes = res.json()["FormattedQuoteResult"]["FormattedQuote"]
        except (ValueError, KeyError, TypeError):
            print(f"    ⚠️  CNBC 応答を解釈できません: {res.text[:120]}")
            continue
        for raw in quotes:
            q = _quote(raw)
            if q["symbol"] and q["last"] is not None:
                out[q["symbol"]] = q
    return out


def fetch_jp_stocks(codes: list[str]) -> dict:
    """日本の個別銘柄を証券コードで取得する。

    CNBC では東証銘柄を「{コード}.T」で引ける（実測で確認）。
    225銘柄でも 12件ずつのまとめ取りで20リクエスト程度に収まる。
    戻り値は {証券コード: 値}。
    """
    if not codes:
        return {}
    quotes = fetch_symbols([f"{c}.T" for c in codes])
    out = {}
    for code in codes:
        q = quotes.get(f"{code}.T")
        if q is not None:
            out[code] = {**q, "code": code}
    missing = len(codes) - len(out)
    print(f"    {'✅' if not missing else '⚠️ '} 個別銘柄: {len(out)}/{len(codes)} 件取得"
          + (f"（{missing}件は取得できず）" if missing else ""))
    return out


def fetch_spec(specs: list[dict]) -> dict:
    """config の定義リスト（key/symbol/label…）を受け取り {key: 値} を返す。"""
    quotes = fetch_symbols([s["symbol"] for s in specs])
    out = {}
    for spec in specs:
        q = quotes.get(spec["symbol"])
        if q is None:
            print(f"    ⚠️  {spec['label']} ({spec['symbol']}) 取得失敗")
            continue
        out[spec["key"]] = {**{k: v for k, v in spec.items() if k != "symbol"}, **q}
        pct = q["change_pct"]
        print(f"    ✅ {spec['label']}: {q['last']:,.2f}"
              + (f" ({pct:+.2f}%)" if pct is not None else " (前日比なし)"))
    return out
