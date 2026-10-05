"""Routine が書いた分析を latest.json・ledger.json・themes.json に差し込む（トークンを減らす。DESIGN.md 23章）。

Routine は中身だけを1つの JSON に書く。決まった形（method・generated_at）と、sources の参照番号（tools/ai_brief.py が
付けた [K3] など）から {title, url} への置き換えはここでやる。自分の区分の ai_* だけを差し替え、ほかには触れない。

  python tools/ai_write.py taibike /tmp/ai_out.json

入力（あるキーだけ書く）:
  {"ai_commentary": {"headline": "...", "sections": [{"title": "...", "body": "..."}], "sources": ["K1", "P3"]},
   "ai_macro":      {同じ形},
   "ai_earnings":   {"headline", "winds": [...], "items": [...], "sources": [...]},
   "ai_picks":      {"items": [{"code": "4478", "why": "..."}]},
   "ledger_notes":  {"4004": {"notes": "...", "source": "K2"}},
   "themes_add":    {"5803": {"name": "フジクラ", "themes": ["電線", "データセンター"]}}}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
REFS = "/tmp/ai_refs_{slot}.json"
AI_KEYS = ("ai_commentary", "ai_macro", "ai_earnings", "ai_picks")
METHOD = {"ai_commentary": "Claude による分析", "ai_macro": "Claude による分析",
          "ai_earnings": "Claude による分析", "ai_picks": "Claude による理由づけ"}


def jst_now() -> str:
    out = subprocess.run(["date", "+%Y-%m-%dT%H:%M:%S+09:00"], capture_output=True, text=True,
                         env={**os.environ, "TZ": "Asia/Tokyo"}).stdout.strip()
    return out


def resolve(src, refs: dict) -> list[dict]:
    out, seen = [], set()
    for s in src or []:
        if isinstance(s, dict) and s.get("url"):
            r = {"title": s.get("title") or s["url"], "url": s["url"]}
        else:
            r = refs.get(str(s).strip("[] "))
        if r and r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


def check(key: str, v: dict) -> list[str]:
    errs = []
    if key in ("ai_commentary", "ai_macro"):
        if not v.get("headline") or not v.get("sections"):
            errs.append(f"{key}: headline と sections が要る")
        for s in v.get("sections") or []:
            if not (s.get("title") and s.get("body")):
                errs.append(f"{key}: セクションに title と body が要る")
    if key == "ai_earnings" and not (v.get("headline") and (v.get("items") or v.get("winds"))):
        errs.append("ai_earnings: headline と items か winds が要る")
    if key == "ai_picks":
        for it in v.get("items") or []:
            if not (it.get("code") and it.get("why")):
                errs.append("ai_picks: items に code と why が要る")
    return errs


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    slot, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as f:
        inp = json.load(f)
    try:
        with open(REFS.format(slot=slot), encoding="utf-8") as f:
            refs = json.load(f)
    except (OSError, ValueError):
        refs = {}
        print("⚠️  参照番号の表がありません（先に tools/ai_brief.py を実行）。番号の sources は捨てます")
    errs = [e for k in AI_KEYS if inp.get(k) for e in check(k, inp[k])]
    if errs:
        print("❌ 書き込みません:\n  " + "\n  ".join(errs))
        return 1
    now = jst_now()
    lp = os.path.join(DATA, "latest.json")
    with open(lp, encoding="utf-8") as f:
        latest = json.load(f)
    data = ((latest.get("slots") or {}).get(slot) or {}).get("data")
    if data is None:
        print(f"❌ latest.json に {slot} がありません")
        return 1
    done = []
    for k in AI_KEYS:
        v = inp.get(k)
        if not v:
            continue
        v = {**v, "method": METHOD[k], "generated_at": now}
        if k == "ai_picks":                 # どの日の引けの注文に書いた理由か（アプリは注文の日付と合うときだけ出す）
            v["asof"] = (((data.get("thermo") or {}).get("swing")) or {}).get("asof")
        if "sources" in v or k != "ai_picks":
            v["sources"] = resolve(v.get("sources"), refs)
        data[k] = v
        done.append(f"{k}（出典 {len(v.get('sources') or [])}）")
    with open(lp, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, separators=(",", ":"))
    notes = inp.get("ledger_notes") or {}
    if notes:
        p = os.path.join(DATA, "ledger.json")
        with open(p, encoding="utf-8") as f:
            led = json.load(f)
        n = 0
        for e in led.get("entries") or []:
            x = notes.get(e.get("code"))
            if x and not e.get("notes") and x.get("notes"):
                e["notes"] = x["notes"]
                src = resolve([x.get("source")], refs) if x.get("source") else []
                if src:
                    e["note_url"] = src[0]["url"]
                n += 1
        with open(p, "w", encoding="utf-8") as f:
            json.dump(led, f, ensure_ascii=False, indent=1)
        done.append(f"台帳の理由 {n}件")
    add = inp.get("themes_add") or {}
    if add:
        p = os.path.join(DATA, "themes.json")
        with open(p, encoding="utf-8") as f:
            th = json.load(f)
        st = th.setdefault("stocks", {})
        n = 0
        for code, x in add.items():
            if code not in st and x.get("themes"):                # 既存の行は変えない
                st[code] = {"name": x.get("name") or code, "themes": list(x["themes"])[:3]}
                n += 1
        with open(p, "w", encoding="utf-8") as f:
            json.dump(th, f, ensure_ascii=False, indent=1)
        done.append(f"テーマ辞書 +{n}")
    print("✅ " + "、".join(done) if done else "書くものがありませんでした")
    return 0


if __name__ == "__main__":
    sys.exit(main())
