"""相場温度計の実行（入出力の層）。計算は thermo.py の純関数、ここはつなぐだけ。

  1. 日足キャッシュを更新する（マクロは毎スロット末尾だけ、個別株は大引で全銘柄・ほかは不足分だけ）
  2. 日経平均の PER を取り足す（寄り前・大引）
  3. 履歴から業績修正・見出しの論調の日次を集め、当日の値（場中値）を差し込んで計算する
  4. 短期の押し目買い（swing.py）: 四本値から今日の注文・もうすぐ注文対象・ルールの検証を出す
  5. docs/data/thermo.json（アプリが読む全体）を書き、latest.json には要約だけを載せる
  6. 大引では、出した注文を docs/data/swing_track.json に記録して結果を四本値で測り、
     業種・警告の判定を docs/data/thermo_track.json に記録して 5日／20日後の成績を更新する

どの段階で失敗しても例外を外に出さない（収集は完走させる）。取れなかった材料は
「材料不足」として温度計から外し、何軸で計算したかを必ず出す。
"""
import json
import os
from datetime import date, timedelta

from . import bars as bars_mod, crowd as CW, hold as HD, sectors as X, spill as SP, store, supply as SU, swing as W, thermo as T
from .config import MARGIN_FETCH_MAX, MARGIN_PATH, SWING_TRACK_PATH, THERMO_PATH, THERMO_TRACK_PATH
from . import ledger as ledger_mod
from .ledger import load_ledger
from .sources import cnbc, margin as margin_src, nikkei225, nikkei_per, press
from .themes import load_themes

HIST_DAYS = 40


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent,
                  separators=None if indent is not None else (",", ":"))
    os.replace(tmp, path)


def next_weekday(iso: str) -> str:
    """注文が有効な日（引けの日の次の平日。祝日は分からないので平日で数える。アプリの nextWeekday と同じ）。"""
    d = date.fromisoformat(iso) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def _trading(hs: dict) -> bool:
    """休場日の履歴（前営業日の値が stale 付きで入っている）を除く。"""
    for slot in ("taibike", "zenba"):
        nk = ((hs.get(slot) or {}).get("indices") or {}).get("nikkei")
        if nk:
            return not nk.get("stale")
    return True


def _day_material(hs: dict) -> list[dict]:
    for slot in ("taibike", "zenba"):
        mat = (hs.get(slot) or {}).get("material")
        if mat is not None:
            return mat
    # 旧い履歴には引け後の開示（最大40件）しか無い
    return T.material_events((hs.get("taibike") or {}).get("after_hours") or [], hs.get("date"))


def _day_tone(hs: dict) -> dict | None:
    for slot in ("taibike", "zenba", "preopen"):
        t = (hs.get(slot) or {}).get("news_tone")
        if t:
            return t
    return None


def headlines(payload: dict) -> list[str]:
    kb = payload.get("kabutan") or {}
    titles = [h.get("title") for h in kb.get("headlines") or []]
    titles += [a.get("headline") for a in kb.get("articles") or []]
    titles += [n.get("headline") for n in payload.get("news") or []]
    titles += press.tone_titles(payload.get("press"))   # 報道各社（日本語）の見出しも数える
    seen, out = set(), []
    for t in titles:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _today_events(payload: dict, today: str) -> list[dict]:
    tables = payload.get("tables") or {}
    rows = list((tables.get("kessan_intraday") or {}).get("rows") or []) + \
        list((tables.get("kessan_after") or {}).get("rows") or [])
    return T.material_events(rows, today)


def _live_prices(payload: dict, stale: bool) -> dict[str, float]:
    if stale:
        return {}
    out = {}
    for r in payload.get("constituents") or []:
        if r.get("price") is not None:
            out[str(r["code"])] = r["price"]
    for w in payload.get("watchlist") or []:
        if w.get("price") is not None and w.get("code"):
            out.setdefault(str(w["code"]), w["price"])
    return out


