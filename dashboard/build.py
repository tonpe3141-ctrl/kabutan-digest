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
from statistics import median

from . import analyze, bars as bars_mod, commentary, earnings as earnings_mod, ledger as ledger_mod, macro as macro_mod, names, newsflow, picks as picks_mod, store, themes as themes_mod, thermo_run, trend
from .config import (
    JP_INDICES, MACRO_SYMBOLS, RANKING_PAGES, SLOTS, SPARK_POINTS, SWING_TRACK_PATH, THERMO_PATH,
    US_INDICES, US_SECTOR_ETFS,
)

from .sources import cnbc, kabutan_news, news, nikkei225, press, tdnet, yahoojp


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


def _fetch_watchlist(tables: dict, disclosures: list[dict],
                     quotes_by_code: dict | None = None) -> list[dict]:
    codes = store.load_watchlist().get("codes", [])
    if not codes:
        return []

    # 日経225の取得ぶんに含まれていればそれを使い、足りない分だけ追加で引く
    have = {c: q for c, q in (quotes_by_code or {}).items() if c in codes}
    missing = [c for c in codes if c not in have]
    if missing:
        print(f"  [CNBC] ウォッチリスト {len(missing)} 銘柄を取得中...")
        have.update(_safe("ウォッチリスト", lambda: cnbc.fetch_jp_stocks(missing), {}) or {})

    # CNBC の社名は英語なので、画面に出す社名は日本語の情報源から（names.py）
    ja = _safe("社名", lambda: names.resolve(codes, fetch=yahoojp.fetch_name), {}) or {}
    quotes = []
    for code in codes:
        q = have.get(code)
        # 始値・高値・安値と時刻は、アプリの保有株カードが前場の値動き（撤退ライン・売り指値に届いたか）を見るため
        quotes.append({"code": code, "name": ja.get(code) or q.get("name"), "price": q.get("last"),
                       "change": q.get("change"), "change_pct": q.get("change_pct"),
                       "sector": q.get("sector"), "open": q.get("open"), "high": q.get("high"),
                       "low": q.get("low"), "at": q.get("last_time")}
                      if q else {"code": code, "name": ja.get(code), "error": True})

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


_PRESS_EMPTY = {"official": [], "headlines": [], "overseas": [], "articles": [], "wire": [], "macro_articles": [],
                "status": [], "ok": False}


def _fetch_press() -> dict:
    print("  [報道・公的機関] ロイター／ブルームバーグ／日経／時事／NHK／日銀／財務省／JPX／FRB／CNBC を取得中...")
    return _safe("報道・公的機関", press.fetch_all, None) or dict(_PRESS_EMPTY)


def _compact_rows(rows: list[dict], limit: int = 30) -> list[dict]:
    return [{"code": r.get("code"), "name": r.get("name"),
             "change_pct": r.get("change_pct")} for r in rows[:limit]]


