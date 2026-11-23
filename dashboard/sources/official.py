"""公的機関の一次情報（DESIGN.md 37章）。例外を投げず、読めなければ空を返す。

  財務省「対外及び対内証券売買契約等の状況（週次・指定報告機関ベース）」
    https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv
    2005年からの週ごとの、非居住者（海外投資家）による日本の株式・中長期債の取得と処分、居住者による外国の株式・中長期債の取得と処分（億円）。
    毎週木曜（8:50）に前の週の分。JPX の投資部門別（東証プライム・現物）より早く、20年分ある。
  日本銀行「金融政策決定会合の開催予定」 https://www.boj.or.jp/mopo/mpmsche_minu/index.htm
  FRB「FOMC の日程」               https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

2026-11-23 に手元回線で確かめた（CSV は Shift_JIS、全角の数字と「．」「〜」で期間を書く）。
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date

from ..http import get, get_text

MOF_WEEK = "https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv"
BOJ_SCHEDULE = "https://www.boj.or.jp/mopo/mpmsche_minu/index.htm"
FOMC_CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# 週次の CSV の列（0 始まり。1列目が期間）。ネット＝取得−処分。対外＝居住者、対内＝非居住者
MOF_COLS = {"res_stock": 3, "res_bond": 6, "nr_stock": 14, "nr_bond": 17}


def _num(s: str) -> float | None:
    s = unicodedata.normalize("NFKC", s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_mof_week(text: str) -> list[dict]:
    """週次の CSV → [{start, end, nr_stock, nr_bond, res_stock, res_bond}]（億円。期間の年は start の年、年をまたぐ週は end を翌年）。"""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        p = unicodedata.normalize("NFKC", row[0]).replace(" ", "")
        m = re.match(r"(\d{4})[.．](\d{1,2})[.．](\d{1,2})[〜~～](?:(\d{4})[.．])?(\d{1,2})[.．](\d{1,2})$", p)
        if not m or len(row) <= max(MOF_COLS.values()):
            continue
        y, m1, d1, y2, m2, d2 = m.groups()
        try:
            start = date(int(y), int(m1), int(d1))
            ey = int(y2) if y2 else (int(y) + 1 if int(m2) < int(m1) else int(y))
            end = date(ey, int(m2), int(d2))
        except ValueError:
            continue
        rec = {"start": start.isoformat(), "end": end.isoformat()}
        for k, col in MOF_COLS.items():
            rec[k] = _num(row[col])
        if rec["nr_stock"] is None:
            continue
        out.append(rec)
    out.sort(key=lambda r: r["start"])
    return out


def mof_weeks() -> list[dict]:
    res = get(MOF_WEEK, timeout=30)
    if res is None:
        return []
    blob = res.content
    for enc in ("cp932", "utf-8-sig"):
        try:
            return parse_mof_week(blob.decode(enc))
        except UnicodeDecodeError:
            continue
    return []


def _plain(html: str) -> str:
    import html as H
    t = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", H.unescape(t))


def parse_boj(html: str) -> list[dict]:
    """日銀の会合の開催日（2日目が結果の公表日）。年ごとの表の「M月D日（曜）・D日（曜）」を読む。"""
    t = unicodedata.normalize("NFKC", _plain(html))
    out = []
    for ym in re.finditer(r"(20\d\d)年 表 \1年 金融政策決定会合", t):
        y = int(ym.group(1))
        nxt = re.search(r"(20\d\d)年 表 \1年 金融政策決定会合", t[ym.end():])
        body = t[ym.end(): ym.end() + nxt.start()] if nxt else t[ym.end():]
        for m in re.finditer(r"(\d{1,2})月\s*(\d{1,2})日\([月火水木金土日]\)・(?:(\d{1,2})月\s*)?(\d{1,2})日\([月火水木金土日]\)", body):
            m1, d1, m2, d2 = int(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(1)), int(m.group(4))
            try:
                out.append({"start": date(y, m1, d1).isoformat(), "end": date(y, m2, d2).isoformat()})
            except ValueError:
                continue
    return sorted({(r["start"], r["end"]): r for r in out}.values(), key=lambda r: r["start"])


_MON = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                     "September", "October", "November", "December"), 1)}


def parse_fomc(html: str) -> list[dict]:
    """FOMC の開催日（米国の日付。2日目の午後2時＝日本の翌朝3〜4時に声明）。"""
    t = _plain(html)
    out = []
    for ym in re.finditer(r"(20\d\d) FOMC Meetings", t):
        y = int(ym.group(1))
        nxt = re.search(r"20\d\d FOMC Meetings", t[ym.end():])
        body = t[ym.end(): ym.end() + nxt.start()] if nxt else t[ym.end():]
        body = body.split("Note:")[0]
        mons = "|".join(_MON)
        for m in re.finditer(rf"\b({mons})(?:/({mons}))?\s+(\d{{1,2}})-(\d{{1,2}})\*?", body):
            m1 = _MON[m.group(1)]
            m2 = _MON[m.group(2)] if m.group(2) else m1
            d1, d2 = int(m.group(3)), int(m.group(4))
            if m2 == m1 and d2 < d1:            # 「April/May 30-1」が「May 30-1」と読まれたとき
                m1 -= 1
            try:
                out.append({"start": date(y, m1, d1).isoformat(), "end": date(y, m2, d2).isoformat(),
                            "sep": m.group(0).endswith("*")})
            except ValueError:
                continue
    return sorted({(r["start"], r["end"]): r for r in out}.values(), key=lambda r: r["start"])


def meetings() -> dict:
    """{boj: [...], fomc: [...]}。取れなかった方は空。"""
    out = {"boj": [], "fomc": []}
    try:
        h = get_text(BOJ_SCHEDULE)
        out["boj"] = parse_boj(h) if h else []
    except Exception:                       # noqa: BLE001  収集は止めない
        pass
    try:
        h = get_text(FOMC_CALENDAR)
        out["fomc"] = parse_fomc(h) if h else []
    except Exception:                       # noqa: BLE001
        pass
    return out