def run(slot: str, target_date: date, payload: dict, sessions: list[dict], fetch=None, fetch_ohlc=None,
        fetch_quotes=None, fetch_margin=None) -> dict:
    """温度計を計算して thermo.json を書き、latest.json に載せる要約と履歴に残す断片を返す。

    fetch だけを渡した場合（tests）は四本値・引け後のクォートを取りに行かない。"""
    if fetch is None:
        fetch, fetch_ohlc = cnbc.fetch_bars, fetch_ohlc or cnbc.fetch_ohlc
        fetch_quotes = fetch_quotes or cnbc.fetch_jp_stocks
        fetch_margin = fetch_margin or (lambda c: margin_src.fetch(c, target_date))
    today = target_date.isoformat()
    nk_live = ((payload.get("indices") or {}).get("nikkei") or {})
    stale = bool(nk_live.get("stale")) if nk_live else True      # 休場日（前営業日の値）
    tone_today = T.headline_tone(headlines(payload), (load_themes().get("themes") or []))
    events_today = _today_events(payload, today) if slot != "preopen" else []
    hist_patch = {"news_tone": tone_today}
    if slot != "preopen":
        hist_patch["material"] = events_today

    # ---- 1. 日足 ----
    data = bars_mod.load()
    ohlc = bars_mod.load_ohlc()
    try:
        bars_mod.update_macro(data, fetch, target_date)
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  マクロ日足の更新で例外: {e}")

    components = nikkei225._load_cache()
    members = {c["code"]: {"sector": c.get("sector"), "name": c.get("name")} for c in components}
    themes = load_themes()
    watch = store.load_watchlist().get("codes", [])
    ledger = load_ledger()
    led_codes = [e["code"] for e in ledger.get("entries") or [] if e.get("status") == "watching"]

    trade_sessions = [s for s in sessions if _trading(s)][:HIST_DAYS]
    hist_events = []
    for hs in trade_sessions[:10]:
        hist_events += _day_material(hs)
    recent_events = events_today + hist_events
    names = {}
    for code, e in (themes.get("stocks") or {}).items():
        names[code] = e.get("name")
    for e in ledger.get("entries") or []:
        names.setdefault(e["code"], e.get("name"))
    for e in recent_events:
        names.setdefault(e["code"], e.get("name"))
    for code, info in members.items():
        names[code] = info.get("name") or names.get(code)
    for w in payload.get("watchlist") or []:
        if w.get("code") and w.get("name"):
            names.setdefault(w["code"], w["name"])

    universe = list(dict.fromkeys(
        list(members) + watch + led_codes + list((themes.get("stocks") or {}).keys())
        + [e["code"] for e in recent_events]))
    universe = [c for c in universe if c and len(c) == 4][:520]
    # 大引（16:45）の時点では、CNBC の日足にまだ当日の分が無いことがある（2026-09-28 の実測: 16:57 でも前営業日まで）。
    # そのままだと翌営業日の注文が出ないので、寄り前に個別株の日足が日経平均の日足より遅れていれば取り直す
    nk_last = (bars_mod.series(data, "nikkei") or [("", None)])[-1][0]
    lagging = bool(ohlc.get("dates")) and ohlc["dates"][-1] < nk_last
    try:
        if slot == "taibike" or not data.get("stocks") or (slot == "preopen" and lagging):
            if slot == "preopen":
                print(f"    ↻ 個別株の日足が {ohlc['dates'][-1]} まで（日経平均は {nk_last} まで）。取り直して、"
                      "前の営業日の引けの注文を寄りの前に出します")
            bars_mod.update_stocks(data, universe, fetch, target_date, ohlc=ohlc, fetch_ohlc=fetch_ohlc)
        else:
            missing = [c for c in universe if c not in (data.get("stocks") or {})
                       or (fetch_ohlc and c not in (ohlc.get("stocks") or {}))]
            if missing:
                bars_mod.add_missing_stocks(data, missing, fetch, target_date, ohlc=ohlc, fetch_ohlc=fetch_ohlc)
    except Exception as e:                      # noqa: BLE001
        print(f"    ⚠️  個別株日足の更新で例外: {e}")
    # 大引で日足に当日の分がまだ無ければ、引け後のクォートから足す（その日の引けで翌営業日の注文を出すため）
    if slot == "taibike" and not stale and fetch_quotes and nk_live.get("close"):
        try:
            nk_now = (bars_mod.series(data, "nikkei") or [("", None)])[-1][0]
            if nk_now < today:
                bars_mod.append_today(data, ohlc, today, nk_live["close"], fetch_quotes(universe))
        except Exception as e:                  # noqa: BLE001  収集は止めない
            print(f"    ⚠️  引け後のクォートから当日の日足を足す処理で例外: {e}")
    data["updated_at"] = store.now_jst().isoformat(timespec="seconds")
    bars_mod.save(data)
    if fetch_ohlc:
        ohlc["updated_at"] = data["updated_at"]
        bars_mod.save_ohlc(ohlc)

    # 発掘台帳の追跡を東証の営業日で測り直す（休場日の実行や古い気配で d1/d5/d20 がずれないように）
    if slot == "taibike" and not stale and ledger.get("entries"):
        try:
            nk_close = dict(bars_mod.series(data, "nikkei"))
            maps: dict[str, dict] = {}

            def close_of(code, d):
                if code not in maps:
                    maps[code] = bars_mod.stock_map(data, code)
                return maps[code].get(d)
            ledger_mod.retrack(ledger, [d for d in data.get("dates") or [] if d <= today], close_of, nk_close.get)
            ledger_mod.save_ledger(ledger)
        except Exception as e:                  # noqa: BLE001  収集は止めない
            print(f"    ⚠️  台帳の測り直しで例外: {e}")

    # ---- 2. PER ----
    per = nikkei_per.update() if slot in ("preopen", "taibike") else nikkei_per.load_cache()

    # ---- 3. 計算 ----
    live_nk = nk_live.get("close") if (nk_live and not stale) else None
    m = {spec_key: bars_mod.series(data, spec_key) for spec_key in (data.get("macro") or {})}
    m["nikkei"] = T.with_live(m.get("nikkei") or [], today, live_nk)
    live = _live_prices(payload, stale)

    nk_map = dict(m["nikkei"])
    eps = sorted((d, nk_map[d] / p) for d, p in per.items() if d in nk_map and p)
    revisions = []
    for hs in reversed(trade_sessions[:25]):
        ev = _day_material(hs)
        revisions.append((hs.get("date"), sum(e["dir"] == "up" for e in ev), sum(e["dir"] == "down" for e in ev)))
    if slot != "preopen":
        revisions.append((today, sum(e["dir"] == "up" for e in events_today),
                          sum(e["dir"] == "down" for e in events_today)))
    tones = [(hs.get("date"), _day_tone(hs)) for hs in reversed(trade_sessions[:5])]
    tones = [(d, t) for d, t in tones if t]
    tones.append((today, tone_today))
    news = [(d, t["pos"], t["neg"]) for d, t in tones]

    dates = list(data.get("dates") or [])
    stocks = {c: list(a) for c, a in (data.get("stocks") or {}).items()}
    if live and dates:
        if dates[-1] < today:
            dates.append(today)
            for c, a in stocks.items():
                a.append(live.get(c))
        elif dates[-1] == today:
            for c, a in stocks.items():
                if live.get(c) is not None:
                    a[-1] = live[c]
    member_stocks = {c: a for c, a in stocks.items() if c in members}
    breadth = T.breadth_series(dates, member_stocks)

    extras = {"eps": eps, "revisions": revisions, "news": news, "breadth": breadth}
    market = T.market_thermo(m, extras=extras)
    bt = T.backtest(m)
    drivers = T.macro_drivers(m)
    sectors = T.sector_board(dates, member_stocks, members, drivers, recent_events)
    sector_of = {c: info.get("sector") for c, info in members.items()}
    sector_cls = {s["sector"]: s["class"] for s in sectors}
    sector_d1 = {s["sector"]: s.get("d1") for s in sectors}
    theme_rows = T.theme_board(dates, stocks, themes, [t for _, t in tones[-3:]])

    def closes(code):
        return [x for x in stocks.get(code) or [] if x is not None]

    def price_on(code, d, before):
        arr = stocks.get(code) or []
        best = None
        for dd, c in zip(dates, arr):
            if c is None:
                continue
            if (dd < d) if before else (dd <= d):
                best = c
        return best

    ex = T.exhaustion(recent_events, price_on, today)
    ex_by_code = {}
    for e in ex:
        ex_by_code.setdefault(e["code"], e)

    metrics = {}
    for code in stocks:
        mt = T.stock_metrics(closes(code))
        if mt:
            metrics[code] = mt

    # ---- 4. 短期の押し目買い（確定した四本値だけで計算する。場中値は使わない） ----
    ind_cache: dict[str, dict | None] = {}

    def ind_of(code):
        if code not in ind_cache:
            bars = W.compact(ohlc.get("dates") or [], (ohlc.get("stocks") or {}).get(code))
            ind_cache[code] = W.indicators(bars) if len(bars) >= 30 else None
        return ind_cache[code]

    sw_rows = {}
    for code in (ohlc.get("stocks") or {}):
        ind = ind_of(code)
        row = W.today_row(ind) if ind else None
        if row:
            sw_rows[code] = row
    swing = swing_block(ohlc, sw_rows, ind_of, names, sector_of, themes.get("stocks") or {})
    # 保有株の判定（自分の判断で持っている銘柄向け。確定した日足だけで決める。前場の値はアプリが突き合わせる）
    hold_rows = {}
    for code in sw_rows:
        try:
            r = HD.today_row(ind_of(code))
        except Exception:                       # noqa: BLE001  1銘柄の不具合で止めない
            r = None
        if r:
            hold_rows[code] = r
    try:
        hold = hold_block({c: ind_of(c) for c in sw_rows}, dict(bars_mod.series(data, "nikkei")), hold_rows)
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  保有株の判定で例外: {e}")
        hold = None
    # 業種の強弱（押し目買いと同じ業種のグループ。注文の条件には使わない）
    try:
        strength = strength_block(ohlc, W.peer_groups(list(sw_rows), themes.get("stocks") or {}, sector_of),
                                  sector_of, names, recent_events)
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  業種の強弱で例外: {e}")
        strength = None

    # 急騰して混み合った銘柄の印と、主役の入れ替わり（予測ではなく、明日の振れ幅と今日の資金の移り先。注文には使わない）
    try:
        crowd, crowd_rows = CW.block(ohlc.get("dates") or [], ohlc.get("stocks") or {}, names, themes.get("stocks") or {})
        # ウォッチリスト（保有株はここに入れておく）の銘柄のうち、印が付いた・主役の入れ替わりに入ったもの。Routine が名前を挙げる
        rot = crowd.get("rotation") or {}
        rot_of = {x["code"]: (k, x) for k, lst in (("out", rot.get("out") or []), ("into", rot.get("into") or [])) for x in lst}
        crowd["watch"] = [{"code": c, "name": names.get(c) or c, "cw": crowd_rows.get(c),
                           "rot": rot_of[c][0] if c in rot_of else None,
                           "prev": rot_of[c][1]["prev"] if c in rot_of else None, "now": rot_of[c][1]["now"] if c in rot_of else None}
                          for c in watch if c in crowd_rows or c in rot_of]
        print(f"    ✅ 混み合い: 印 {len(crowd_rows)}銘柄、入れ替わり 主役→置き去り {rot.get('n_out')}・"
              f"出遅れ→買われ {rot.get('n_into')}、ウォッチに該当 {len(crowd['watch'])}")
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  混み合いで例外: {e}")
        crowd, crowd_rows = None, {}

    # 同業の急騰・急落と、動かなかった仲間（連想の材料と、その検証。注文には使わない。spill.py）
    try:
        spill = SP.block(ohlc.get("dates") or [], ohlc.get("stocks") or {},
                         W.peer_groups(list(sw_rows), themes.get("stocks") or {}, sector_of), names)
        if spill:
            print(f"    ✅ 同業の急騰・急落: 今日 {len(spill['today'])}件")
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  同業の急騰・急落で例外: {e}")
        spill = None

    # 需給: 上値のしこり（日足から全銘柄）と信用残（Yahoo の銘柄ページ。週1回の残高。大引でだけ取りに行く）。注文には使わない
    try:
        mstore = _read(MARGIN_PATH, {}) or {}
        if slot == "taibike" and not stale and fetch_margin:
            first = watch + [o["code"] for o in (swing.get("orders") or []) + (swing.get("near") or [])
                             + ((swing.get("mid") or {}).get("orders") or [])]
            n_got = margin_update(mstore, list(dict.fromkeys(first + universe)), set(first), fetch_margin, today)
            mstore["updated_at"] = store.now_jst().isoformat(timespec="seconds")
            _write(MARGIN_PATH, mstore, indent=0)
            print(f"    ✅ 信用残: {n_got}銘柄を取り直し（たまっている {len(mstore.get('stocks') or {})}銘柄）")
        supply, jk_rows, mg_rows = SU.block(ohlc.get("dates") or [], ohlc.get("stocks") or {}, mstore)
        supply["watch"] = [{"code": c, "name": names.get(c) or c, "jk": jk_rows.get(c), "mg": mg_rows.get(c)}
                           for c in watch if c in jk_rows or c in mg_rows]
        print(f"    ✅ 需給: しこり {supply['n']}銘柄（多い {supply['n_heavy']}）・信用残 {supply['n_margin']}銘柄")
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  需給で例外: {e}")
        supply, jk_rows, mg_rows = None, {}, {}

    stock_rows = {}
    for code, mt in metrics.items():
        cls = T.stock_class(mt)
        e = ex_by_code.get(code)
        ev = ({"label": e["label"], "tone": e["tone"], "dir": e["dir"],
               "date": e["date"], "since": e["since_pct"], "title": e["title"]} if e else None)
        stock_rows[code] = {
            "n": names.get(code) or code, "s": sector_of.get(code),
            "th": ((themes.get("stocks") or {}).get(code) or {}).get("themes") or [],
            **mt, "cls": cls, "ev": ev,
            "sc": sector_cls.get(sector_of.get(code)),
            "sw": _sw_brief(sw_rows.get(code)),
            "hd": _hd_brief(hold_rows.get(code)),
        }
        if code in crowd_rows:
            stock_rows[code]["cw"] = crowd_rows[code]
        if code in jk_rows:
            stock_rows[code]["jk"] = {k: v for k, v in jk_rows[code].items() if k != "asof"}
        if code in mg_rows:
            stock_rows[code]["mg"] = mg_rows[code]

    def pick_list(kind, key_fn, limit, reverse=False):
        rows = [dict(code=c, **r) for c, r in stock_rows.items() if kind in r["cls"]]
        rows.sort(key=key_fn, reverse=reverse)
        return [_brief(r) for r in rows[:limit]]

    lists = {
        "hot": pick_list("高値掴み注意", lambda r: -max((r.get("rsi") or 0) - 75, (r.get("dev25") or 0) - 15,
                                                     (r.get("r5") or 0) - 15), 10),
        "bad_out": [_ev_brief(e, stock_rows) for e in ex if e["label"].startswith("悪材料出尽くし")][:8],
        "good_out": [_ev_brief(e, stock_rows) for e in ex if e["label"] == "好材料出尽くし"][:8],
    }
    nk_d1 = None
    nkx = [c for _, c in m["nikkei"]]
    if len(nkx) > 1:
        nk_d1 = T.r2(T.ret(nkx, 1))
    guard = watch_guard(watch, stock_rows, sector_d1, nk_d1, recent_events, today)

    thermo = {
        "asof": today, "slot": slot, "generated_at": store.now_jst().isoformat(timespec="seconds"),
        "market": market, "backtest": bt,
        "drivers": [{"key": k, "label": T.DRIVER_LABEL[k], **v} for k, v in drivers.items()],
        "sectors": sectors, "themes": theme_rows[:14], "lists": lists, "watch_guard": guard,
        "swing": swing, "hold": hold, "strength": strength, "crowd": crowd, "spill": spill, "supply": supply, "stocks": stock_rows, "live": bool(live),
        "coverage": {"macro": len(data.get("macro") or {}), "stocks": len(stock_rows),
                     "stock_days": len(dates), "eps_days": len(eps), "news_titles": tone_today["n"],
                     "ohlc_stocks": len(sw_rows), "ohlc_days": len(ohlc.get("dates") or [])},
    }

    # 出した注文の記録と結果（大引のみ。休場日は進めない）。結果は四本値で swing.simulate と同じ規則で測る
    sw_track = _read(SWING_TRACK_PATH, {})
    # 中期の押し目は、枠（6銘柄）の数まで上から記録する（短期は注文の5件）
    mid_orders = ((swing.get("mid") or {}).get("orders") or [])[:W.MID_SLOTS]
    # 大引の時点で当日の日足が無かった日は、寄り前に初めて注文が出る。その注文（今日有効）も実績に記録する
    fresh = slot == "preopen" and swing.get("asof") and next_weekday(swing["asof"]) >= today \
        and (swing.get("orders") or mid_orders)
    if (slot == "taibike" and not stale) or fresh:
        mid_prev = sw_track.get("mid") or {}
        new = W.paper_update(sw_track, today, swing.get("orders") or [], ind_of)
        if mid_orders or mid_prev.get("orders"):
            new["mid"] = W.paper_update(mid_prev, today, mid_orders, ind_of, mid=True)
        if new.get("orders") or new.get("mid"):
            sw_track = new
            _write(SWING_TRACK_PATH, sw_track, indent=0)
    close_maps: dict[str, dict] = {}

    def close_of(code, d):
        if code not in close_maps:
            ind = ind_of(code)
            close_maps[code] = dict(zip(ind["d"], ind["c"])) if ind else {}
        return close_maps[code].get(d)
    try:
        paper_acct = W.paper_account(sw_track, ohlc.get("dates") or [], close_of, sector_of,
                                     mid_track=sw_track.get("mid"))
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  注文の実績（口座）で例外: {e}")
        paper_acct = None
    swing["paper"] = {**(sw_track.get("stats") or {}),
                      "recent": [o for o in (sw_track.get("orders") or [])][-40:], "account": paper_acct}
    mt = sw_track.get("mid") or {}
    if swing.get("mid") is not None and mt.get("orders"):
        swing["mid"]["paper"] = {**(mt.get("stats") or {}), "recent": mt["orders"][-40:]}

    # ---- 5. 提案の記録（大引のみ。休場日は記録しない＝営業日として数えない） ----
    track = _read(THERMO_TRACK_PATH, {})
    if slot == "taibike" and not stale:
        picks = []
        for s in [x for x in sectors if is_pick(x)][:3]:
            picks.append({"kind": "注目業種", "key": s["sector"], "name": s["sector"], "price": None})
        for s in sectors:
            if s["class"] == "過熱":
                picks.append({"kind": "過熱業種", "key": s["sector"], "name": s["sector"], "price": None})
        # 買う側は swing_track.json（注文の実績）で測る。ここは業種と「避ける」側の判定だけ
        for kind, key, n in (("高値掴み注意", "hot", 5), ("悪材料出尽くし", "bad_out", 5),
                             ("好材料出尽くし", "good_out", 5)):
            for r in lists[key][:n]:
                picks.append({"kind": kind, "key": r["code"], "name": r["name"], "price": r["price"]})

        by_sector = {}
        for c, info in members.items():
            by_sector.setdefault(info.get("sector"), []).append(c)

        def ret_of(e):
            if e["kind"] in ("注目業種", "過熱業種"):
                rs = []
                for c in by_sector.get(e["key"], []):
                    base, now = price_on(c, e["date"], False), price_on(c, today, False)
                    if base and now:
                        rs.append(T.pct(now, base))
                return sum(rs) / len(rs) if rs else None
            now = price_on(e["key"], today, False)
            return T.pct(now, e.get("price")) if now and e.get("price") else None

        track = T.track_update(track, today, picks, ret_of, live_nk or (nkx[-1] if nkx else None))
        _write(THERMO_TRACK_PATH, track, indent=0)
    thermo["track"] = track.get("stats") or []
    _write(THERMO_PATH, thermo)

    if market and market.get("temp") is not None:
        print(f"    ✅ 相場温度 {market['temp']}（{market['zone']}）"
              f" 追い風 {market['tailwind']}／向かい風 {market['headwind']}／{market['n']}軸"
              + (f"、{market['consensus']}" if market.get("consensus") else ""))
    hist_patch["thermo"] = ({"temp": market.get("temp"), "zone": market.get("zone"),
                             "scores": {f["key"]: f["score"] for f in market.get("factors") or []}}
                            if market else None)
    return {"summary": summary(thermo), "history": hist_patch}