# ==================== 寄り前 ====================
def build_preopen(target_date: date) -> dict:
    print("  [Yahoo] 相場振り返り記事を取得中...")
    market_news = _safe("市況記事", lambda: news.fetch_market_topics(6), []) or []
    print("  [株探] 見出しと配信記事を取得中...")
    kabutan = _safe("株探ニュース", lambda: kabutan_news.fetch_for_slot("preopen"),
                    {"headlines": [], "articles": [], "ok": False}) or {"headlines": [], "articles": [], "ok": False}
    press_news = _fetch_press()

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
    q = jp.get(".N225") or {}
    # 寄り付き（09:00）より後に走ると last は当日の場中値になる（保険の cron が遅れて発火したとき）。
    # その場合は前日終値（prev）を使う。場中値を基準にすると想定オープンが当日の値動きと混ざる
    base = q.get("prev") if q.get("asof") == target_date.isoformat() else q.get("last")
    if base is not None:
        prev_close = base
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

    # 前営業日の引け後の開示は、大引（16:45）の時点の分しか履歴に無い。決算の多くは 15:30〜17:00 に出るので、
    # 寄り前に TDnet を取り直して全部そろえる（取れなければ履歴の分のまま）
    prev_disc, prev_others = [], []
    if prev_summary and prev_summary.get("date"):
        prev_day = date.fromisoformat(prev_summary["date"])
        print(f"  [TDnet] 前営業日（{prev_day}）の適時開示を取り直し中...")
        got = _safe("前営業日の適時開示", lambda: tdnet.fetch_disclosures(prev_day), {}) or {}
        prev_disc = tdnet.split_by_session(got.get("rows") or [])["after"]
        prev_others = tdnet.split_by_session(got.get("others") or [])["after"]
        if len(prev_disc) > len(after_hours):
            after_hours = prev_disc

    spx = us.get("spx") or {}
    payload = {
        "us": us,
        "macro": macro,
        "sectors_us": sectors_us,
        "implied_open": analyze.implied_open(drivers, prev_close, None),
        "risk": analyze.risk_regime(us, macro),
        "sector_outlook": analyze.sector_outlook(drivers),
        "carryover": {"prev_session": prev_summary, "after_hours_kessan": after_hours[:80]},
        "watchlist": _fetch_watchlist({}, after_hours),
        "freshness": {"us_asof": spx.get("asof"),
                      "market_status": spx.get("market_status")},
    }
    payload["news"] = market_news
    payload["kabutan"] = kabutan
    payload["press"] = press_news
    payload["index_trend"] = trend.index_trend(sessions, None)
    payload["commentary"] = commentary.preopen_commentary(payload)
    # バックアップ実行（07:35 等）で上書きされても、Routine が既に書いた
    # ai_commentary・ai_macro を消さないよう同日分があれば引き継ぐ
    latest = store.load_latest()
    before = (((latest.get("slots", {}).get("preopen") or {}).get("data") or {})
              if latest.get("date") == target_date.isoformat() else {})
    payload["ai_commentary"] = before.get("ai_commentary")
    payload["ai_macro"] = before.get("ai_macro")
    payload["ai_earnings"] = before.get("ai_earnings")
    payload["ai_picks"] = before.get("ai_picks")
    payload["ai_news"] = before.get("ai_news")
    payload["_earn_rows"] = after_hours
    payload["_news_disc"] = [dict(r, when="前営業日の引け後") for r in after_hours + prev_others]
    return payload


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

    print("  [CNBC] 為替・金利・商品を取得中...")
    macro = _safe("マクロ", lambda: cnbc.fetch_spec(MACRO_SYMBOLS), {}) or {}

    print("  [Yahoo] ランキングを取得中...")
    tables = {}
    for page in RANKING_PAGES.get(slot, []):
        t = _safe(page["label"], lambda p=page: yahoojp.fetch_ranking(p))
        if t:
            tables[page["key"]] = t

    print("  [Yahoo] 相場振り返り記事を取得中...")
    market_news = _safe("市況記事", lambda: news.fetch_market_topics(6), []) or []
    print("  [株探] 見出しと配信記事を取得中...")
    kabutan = _safe("株探ニュース", lambda: kabutan_news.fetch_for_slot(slot),
                    {"headlines": [], "articles": [], "ok": False}) or {"headlines": [], "articles": [], "ok": False}
    press_news = _fetch_press()
    # 東証33業種の騰落は株探の「【業種】騰落ランキング」記事の本文から読む（公式値の代替として最良）
    session_word = "大引け" if slot == "taibike" else "前引け"
    sectors33 = (kabutan_news.sectors33_from_articles(kabutan.get("articles"), session_word)
                 or kabutan_news.sectors33_from_articles(kabutan.get("articles")))
    if sectors33:
        print(f"    ✅ 東証33業種: {len(sectors33['rows'])} 業種（{sectors33.get('headline')}）")

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

    # 日経225の構成銘柄と、その値動き（ヒートマップと業種別の材料）
    print("  [日経] 構成銘柄を取得中...")
    components = _safe("構成銘柄", nikkei225.fetch_components, []) or []
    quotes_by_code = {}
    constituents = []
    if components:
        print("  [CNBC] 構成銘柄の株価を取得中...")
        quotes_by_code = _safe(
            "構成銘柄の株価",
            lambda: cnbc.fetch_jp_stocks([c["code"] for c in components]), {}) or {}
        for c in components:
            q = quotes_by_code.get(c["code"])
            if not q:
                continue
            constituents.append({
                "code": c["code"], "name": c["name"], "sector": c["sector"],
                "price": q.get("last"), "change": q.get("change"),
                "change_pct": q.get("change_pct"),
            })

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

    sectors_jp = analyze.sector_performance(constituents)

    # テーマ別の資金の向き（辞書で決定的に束ねる）と、そのテーマが何日目か
    themes = themes_mod.load_themes()
    history_tops = [((h.get(slot) or {}).get("theme_top") or (h.get("taibike") or {}).get("theme_top") or [])
                    for h in sessions]
    flow = themes_mod.theme_flow(tables, themes, history_tops[0] if history_tops else None)
    flow["streaks"] = themes_mod.theme_streaks(flow["top"], history_tops)

    payload = {
        "indices": indices,
        "macro": macro,
        "tables": tables,
        "divergence": divergence,
        "ranking_delta": analyze.ranking_delta(value_rows, prev_value),
        "streaks": analyze.streaks(value_rows, history_rows),
        "disclosure_summary": analyze.disclosure_summary(disc.get("rows", [])),
        "constituents": constituents,
        "sectors_jp": sectors_jp,
        "sectors33": sectors33,
        "breadth": analyze.constituent_breadth(constituents),
        "news": market_news,
        "kabutan": kabutan,
        "press": press_news,
        "theme_flow": flow,
        "sector_trend": trend.sector_trend(sectors_jp, sessions),
        "index_trend": trend.index_trend(sessions, (indices.get("nikkei") or {}).get("close"), indices),
    }

    latest = store.load_latest()
    slots = latest.get("slots", {}) if latest.get("date") == target_date.isoformat() else {}
    if slot == "zenba":
        implied = ((slots.get("preopen") or {}).get("data") or {}).get("implied_open")
        payload["verify_open"] = analyze.verify_open(implied, nikkei_pct)
    else:
        zenba_idx = ((slots.get("zenba") or {}).get("data") or {}).get("indices")
        payload["session_shift"] = analyze.session_shift(zenba_idx, indices)

    payload["watchlist"] = _fetch_watchlist(tables, disc.get("rows", []), quotes_by_code)

    # 発掘台帳は1日1回、大引で進める（候補の入口・追跡・成績）。休場日は進めない
    # （前営業日の顔ぶれを新規として数え直したり、休場日を営業日として追跡したりしないため）
    holiday = bool((indices.get("nikkei") or {}).get("stale"))
    if slot == "taibike" and holiday:
        print("  [台帳] 休場日のため更新しません")
    if slot == "taibike" and not holiday:
        print("  [台帳] 候補の更新と追跡...")
        def _lookup(codes):
            have = {c: q.get("last") for c, q in quotes_by_code.items() if q.get("last") is not None}
            missing = [c for c in codes if c not in have]
            if missing:
                have.update({c: q.get("last") for c, q in (cnbc.fetch_jp_stocks(missing) or {}).items()
                             if q.get("last") is not None})
            return have
        led = _safe("発掘台帳", lambda: ledger_mod.update(
            target_date, payload, sessions, themes, flow, _lookup,
            (indices.get("nikkei") or {}).get("close")))
        if led:
            ledger_mod.save_ledger(led)
            payload["ledger_today"] = {
                "added": [{"code": e["code"], "name": e["name"], "signals": e["signals"]}
                          for e in led["entries"] if e.get("first_seen") == target_date.isoformat()],
                "watching": sum(1 for e in led["entries"] if e.get("status") == "watching"),
            }
            print(f"    ✅ 台帳: 新規 {len(payload['ledger_today']['added'])} / 追跡中 {payload['ledger_today']['watching']}")

    carry_over(payload, (slots.get(slot) or {}).get("data"))
    payload["commentary"] = commentary.session_commentary(payload, slot)
    # バックアップ実行で上書きされても、Routine が既に書いた ai_commentary / ai_macro を消さない
    payload["ai_commentary"] = ((slots.get(slot) or {}).get("data") or {}).get("ai_commentary")
    payload["ai_macro"] = ((slots.get(slot) or {}).get("data") or {}).get("ai_macro")
    payload["ai_earnings"] = ((slots.get(slot) or {}).get("data") or {}).get("ai_earnings")
    payload["ai_picks"] = ((slots.get(slot) or {}).get("data") or {}).get("ai_picks")
    payload["ai_news"] = ((slots.get(slot) or {}).get("data") or {}).get("ai_news")
    payload["_earn_rows"] = disc.get("rows") or []
    payload["_after_hours"] = split["after"]
    # 動いた銘柄の材料の照合に使う開示: 今日の開示（分類に当たらない提携・受注・採択なども）と、前営業日の引け後の開示
    # （今日の値動きの材料。寄り前に取り直した一覧があればそれを、無ければ履歴の分）
    pre = ((slots.get("preopen") or {}).get("data") or {})
    prev_after = (pre.get("carryover") or {}).get("after_hours_kessan") or next(
        ((h.get("taibike") or {}).get("after_hours") or [] for h in sessions if (h.get("taibike") or {}).get("after_hours")), [])
    prev_others = next(((h.get("taibike") or {}).get("after_others") or [] for h in sessions[:1]), [])
    payload["_news_disc"] = ([dict(r, when="今日") for r in (disc.get("rows") or []) + (disc.get("others") or [])]
                             + [dict(r, when="前営業日の引け後") for r in prev_after + prev_others])
    payload["_after_others"] = [{k: r.get(k) for k in ("code", "name", "title", "time")}
                                for r in tdnet.split_by_session(disc.get("others") or [])["after"]][:80]
    return payload


