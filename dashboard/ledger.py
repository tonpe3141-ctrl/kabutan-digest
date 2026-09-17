"""発掘台帳: 候補を溜め、理由を残し、その後を測る。

入口（どの銘柄を候補にするか）は機械が決める。理由づけは後段の LLM が書く。
検証（シグナル種別ごとの成績）はここで集計し、週報が読む。
「当てる」ためではなく「自分の見方が効いているか」を確かめるための仕組み。

docs/data/ledger.json:
  {
    "updated_at": "...",
    "entries": [
      {"code": "5803", "name": "フジクラ", "first_seen": "2026-09-10",
       "signals": ["資金流入の継続", "テーマ初動: 電線"], "why": ["売買代金上位に 4営業日連続"],
       "themes": ["電線"], "price_at_flag": 8120, "nikkei_at_flag": 64000,
       "track": {"d1": 1.8, "d5": 6.2, "d20": null, "last": 6.2, "last_date": "2026-09-17",
                 "excess": 4.1, "days": 5},
       "status": "watching", "notes": null, "note_url": null}
    ],
    "stats": {"asof": "...", "by_signal": [{"signal": "...", "count": 3, "d5_median": 1.2, "hit_rate": 0.67}]}
  }
"""
import json
import os
import re
from datetime import date
from statistics import median

from .config import LEDGER_MAX_STREAK, LEDGER_MIN_STREAK, LEDGER_PATH, LEDGER_TRACK_DAYS
from .themes import themes_of

UPGRADE_RE = re.compile(r"上方|増配|増額")
SIGNAL_WEIGHT = {
    "資金流入の継続": 2, "新規の資金流入": 1, "順位の急上昇": 1, "上方修正・増配": 2,
    "決算で商い": 2, "上昇率×売買代金": 2, "年初来高値×商い": 2, "出来高急増×上昇": 1,
    "修正後に資金流入": 3, "テーマ初動": 1,
}


