"""相場温度計の実行（入出力の層）。計算は thermo.py の純関数、ここはつなぐだけ。

  1. 日足キャッシュを更新する（マクロは毎スロット末尾だけ、個別株は大引で全銘柄・ほかは不足分だけ）
  2. 日経平均の PER を取り足す（寄り前・大引）
  3. 履歴から業績修正・見出しの論調の日次を集め、当日の値（場中値）を差し込んで計算する
  4. docs/data/thermo.json（アプリが読む全体）を書き、latest.json には要約だけを載せる
  5. 大引では提案を docs/data/thermo_track.json に記録し、5日／20日後の成績を更新する

どの段階で失敗しても例外を外に出さない（収集は完走させる）。取れなかった材料は
「材料不足」として温度計から外し、何軸で計算したかを必ず出す。
"""
import json
import os
from datetime import date

from . import bars as bars_mod, store, thermo as T
from .config import THERMO_PATH, THERMO_TRACK_PATH
from . import ledger as ledger_mod
from .ledger import load_ledger
from .sources import cnbc, nikkei225, nikkei_per
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


def run(slot: str, target_date: date, payload: dict, sessions: list[dict], fetch=None) -> dict:
    """温度計を計算して thermo.json を書き、latest.json に載せる要約と履歴に残す断片を返す。"""
    fetch = fetch or cnbc.fetch_bars
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
    try:
        if slot == "taibike" or not data.get("stocks"):
            bars_mod.update_stocks(data, universe, fetch, target_date)
        else:
            missing = [c for c in universe if c not in (data.get("stocks") or {})]
            if missing:
                bars_mod.add_missing_stocks(data, missing, fetch, target_date)
    except Exception as e:                      # noqa: BLE001
        print(f"    ⚠️  個別株日足の更新で例外: {e}")
    data["updated_at"] = store.now_jst().isoformat(timespec="seconds")
    bars_mod.save(data)

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
    ranks = T.rs_ranks({c: mt.get("r60") for c, mt in metrics.items()})
    stock_rows = {}
    for code, mt in metrics.items():
        cls = T.stock_class(mt, ranks.get(code))
        e = ex_by_code.get(code)
        ev = ({"label": e["label"], "tone": e["tone"], "dir": e["dir"],
               "date": e["date"], "since": e["since_pct"], "title": e["title"]} if e else None)
        stock_rows[code] = {
            "n": names.get(code) or code, "s": sector_of.get(code),
            "th": ((themes.get("stocks") or {}).get(code) or {}).get("themes") or [],
            **mt, "rs": ranks.get(code), "cls": cls, "ev": ev,
            "sc": sector_cls.get(sector_of.get(code)),
            "plan": T.trade_plan(mt, T.plan_kind(cls, ev)),
        }

    def pick_list(kind, key_fn, limit, reverse=False):
        rows = [dict(code=c, **r) for c, r in stock_rows.items() if kind in r["cls"]]
        rows.sort(key=key_fn, reverse=reverse)
        return [_brief(r) for r in rows[:limit]]

    lists = {
        "dip": pick_list("押し目", lambda r: -(r.get("r60") or 0), 8),
        "oversold": pick_list("売られすぎ・下げ止まり", lambda r: (r.get("rsi") or 50), 8),
        "leaders": pick_list("相対力リーダー", lambda r: -(r.get("rs") or 0), 8),
        "deep": pick_list("深押し", lambda r: (r.get("r5") or 0), 8),
        "turn": pick_list("上向き転換", lambda r: -(r.get("slope25") or 0), 8),
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

    # 型ごとの過去成績は確定した日足だけで測る（当日の場中値は入れない）
    try:
        setups = T.setup_backtest(list(data.get("dates") or []), data.get("stocks") or {})
    except Exception as e:                      # noqa: BLE001  収集は止めない
        print(f"    ⚠️  型の成績の計算で例外: {e}")
        setups = None
    watching = {e["code"]: e for e in ledger.get("entries") or [] if e.get("status") == "watching"}
    board = T.action_board(stock_rows, setups, watching)
    stance = T.market_stance(market, setups, nkx, bt)

    thermo = {
        "asof": today, "slot": slot, "generated_at": store.now_jst().isoformat(timespec="seconds"),
        "market": market, "backtest": bt,
        "drivers": [{"key": k, "label": T.DRIVER_LABEL[k], **v} for k, v in drivers.items()],
        "sectors": sectors, "themes": theme_rows[:14], "lists": lists, "watch_guard": guard,
        "setups": setups, "board": board, "stance": stance,
        "stocks": stock_rows, "live": bool(live),
        "coverage": {"macro": len(data.get("macro") or {}), "stocks": len(stock_rows),
                     "stock_days": len(dates), "eps_days": len(eps), "news_titles": tone_today["n"]},
    }

    # ---- 5. 提案の記録（大引のみ。休場日は記録しない＝営業日として数えない） ----
    track = _read(THERMO_TRACK_PATH, {})
    if slot == "taibike" and not stale:
        picks = []
        for s in [x for x in sectors if is_pick(x)][:3]:
            picks.append({"kind": "注目業種", "key": s["sector"], "name": s["sector"], "price": None})
        for s in sectors:
            if s["class"] == "過熱":
                picks.append({"kind": "過熱業種", "key": s["sector"], "name": s["sector"], "price": None})
        for kind, key, n in (("押し目候補", "dip", 5), ("売られすぎ反発", "oversold", 5),
                             ("相対力リーダー", "leaders", 5), ("深押し", "deep", 5), ("上向き転換", "turn", 5),
                             ("高値掴み注意", "hot", 5), ("悪材料出尽くし", "bad_out", 5),
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


def is_pick(s: dict) -> bool:
    """注目業種は逆張りで拾う側（押し目・下げ止まり）に限る。素直な上昇トレンドは「押しを待つ」側。"""
    return s["pick"] > 0 and s["class"] in CONTRA


def _brief(r: dict) -> dict:
    return {"code": r["code"], "name": r["n"], "sector": r.get("s"), "price": r.get("price"),
            "d1": r.get("d1"), "r5": r.get("r5"), "r20": r.get("r20"), "r60": r.get("r60"),
            "rsi": r.get("rsi"), "dev25": r.get("dev25"), "ma25": r.get("ma25"), "rs": r.get("rs"),
            "from_hi": r.get("from_hi"), "plan": r.get("plan"), "slope25": r.get("slope25"),
            "to_ma25": T.r1(T.pct(r.get("ma25"), r.get("price"))) if r.get("ma25") else None}


def _ev_brief(e: dict, rows: dict) -> dict:
    r = rows.get(e["code"]) or {}
    return {"code": e["code"], "name": r.get("n") or e.get("name"), "label": e["label"], "dir": e["dir"],
            "date": e["date"], "since": e["since_pct"], "title": e["title"], "price": r.get("price"),
            "rsi": r.get("rsi"), "dev25": r.get("dev25"), "plan": r.get("plan")}


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
        if "高値掴み注意" in r["cls"]:
            notes.append({"tone": "warn", "text": "短期で上がりすぎ。買い増しは25日線まで待つ"})
        if "押し目" in r["cls"]:
            notes.append({"tone": "chance", "text": "上昇トレンドの押し目。買い増しを検討できる水準"})
        if "売られすぎ・下げ止まり" in r["cls"]:
            notes.append({"tone": "chance", "text": "売られすぎから下げ止まり。ここでの損切りは反発を取り逃がしやすい"})
        if r.get("ev"):
            notes.append({"tone": r["ev"]["tone"], "text": f"{r['ev']['label']}（{r['ev']['date']} の開示から {r['ev']['since']:+.1f}%）"})
        if notes:
            out.append({"code": code, "name": r["n"], "price": r.get("price"), "d1": d1,
                        "rsi": r.get("rsi"), "dev25": r.get("dev25"), "notes": notes})
    return out


def summary(th: dict) -> dict | None:
    """latest.json の各スロットに載せる要約（Routine の分析と「今日」タブの要点カードが読む）。"""
    mk = th.get("market") or {}
    if not mk:
        return None
    sec = th.get("sectors") or []
    return {
        "stance": th.get("stance"),
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
        "dip": [{"code": r["code"], "name": r["name"], "ma25": r.get("ma25"), "to_ma25": r.get("to_ma25")}
                for r in (th.get("lists") or {}).get("dip", [])[:5]],
        "oversold": [{"code": r["code"], "name": r["name"], "rsi": r.get("rsi")}
                     for r in (th.get("lists") or {}).get("oversold", [])[:5]],
        "hot": [{"code": r["code"], "name": r["name"], "rsi": r.get("rsi"), "r5": r.get("r5")}
                for r in (th.get("lists") or {}).get("hot", [])[:5]],
        "bad_out": [{"code": r["code"], "name": r["name"], "since": r.get("since")}
                    for r in (th.get("lists") or {}).get("bad_out", [])[:5]],
        "good_out": [{"code": r["code"], "name": r["name"], "since": r.get("since")}
                     for r in (th.get("lists") or {}).get("good_out", [])[:5]],
        "deep": [{"code": r["code"], "name": r["name"], "r5": r.get("r5")}
                 for r in (th.get("lists") or {}).get("deep", [])[:5]],
        "turn": [{"code": r["code"], "name": r["name"], "slope25": r.get("slope25")}
                 for r in (th.get("lists") or {}).get("turn", [])[:5]],
        "themes": [{"theme": t["theme"], "label": t["label"], "tone": t["tone"]}
                   for t in th.get("themes") or [] if t.get("label")][:5],
        "watch_guard": th.get("watch_guard") or [],
        # 型ごとの直近の成績（効いている／効いていない）と、今日計画を立てられる銘柄の上位
        "setups": [{"setup": s["setup"], "verdict": s["verdict"], "tone": s["tone"],
                    "x5": s.get("x5"), "x20": s.get("x20"), "n": s.get(f"n{s['judged_on']}"),
                    "avg_r": s.get("avg_r")}
                   for s in (th.get("setups") or {}).get("setups") or []],
        "board": [{"code": b["code"], "name": b["name"], "setup": b["setup"], "verdict": b.get("verdict"),
                   "tone": b.get("tone"), "price": b.get("price"), "d1": b.get("d1"),
                   "entry": b["plan"].get("entry"), "stop": b["plan"].get("stop"),
                   "target": b["plan"].get("target"), "rr": b["plan"].get("rr"), "plan": b["plan"]}
                  for b in (th.get("board") or [])[:5]],
    }