CONTRA = ("押し目", "売られすぎ・下げ止まり")


def margin_update(mstore: dict, codes: list[str], first: set[str], fetch_margin, today: str,
                  limit: int = MARGIN_FETCH_MAX) -> int:
    """信用残を取り直す。ウォッチ・注文の銘柄は残高の週が古ければ毎日、ほかは3日おきに、上限まで。取れた銘柄の数を返す。"""
    got = tried = 0
    for code in codes:
        if tried >= limit:
            break
        if not SU.due(mstore, code, today, every=1 if code in first else 3):
            continue
        tried += 1
        try:
            rec = fetch_margin(code)
        except Exception:                       # noqa: BLE001  1銘柄の不具合で止めない
            rec = None
        e = mstore.setdefault("stocks", {}).setdefault(code, {"hist": []})
        e["fetched"] = today
        if rec:
            SU.merge_margin(mstore, code, rec, today)
            got += 1
    return got


def is_pick(s: dict) -> bool:
    """注目業種は逆張りで拾う側（押し目・下げ止まり）に限る。素直な上昇トレンドは「押しを待つ」側。"""
    return s["pick"] > 0 and s["class"] in CONTRA


def _brief(r: dict) -> dict:
    return {"code": r["code"], "name": r["n"], "sector": r.get("s"), "price": r.get("price"),
            "d1": r.get("d1"), "r5": r.get("r5"), "r20": r.get("r20"), "r60": r.get("r60"),
            "rsi": r.get("rsi"), "dev25": r.get("dev25"), "ma25": r.get("ma25"),
            "to_ma25": T.r1(T.pct(r.get("ma25"), r.get("price"))) if r.get("ma25") else None}