def carry_over(payload: dict, before: dict | None) -> list[str]:
    """同じ日の同じ区分を取り直したとき、今回取れなかった区画は前回の値を引き継ぐ。

    夜に遅れて走った実行では、株探の「業種騰落ランキング」記事が一覧から落ちて東証33業種が
    消える、ランキングの取得に失敗する、といった形で、先に取れていた情報が欠けていた
    （2026-09-25 23:36 の実測）。取り直しで情報が減らないようにする。戻り値は引き継いだ区画名。
    """
    if not before:
        return []
    kept = []
    if not payload.get("sectors33") and before.get("sectors33"):
        payload["sectors33"] = before["sectors33"]
        kept.append("sectors33")
    tables, old = payload.setdefault("tables", {}), before.get("tables") or {}
    for key, t in old.items():
        if (t or {}).get("rows") and not ((tables.get(key) or {}).get("rows")):
            tables[key] = t
            kept.append(f"tables.{key}")
    kb, okb = payload.get("kabutan") or {}, before.get("kabutan") or {}
    if okb.get("articles"):
        have = {a.get("headline") for a in kb.get("articles") or []}
        lost = [a for a in okb["articles"] if a.get("headline") not in have]
        if lost:
            kb["articles"] = list(kb.get("articles") or []) + lost
            payload["kabutan"] = kb
            kept.append(f"kabutan.articles+{len(lost)}")
    # 報道・公的機関も同じ。遅れた実行では鮮度の窓から落ちた見出しや、一覧から消えた記事がある
    pr, opr = payload.get("press"), before.get("press") or {}
    if pr is not None:
        for key, name in (("official", "title"), ("headlines", "title"), ("overseas", "title"),
                          ("wire", "title"), ("articles", "headline"), ("macro_articles", "headline")):
            have = {x.get(name) for x in pr.get(key) or []}
            lost = [x for x in opr.get(key) or [] if x.get(name) not in have]
            if lost:
                pr[key] = list(pr.get(key) or []) + lost
                if key not in ("articles", "macro_articles"):
                    pr[key].sort(key=lambda x: x.get("published") or "", reverse=True)
                kept.append(f"press.{key}+{len(lost)}")
    if kept:
        print(f"  ↩︎ 前回の同じ区分から引き継ぎ: {', '.join(kept)}")
    return kept


