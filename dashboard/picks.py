"""売買タブの「買う候補」に、決算から読んだ材料を添える（DESIGN.md 23章）。

買う候補を決めるのは swing.py の2つのルールだけ（注文・並べ方は変えない）。ここは決まった候補と監視の銘柄に、
earnings.json の記録（直近に読んだ決算・修正の開示）から機械的に引ける材料を添えるだけ:

  自社の決算   … その銘柄自身が直近 OWN_DAYS 暦日に出した決算・修正の向き（上向き／下向き／まちまち）
  連想         … 直近 EARN_WIND_DAYS 営業日に読んだ開示のうち、その銘柄を類似銘柄に挙げた会社（株探の比較銘柄 → 同じテーマ →
                 225 の同じ業種）と、同じテーマの会社の向き
  向き（t）    … 自社の決算があればその向き。無ければ連想の上向き・下向きが2件以上かつ反対の2倍以上のときだけその向き
  次の決算     … 決算予定（株探の決算発表予定）。短期の計画（10営業日）の間に決算をまたぐと、窓開けの幅が損切りの想定を超えうる

「材料の監視」（DESIGN.md 25章。以前の「決算の連想」を広げた）: 上昇トレンド・売買代金の条件を満たす銘柄のうち、次のどれかの材料が
ある銘柄を、材料の数 → 注文対象までの近さで並べる（見つけた日に買わず、ルールの形＝押した日を待つ。注文は出さない）:
  earn  決算の向きが上向き（自社の決算、または類似銘柄・同じテーマの決算が上向き2件以上）
  prog  自社の決算速報で、進捗率が過去の平均を5ポイント以上上回った（計画の上振れの余地を見る材料。確かめていない）
  news  今日、開示か報道があって市場より上げた（ニュースから読む。newsflow の動いた銘柄）か、2媒体以上が報じた
  spill 同じテーマの銘柄が急騰した日に動かなかった（spill.py。検証では追いつくとは言えなかったので、材料の把握だけ）
どの材料も、押し目買いの成績を良くするかは確かめていない。注文の記録（swing_track）に決算の向きを残し、たまってから比べる。

決算の向きが押し目買いの成績を変えるかは、まだ記録が足りず確かめていない。だから注文の条件にも並べ方にも入れず、
アプリが出した注文の記録（swing_track.json）に向きを書き残し、記録がたまってから比べる（tag_track）。
"""
from datetime import date, timedelta

from .config import EARN_WIND_DAYS
from . import swing as W

OWN_DAYS = 45            # 自社の決算として見る暦日（四半期の決算から次の押しまで）
WATCH_MAX = 10           # 「材料の監視」に出す数
PROG_GAP = 5.0           # 進捗率が過去の平均を何ポイント上回れば材料にするか（earnings.PROG_GAP と同じ）
BY_MAX = 3               # 連想の会社を何社まで名前で出すか


def _recent_dates(log: list[dict], today: str, days: int) -> set[str]:
    return set(sorted({e["date"] for e in log if e.get("date") and e["date"] <= today})[-days:])


def context(code: str, log: list[dict], themes_of: dict, today: str, schedule: dict | None = None) -> dict | None:
    """1銘柄の決算の材料。何も無ければ None。"""
    schedule = schedule or {}
    own_from = (date.fromisoformat(today) - timedelta(days=OWN_DAYS)).isoformat()
    own = None
    for e in log:
        if e.get("code") == code and own_from <= (e.get("date") or "") <= today:
            if own is None or e["date"] >= own["date"]:
                own = {"date": e["date"], "dir": e.get("dir"), "head": e.get("head")}
    recent = _recent_dates(log, today, EARN_WIND_DAYS)
    mine = set(themes_of.get(code) or [])
    by, seen = [], set()
    for e in sorted(log, key=lambda x: x.get("date") or "", reverse=True):
        if e.get("date") not in recent or e.get("code") == code or e["code"] in seen:
            continue
        if code in (e.get("peers") or []):
            via = "類似銘柄"
        else:
            common = [t for t in e.get("themes") or [] if t in mine]
            if not common:
                continue
            via = f"テーマ: {common[0]}"
        seen.add(e["code"])
        by.append({"code": e["code"], "name": e.get("name"), "dir": e.get("dir"), "date": e["date"], "via": via})
    up = sum(1 for b in by if b["dir"] == "up")
    down = sum(1 for b in by if b["dir"] == "down")
    if own and own["dir"] in ("up", "down", "mixed"):
        t = own["dir"]
    elif up >= 2 and up >= 2 * down:
        t = "up"
    elif down >= 2 and down >= 2 * up:
        t = "down"
    elif up + down >= 2:
        t = "mixed"
    else:
        t = None
    nxt = (schedule.get(code) or {}).get("date")
    nxt = nxt if nxt and nxt >= today else None
    if not (own or by or nxt):
        return None
    by.sort(key=lambda b: b["dir"] not in ("up", "down"))     # 向きの分かる会社を先に（安定ソートなので新しい順のまま）
    return {"t": t, "own": own, "up": up, "down": down, "n": len(by), "by": by[:BY_MAX], "next": nxt}