def _ev_brief(e: dict, rows: dict) -> dict:
    r = rows.get(e["code"]) or {}
    return {"code": e["code"], "name": r.get("n") or e.get("name"), "label": e["label"], "dir": e["dir"],
            "date": e["date"], "since": e["since_pct"], "title": e["title"], "price": r.get("price"),
            "rsi": r.get("rsi"), "dev25": r.get("dev25")}


def _sw_brief(row: dict | None) -> dict | None:
    """銘柄ごとの、短期の押し目買いでの位置（アプリの買う前チェック・保有中の売り指値が読む）。"""
    if not row:
        return None
    out = {"st": row["state"], "asof": row["asof"], "sell": row.get("sell"), "atr": row.get("atr"),
           "rsi2": row.get("rsi2"), "tv": row.get("tv"), "up": row.get("up"), "ma200": row.get("ma200")}
    if row.get("trig") is not None:
        out.update({"trig": row["trig"], "to": row.get("to_trig"), "ok": row.get("trig_ok")})
    pc = row.get("peer")
    if pc:
        # 業種の中での位置（g: 比べるグループ、g20: 業種の20日騰落、rel: 業種との差、pc: 押しの形）
        out.update({"g": pc["g"], "g20": pc["g20"], "rel": pc["rel20"], "pc": pc["cls"]})
    if row.get("mid"):
        # 中期の押し目の形（st: signal=注文対象／setup=調整中・押し待ち、rank: 12か月の強さの順位%、hi: 60日高値、
        # age: 高値からの営業日数、dd: 高値からの下げ%、low: 調整の安値、trig/to/ok: 押し待ちの価格）
        out["md"] = row["mid"]
    return out


