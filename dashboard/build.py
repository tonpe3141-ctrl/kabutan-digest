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
    JP_INDICES, MACRO_SYMBOLS, RANKING_PAGES, SLOTS, SPARK_POINTS,
    US_INDICES, US_SECTOR_ETFS,
)

from .sources import cnbc, tdnet, yahoojp


def _safe(label: str, fn, default=None):
    try:
        return fn()
    except Exception:
        print(f"  ❌ {label} で例外:", file=sys.stderr)
        traceback.print_exc()
        return default


def _snapshot(*quote_maps: dict) -> dict:
    """履歴に残す {key: 終値} のスナップショット。スパークラインの材料になる。"""
    snap = {}
    for m in quote_maps:
        for key, q in (m or {}).items():
            if q.get("last") is not None:
                snap[key] = q["last"]
    return snap


def _attach_series(quote_maps: list[dict], sessions: list[dict], slot: str) -> None:
    """過去の履歴から系列を組み立てて各銘柄に series を付ける。

    CNBC は履歴を返さないので、自分が毎日残したスナップショットを使う。
    日数が溜まるまでスパークラインは出ない（UI 側で 3 点未満は描画しない）。
    """
    past = []
    for hist in reversed(sessions[:SPARK_POINTS]):     # 古い順に並べ直す
        snap = (hist.get(slot) or {}).get("quotes")
        if snap:
            past.append(snap)
    for m in quote_maps:
        for key, q in (m or {}).items():
            series = [p[key] for p in past if key in p]
            if q.get("last") is not None:
                series.append(q["last"])
            if len(series) >= 3:
                q["series"] = series


def _fetch_watchlist(tables: dict, disclosures: list[dict]) -> list[dict]:
    codes = store.load_watchlist().get("codes", [])
    if not codes:
        return []
    print(f"  [Yahoo] ウォッチリスト {len(codes)} 銘柄を取得中...")
    quotes = []
    for code in codes:
        q = _safe(f"銘柄 {code}", lambda c=code: yahoojp.fetch_stock(c))
        quotes.append(q or {"code": code, "name": None, "error": True})

    enriched = analyze.enrich_watchlist(quotes, tables, [])
    # 適時開示に出ていればそれも貼る（決算・修正はウォッチリストで最重要）
    by_code: dict[str, list[str]] = {}
    for d in disclosures or []:
        by_code.setdefault(d["code"], []).append(d.get("category") or "開示")
    for item in enriched:
        hits = by_code.get(item.get("code"))
        if hits:
            item["disclosures"] = sorted(set(hits))
    return enriched


def _compact_rows(rows: list[dict], limit: int = 30) -> list[dict]:
    return [{"code": r.get("code"), "name": r.get("name"),
             "change_pct": r.get("change_pct")} for r in rows[:limit]]


# ==================== 寄り前 ====================
def build_preopen(target_date: date) -> dict:
    print("  [CNBC] 米国指数を取得中...")
    us = _safe("米国指数", lambda: cnbc.fetch_spec(US_INDICES), {}) or {}
    print("  [CNBC] 為替・金利・商品を取得中...")
    macro = _safe("マクロ", lambda: cnbc.fetch_spec(MACRO_SYMBOLS), {}) or {}
    print("  [CNBC] 米国セクターETFを取得中...")
    sectors_us = _safe("米セクター", lambda: cnbc.fetch_spec(US_SECTOR_ETFS), {}) or {}

    sessions = store.previous_sessions(target_date, count=SPARK_POINTS)
    _attach_series([us, macro, sectors_us], sessions, "preopen")

    drivers = analyze.build_drivers(us, macro, sectors_us)

    # 前営業日の日経平均終値。初日は履歴が無いので CNBC の .N225 を使う
    # （07:00 JST 時点では前営業日の大引け値が返る）。
    prev_close = None
    jp = _safe("日経平均(前日終値)", lambda: cnbc.fetch_symbols([".N225"]), {}) or {}
    if jp.get(".N225", {}).get("last") is not None:
        prev_close = jp[".N225"]["last"]
        print(f"    ✅ 前営業日の日経平均終値: {prev_close:,.2f}")

    prev_summary = None
    after_hours = []
    for hist in sessions:
        idx = (hist.get("taibike") or {}).get("indices") or {}
        nk = idx.get("nikkei")
        if nk and nk.get("close") is not None:
            prev_close = prev_close or nk["close"]
            prev_summary = {"date": hist.get("date"), "indices": idx,
                            "session_shift": (hist.get("taibike") or {}).get("session_shift")}
            after_hours = (hist.get("taibike") or {}).get("after_hours") or []
            break

    spx = us.get("spx") or {}
    return {
        "us": us,
        "macro": macro,
        "sectors_us": sectors_us,
        "implied_open": analyze.implied_open(drivers, prev_close, None),
        "risk": analyze.risk_regime(us, macro),
        "sector_outlook": analyze.sector_outlook(drivers),
        "carryover": {"prev_session": prev_summary, "after_hours_kessan": after_hours[:20]},
        "watchlist": _fetch_watchlist({}, after_hours),
        "freshness": {"us_asof": spx.get("asof"),
                      "market_status": spx.get("market_status")},
    }


