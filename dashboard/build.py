"""スロット単位でデータを収集・分析し、docs/data/latest.json を更新する。

  python -m dashboard.build --slot preopen     # 07:00 JST
  python -m dashboard.build --slot zenba       # 12:00 JST
  python -m dashboard.build --slot taibike     # 17:30 JST
  python -m dashboard.build --slot auto        # 現在時刻から判定

設計方針:
  どの区画が取得できなくても全体は必ず完走し、取れた分だけを出す。
  収集系は例外を投げない前提だが、想定外に備えて区画ごとに握り潰す。
"""
import argparse
import sys
import traceback
from datetime import date, datetime, timedelta

from . import analyze, store
from .config import (
    ARTICLE_PATTERNS, MACRO_SYMBOLS, NIKKEI_FUTURES_CANDIDATES,
    RANKING_PAGES, SLOTS, US_INDICES, US_SECTOR_ETFS,
)
from .sources import kabutan, stooq


def _safe(label: str, fn, default=None):
    try:
        return fn()
    except Exception:
        print(f"  ❌ {label} で例外: ", file=sys.stderr)
        traceback.print_exc()
        return default


def _fetch_tables(slot: str) -> dict:
    pages = RANKING_PAGES.get(slot, [])
    if not pages:
        return {}
    print("  [株探] ランキング・決算テーブルを取得中...")
    out = {}
    for page in pages:
        table = _safe(page["label"], lambda p=page: kabutan.fetch_table(p))
        if table:
            out[page["key"]] = table
    return out


def _fetch_watchlist(tables: dict, articles: list) -> list[dict]:
    codes = store.load_watchlist().get("codes", [])
    if not codes:
        return []
    print(f"  [株探] ウォッチリスト {len(codes)} 銘柄を取得中...")
    quotes = []
    for code in codes:
        q = _safe(f"銘柄 {code}", lambda c=code: kabutan.fetch_stock(c))
        quotes.append(q or {"code": code, "name": None, "error": True})
    return analyze.enrich_watchlist(quotes, tables, articles)


def _compact_rows(rows: list[dict], limit: int = 30) -> list[dict]:
    """履歴に残す用に、差分分析で使う列だけへ絞る。"""
    return [{"code": r.get("code"), "name": r.get("name"),
             "change_pct": r.get("change_pct")} for r in rows[:limit]]


def _stale_days(asof: str | None, today: date) -> int | None:
    if not asof:
        return None
    try:
        return (today - datetime.strptime(asof, "%Y-%m-%d").date()).days
    except ValueError:
        return None


# ==================== 寄り前 ====================
def build_preopen(target_date: date) -> dict:
    print("  [stooq] 米国指数を取得中...")
    us = _safe("米国指数", lambda: stooq.fetch_many(US_INDICES, target_date), {}) or {}
    print("  [stooq] 為替・金利・商品を取得中...")
    macro = _safe("マクロ", lambda: stooq.fetch_many(MACRO_SYMBOLS, target_date), {}) or {}
    print("  [stooq] 米国セクターETFを取得中...")
    sectors_us = _safe("米セクター", lambda: stooq.fetch_many(US_SECTOR_ETFS, target_date), {}) or {}
    futures = _safe("日経先物",
                    lambda: stooq.fetch_first_available(NIKKEI_FUTURES_CANDIDATES, target_date))

    drivers = analyze.build_drivers(us, macro, sectors_us)

    # 前営業日の大引け（日経平均）を履歴から拾う
    prev_close = None
    prev_kessan = []
    prev_summary = None
    for hist in store.previous_sessions(target_date, count=5):
        idx = (hist.get("taibike") or {}).get("indices") or {}
        nk = idx.get("nikkei")
        if prev_close is None and nk and nk.get("close") is not None:
            prev_close = nk["close"]
            prev_summary = {"date": hist.get("date"), "indices": idx,
                            "session_shift": (hist.get("taibike") or {}).get("session_shift")}
            prev_kessan = (hist.get("taibike") or {}).get("kessan_after") or []
            break

    implied = analyze.implied_open(drivers, prev_close, futures)
    risk = analyze.risk_regime(us, macro)
    outlook = analyze.sector_outlook(drivers)

    watchlist = _fetch_watchlist({}, [])

    spx = us.get("spx") or {}
    return {
        "us": us,
        "macro": macro,
        "sectors_us": sectors_us,
        "futures": futures,
        "implied_open": implied,
        "risk": risk,
        "sector_outlook": outlook,
        "carryover": {"prev_session": prev_summary,
                      "after_hours_kessan": prev_kessan[:20]},
        "watchlist": watchlist,
        "freshness": {"us_asof": spx.get("asof"),
                      "us_stale_days": _stale_days(spx.get("asof"), target_date)},
    }