def _hd_brief(row: dict | None) -> dict | None:
    """銘柄ごとの、保有株の判定（アプリの「保有株」カードと売る前チェックが読む）。"""
    if not row:
        return None
    return {k: row.get(k) for k in ("asof", "c", "cls", "v", "r20", "rsi2", "r5", "dd60", "atr", "sell")}


def hold_block(inds: dict, nk: dict, rows: dict) -> dict:
    """thermo.json の hold。判定の定数と一言、日足キャッシュに毎日当てた検証、研究の値（出口の規則・後場）。"""
    try:
        verify = HD.verify(inds, nk)
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  保有株の判定の検証で例外: {e}")
        verify = None
    cnt = {}
    for r in rows.values():
        cnt[r["v"]] = cnt.get(r["v"], 0) + 1
    print(f"    ✅ 保有株の判定: {len(rows)}銘柄（" + "・".join(f"{HD.VERDICTS[k]['label']} {n}" for k, n in sorted(cnt.items()))
          + "）" + (f"、検証 {verify['from']}〜{verify['to']}" if verify else "、検証なし"))
    return {"rules": {"r_n": HD.R_N, "win": HD.WIN_R, "lose": HD.LOSE_R, "crash": HD.CRASH_R, "dip": HD.DIP_RSI,
                      "bounce": HD.BOUNCE_RSI, "exit_n": HD.EXIT_N, "exec_n": HD.EXEC_N, "gap": HD.GAP,
                      "stop_atr": HD.STOP_ATR, "h": HD.H},
            "verdicts": HD.VERDICTS, "classes": HD.CLASSES, "verify": verify,
            "research": HD.RESEARCH, "exits": HD.EXIT_STATS, "pm": HD.PM_STATS}


NEAR_PCT = -3.0      # 「もうすぐ注文対象」: あと3%以内の下げで入口に入り、その価格でも上昇トレンドを保つ


