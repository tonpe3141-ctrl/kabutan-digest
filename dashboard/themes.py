"""銘柄 → テーマの辞書と、テーマ別の資金の向き。

「フジクラ・古河電工・SWCC が並んでいる」を「電線に資金が来ている」と言えるようにする層。

辞書（docs/data/themes.json）は決定的に使う。同じランキングからは同じ集計が出る。
辞書に無い銘柄は unknown_codes として残し、後段の LLM Routine が記事を読んだうえで
テーマを付けて辞書に追記する（= LLM は辞書を育てるだけで、日々の判定はしない）。
"""
import json
import os

from .config import THEMES_PATH


def load_themes(path: str = THEMES_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("themes", [])
    data.setdefault("stocks", {})
    return data


def themes_of(code: str, themes: dict) -> list[str]:
    entry = (themes.get("stocks") or {}).get(str(code))
    return list(entry.get("themes") or []) if entry else []


def theme_flow(tables: dict, themes: dict, prev_top: list[dict] | None = None,
               top_n: int = 8, unknown_limit: int = 15) -> dict:
    """ランキング上位の顔ぶれをテーマで束ねる。

    対象は 売買代金上位（資金の向き）・上昇率上位（初動）・年初来高値更新（トレンド）。
    出力:
      top[]           … テーマごとの {theme, count, avg_pct, names[], sources{}} を出現数→平均騰落で並べた上位
      unknown_codes[] … 辞書に無い銘柄（売買代金・上昇率上位から。LLM が育てる材料）
      coverage        … 対象銘柄のうち辞書で束ねられた割合
    """
    stocks = themes.get("stocks") or {}
    weights = {"value": 1.0, "gainer": 0.8, "ytd_high": 0.6}
    agg: dict[str, dict] = {}
    seen_codes: set[str] = set()
    unknown: list[dict] = []
    total = 0
    for key, w in weights.items():
        rows = ((tables or {}).get(key) or {}).get("rows") or []
        for r in rows:
            code = str(r.get("code") or "")
            if not code:
                continue
            first_time = code not in seen_codes
            seen_codes.add(code)
            if first_time:
                total += 1
            ts = themes_of(code, stocks and themes)
            if not ts:
                if first_time and key in ("value", "gainer") and len(unknown) < unknown_limit:
                    unknown.append({"code": code, "name": r.get("name")})
                continue
            for t in ts:
                a = agg.setdefault(t, {"theme": t, "count": 0, "weight": 0.0, "pcts": [],
                                       "names": [], "codes": set(), "sources": {}})
                if code in a["codes"]:
                    a["sources"][key] = a["sources"].get(key, 0) + 1
                    continue
                a["codes"].add(code)
                a["count"] += 1
                a["weight"] += w
                a["sources"][key] = a["sources"].get(key, 0) + 1
                if r.get("change_pct") is not None:
                    a["pcts"].append(float(r["change_pct"]))
                if len(a["names"]) < 4:
                    a["names"].append(_short(r.get("name")) or code)

    # 前日の上位が分からない（履歴なし）日は「初動」と言わない
    prev_rank = {p["theme"]: i for i, p in enumerate(prev_top or [])}
    out = []
    for a in agg.values():
        avg = round(sum(a["pcts"]) / len(a["pcts"]), 2) if a["pcts"] else None
        out.append({"theme": a["theme"], "count": a["count"], "score": round(a["weight"], 2),
                    "avg_pct": avg, "names": a["names"], "sources": a["sources"],
                    "was_top": (a["theme"] in prev_rank) if prev_top else True})
    out.sort(key=lambda x: (-x["score"], -(x["avg_pct"] or 0)))
    covered = sum(1 for c in seen_codes if themes_of(c, themes))
    return {"top": out[:top_n], "all_count": len(out),
            "unknown_codes": unknown,
            "coverage": round(covered / total, 2) if total else 0.0,
            "dictionary_size": len(stocks)}


def theme_streaks(current_top: list[dict], history_tops: list[list[dict]], keep: int = 5) -> list[dict]:
    """テーマが上位 keep 位に何営業日連続で入っているか（今日を含む）。history_tops は新しい順。"""
    out = []
    for i, t in enumerate(current_top[:keep]):
        days = 1
        for past in history_tops:
            names = [p.get("theme") for p in (past or [])[:keep]]
            if t["theme"] in names:
                days += 1
            else:
                break
        out.append({"theme": t["theme"], "days": days})
    return out


def _short(name) -> str:
    return str(name or "").replace("(株)", "").replace("（株）", "").replace("株式会社", "").strip()


def save_themes(data: dict, path: str = THEMES_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