# ==================== 前場 / 大引 ====================
def build_session(target_date: date, slot: str) -> dict:
    indices = _safe("指数", kabutan.fetch_indices, {}) or {}
    tables = _fetch_tables(slot)

    hints = store.load_hints().get(slot, [])
    articles, hit_numbers = _safe(
        "記事",
        lambda: kabutan.fetch_articles(target_date, ARTICLE_PATTERNS.get(slot, []), hints),
        ([], []),
    )
    if hit_numbers:
        store.save_hints(slot, hit_numbers)

    breadth = analyze.market_breadth(tables.get("sector"))
    nikkei_pct = (indices.get("nikkei") or {}).get("change_pct")
    breadth_note = analyze.breadth_vs_index(breadth, nikkei_pct)

    # 前営業日との比較（売買代金の顔ぶれ）
    prev_sessions = store.previous_sessions(target_date, count=8)
    prev_value = None
    for hist in prev_sessions:
        rows = ((hist.get(slot) or {}).get("value_rows")
                or (hist.get("taibike") or {}).get("value_rows"))
        if rows:
            prev_value = rows
            break
    value_rows = (tables.get("value") or {}).get("rows", [])
    delta = analyze.ranking_delta(value_rows, prev_value)
    history_rows = [
        ((h.get(slot) or {}).get("value_rows") or (h.get("taibike") or {}).get("value_rows") or [])
        for h in prev_sessions
    ]
    streak = analyze.streaks(value_rows, history_rows)

    payload = {
        "indices": indices,
        "tables": tables,
        "breadth": breadth,
        "breadth_note": breadth_note,
        "ranking_delta": delta,
        "streaks": streak,
        "articles": articles,
    }

    latest = store.load_latest()
    slots = latest.get("slots", {}) if latest.get("date") == target_date.isoformat() else {}

    if slot == "zenba":
        implied = ((slots.get("preopen") or {}).get("data") or {}).get("implied_open")
        payload["verify_open"] = analyze.verify_open(implied, nikkei_pct)
    else:
        zenba_idx = ((slots.get("zenba") or {}).get("data") or {}).get("indices")
        payload["session_shift"] = analyze.session_shift(zenba_idx, indices)

    payload["watchlist"] = _fetch_watchlist(tables, articles)
    return payload


# ==================== エントリポイント ====================
def resolve_slot(now: datetime | None = None) -> str:
    now = now or store.now_jst()
    minutes = now.hour * 60 + now.minute
    if minutes < 10 * 60:
        return "preopen"
    if minutes < 15 * 60:
        return "zenba"
    return "taibike"


def run(slot: str, target_date: date | None = None) -> dict:
    target_date = target_date or store.now_jst().date()
    print(f"\n{'=' * 52}")
    print(f"  マーケットダッシュボード  slot={slot}  date={target_date}")
    print(f"{'=' * 52}\n")

    if slot == "preopen":
        payload = build_preopen(target_date)
        hist_patch = {"preopen": {
            "implied_open": payload.get("implied_open"),
            "risk": payload.get("risk"),
        }}
    else:
        payload = build_session(target_date, slot)
        hist_patch = {slot: {
            "indices": payload.get("indices"),
            "value_rows": _compact_rows((payload.get("tables", {}).get("value") or {}).get("rows", [])),
            "session_shift": payload.get("session_shift"),
            "kessan_after": _compact_rows(
                (payload.get("tables", {}).get("kessan_after") or {}).get("rows", []), limit=40),
        }}

    store.save_slot(target_date, slot, payload)
    store.update_history(target_date, hist_patch)
    removed = store.prune_history()
    if removed:
        print(f"  🧹 古い履歴を {removed} 件削除")

    print(f"\n✅ {slot} を更新しました → {store.latest_path()}")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="マーケットダッシュボードのデータ生成")
    parser.add_argument("--slot", choices=SLOTS + ["auto"], default="auto")
    parser.add_argument("--date", help="対象日 YYYYMMDD（省略時は今日）")
    args = parser.parse_args(argv)

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y%m%d").date()
        except ValueError:
            parser.error(f"日付形式が不正です: {args.date}（例: 20260906）")

    slot = resolve_slot() if args.slot == "auto" else args.slot
    run(slot, target_date)


if __name__ == "__main__":
    main()