def _liquid(sw: dict, price) -> bool:
    return (sw.get("tv") or 0) >= W.LIQ_MIN and (price or 0) >= W.MIN_PRICE


def _prog(code: str, log: list[dict], today: str) -> dict | None:
    """自社の直近の決算速報で、進捗率が過去の平均を PROG_GAP 以上上回ったか。"""
    own_from = (date.fromisoformat(today) - timedelta(days=OWN_DAYS)).isoformat()
    best = None
    for e in log:
        m = e.get("m") or {}
        if e.get("code") == code and own_from <= (e.get("date") or "") <= today and m.get("pg") is not None \
                and m.get("pa") is not None and m["pg"] - m["pa"] >= PROG_GAP:
            if best is None or e["date"] > best["date"]:
                best = {"date": e["date"], "pg": m["pg"], "pa": m["pa"], "kp": bool(m.get("kp"))}
    return best


def material_reasons(newsflow: dict | None, spill: dict | None) -> dict[str, list[dict]]:
    """今日のニュース（newsflow）と同業の急騰（spill）から、銘柄ごとの材料。"""
    out: dict[str, list[dict]] = {}
    nf = newsflow or {}
    for r in ((nf.get("movers") or {}).get("rows") or []):
        if r.get("kind") in ("disc", "news") and (r.get("ex") if r.get("ex") is not None else r.get("pct") or 0) > 0:
            if r["kind"] == "disc":
                src = "開示: " + str((r.get("disc") or [{}])[0].get("title") or "")
            else:
                said = next((x["text"] for x in r.get("said") or [] if not x.get("list")), None)
                head = next((x for x in r.get("news") or [] if not x.get("roundup")), None)
                src = ("記事: " + said) if said else ("見出し: " + head["title"]) if head else "株探の話題株に挙がった"
            out.setdefault(r["code"], []).append({"k": "news", "t": f"今日 {r['pct']:+.1f}%・{src[:54]}"})
    for b in nf.get("buzz") or []:
        if len(b.get("media") or []) >= 2 and not any(x["k"] == "news" for x in out.get(b["code"], [])):
            out.setdefault(b["code"], []).append({"k": "news", "t": f"{len(b['media'])}媒体が報道: {str((b.get('items') or [{}])[0].get('title') or '')[:44]}"})
    for ev in (spill or {}).get("today") or []:
        if ev.get("kind") != "up":
            continue
        lead = (ev.get("ev") or [{}])[0]
        for m in ev.get("mates") or []:
            out.setdefault(m["code"], []).append(
                {"k": "spill", "t": f"同じ{ev['g']}の{lead.get('name')}が{lead.get('d1', 0):+.1f}%の日に{m['d1']:+.1f}%"})
    return out