def already_done(slot: str, target_date: date) -> bool:
    """保険の cron 用。その日のその区分が既に作られていれば True（取り直さない）。

    ただし大引は、その日の実行の時点で CNBC の日足に当日の分がまだ無く、注文が前の営業日の引けのまま
    （翌営業日の注文が出ていない）なら False にする。遅れて発火する保険（実測で夜）が取り直すと、当日の引けの注文が出る。
    休場日（日経が前営業日の値）は取り直しても変わらないので True。"""
    latest = store.load_latest()
    today = target_date.isoformat()
    if latest.get("date") != today or slot not in (latest.get("slots") or {}):
        return False
    if slot == "taibike":
        data = (latest["slots"]["taibike"] or {}).get("data") or {}
        if ((data.get("indices") or {}).get("nikkei") or {}).get("stale"):
            return True
        asof = ((data.get("thermo") or {}).get("swing") or {}).get("asof")
        if asof and asof < today:
            print(f"  大引の注文が {asof} の引けのまま（当日の日足が未着だった）なので、保険の実行で取り直します")
            return False
    return True


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


def insurance_target(slot: str, now: datetime | None = None) -> tuple[str, date]:
    """保険の cron の区分と対象日。日付をまたいで発火したら前の日の大引として扱う。

    前場・大引の保険は数時間遅れて深夜に発火することがある（2026-09-29 の大引の保険が 09-30 00:41 に発火）。
    そのまま今日の日付で作ると、前の日の引けのデータが「今日の大引」として保存され、翌日の保険が
    「今日の大引はもうある」と見送り、Routine の待機も今日のデータとして受け取ってしまう。"""
    now = now or store.now_jst()
    if slot in ("zenba", "taibike") and now.hour < 7:
        return "taibike", (now - timedelta(days=1)).date()
    return adjust_slot(slot, now), now.date()


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
        after_others = payload.pop("_after_others", [])
        hist_patch = {slot: {
            "indices": payload.get("indices"),
            "value_rows": _compact_rows((payload.get("tables", {}).get("value") or {}).get("rows", [])),
            "session_shift": payload.get("session_shift"),
            "after_hours": after_hours[:40],
            # 分類に当たらない引け後の開示（翌営業日の「動いた銘柄の材料」の照合に使う）
            "after_others": after_others,
            "quotes": {k: v["close"] for k, v in (payload.get("indices") or {}).items()
                       if v.get("close") is not None},
            # 市況の推移（日々の記録）の「その日の大枠」用。大引時点の為替・原油（寄り前の米国時間の値ではない）
            "macro": {k: {"label": v.get("label"), "last": v.get("last"),
                          "change": v.get("change"), "change_pct": v.get("change_pct")}
                      for k, v in (payload.get("macro") or {}).items()
                      if v.get("last") is not None},
            # 時間軸（trend.py）とテーマの連続日数のために残す
            "sectors": [{"sector": x["sector"], "avg_pct": x["avg_pct"], "count": x.get("count")}
                        for x in (payload.get("sectors_jp") or [])],
            "sectors33": [{"sector": x["sector"], "change_pct": x["change_pct"]}
                          for x in ((payload.get("sectors33") or {}).get("rows") or [])],
            "theme_top": [{"theme": t["theme"], "score": t["score"], "count": t["count"], "avg_pct": t["avg_pct"]}
                          for t in ((payload.get("theme_flow") or {}).get("top") or [])],
            "breadth": payload.get("breadth"),
        }}

    # ニュースから読む（動いた銘柄の材料・話題の銘柄・テーマの話題）。照合と数え上げだけで、文章は Routine が ai_news に書く
    print("  [ニュース] 値動きと開示・見出しを突き合わせ中...")
    news_disc = payload.pop("_news_disc", None) or []
    payload["newsflow"] = _safe("ニュースから読む", lambda: newsflow.build(
        slot, payload, names=names.known(), themes=themes_mod.load_themes(), disclosures=news_disc,
        history_counts=[((h.get(slot) or {}).get("news_themes") or {})
                        for h in store.previous_sessions(target_date, count=newsflow.HIST_DAYS)]))
    if payload["newsflow"]:
        hist_patch[slot]["news_themes"] = payload["newsflow"].pop("counts", {})
        mv = payload["newsflow"].get("movers") or {}
        print(f"    ✅ 見出し {payload['newsflow']['n_items']} 本・動いた銘柄 {mv.get('n', 0)}"
              f"（開示 {mv.get('disc', 0)}・報道 {mv.get('news', 0)}・業種ぐるみ {mv.get('group', 0)}・見当たらない {mv.get('none', 0)}）")

    # 相場温度計（逆張りガード）。日足キャッシュを更新し docs/data/thermo.json を書く
    print("  [温度計] 日足の更新と相場温度の計算...")
    th = _safe("相場温度計", lambda: thermo_run.run(
        slot, target_date, payload, store.previous_sessions(target_date, count=thermo_run.HIST_DAYS)))
    payload["thermo"] = (th or {}).get("summary")
    sec = commentary.thermo_section(payload["thermo"], slot, target_date.isoformat())
    if sec and payload.get("commentary"):
        payload["commentary"].setdefault("sections", []).append(sec)
    if th and th.get("history"):
        hist_patch[slot].update(th["history"])

    # マクロ環境（金利・為替・商品の流れと、報道で多かった話題）。温度計が更新した日足キャッシュを読む
    print("  [マクロ] 金利・為替・商品の流れと、報道の話題をまとめ中...")
    payload["macro_view"] = _safe("マクロ環境", lambda: macro_mod.build(
        bars_mod.load().get("macro"), payload.get("macro"), payload.get("press"), payload.get("kabutan")))
    if payload["macro_view"]:
        hist_patch[slot]["macro_headline"] = payload["macro_view"]["headline"]

    # 決算から読む（会社の説明・決算速報・類似銘柄・業種の風向き）。理由づけは Routine が ai_earnings に書く
    print("  [決算] 決算・修正の開示を読んでいます...")
    earn_rows = payload.pop("_earn_rows", None) or []
    payload["earnings"] = _safe("決算から読む", lambda: _build_earnings(slot, target_date, payload, earn_rows))
    if not payload["earnings"]:
        # 取り直しで情報を減らさない（同じ日・同じ区分で前回読めていれば引き継ぐ）
        latest = store.load_latest()
        if latest.get("date") == target_date.isoformat():
            payload["earnings"] = (((latest.get("slots") or {}).get(slot) or {}).get("data") or {}).get("earnings")

    # 売買タブの買う候補に、決算の材料（自社の決算・類似銘柄の決算・次の決算日）を添える。注文は変えない（DESIGN.md 23章）
    payload["picks"] = _safe("決算の材料（売買）", lambda: _build_picks(target_date, payload.get("newsflow")))

    store.save_slot(target_date, slot, payload)
    store.update_history(target_date, hist_patch)
    removed = store.prune_history()
    if removed:
        print(f"  🧹 古い履歴を {removed} 件削除")

    print(f"\n✅ {slot} を更新しました → {store.latest_path()}")
    return payload


