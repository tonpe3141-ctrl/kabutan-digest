"""日足キャッシュ（docs/data/cache/bars.json）。

相場温度計は数か月〜3年の日足を使う。自前の履歴（history/）は溜まるまで時間がかかり、
休場日に前営業日の値が重複して入るので、CNBC の日足（分割調整済み）を別に持つ。

  {"updated_at": "...",
   "macro":  {"nikkei": {"d": ["2023-09-19", ...], "c": [33242.59, ...]}, ...},
   "dates":  ["2026-03-20", ...],            # 個別株の日付軸（東証の営業日、古い順）
   "stocks": {"7203": [2925, 2937, null, ...]}}  # dates と同じ長さ。取れない日は null

更新の方針:
  - 毎回は末尾だけ（直近10本ほど）を取り直してつなぐ。
  - つなぎ目の重なりで過去値が 1% 超ずれたら、株式分割などで調整値が変わったとみなして全期間を取り直す。
  - 取得に失敗した系列は前回の値をそのまま使う（収集は止めない）。
"""
import json
import os
from datetime import date, timedelta

from .config import (BARS_PATH, MACRO_BAR_SYMBOLS, MACRO_BARS_CALENDAR_DAYS,
                     STOCK_BARS_CALENDAR_DAYS, STOCK_BARS_KEEP)

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


def stock_map(data: dict, code: str) -> dict[str, float]:
    arr = (data.get("stocks") or {}).get(code) or []
    return {d: c for d, c in zip(data.get("dates") or [], arr) if c is not None}


def update_stocks(data: dict, codes: list[str], fetch, today: date) -> dict:
    """個別株を更新する。日付軸は日経平均の日足（東証の営業日）の直近 STOCK_BARS_KEEP 本。"""
    axis = [d for d, _ in series(data, "nikkei")][-STOCK_BARS_KEEP:]
    if not axis:
        print("    ⚠️  日経平均の日足が無いため個別株の日足は更新しません")
        return data
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


def add_missing_stocks(data: dict, codes: list[str], fetch, today: date, limit: int = 40) -> dict:
    """寄り前・前場で、キャッシュに無い銘柄（新しくウォッチリストに入れた等）だけを足す。
    日付軸は変えない（大引で揃え直す）。"""
    axis = data.get("dates") or []
    if not axis:
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