def swing_block(ohlc: dict, rows: dict, ind_of, names: dict, sector_of: dict, theme_stocks: dict | None = None) -> dict:
    """thermo.json の swing（売買タブの中心）。注文・次点・見送り・もうすぐ注文対象・業種の中の位置・ルールの検証。

    注文は最新の日付（asof）の引けまで日足がそろった銘柄からだけ出す。大引の時点で当日の日足がまだ無い銘柄は、
    前の営業日の引けの注文（もう期限が過ぎている）を出さない。業種と比べるのも asof の日足がそろった銘柄どうしだけ。"""
    asof = max((r["asof"] for r in rows.values()), default=None)
    groups = W.peer_groups(list(rows), theme_stocks or {}, sector_of)
    rets = {}
    for c, r in rows.items():
        ind = ind_of(c)
        if r["asof"] == asof and ind:
            rets[c] = W.ret_n(ind, len(ind["c"]) - 1)
    ctx = W.peer_context(rets, groups)
    for c, r in rows.items():
        r["peer"] = ctx.get(c)
    mid = mid_block(rows, ind_of, names, sector_of, asof)
    mid_codes = {o["code"] for o in mid["orders"]}
    sig = []
    for code, r in rows.items():
        # 中期の形の押しは中期の計画で出す（1銘柄に1つの計画）
        if r["state"] != "signal" or r["asof"] != asof or code in mid_codes:
            continue
        o = W.order_of(ind_of(code))
        if o:
            sig.append({"code": code, "name": names.get(code) or code, "sector": sector_of.get(code),
                        **o, "dev25": r.get("dev25"), "r5": r.get("r5"), "rsi2": r.get("rsi2"), "tv": r.get("tv"),
                        "peer": r.get("peer")})
    orders, more, skip = W.rank_orders(sig, sector_of)
    near = [{"code": c, "name": names.get(c) or c, "sector": sector_of.get(c), "price": r["price"],
             "trig": r["trig"], "to": r["to_trig"], "rsi2": r.get("rsi2"), "dev25": r.get("dev25"),
             "peer": r.get("peer")}
            for c, r in rows.items() if r["state"] == "wait" and r.get("trig_ok") and r.get("to_trig") is not None
            and r["to_trig"] >= NEAR_PCT and r["asof"] == asof
            and (r.get("peer") or {}).get("cls") not in W.PEER_SKIP and not r.get("mid")]
    near.sort(key=lambda x: (-x["to"], x["code"]))
    board = W.peer_board(ctx, rets, groups)
    for g in board:
        for m in g["members"]:
            r = rows.get(m["code"]) or {}
            m.update({"name": names.get(m["code"]) or m["code"], "st": r.get("state"), "trig": r.get("trig"), "to": r.get("to_trig"),
                      "ok": r.get("trig_ok"), "pc": (r.get("peer") or {}).get("cls"), "price": r.get("price")})
    try:
        verify = W.verify({c: W.compact(ohlc.get("dates") or [], (ohlc.get("stocks") or {}).get(c))
                           for c in rows}, groups=groups, sector_of=sector_of)
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  短期の押し目買いの検証で例外: {e}")
        verify = None
    if orders:
        print(f"    ✅ 短期の押し目買い: 注文 {len(orders)}（次点 {len(more)}・見送り {len(skip)}）、もうすぐ {len(near)}、"
              f"{asof} の引けから")
    else:
        print(f"    ✅ 短期の押し目買い: 注文なし（見送り {len(skip)}）、もうすぐ {len(near)}、{asof} の引けから")
    print(f"    ✅ 中期の押し目: 注文 {len(mid['orders'])}、調整中の候補 {mid['n_shape']}（押し待ち {len(mid['near'])}）")
    # cal は保有中の営業日を数えるのに使う（中期は60営業日持つ）
    return {"asof": asof, "rules": W.RULES, "classes": W.PEER_CLASSES, "orders": orders, "more": more[:10],
            "skip": skip[:10], "near": near[:12], "peers": board[:12], "groups": len(set(groups.values())),
            "mid": mid, "verify": verify, "cal": (ohlc.get("dates") or [])[-(W.MID_HOLD + 20):]}


def mid_block(rows: dict, ind_of, names: dict, sector_of: dict, asof: str | None) -> dict:
    """中期の押し目（DESIGN.md 21章）: 大きなトレンドの中で1〜3か月調整している銘柄と、今日の引けで出る注文。

    12か月の強さの順位は、asof の日足がそろった銘柄のうち売買代金・株価の条件を満たすものの中で決める。
    rows の各行に mid（形の材料と、注文対象か・押し待ちか）を書き足す（銘柄ごとの sw.md になる）。"""
    states, moms = {}, {}
    for c, r in rows.items():
        ind = ind_of(c)
        if r["asof"] != asof or not ind:
            continue
        i = len(ind["c"]) - 1
        st = W.mid_state(ind, i)
        if st:
            states[c] = st
            if W.mid_liquid(ind, i):
                moms[c] = st["mom"]
    ranks = W.mid_ranks(moms)
    orders, near = [], []
    for c, st in states.items():
        rk = ranks.get(c)
        if not W.is_mid_shape(st, rk):
            continue
        ind = ind_of(c)
        i = len(ind["c"]) - 1
        r = rows[c]
        info = {"rank": round(rk * 100), "hi": st["hi"], "age": st["age"], "dd": W.r1(st["dd"]), "low": st["low"],
                "mom": W.r1(st["mom"]), "st": "setup"}
        r["mid"] = info
        base = {"code": c, "name": names.get(c) or c, "sector": sector_of.get(c), "rank": info["rank"],
                "rsi2": r.get("rsi2"), "dev25": r.get("dev25"), "peer": r.get("peer")}
        if W.is_mid_signal(ind, i, st, rk):
            info["st"] = "signal"
            orders.append({**base, **W.mid_order_of(ind, st), "key": W.r2(W.mid_key(ind, i))})
            continue
        trig = W.trigger_price(ind, i)
        if trig is not None and trig < r["price"]:
            info.update({"trig": W.r1(trig), "to": W.r1((trig / r["price"] - 1) * 100), "ok": W.mid_holds_at(ind, i, trig)})
        near.append({**base, "price": r["price"], "hi": st["hi"], "age": st["age"], "dd": info["dd"], "low": st["low"],
                     "trig": info.get("trig"), "to": info.get("to"), "ok": info.get("ok")})
    orders.sort(key=lambda o: (o["key"], o["code"]))
    near.sort(key=lambda x: (-(x["to"] if x["to"] is not None else -99), x["code"]))
    return {"orders": orders[:10], "near": near[:20], "n_shape": len(orders) + len(near), "slots": W.MID_SLOTS}


def strength_block(ohlc: dict, groups: dict, sector_of: dict, names: dict, events: list[dict]) -> dict | None:
    """thermo.json の strength（業種タブ）。四本値のキャッシュの終値と売買代金から、業種の強弱と、その物差しの検証。"""
    dates = list(ohlc.get("dates") or [])
    closes, vols = {}, {}
    for c, rows in (ohlc.get("stocks") or {}).items():
        rows = (list(rows or []) + [None] * len(dates))[:len(dates)]
        closes[c] = [r[3] if r and r[3] and r[3] > 0 else None for r in rows]
        vols[c] = [r[4] if r else None for r in rows]
    panel = X.Panel(dates, closes, groups)
    b = X.board(dates, closes, vols, groups, sector_of, events, panel=panel)
    if not b:
        return None
    for r in b["rows"]:
        for m in r["members"]:
            m["name"] = names.get(m["code"]) or m["code"]
    b["verify"] = X.verify(panel)
    top = b["rows"][0]
    print(f"    ✅ 業種の強弱: {b['n']}業種、最も強い「{top['g']}」（{X.QUADS.get(top['quad'])}）、"
          f"最も弱い「{b['rows'][-1]['g']}」")
    return b


