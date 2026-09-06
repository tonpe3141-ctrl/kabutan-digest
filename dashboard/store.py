"""JSON の読み書きと履歴管理。

latest.json は 1 日分の 3 スロットを持つ「現在の状態」。
各スロットの実行は自分の区画だけを更新し、他スロットの内容は保持する
（= 12:00 の実行で 07:00 の寄り前タブが消えない）。

history/YYYY-MM-DD.json には、翌日以降の差分分析に必要な最小限だけを残す。
"""
import json
import os
from datetime import date, datetime, timedelta, timezone

from .config import (
    DATA_DIR, HISTORY_DIR, WATCHLIST_PATH, HISTORY_KEEP_DAYS, SLOTS,
)

JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    return datetime.now(JST)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ==================== latest.json ====================
def latest_path() -> str:
    return os.path.join(DATA_DIR, "latest.json")


def load_latest() -> dict:
    return _read_json(latest_path(), {})


def save_slot(target_date: date, slot: str, payload: dict) -> dict:
    """1 スロット分を latest.json に差し込む。日付が変わっていれば作り直す。"""
    latest = load_latest()
    date_str = target_date.isoformat()

    if latest.get("date") != date_str:
        latest = {"date": date_str, "slots": {}}

    latest.setdefault("slots", {})
    latest["slots"][slot] = {
        "updated_at": now_jst().isoformat(timespec="seconds"),
        "data": payload,
    }
    latest["generated_at"] = now_jst().isoformat(timespec="seconds")
    latest["available_slots"] = [s for s in SLOTS if s in latest["slots"]]
    _write_json(latest_path(), latest)
    return latest


# ==================== history ====================
def history_path(d: date) -> str:
    return os.path.join(HISTORY_DIR, f"{d.isoformat()}.json")


def load_history(d: date) -> dict:
    return _read_json(history_path(d), {})


def update_history(d: date, patch: dict) -> dict:
    hist = load_history(d)
    hist.update(patch)
    hist["date"] = d.isoformat()
    _write_json(history_path(d), hist)
    return hist


def list_history_dates() -> list[date]:
    try:
        names = os.listdir(HISTORY_DIR)
    except OSError:
        return []
    out = []
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            out.append(date.fromisoformat(n[:-5]))
        except ValueError:
            continue
    return sorted(out)


def previous_sessions(before: date, count: int = 10) -> list[dict]:
    """指定日より前の履歴を、新しい順に最大 count 件返す。"""
    dates = [d for d in list_history_dates() if d < before]
    return [load_history(d) for d in reversed(dates[-count:])]


def prune_history(keep_days: int = HISTORY_KEEP_DAYS) -> int:
    dates = list_history_dates()
    if len(dates) <= keep_days:
        return 0
    removed = 0
    for d in dates[:-keep_days]:
        try:
            os.remove(history_path(d))
            removed += 1
        except OSError:
            pass
    return removed


# ==================== 記事番号ヒント ====================
HINTS_PATH = os.path.join(DATA_DIR, "article_hints.json")


def load_hints() -> dict:
    return _read_json(HINTS_PATH, {})


def save_hints(slot: str, numbers: list[int]) -> None:
    """当たった記事番号を覚えておき、次回のスキャン開始位置に使う。"""
    if not numbers:
        return
    hints = load_hints()
    prev = hints.get(slot, [])
    merged = numbers + [n for n in prev if n not in numbers]
    hints[slot] = merged[:12]
    _write_json(HINTS_PATH, hints)


# ==================== ウォッチリスト ====================
def load_watchlist() -> dict:
    data = _read_json(WATCHLIST_PATH, {"codes": []})
    codes = [str(c).strip().upper() for c in data.get("codes", []) if str(c).strip()]
    # 重複除去（順序は保つ）
    seen, uniq = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return {**data, "codes": uniq[:40]}


def save_watchlist(codes: list[str]) -> None:
    _write_json(WATCHLIST_PATH, {
        "codes": codes,
        "updated_at": now_jst().isoformat(timespec="seconds"),
    })
