"""売買タブの「買う候補」に、決算から読んだ材料を添える（DESIGN.md 23章）。

買う候補を決めるのは swing.py の2つのルールだけ（注文・並べ方は変えない）。ここは決まった候補と監視の銘柄に、
earnings.json の記録（直近に読んだ決算・修正の開示）から機械的に引ける材料を添えるだけ:

  自社の決算   … その銘柄自身が直近 OWN_DAYS 暦日に出した決算・修正の向き（上向き／下向き／まちまち）
  連想         … 直近 EARN_WIND_DAYS 営業日に読んだ開示のうち、その銘柄を類似銘柄に挙げた会社（株探の比較銘柄 → 同じテーマ →
                 225 の同じ業種）と、同じテーマの会社の向き
  向き（t）    … 自社の決算があればその向き。無ければ連想の上向き・下向きが2件以上かつ反対の2倍以上のときだけその向き
  次の決算     … 決算予定（株探の決算発表予定）。短期の計画（10営業日）の間に決算をまたぐと、窓開けの幅が損切りの想定を超えうる

「決算の連想」の監視: 向きが上向きで、上昇トレンド・売買代金の条件を満たす銘柄を、注文対象までの近さで並べる
（見つけた日に買わず、ルールの形＝押した日を待つ。注文は出さない）。

決算の向きが押し目買いの成績を変えるかは、まだ記録が足りず確かめていない。だから注文の条件にも並べ方にも入れず、
アプリが出した注文の記録（swing_track.json）に向きを書き残し、記録がたまってから比べる（tag_track）。
"""
from datetime import date, timedelta

from .config import EARN_WIND_DAYS
from . import swing as W

OWN_DAYS = 45            # 自社の決算として見る暦日（四半期の決算から次の押しまで）
WATCH_MAX = 8            # 「決算の連想」の監視に出す数
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


def build(thermo: dict | None, earn_store: dict | None, themes: dict | None, today: str) -> dict | None:
    """latest.json の slots.{SLOT}.data.picks。注文・中期の注文・もうすぐの銘柄の決算の材料と、「決算の連想」の監視。"""
    sw = (thermo or {}).get("swing") or {}
    stocks = (thermo or {}).get("stocks") or {}
    log = (earn_store or {}).get("log") or []
    schedule = (earn_store or {}).get("schedule") or {}
    if not sw or not (log or schedule):
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
        ctx = earn.get(c) or context(c, log, themes_of, today, schedule)
        if not ctx or ctx["t"] != "up":
            continue
        md = swr.get("md") or {}
        to = md.get("to") if md.get("to") is not None else swr.get("to")
        watch.append({"code": c, "name": s.get("n") or c, "to": to, "own": bool(ctx["own"]), "ctx": ctx})
        earn[c] = ctx
    watch.sort(key=lambda x: (not x["own"], -(x["to"] if x["to"] is not None else -99), x["code"]))
    watch = watch[:WATCH_MAX]
    for x in watch:
        x.pop("ctx", None)
    keep = set(pick_codes) | set(near_codes) | {x["code"] for x in watch}
    return {"asof": sw.get("asof"), "today": today, "own_days": OWN_DAYS, "wind_days": EARN_WIND_DAYS,
            "earn": {c: v for c, v in earn.items() if c in keep}, "watch": watch}


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