def watch_guard(watch: list[str], rows: dict, sector_d1: dict, nk_d1, events: list[dict],
                today: str) -> list[dict]:
    """ウォッチリスト（保有・注目）の銘柄ごとに、衝動に対する一言を機械的に付ける。"""
    own = {}
    for e in events:
        own.setdefault(e["code"], []).append(e)
    out = []
    for code in watch:
        r = rows.get(code)
        if not r:
            continue
        notes = []
        d1 = r.get("d1")
        sw = r.get("sw") or {}
        downs = [e for e in own.get(code, []) if e["dir"] == "down"]
        sd1 = sector_d1.get(r.get("s"))
        if d1 is not None and d1 <= -3:
            if downs:
                notes.append({"tone": "warn", "text": f"下方修正・減配の開示あり（{downs[0]['date']}）。"
                                                      "保有の理由（業績）が崩れていないかを確かめる"})
            elif (nk_d1 is not None and nk_d1 < 0) or (sd1 is not None and sd1 < 0):
                notes.append({"tone": "chance", "text": "個別の悪材料は見当たらず、地合い・業種と一緒の下げ。"
                                                        "狼狽売りになりやすい場面"})
            else:
                notes.append({"tone": "info", "text": "個別に大きく下げた。開示が無いか、ニュースを確かめる"})
        if sw.get("st") == "signal" and sw.get("pc") in W.PEER_SKIP:
            notes.append({"tone": "info", "text": f"押した形だが、業種（{sw.get('g')} {sw.get('g20'):+.1f}%）の上げに沿った押しなので"
                                                  "注文は見送り（検証で勝率が低かった形）"})
        elif (sw.get("md") or {}).get("st") == "signal":
            notes.append({"tone": "chance", "text": "中期の押し目の注文対象（大きなトレンドの中の調整で押した日。売買タブの中期の押し目を参照）"})
        elif sw.get("st") == "signal":
            notes.append({"tone": "chance", "text": "短期の押し目買いの注文対象（売買タブの注文を参照）"})
        if "高値掴み注意" in r["cls"]:
            notes.append({"tone": "warn", "text": "短期で上がりすぎ。買い増しは押すまで待つ"})
        if "売られすぎ・下げ止まり" in r["cls"]:
            notes.append({"tone": "chance", "text": "売られすぎから下げ止まり。ここでの損切りは反発を取り逃がしやすい"})
        if r.get("ev"):
            notes.append({"tone": r["ev"]["tone"], "text": f"{r['ev']['label']}（{r['ev']['date']} の開示から {r['ev']['since']:+.1f}%）"})
        if notes:
            out.append({"code": code, "name": r["n"], "price": r.get("price"), "d1": d1,
                        "rsi": r.get("rsi"), "dev25": r.get("dev25"), "notes": notes})
    return out


def _peer_brief(pc: dict | None) -> dict | None:
    """業種の中での位置を、要約用に（押しの形の名前・比べたグループ・業種の20日騰落・業種との差）。"""
    if not pc:
        return None
    return {"label": W.PEER_CLASSES.get(pc["cls"]), "group": pc["g"], "g20": pc["g20"], "rel20": pc["rel20"]}


def summary(th: dict) -> dict | None:
    """latest.json の各スロットに載せる要約（Routine の分析が読む。アプリは thermo.json を直接読む）。"""
    mk = th.get("market") or {}
    if not mk:
        return None
    sec = th.get("sectors") or []
    sw = th.get("swing") or {}
    ver = sw.get("verify") or {}
    pick = lambda st: {k: st.get(k) for k in ("n", "win", "avg", "pf", "small", "big_loss", "days", "d2")} if st else None   # noqa: E731
    # 口座の再現（本番どおりの置き方）。曲線は thermo.json にだけ置く
    acct = lambda a: {k: a.get(k) for k in ("from", "to", "days", "slot", "cagr", "dd", "util", "m_up", "q_up",   # noqa: E731
                                              "under", "final")} if a else None
    paper = sw.get("paper") or {}
    return {
        "temp": mk.get("temp"), "zone": mk.get("zone"), "tone": mk.get("tone"), "guide": mk.get("guide"),
        "consensus": mk.get("consensus"), "tailwind": mk.get("tailwind"), "headwind": mk.get("headwind"),
        "n": mk.get("n"), "temp_before": mk.get("temp_before"), "turning": mk.get("turning"),
        "improving": mk.get("improving"), "worsening": mk.get("worsening"),
        "factors": [{"key": f["key"], "label": f["label"], "score": f["score"], "change": f.get("change"),
                     "text": "、".join(s["text"] for s in f.get("subs") or [] if s.get("text"))}
                    for f in mk.get("factors") or []],
        "sector_picks": [{"sector": s["sector"], "class": s["class"], "pick": s["pick"],
                          "macro": (s.get("macro") or {}).get("label")} for s in sec if is_pick(s)][:3],
        "sector_hot": [s["sector"] for s in sec if s["class"] == "過熱"][:5],
        "hot": [{"code": r["code"], "name": r["name"], "rsi": r.get("rsi"), "r5": r.get("r5")}
                for r in (th.get("lists") or {}).get("hot", [])[:5]],
        "bad_out": [{"code": r["code"], "name": r["name"], "since": r.get("since")}
                    for r in (th.get("lists") or {}).get("bad_out", [])[:5]],
        "good_out": [{"code": r["code"], "name": r["name"], "since": r.get("since")}
                     for r in (th.get("lists") or {}).get("good_out", [])[:5]],
        "themes": [{"theme": t["theme"], "label": t["label"], "tone": t["tone"]}
                   for t in th.get("themes") or [] if t.get("label")][:5],
        "watch_guard": th.get("watch_guard") or [],
        # 短期の押し目買い: asof の引けで出た注文（次の営業日だけ有効）と、ルールの検証・注文の実績
        "swing": {
            "asof": sw.get("asof"),
            "orders": [{**{k: o.get(k) for k in ("code", "name", "sector", "close", "limit", "to_limit", "stop",
                                                  "stop_pct", "sell", "sell_pct", "floor", "dev25", "rsi2")},
                        "peer": _peer_brief(o.get("peer"))}
                       for o in sw.get("orders") or []],
            "more": len(sw.get("more") or []),
            "skip": [{"code": o["code"], "name": o.get("name"), "peer": _peer_brief(o.get("peer"))}
                     for o in (sw.get("skip") or [])[:5]],
            "near": [{k: x.get(k) for k in ("code", "name", "trig", "to")} for x in (sw.get("near") or [])[:5]],
            "verify": {"all": pick(ver.get("all")), "recent": pick(ver.get("recent")),
                       "base": ver.get("base"), "from": ver.get("from"), "to": ver.get("to"),
                       "peer": {W.PEER_CLASSES[k]: pick(v) for k, v in (ver.get("peer") or {}).items() if v.get("n")},
                       "skipped": pick(ver.get("skipped")) if (ver.get("skipped") or {}).get("n") else None,
                       "account": acct(ver.get("account"))}
            if ver else None,
            "paper": {**pick(paper), "account": acct(paper.get("account"))} if paper.get("n") else None,
            "slot_pct": W.SLOT_PCT,
            # 中期の押し目（大きなトレンドの中の1〜3か月の調整で押した日）。短期の注文とは別の計画（1銘柄に1つ）
            "mid": _mid_brief(sw.get("mid"), ver, pick, acct),
        },
        # 業種の強弱（強い順の上位と下位。先行＝強い・勢いあり、一服、出遅れ、改善＝弱い業種の戻り）
        "strength": _strength_brief(th.get("strength")),
        # 急騰して混み合った銘柄の印（明日の振れ幅）と、主役の入れ替わり（今日の資金の移り先）。方向の予測ではない
        "crowd": _crowd_brief(th.get("crowd")),
        # 同業の急騰・急落と動かなかった仲間（連想の材料）。検証では「動かなかった仲間が追いつく」とは言えなかった
        "spill": _spill_brief(th.get("spill")),
        # 需給（上値のしこり・信用残）。ウォッチと注文の銘柄だけ。注文・判定には使わない
        "supply": _supply_brief(th.get("supply"), th.get("stocks") or {},
                                [o["code"] for o in (sw.get("orders") or []) + ((sw.get("mid") or {}).get("orders") or [])]),
    }