def _build_picks(target_date: date, newsflow: dict | None = None) -> dict | None:
    thermo = thermo_run._read(THERMO_PATH, None)
    out = picks_mod.build(thermo, earnings_mod.load(), themes_mod.load_themes(), target_date.isoformat(), newsflow)
    if out:
        track = thermo_run._read(SWING_TRACK_PATH, None)
        if picks_mod.tag_track(track, out.get("asof"), out["earn"]):
            thermo_run._write(SWING_TRACK_PATH, track, indent=0)
        print(f"    ✅ 決算の材料: 候補 {len(out['earn'])} 銘柄、材料の監視 {len(out['watch'])} 銘柄")
    return out


def _build_earnings(slot: str, target_date: date, payload: dict, rows: list[dict]) -> dict | None:
    """寄り前は前営業日の引け後の開示を、前場・大引はその日の開示を読む。"""
    prev_day, mkt = None, None
    if slot == "preopen":
        asof = ((payload.get("carryover") or {}).get("prev_session") or {}).get("date")
        scope = "前営業日の引け後"
    else:
        asof, scope = target_date.isoformat(), ("今日の開示（前場まで）" if slot == "zenba" else "今日の開示")
        if slot == "taibike" and not ((payload.get("indices") or {}).get("nikkei") or {}).get("stale"):
            # 決算への株価の反応を記録する（前営業日の引け後の開示と今日の場中の開示）。市場＝225採用銘柄の前日比の中央値
            pcts = [c["change_pct"] for c in payload.get("constituents") or [] if c.get("change_pct") is not None]
            mkt = median(pcts) if len(pcts) >= 100 else None
            prev = store.previous_sessions(target_date, count=1)
            prev_day = prev[0].get("date") if prev else None
    if not asof or (not rows and mkt is None):
        return None
    members = {c["code"]: {"sector": c.get("sector"), "name": c.get("name")} for c in nikkei225._load_cache()}
    themes = themes_mod.load_themes()
    focus = set(members) | set((themes.get("stocks") or {}).keys()) | set(store.load_watchlist().get("codes", []))
    focus |= {e["code"] for e in ledger_mod.load_ledger().get("entries") or [] if e.get("status") == "watching"}
    bars = bars_mod.load()
    return earnings_mod.build(
        slot, target_date, rows, asof, scope, focus=focus, themes=themes, members=members,
        articles=(payload.get("kabutan") or {}).get("articles") or [],
        quotes_of=lambda codes: _safe("類似銘柄の株価", lambda: cnbc.fetch_jp_stocks(codes), {}) or {},
        closes_of=lambda code: bars_mod.stock_map(bars, code),
        names_of=names.known, prev_day=prev_day, mkt=mkt)