def build(thermo: dict | None, earn_store: dict | None, themes: dict | None, today: str,
          newsflow: dict | None = None) -> dict | None:
    """latest.json の slots.{SLOT}.data.picks。注文・中期の注文・もうすぐの銘柄の決算の材料と、「材料の監視」。"""
    sw = (thermo or {}).get("swing") or {}
    stocks = (thermo or {}).get("stocks") or {}
    log = (earn_store or {}).get("log") or []
    schedule = (earn_store or {}).get("schedule") or {}
    spill = (thermo or {}).get("spill") or {}
    mats = material_reasons(newsflow, spill)
    if not sw or not (log or schedule or mats):
        return None
    themes_of = {c: (e.get("themes") or []) for c, e in ((themes or {}).get("stocks") or {}).items()}
    for c, s in stocks.items():
        if s.get("th") and c not in themes_of:
            themes_of[c] = s["th"]
    mid = sw.get("mid") or {}
    pick_codes = [o["code"] for o in (mid.get("orders") or []) + (sw.get("orders") or []) + (sw.get("more") or [])]
    near_codes = [x["code"] for x in (sw.get("near") or []) + (mid.get("near") or [])]
    earn = {}
    for c in dict.fromkeys(pick_codes + near_codes):
        ctx = context(c, log, themes_of, today, schedule)
        if ctx:
            earn[c] = ctx
    taken = set(pick_codes)
    watch = []
    for c, s in stocks.items():
        swr = s.get("sw") or {}
        if c in taken or not swr or swr.get("st") in ("thin", "short", "out") or not _liquid(swr, s.get("price")):
            continue
        if not (swr.get("up") or swr.get("md")):
            continue
        md0 = swr.get("md") or {}
        # 押し待ちの価格まで下げると上昇トレンド（中期なら200日線）の条件も割れる銘柄は、買い候補になりにくいので出さない
        if (md0.get("ok") if md0 else swr.get("ok")) is False:
            continue
        why = []
        ctx = earn.get(c) or context(c, log, themes_of, today, schedule)
        if ctx and ctx["t"] == "up":
            if ctx["own"]:
                why.append({"k": "earn", "t": f"自社の決算 {ctx['own']['date'][5:].replace('-', '/')}: {str(ctx['own'].get('head') or '')[:40]}"})
            else:
                why.append({"k": "earn", "t": f"類似・同じテーマの決算が上向き {ctx['up']}社（" + "、".join(
                    str(b.get("name") or b["code"]) for b in ctx["by"] if b.get("dir") == "up") + "）"})
        pg = _prog(c, log, today)
        if pg:
            why.append({"k": "prog", "t": f"進捗 {pg['pg']:g}%（過去の平均 {pg['pa']:g}%）" + ("・計画は据え置き" if pg["kp"] else "")})
        why += mats.get(c) or []
        if not why:
            continue
        md = swr.get("md") or {}
        to = md.get("to") if md.get("to") is not None else swr.get("to")
        watch.append({"code": c, "name": s.get("n") or c, "to": to, "own": bool(ctx and ctx["own"] and ctx["t"] == "up"),
                      "why": why, "kinds": sorted({w["k"] for w in why})})
        if ctx:
            earn[c] = ctx
    watch.sort(key=lambda x: (-len(x["kinds"]), not x["own"], -(x["to"] if x["to"] is not None else -99), x["code"]))
    watch = watch[:WATCH_MAX]
    keep = set(pick_codes) | set(near_codes) | {x["code"] for x in watch}
    return {"asof": sw.get("asof"), "today": today, "own_days": OWN_DAYS, "wind_days": EARN_WIND_DAYS,
            "earn": {c: v for c, v in earn.items() if c in keep}, "watch": watch, "proof": _proof(spill, log)}


def _proof(spill: dict, log: list[dict]) -> dict:
    """材料の監視に添える「当てになるか」: 同業の急騰の検証（5日後）と、決算への反応の記録。"""
    from .earnings import react_stats
    v = (spill or {}).get("verify") or {}
    out = {"react": react_stats(log)}
    try:
        j = (v.get("hs") or []).index(5)
        out["spill"] = {"split": v.get("split"), "from": v.get("from"), "to": v.get("to"),
                        "lag": [h[j].get("avg") for h in v["lag"]], "base": [h[j].get("avg") for h in v["base"]],
                        "n": [h[j].get("n") for h in v["lag"]]}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    return out


def tag_track(track: dict | None, asof: str | None, earn: dict) -> bool:
    """アプリが出した注文の記録に、出した日の決算の向きを書き残す（あとで向きの有無で成績を比べるため）。変えたら True。"""
    if not track or not asof:
        return False
    changed = False
    for tr in (track, track.get("mid") or {}):
        for e in tr.get("orders") or []:
            if e.get("asof") == asof and "earn" not in e:
                e["earn"] = ((earn.get(e["code"]) or {}).get("t")) or "none"
                changed = True
    return changed
