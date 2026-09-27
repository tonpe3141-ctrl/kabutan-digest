"""銘柄コード → 日本語の社名。

CNBC の気配は英語の社名（"ASICS Corporation"）を返すので、画面に出す社名は日本語の情報源から引く。
優先順: 日経225の構成銘柄（略称。アプリのほかの画面と同じ表記）→ テーマ辞書 → 発掘台帳 → 社名のキャッシュ
（docs/data/cache/names.json。Yahoo!ファイナンスの銘柄ページの題名から取ったもの）。
どれにも無いコードだけ Yahoo!ファイナンスに取りに行き、キャッシュに足す。取れなければ元の社名のまま（収集は止めない）。
"""
import json
import os
import re

from .config import LEDGER_PATH, NAMES_PATH, THEMES_PATH
from .sources import nikkei225

JA_RE = re.compile(r"[぀-ヿ㐀-鿿]")
FETCH_MAX = 40          # 1回に取りに行く上限（ウォッチリストの上限と同じ）


def has_japanese(name: str | None) -> bool:
    return bool(name and JA_RE.search(name))


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def load_cache(path: str = NAMES_PATH) -> dict[str, str]:
    return {k: v for k, v in (_load(path).get("names") or {}).items() if v}


def save_cache(names: dict[str, str], path: str = NAMES_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"names": dict(sorted(names.items()))}, f, ensure_ascii=False, indent=0)
        f.write("\n")
    os.replace(tmp, path)


def known(cache: dict[str, str] | None = None) -> dict[str, str]:
    """手元にある日本語の社名（優先順の高いものが勝つ）。"""
    out: dict[str, str] = dict(cache if cache is not None else load_cache())
    for e in _load(LEDGER_PATH).get("entries") or []:
        if e.get("code") and has_japanese(e.get("name")):
            out[e["code"]] = e["name"]
    for code, e in (_load(THEMES_PATH).get("stocks") or {}).items():
        if e.get("name"):
            out[code] = e["name"]
    for c in nikkei225._load_cache():
        if c.get("code") and c.get("name"):
            out[c["code"]] = c["name"]
    return out


def resolve(codes: list[str], fetch=None, path: str = NAMES_PATH) -> dict[str, str]:
    """codes の日本語の社名。手元に無いものは fetch(code) -> 社名 で取り、キャッシュに足す。"""
    cache = load_cache(path)
    have = known(cache)
    out = {c: have[c] for c in codes if c in have}
    missing = [c for c in codes if c not in out][:FETCH_MAX]
    if missing and fetch:
        added = 0
        for c in missing:
            try:
                n = fetch(c)
            except Exception:                   # noqa: BLE001  社名が取れなくても収集は止めない
                n = None
            if n:
                out[c] = cache[c] = n
                added += 1
        if added:
            save_cache(cache, path)
    return out