def _jk_brief(code: str, st: dict) -> dict | None:
    jk, mg = st.get("jk"), st.get("mg")
    if not jk and not mg:
        return None
    out = {"code": code, "name": st.get("n")}
    if jk:
        out.update({"above": jk.get("above"), "vwap": jk.get("vwap"), "gap": jk.get("gap"), "wall": jk.get("wall")})
    if mg:
        out["mg"] = {k: mg.get(k) for k in ("date", "buy", "sell", "buy_chg", "ratio", "days", "w", "buy_w", "px_w")}
        out["mg"]["notes"] = [n["t"] for n in mg.get("notes") or []]
    return out


def _supply_brief(su: dict | None, stocks: dict, order_codes: list[str]) -> dict | None:
    if not su:
        return None
    v = (su.get("verify") or {}).get("cls") or {}
    up = {k: [x.get("up") if x else None for x in v.get(k) or []] for k in ("light", "mid", "heavy", "heavier", "all")} if v else None
    watch = [w["code"] for w in su.get("watch") or []]
    return {"rule": (su.get("rules") or {}).get("text"), "h": (su.get("verify") or {}).get("h"), "up": up,
            "n": su.get("n"), "n_heavy": su.get("n_heavy"), "margin_asof": su.get("margin_asof"),
            "watch": [b for b in (_jk_brief(c, stocks.get(c) or {}) for c in watch) if b],
            "orders": [b for b in (_jk_brief(c, stocks.get(c) or {}) for c in dict.fromkeys(order_codes)) if b]}


def _spill_brief(sp: dict | None) -> dict | None:
    if not sp:
        return None
    v = sp.get("verify") or {}
    pick = lambda k: [[x for x in half] for half in v.get(k) or []]     # noqa: E731
    return {"rule": sp.get("rule"), "today": sp.get("today") or [],
            "verify": {"from": v.get("from"), "to": v.get("to"), "split": v.get("split"), "hs": v.get("hs"),
                       "base": pick("base"), "lag": pick("lag"), "ev": pick("ev")} if v else None}


def _crowd_brief(cw: dict | None) -> dict | None:
    if not cw:
        return None
    rot = cw.get("rotation") or {}
    v = (cw.get("verify") or {}).get("cls") or {}
    return {
        "rule": (cw.get("rules") or {}).get("text"), "n_flagged": cw.get("n"),
        "verify": {k: v.get(k) for k in ("hot", "all")} if v else None, "big": (cw.get("verify") or {}).get("big"),
        "watch": cw.get("watch") or [],
        "rotation": {"asof": rot.get("asof"), "prev": rot.get("prev"), "mkt": rot.get("mkt"),
                     "n_out": rot.get("n_out"), "n_into": rot.get("n_into"),
                     "out": [{k: x.get(k) for k in ("code", "name", "th", "prev", "now")} for x in (rot.get("out") or [])[:6]],
                     "into": [{k: x.get(k) for k in ("code", "name", "th", "prev", "now")} for x in (rot.get("into") or [])[:6]],
                     "themes": rot.get("themes")} if rot else None,
    }


def _mid_brief(mid: dict | None, ver: dict, pick, acct) -> dict | None:
    if mid is None:
        return None
    mv = ver.get("mid") or {}
    paper = mid.get("paper") or {}
    return {
        "orders": [{k: o.get(k) for k in ("code", "name", "sector", "close", "limit", "to_limit", "stop", "stop_pct",
                                          "hi", "to_hi", "age", "dd", "rank", "hold")} for o in mid.get("orders") or []],
        "slots": mid.get("slots"), "n_shape": mid.get("n_shape"),
        "near": [{k: x.get(k) for k in ("code", "name", "trig", "to", "dd", "age")} for x in (mid.get("near") or [])[:5]],
        "verify": {"all": pick(mv.get("all")), "base": pick((mv.get("base") or {}).get("all")),
                   "account": acct(ver.get("account")), "account_prev": acct(ver.get("account_prev"))} if mv else None,
        "paper": pick(paper) if paper.get("n") else None,
    }


def _strength_brief(st: dict | None) -> dict | None:
    if not st or not st.get("rows"):
        return None
    brief = lambda r: {"g": r["g"], "rank": r["rank"], "score": r["score"], "quad": X.QUADS.get(r["quad"]),   # noqa: E731
                       "rs60": r["rs60"], "rs120": r["rs120"], "rs20": r["rs20"], "br50": r["br50"],
                       "rank20": r.get("rank20")}
    rows = st["rows"]
    v = st.get("verify") or {}
    return {"asof": st.get("asof"), "n": st.get("n"), "top": [brief(r) for r in rows[:5]],
            "bottom": [brief(r) for r in rows[-5:][::-1]],
            "rising": [brief(r) for r in sorted((r for r in rows if r.get("rank20")), key=lambda r: r["rank"] - r["rank20"])[:3]
                       if r["rank20"] - r["rank"] >= 5],
            "verify": {k: v.get(k) for k in ("from", "to", "dates", "top", "bottom", "beat")} if v else None}