def refresh_watchlist() -> dict:
    """アプリからウォッチリストを保存したとき（watchlist.json の push）に走る。

    区分を作り直さず、latest.json の既存の各区分の watchlist だけを差し替える。
    以前は通常の収集（--slot auto）を走らせていたが、休場日や時間外に走ると
    前営業日の区分が消え、休場日の区分と履歴ができてしまっていた（2026-09-26 土曜の実測）。
    ランキング・開示との突き合わせは、その区分が保存済みの一覧で行う。
    """
    latest = store.load_latest()
    slots = latest.get("slots") or {}
    if not slots:
        print("  latest.json に区分が無いため、ウォッチリストは次回の収集で反映します")
        return latest
    codes = store.load_watchlist().get("codes", [])
    quotes = {}
    if codes:
        print(f"  [CNBC] ウォッチリスト {len(codes)} 銘柄を取得中...")
        quotes = _safe("ウォッチリスト", lambda: cnbc.fetch_jp_stocks(codes), {}) or {}
    for name, s in slots.items():
        data = s.get("data") or {}
        if name == "preopen":
            tables, disc = {}, (data.get("carryover") or {}).get("after_hours_kessan") or []
        else:
            tables = data.get("tables") or {}
            disc = [r for key in ("kessan_after", "kessan_intraday")
                    for r in (tables.get(key) or {}).get("rows") or []]
        data["watchlist"] = _fetch_watchlist(tables, disc, quotes)
        print(f"    ✅ {name}: {len(data['watchlist'])} 銘柄")
    latest["generated_at"] = store.now_jst().isoformat(timespec="seconds")
    store.save_latest(latest)
    return latest


def main(argv=None):
    parser = argparse.ArgumentParser(description="マーケットダッシュボードのデータ生成")
    parser.add_argument("--slot", choices=SLOTS + ["auto", "watchlist"], default="auto")
    parser.add_argument("--date", help="対象日 YYYYMMDD（省略時は今日）")
    parser.add_argument("--insurance", action="store_true",
                        help="保険の cron。その日の区分が既にあれば何もしない（遅れて発火した実行で上書きしない）")
    args = parser.parse_args(argv)

    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y%m%d").date()
        except ValueError:
            parser.error(f"日付形式が不正です: {args.date}（例: 20260906）")

    if args.slot == "watchlist":
        refresh_watchlist()
        return
    slot = resolve_slot() if args.slot == "auto" else args.slot
    if args.insurance:
        slot, day = insurance_target(slot)
        if target_date is None and day != store.now_jst().date():
            print(f"  ⚠️  保険の実行が日付をまたいで遅れたため、{day} の大引として扱います")
            target_date = day
        if already_done(slot, target_date or day):
            print(f"  {slot} は {target_date or day} の分がすでに更新済みです（Routine の合図で実行済み）。保険の実行は見送ります")
            return
    run(slot, target_date)


if __name__ == "__main__":
    main()
