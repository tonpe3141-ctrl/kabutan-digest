"""信用残（銘柄ごと・週1回）を Yahoo!ファイナンスの信用残の時系列から取る（DESIGN.md 26章）。

株探はクラウドIPを拒否するので、Actions から届く Yahoo のページを読む。2026-10-10 の実測（tools/probe_margin.py）:

  https://finance.yahoo.co.jp/quote/{code}.T/history?styl=margin[&page=N]
  <table aria-label="信用残時系列のテーブル"> 見出し: 日付 | 売残 | 買残 | 売残増減 | 買残増減 | 信用倍率
  1ページ20週、新しい順。page=8 で 2023年まで遡れる（上場が新しい銘柄は表が無いページがある）

残高は JPX の銘柄別信用取引週末残高（金曜の残高を翌週の火曜に公表）と同じもの。株数は**分割を調整していない**
（キオクシアは 2026-09-29 の 1:3 分割で、買残が 1,393万株 → 4,147万株 に見えた）。分割の補正は supply.adjust_splits が行う。
見出しの名前で列を決める（列の順が変わっても読める）。
"""
import re
from datetime import date

from bs4 import BeautifulSoup

from ..http import get_text

URL = "https://finance.yahoo.co.jp/quote/{code}.T/history?styl=margin"
_DATE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_COLS = {"日付": "date", "売残": "sell", "買残": "buy", "売残増減": "sell_chg", "買残増減": "buy_chg", "信用倍率": "ratio"}


def _num(t: str):
    t = (t or "").replace(",", "").replace("−", "-").replace("+", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def parse_history(html: str) -> list[dict]:
    """時系列の表を [{date, buy, sell, buy_chg, sell_chg, ratio}]（新しい順）にする。表が無ければ []。"""
    if not html or "信用残時系列" not in html:
        return []
    k = html.find("信用残時系列のテーブル")
    soup = BeautifulSoup(html[max(0, k - 400): k + 60000] if k >= 0 else html, "html.parser")
    tb = soup.find("table", attrs={"aria-label": re.compile("信用残")})
    if tb is None and k >= 0:                     # 切り出しで表が壊れたら全体を読む
        tb = BeautifulSoup(html, "html.parser").find("table", attrs={"aria-label": re.compile("信用残")})
    if tb is None:
        return []
    heads = [th.get_text(strip=True) for th in (tb.find("thead") or tb).find_all("th")]
    cols = [_COLS.get(x) for x in heads]
    if "date" not in cols or "buy" not in cols:
        return []
    out = []
    for tr in (tb.find("tbody") or tb).find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) != len(cols):
            continue
        row = {}
        for key, val in zip(cols, cells):
            if key == "date":
                m = _DATE.match(val)
                row["date"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat() if m else None
            elif key:
                v = _num(val)
                row[key] = v if key == "ratio" or v is None else int(v)
        if row.get("date") and (row.get("buy") is not None or row.get("sell") is not None):
            out.append({k2: row.get(k2) for k2 in ("date", "buy", "sell", "buy_chg", "sell_chg", "ratio")})
    return out


def fetch_history(code: str, pages: int = 1) -> list[dict]:
    """新しい順に pages ページ分（1ページ20週）。取れなければ []。"""
    out: list[dict] = []
    for p in range(1, pages + 1):
        html = get_text(URL.format(code=code) + (f"&page={p}" if p > 1 else ""), timeout=20)
        rows = parse_history(html) if html else []
        if not rows:
            break
        out += rows
    return out


def fetch(code: str) -> list[dict]:
    """大引で使う: 直近20週。"""
    return fetch_history(code, pages=1)
