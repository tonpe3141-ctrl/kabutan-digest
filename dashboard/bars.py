"""日足キャッシュ（docs/data/cache/bars.json）。

相場温度計は数か月〜3年の日足を使う。自前の履歴（history/）は溜まるまで時間がかかり、
休場日に前営業日の値が重複して入るので、CNBC の日足（分割調整済み）を別に持つ。

  {"updated_at": "...",
   "macro":  {"nikkei": {"d": ["2023-09-19", ...], "c": [33242.59, ...]}, ...},
   "dates":  ["2026-03-20", ...],            # 個別株の日付軸（東証の営業日、古い順）
   "stocks": {"7203": [2925, 2937, null, ...]}}  # dates と同じ長さ。取れない日は null

個別株の四本値と出来高は別のファイル（docs/data/cache/ohlc.json、約2年）に持つ。短期の押し目買い（swing.py）の
200日線・ATR・売買代金と、場中の安値での損切りの再現に使う。アプリは読まない:

  {"updated_at": "...",
   "dates":  ["2024-09-..", ...],             # 東証の営業日（古い順、STOCK_OHLC_KEEP 本）
   "stocks": {"7203": [[始値, 高値, 安値, 終値, 出来高(100株)], null, ...]}}

四本値を取れるときは、同じ1回の取得から終値（bars.json）も作る（銘柄ごとに2回取りに行かない）。

更新の方針:
  - 毎回は末尾だけ（直近10本ほど）を取り直してつなぐ。
  - つなぎ目の重なりで過去値が 1% 超ずれたら、株式分割などで調整値が変わったとみなして全期間を取り直す。
  - 取得に失敗した系列は前回の値をそのまま使う（収集は止めない）。
"""
import json
import os
from datetime import date, timedelta

from .config import (BARS_PATH, MACRO_BAR_SYMBOLS, MACRO_BARS_CALENDAR_DAYS, OHLC_PATH,
                     STOCK_BARS_CALENDAR_DAYS, STOCK_BARS_KEEP, STOCK_OHLC_CALENDAR_DAYS, STOCK_OHLC_KEEP)

OVERLAP = 8          # つなぎ目で照合する本数
SPLIT_TOL = 0.01     # 過去値のずれがこれを超えたら取り直す