# ==================== 前場 / 大引 ====================
def build_session(target_date: date, slot: str) -> dict:
    print("  [CNBC] 日本の指数を取得中...")
    raw = _safe("日本指数", lambda: cnbc.fetch_spec(JP_INDICES), {}) or {}
    # asof が対象日と違えば、市場が閉じていて前営業日の値が返っている
    indices = {k: {"label": v.get("label"), "close": v.get("last"),
                   "change": v.get("change"), "change_pct": v.get("change_pct"),
                   "high": v.get("high"), "low": v.get("low"),
                   "open": v.get("open"), "asof": v.get("asof"),
                   "stale": bool(v.get("asof") and v["asof"] != target_date.isoformat())}
               for k, v in raw.items()}
    if any(v["stale"] for v in indices.values()):
        print("    ⚠️  指数が対象日の値ではありません（休場、または取得タイミングが早い）")

    print("  [Yahoo] ランキングを取得中...")
    tables = {}
    for page in RANKING_PAGES.get(slot, []):
        t = _safe(page["label"], lambda p=page: yahoojp.fetch_ranking(p))
        if t:
            tables[page["key"]] = t

    print("  [TDnet] 適時開示を取得中...")
    disc = _safe("適時開示", lambda: tdnet.fetch_disclosures(target_date),
                 {"rows": [], "ok": False}) or {"rows": [], "ok": False}
    split = tdnet.split_by_session(disc.get("rows", []))
    if split["intraday"]:
        tables["kessan_intraday"] = {"key": "kessan_intraday",
                                     "label": "場中の開示（決算・業績修正）",
                                     "url": disc.get("url"), "rows": split["intraday"],
                                     "ok": True}
    if slot == "taibike" and split["after"]:
        tables["kessan_after"] = {"key": "kessan_after",
                                  "label": "引け後の開示（決算・業績修正）",
                                  "url": disc.get("url"), "rows": split["after"],
                                  "ok": True}

    nikkei_pct = (indices.get("nikkei") or {}).get("change_pct")
    divergence = analyze.index_divergence(indices)

    sessions = store.previous_sessions(target_date, count=8)
    prev_value = None
    for hist in sessions:
        rows = ((hist.get(slot) or {}).get("value_rows")
                or (hist.get("taibike") or {}).get("value_rows"))
        if rows:
            prev_value = rows
            break
    value_rows = (tables.get("value") or {}).get("rows", [])
    history_rows = [
        ((h.get(slot) or {}).get("value_rows") or (h.get("taibike") or {}).get("value_rows") or [])
        for h in sessions
    ]

    payload = {
        "indices": indices,
        "tables": tables,
        "divergence": divergence,
        "ranking_delta": analyze.ranking_delta(value_rows, prev_value),
        "streaks": analyze.streaks(value_rows, history_rows),
        "disclosure_summary": analyze.disclosure_summary(disc.get("rows", [])),
    }

    latest = store.load_latest()
    slots = latest.get("slots", {}) if latest.get("date") == target_date.isoformat() else {}
    if slot == "zenba":
        implied = ((slots.get("preopen") or {}).get("data") or {}).get("implied_open")
        payload["verify_open"] = analyze.verify_open(implied, nikkei_pct)
    else:
        zenba_idx = ((slots.get("zenba") or {}).get("data") or {}).get("indices")
        payload["session_shift"] = analyze.session_shift(zenba_idx, indices)

    payload["watchlist"] = _fetch_watchlist(tables, disc.get("rows", []))
    payload["_after_hours"] = split["after"]
    return payload


# ==================== エントリポイント ====================
def adjust_slot(slot: str, now: datetime | None = None) -> str:
    """cron が遅れて発火したときに、実際の時刻と矛盾する区分を補正する。

    GitHub の schedule はベストエフォートで、混雑時は数時間ずれる
    （2026-09-07 に「12:40 JST」の cron が 17:36 JST に発火した実測あり）。
    そのまま前場として保存すると、大引けの数字が前場タブに入ってしまう。

    寄り前は前夜の米国市場の終値を見るだけなので、遅れても内容は変わらない。
    補正するのは前場だけでよい。
    """
    now = now or store.now_jst()
    minutes = now.hour * 60 + now.minute
    if slot == "zenba" and minutes >= 15 * 60:
        print(f"  ⚠️  前場の実行が {now:%H:%M} まで遅れているため、大引として扱います")
        return "taibike"
    return slot


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
    slot = adjust_slot(slot)
    print(f"\n{'=' * 52}")
    print(f"  マーケットダッシュボード  slot={slot}  date={target_date}")
    print(f"{'=' * 52}\n")

    if slot == "preopen":
        payload = build_preopen(target_date)
        hist_patch = {"preopen": {
            "implied_open": payload.get("implied_open"),
            "risk": payload.get("risk"),
            "quotes": _snapshot(payload.get("us"), payload.get("macro"),
                                payload.get("sectors_us")),
        }}
    else:
        payload = build_session(target_date, slot)
        after_hours = payload.pop("_after_hours", [])
        hist_patch = {slot: {
            "indices": payload.get("indices"),
            "value_rows": _compact_rows((payload.get("tables", {}).get("value") or {}).get("rows", [])),
            "session_shift": payload.get("session_shift"),
            "after_hours": after_hours[:40],
            "quotes": {k: v["close"] for k, v in (payload.get("indices") or {}).items()
                       if v.get("close") is not None},
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

    run(resolve_slot() if args.slot == "auto" else args.slot, target_date)


if __name__ == "__main__":
    main()