def load_ledger(path: str = LEDGER_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("entries", [])
    data.setdefault("stats", {})
    return data


def save_ledger(data: dict, path: str = LEDGER_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _short(name) -> str:
    return str(name or "").replace("(株)", "").replace("（株）", "").replace("株式会社", "").strip()


def _is_upgrade(r: dict) -> bool:
    if r.get("category") not in ("業績予想の修正", "配当予想の修正"):
        return False
    return bool(UPGRADE_RE.search((r.get("title") or "")))


# ==================== 入口 ====================
def detect_signals(payload: dict, history_sessions: list[dict], themes: dict,
                   theme_flow: dict | None) -> list[dict]:
    """今日のデータから候補と、その理由を機械的に出す。

    history_sessions は新しい順（store.previous_sessions の出力）。
    """
    tables = payload.get("tables") or {}
    value_rows = (tables.get("value") or {}).get("rows") or []
    gainer_rows = (tables.get("gainer") or {}).get("rows") or []
    ytd_rows = (tables.get("ytd_high") or {}).get("rows") or []
    vol_rows = (tables.get("vol_surge") or {}).get("rows") or []
    by_code: dict[str, dict] = {}
    for rows in (value_rows, gainer_rows, ytd_rows, vol_rows):
        for r in rows:
            c = str(r.get("code") or "")
            if c and c not in by_code:
                by_code[c] = r
    value_set = {str(r["code"]) for r in value_rows if r.get("code")}
    gainer_set = {str(r["code"]) for r in gainer_rows if r.get("code")}
    vol_set = {str(r["code"]) for r in vol_rows if r.get("code")}

    cands: dict[str, dict] = {}

    def add(code, name, signal, why, price=None, change_pct=None):
        code = str(code or "")
        if not code:
            return
        c = cands.setdefault(code, {"code": code, "name": _short(name) or code, "signals": [],
                                    "why": [], "price": None, "change_pct": None})
        base = by_code.get(code) or {}
        if c["price"] is None:
            c["price"] = price if price is not None else base.get("price")
        if c["change_pct"] is None:
            c["change_pct"] = change_pct if change_pct is not None else base.get("change_pct")
        if signal not in c["signals"]:
            c["signals"].append(signal)
        if why and why not in c["why"]:
            c["why"].append(why)

    # 3〜6日連続 = 「資金が入り始めて定着しつつある」段階。7日以上は常連（大型株）なので発掘の対象にしない
    for s in payload.get("streaks") or []:
        if LEDGER_MIN_STREAK <= s.get("days", 0) <= LEDGER_MAX_STREAK:
            add(s["code"], s.get("name"), "資金流入の継続", f"売買代金上位に {s['days']}営業日連続")
    dl = payload.get("ranking_delta") or {}
    for n in dl.get("new") or []:
        pct = n.get("change_pct")
        add(n["code"], n.get("name"), "新規の資金流入",
            f"売買代金 {n.get('rank')}位に新規ランクイン" + (f"（{pct:+.2f}%）" if pct is not None else ""))
    for n in dl.get("rank_up") or []:
        if (n.get("jump") or 0) >= 8:
            add(n["code"], n.get("name"), "順位の急上昇", f"売買代金 {n.get('prev_rank')}位 → {n.get('rank')}位")

    discl = list((tables.get("kessan_after") or {}).get("rows") or []) + \
            list((tables.get("kessan_intraday") or {}).get("rows") or [])
    for r in discl:
        code = str(r.get("code") or "")
        if _is_upgrade(r):
            add(code, r.get("name"), "上方修正・増配", f"{r.get('time') or ''} {r.get('title')}".strip())
        elif r.get("category") == "決算短信" and (code in value_set or code in gainer_set):
            add(code, r.get("name"), "決算で商い", "決算短信の当日に売買代金／上昇率上位")

    for r in gainer_rows:
        code = str(r.get("code") or "")
        if code in value_set:
            add(code, r.get("name"), "上昇率×売買代金", f"上昇率 {r.get('rank')}位、かつ売買代金上位")
        elif code in vol_set:
            add(code, r.get("name"), "出来高急増×上昇", f"上昇率 {r.get('rank')}位、かつ出来高急増")
    for r in ytd_rows:
        code = str(r.get("code") or "")
        if code in value_set or code in vol_set:
            add(code, r.get("name"), "年初来高値×商い",
                "年初来高値を更新し、売買代金上位または出来高急増",
                price=r.get("price"))

    # 過去の引け後開示（上方修正）が、その後に売買代金上位へ入っているか
    seen_value: dict[str, list[str]] = {}
    for hs in reversed(history_sessions):          # 古い順に
        rows = ((hs.get("taibike") or {}).get("value_rows") or (hs.get("zenba") or {}).get("value_rows") or [])
        for r in rows:
            seen_value.setdefault(str(r.get("code")), []).append(hs.get("date"))
    for hs in history_sessions:
        for r in (hs.get("taibike") or {}).get("after_hours") or []:
            if not _is_upgrade(r):
                continue
            later = [d for d in seen_value.get(str(r.get("code")), []) if d and d > hs.get("date", "")]
            if later:
                add(r["code"], r.get("name"), "修正後に資金流入",
                    f"{hs.get('date')} 上方修正 → その後 {len(later)}営業日 売買代金上位")

    # テーマ初動: 今日初めて上位に入ったテーマに属する銘柄
    fresh = {t["theme"] for t in (theme_flow or {}).get("top", [])[:5] if not t.get("was_top")}
    if fresh:
        for code, r in by_code.items():
            hit = [t for t in themes_of(code, themes) if t in fresh]
            if hit and (code in value_set or code in gainer_set):
                add(code, r.get("name"), "テーマ初動", "テーマ初動: " + "・".join(hit))

    out = []
    for c in cands.values():
        c["themes"] = themes_of(c["code"], themes)
        c["score"] = sum(SIGNAL_WEIGHT.get(s, 1) for s in c["signals"])
        out.append(c)
    out.sort(key=lambda c: (-c["score"], -(c["change_pct"] or 0)))
    return out


# ==================== 追跡 ====================
def _business_days_between(d0: str, d1: str, dates: list[str]) -> int:
    """履歴の日付リスト（営業日の代わり）で d0 から d1 までの営業日数。"""
    return len([d for d in dates if d0 < d <= d1])


def update(target_date: date, payload: dict, history_sessions: list[dict], themes: dict,
           theme_flow: dict | None, price_lookup, nikkei_close: float | None,
           ledger: dict | None = None, max_new_per_day: int = 12) -> dict:
    """台帳を1日ぶん進める。新規候補を載せ、追跡中の候補の成績を更新し、成績を集計する。

    price_lookup(codes) -> {code: price} で追跡中の銘柄の当日終値を取る（CNBC）。
    """
    today = target_date.isoformat()
    ledger = ledger if ledger is not None else load_ledger()
    entries: list[dict] = ledger.get("entries") or []
    by_code = {e["code"]: e for e in entries}
    dates = sorted({hs.get("date") for hs in history_sessions if hs.get("date")} | {today})

    # 1) 新規・再点灯
    signals = detect_signals(payload, history_sessions, themes, theme_flow)
    added = 0
    for c in signals:
        e = by_code.get(c["code"])
        if e and e.get("status") == "watching":
            for s in c["signals"]:
                if s not in e["signals"]:
                    e["signals"].append(s)
            for w in c["why"]:
                if w not in e["why"]:
                    e["why"].append(w)
            continue
        if e and e.get("status") == "closed" and e.get("first_seen", "") >= _days_ago(dates, today, 10):
            continue                                   # 直近に追跡を終えたばかりは再登録しない
        if added >= max_new_per_day or len(c["signals"]) == 0:
            continue
        if c["score"] < 2:
            continue                                   # 単独の弱いシグナルは台帳に載せない
        entry = {"code": c["code"], "name": c["name"], "first_seen": today,
                 "signals": c["signals"], "why": c["why"], "themes": c["themes"],
                 "price_at_flag": c["price"], "nikkei_at_flag": nikkei_close,
                 "track": {"d1": None, "d5": None, "d20": None, "last": None,
                           "last_date": today, "excess": None, "days": 0},
                 "status": "watching", "notes": None, "note_url": None}
        if e:                                          # 以前 closed だった行は置き換える
            entries[entries.index(e)] = entry
        else:
            entries.append(entry)
        by_code[c["code"]] = entry
        added += 1

    # 2) 追跡
    open_entries = [e for e in entries if e.get("status") == "watching" and e.get("first_seen") != today]
    prices = {}
    if open_entries:
        try:
            prices = price_lookup([e["code"] for e in open_entries]) or {}
        except Exception:
            prices = {}
    nk_pct = None
    for e in open_entries:
        p = prices.get(e["code"])
        base = e.get("price_at_flag")
        days = _business_days_between(e["first_seen"], today, dates)
        tr = e.setdefault("track", {})
        tr["days"] = days
        if p is not None and base:
            ret = round((p / base - 1) * 100, 2)
            tr["last"] = ret
            tr["last_date"] = today
            if nikkei_close and e.get("nikkei_at_flag"):
                nk_pct = round((nikkei_close / e["nikkei_at_flag"] - 1) * 100, 2)
                tr["excess"] = round(ret - nk_pct, 2)
            for horizon in (1, 5, 20):
                key = f"d{horizon}"
                if tr.get(key) is None and days >= horizon:
                    tr[key] = ret
        if days >= LEDGER_TRACK_DAYS:
            e["status"] = "closed"

    # 3) 成績
    ledger["entries"] = entries[-400:]
    ledger["stats"] = compute_stats(entries, today)
    ledger["updated_at"] = today
    ledger["added_today"] = [e["code"] for e in entries if e.get("first_seen") == today]
    return ledger


def _days_ago(dates: list[str], today: str, n: int) -> str:
    past = [d for d in dates if d <= today]
    return past[-n] if len(past) >= n else (past[0] if past else today)


def compute_stats(entries: list[dict], today: str) -> dict:
    by_signal: dict[str, list[float]] = {}
    for e in entries:
        d5 = (e.get("track") or {}).get("d5")
        if d5 is None:
            continue
        for s in e.get("signals") or []:
            by_signal.setdefault(s, []).append(d5)
    rows = []
    for s, vals in by_signal.items():
        rows.append({"signal": s, "count": len(vals), "d5_median": round(median(vals), 2),
                     "hit_rate": round(sum(1 for v in vals if v > 0) / len(vals), 2)})
    rows.sort(key=lambda r: -r["count"])
    all_d5 = [v for vals in by_signal.values() for v in vals]
    return {"asof": today, "by_signal": rows,
            "overall": {"count": len(all_d5), "d5_median": round(median(all_d5), 2) if all_d5 else None}}