def load(path: str = BARS_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("macro", {})
    data.setdefault("dates", [])
    data.setdefault("stocks", {})
    return data


def _num(v):
    """1円単位の株価は整数で残してファイルを小さくする。"""
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(v, 4)


def save(data: dict, path: str = BARS_PATH) -> None:
    """1系列1行で書く（git の差分が系列の末尾だけで済む）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["{", f'"updated_at":{json.dumps(data.get("updated_at"))},', '"macro":{']
    macro = data.get("macro") or {}
    for i, (k, s) in enumerate(sorted(macro.items())):
        body = json.dumps({"d": s["d"], "c": [_num(x) for x in s["c"]]}, separators=(",", ":"))
        lines.append(f'{json.dumps(k)}:{body}' + ("," if i < len(macro) - 1 else ""))
    lines.append("},")
    lines.append(f'"dates":{json.dumps(data.get("dates") or [])},')
    lines.append('"stocks":{')
    stocks = data.get("stocks") or {}
    for i, (k, arr) in enumerate(sorted(stocks.items())):
        lines.append(f'{json.dumps(k)}:{json.dumps([_num(x) for x in arr], separators=(",", ":"))}'
                     + ("," if i < len(stocks) - 1 else ""))
    lines.append("}}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def merge_series(old: list[tuple[str, float]], new: list[tuple[str, float]]) -> tuple[list, bool]:
    """古い系列に新しい系列をつなぐ。重なりで値がずれていれば (new, True)＝取り直しが要る。"""
    if not old:
        return list(new), False
    if not new:
        return list(old), False
    old_map = dict(old)
    for d, c in new[:OVERLAP]:
        o = old_map.get(d)
        if o and abs(c / o - 1) > SPLIT_TOL:
            return list(new), True
    first_new = new[0][0]
    merged = [(d, c) for d, c in old if d < first_new] + list(new)
    return merged, False


def series(data: dict, key: str) -> list[tuple[str, float]]:
    s = (data.get("macro") or {}).get(key) or {}
    return list(zip(s.get("d") or [], s.get("c") or []))


def update_macro(data: dict, fetch, today: date) -> dict:
    """マクロ系列を更新する。fetch(symbol, start, end) -> [(date, close)] | None"""
    end = today + timedelta(days=1)
    for spec in MACRO_BAR_SYMBOLS:
        key = spec["key"]
        old = series(data, key)
        full = len(old) < 200
        start = (today - timedelta(days=MACRO_BARS_CALENDAR_DAYS) if full
                 else date.fromisoformat(old[-OVERLAP][0]))
        new = fetch(spec["symbol"], start, end)
        if not new:
            print(f"    ⚠️  日足 {spec['label']} 取得失敗（前回の値を使用）")
            continue
        merged, refetch = merge_series(old, new)
        if refetch:
            again = fetch(spec["symbol"], today - timedelta(days=MACRO_BARS_CALENDAR_DAYS), end)
            merged = again or merged
            print(f"    ↻ 日足 {spec['label']} は過去値がずれていたため全期間を取り直し")
        cutoff = (today - timedelta(days=MACRO_BARS_CALENDAR_DAYS + 30)).isoformat()
        merged = [(d, c) for d, c in merged if d >= cutoff]
        data["macro"][key] = {"d": [d for d, _ in merged], "c": [c for _, c in merged]}
    n = len(series(data, "nikkei"))
    print(f"    ✅ マクロ日足: {len(data['macro'])} 系列（日経 {n} 本）")
    return data


def load_ohlc(path: str = OHLC_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("dates", [])
    data.setdefault("stocks", {})
    return data


def save_ohlc(data: dict, path: str = OHLC_PATH) -> None:
    """1銘柄1行で書く。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["{", f'"updated_at":{json.dumps(data.get("updated_at"))},',
             f'"dates":{json.dumps(data.get("dates") or [])},', '"stocks":{']
    stocks = data.get("stocks") or {}
    for i, (k, rows) in enumerate(sorted(stocks.items())):
        body = json.dumps([[_num(x) for x in r] if r else None for r in rows], separators=(",", ":"))
        lines.append(f"{json.dumps(k)}:{body}" + ("," if i < len(stocks) - 1 else ""))
    lines.append("}}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def ohlc_map(ohlc: dict, code: str) -> dict[str, list]:
    rows = (ohlc.get("stocks") or {}).get(code) or []
    return {d: r for d, r in zip(ohlc.get("dates") or [], rows) if r}


def _ohlc_rows(new: list[tuple]) -> dict[str, list]:
    """fetch_ohlc の出力を {日付: [始値, 高値, 安値, 終値, 出来高(100株)]} にする。"""
    return {d: [o, h, lo, c, round((v or 0) / 100)] for d, o, h, lo, c, v in new}


def _fetch_ohlc_merged(code: str, old: dict[str, list], fetch_ohlc, today: date) -> tuple[dict, bool, bool]:
    """1銘柄の四本値を更新する。戻り値は (日付→行, 取れたか, 全期間を取ったか)。"""
    end = today + timedelta(days=1)
    full_start = today - timedelta(days=STOCK_OHLC_CALENDAR_DAYS)
    old_items = sorted(old.items())
    need_full = len(old_items) < STOCK_OHLC_KEEP * 0.6
    start = full_start if need_full else date.fromisoformat(old_items[-min(OVERLAP, len(old_items))][0])
    new = fetch_ohlc(f"{code}.T", start, end)
    if not new:
        return dict(old), False, False
    _, refetch = merge_series([(d, r[3]) for d, r in old_items], [(d, c) for d, _o, _h, _l, c, _v in new])
    if refetch and not need_full:
        new = fetch_ohlc(f"{code}.T", full_start, end) or new
    rows = _ohlc_rows(new)
    if need_full or refetch:
        return rows, True, True
    first = min(rows)
    merged = {d: r for d, r in old.items() if d < first}
    merged.update(rows)
    return merged, True, False


def stock_map(data: dict, code: str) -> dict[str, float]:
    arr = (data.get("stocks") or {}).get(code) or []
    return {d: c for d, c in zip(data.get("dates") or [], arr) if c is not None}


def update_stocks(data: dict, codes: list[str], fetch, today: date, ohlc: dict | None = None,
                  fetch_ohlc=None) -> dict:
    """個別株を更新する。日付軸は日経平均の日足（東証の営業日）の直近 STOCK_BARS_KEEP 本。

    ohlc と fetch_ohlc を渡すと、四本値（直近 STOCK_OHLC_KEEP 本）も同じ取得で更新し、終値はそこから作る。"""
    nk_days = [d for d, _ in series(data, "nikkei")]
    axis = nk_days[-STOCK_BARS_KEEP:]
    if not axis:
        print("    ⚠️  日経平均の日足が無いため個別株の日足は更新しません")
        return data
    if ohlc is not None and fetch_ohlc is not None:
        return _update_with_ohlc(data, codes, today, ohlc, fetch_ohlc, axis, nk_days[-STOCK_OHLC_KEEP:])
    end = today + timedelta(days=1)
    stocks = {}
    ok = full = 0
    for code in codes:
        old = sorted(stock_map(data, code).items())
        need_full = len(old) < STOCK_BARS_KEEP * 0.6
        start = (today - timedelta(days=STOCK_BARS_CALENDAR_DAYS) if need_full
                 else date.fromisoformat(old[-min(OVERLAP, len(old))][0]))
        new = fetch(f"{code}.T", start, end)
        if new:
            merged, refetch = merge_series(old, new)
            if refetch and not need_full:
                merged = fetch(f"{code}.T", today - timedelta(days=STOCK_BARS_CALENDAR_DAYS), end) or merged
            full += need_full or refetch
            ok += 1
        else:
            merged = old
        m = dict(merged)
        if m:
            stocks[code] = [m.get(d) for d in axis]
    data["dates"] = axis
    data["stocks"] = stocks
    print(f"    ✅ 個別株日足: {ok}/{len(codes)} 銘柄更新（うち全期間 {full}）、軸 {len(axis)} 営業日")
    return data


def _update_with_ohlc(data: dict, codes: list[str], today: date, ohlc: dict, fetch_ohlc,
                      axis: list[str], oaxis: list[str]) -> dict:
    stocks, rows_out = {}, {}
    ok = full = 0
    for code in codes:
        rows, got, was_full = _fetch_ohlc_merged(code, ohlc_map(ohlc, code), fetch_ohlc, today)
        ok += got
        full += was_full
        closes = stock_map(data, code)                 # 四本値が取れない銘柄は前回の終値を残す
        closes.update({d: r[3] for d, r in rows.items()})
        if closes:
            stocks[code] = [closes.get(d) for d in axis]
        if rows:
            rows_out[code] = [rows.get(d) for d in oaxis]
    data["dates"], data["stocks"] = axis, stocks
    ohlc["dates"], ohlc["stocks"] = oaxis, rows_out
    print(f"    ✅ 個別株日足（四本値）: {ok}/{len(codes)} 銘柄更新（うち全期間 {full}）、"
          f"軸 {len(axis)}／{len(oaxis)} 営業日")
    return data


CLOSE_TIME = "15:30"     # 東証の大引（2024-11 から）。これ以降の約定のクォートを当日の日足とみなす


def append_today(data: dict, ohlc: dict, today: str, nk_close: float | None, quotes: dict[str, dict]) -> int:
    """大引の時点で CNBC の日足にまだ当日の分が無いとき、引け後のクォートから当日の日足を足す。足した銘柄数を返す。

    大引（16:45）の実行では、日足 API に当日の分が入るのが夜（17:16 では未着・23:12 には入っていた。2026-09 の実測）で、
    そのままでは翌営業日の注文が出ない。引け後のクォートの始値・高値・安値・終値・出来高は、あとで入る日足と一致した
    （2026-09-28 の実測）。最後の約定が当日の 15:30 以降の銘柄だけを使い、日付の軸（日経平均の日足）には当日の終値を足す。
    次に日足を取り直したとき（末尾をつなぐ）に正式な値で上書きされる。日経平均の当日の終値が無ければ何もしない。"""
    nk = (data.get("macro") or {}).get("nikkei")
    if not nk or not nk.get("d") or nk["d"][-1] >= today or not nk_close:
        return 0
    rows = {}
    for code, q in (quotes or {}).items():
        t = q.get("last_time") or ""
        c = q.get("last")
        if t[:10] != today or t[11:16] < CLOSE_TIME or not c or c <= 0:
            continue
        o = q.get("open") if q.get("open") and q["open"] > 0 else c
        h = max(x for x in (q.get("high"), o, c) if x)
        lo = min(x for x in (q.get("low"), o, c) if x and x > 0)
        rows[code] = [o, h, lo, c, round((q.get("volume") or 0) / 100)]
    if not rows:
        return 0
    nk["d"].append(today)
    nk["c"].append(nk_close)
    if data.get("dates") and data["dates"][-1] < today:
        data["dates"].append(today)
        for code, arr in (data.get("stocks") or {}).items():
            arr.append(rows[code][3] if code in rows else None)
    if ohlc.get("dates") and ohlc["dates"][-1] < today:
        ohlc["dates"].append(today)
        for code, arr in (ohlc.get("stocks") or {}).items():
            arr.append(rows.get(code))
    print(f"    ✅ 日足に当日の分がまだ無いため、引け後のクォートから当日の日足を足しました（{len(rows)} 銘柄）")
    return len(rows)


def add_missing_stocks(data: dict, codes: list[str], fetch, today: date, limit: int = 40,
                       ohlc: dict | None = None, fetch_ohlc=None) -> dict:
    """寄り前・前場で、キャッシュに無い銘柄（新しくウォッチリストに入れた等）だけを足す。
    日付軸は変えない（大引で揃え直す）。"""
    axis = data.get("dates") or []
    if not axis:
        return data
    if ohlc is not None and fetch_ohlc is not None:
        added = 0
        for code in codes[:limit]:
            rows, got, _ = _fetch_ohlc_merged(code, {}, fetch_ohlc, today)
            if not got:
                continue
            data.setdefault("stocks", {})[code] = [(rows.get(d) or [None] * 4)[3] for d in axis]
            if ohlc.get("dates"):
                ohlc.setdefault("stocks", {})[code] = [rows.get(d) for d in ohlc["dates"]]
            added += 1
        print(f"    ✅ 個別株日足: 不足 {len(codes)} 銘柄のうち {added} 銘柄を追加（四本値）")
        return data
    end = today + timedelta(days=1)
    added = 0
    for code in codes[:limit]:
        new = fetch(f"{code}.T", today - timedelta(days=STOCK_BARS_CALENDAR_DAYS), end)
        if not new:
            continue
        m = dict(new)
        data.setdefault("stocks", {})[code] = [m.get(d) for d in axis]
        added += 1
    print(f"    ✅ 個別株日足: 不足 {len(codes)} 銘柄のうち {added} 銘柄を追加")
    return data
