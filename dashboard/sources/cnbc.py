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
        "volume": _num(raw.get("volume")),
        "asof": (raw.get("last_time") or "")[:10] or None,
        "last_time": raw.get("last_time") or None,       # 最後の約定の時刻（東証の個別株は引けで "…T15:30:00.000+0900"）
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


# ==================== 日足（相場温度計の材料） ====================
BARS_ENDPOINT = ("https://ts-api.cnbc.com/harmony/app/bars/{sym}/1D/"
                 "{start}000000/{end}000000/adjusted/EST5EDT.json")


def parse_bars(data: dict) -> list[tuple[str, float]]:
    """bars API の応答を [(YYYY-MM-DD, 終値)] の古い順にする。終値 0 以下は捨てる。"""
    out = []
    for b in ((data or {}).get("barData") or {}).get("priceBars") or []:
        t = str(b.get("tradeTime") or "")
        close = _num(b.get("close"))
        if len(t) < 8 or close is None or close <= 0:
            continue
        out.append((f"{t[:4]}-{t[4:6]}-{t[6:8]}", close))
    out.sort()
    # 同じ日付が重なったら後ろ（新しい値）を採る
    dedup: dict[str, float] = {}
    for d, c in out:
        dedup[d] = c
    return sorted(dedup.items())


def parse_ohlc(data: dict) -> list[tuple]:
    """bars API の応答を [(YYYY-MM-DD, 始値, 高値, 安値, 終値, 出来高)] の古い順にする。

    実測（2026-09-27, 手元回線）: 東証個別株は始値・高値・安値・出来高も返る。終値 0 以下の日は捨て、
    始値・高値・安値が欠けた日は終値で埋める（その日の値幅は 0 として扱う）。"""
    out: dict[str, tuple] = {}
    for b in ((data or {}).get("barData") or {}).get("priceBars") or []:
        t = str(b.get("tradeTime") or "")
        close = _num(b.get("close"))
        if len(t) < 8 or close is None or close <= 0:
            continue
        o, h, lo = (_num(b.get(k)) for k in ("open", "high", "low"))
        o = o if o and o > 0 else close
        h = max(x for x in (h, o, close) if x)
        lo = min(x for x in (lo, o, close) if x and x > 0)
        try:
            v = int(float(b.get("volume") or 0))
        except (TypeError, ValueError):
            v = 0
        out[f"{t[:4]}-{t[4:6]}-{t[6:8]}"] = (o, h, lo, close, v)     # 同じ日付は後ろ（新しい値）を採る
    return [(d, *x) for d, x in sorted(out.items())]


def fetch_ohlc(symbol: str, start, end) -> list[tuple] | None:
    """日足の四本値と出来高（分割調整済み）。取れなければ None（例外は投げない）。"""
    url = BARS_ENDPOINT.format(sym=symbol, start=start.strftime("%Y%m%d"),
                               end=end.strftime("%Y%m%d"))
    res = get(url, timeout=20, retries=1)
    if res is None:
        return None
    try:
        return parse_ohlc(res.json())
    except ValueError:
        return None


def fetch_bars(symbol: str, start, end) -> list[tuple[str, float]] | None:
    """日足（分割調整済み）を取る。取れなければ None（例外は投げない）。

    実測（2026-09-23, Actions）: 指数・為替・金利・商品・東証個別株（{コード}.T）で
    3年分まで返る。日本の銘柄の日付は東証の営業日。
    """
    url = BARS_ENDPOINT.format(sym=symbol, start=start.strftime("%Y%m%d"),
                               end=end.strftime("%Y%m%d"))
    res = get(url, timeout=20, retries=1)
    if res is None:
        return None
    try:
        return parse_bars(res.json())
    except ValueError:
        return None
