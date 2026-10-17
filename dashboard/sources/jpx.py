"""市場全体の需給を JPX の公表ファイルから取る（週1回。DESIGN.md 28章）。

  信用取引現在高（二市場の信用買残・売残。申込日＝金曜の残高を翌週の火曜に公表）
    https://www.jpx.co.jp/markets/statistics-equities/margin/04.html  …  YYYYMMDD_mtcurrent.xlsx（直近3週）
  投資部門別 株式売買状況（週間。海外投資家・個人・信託銀行などの売り買い）
    https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html  …  stock_1_w_YYYYMMDD_YYYYMMDD.xlsx

2026-10-17 の実測（手元回線。Actions からも xlsx に届くことは 26章で確かめた）。xlsx は標準ライブラリ（zipfile と XML）で読む。
2002-07 からの信用残の履歴（過去推移表 *_mtgk.xls、旧形式）は一度だけ手元で JSON にして docs/data/flows.json の種にした。
例外を投げない。読めなければ空を返す。
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date

from ..http import get, get_text

BASE = "https://www.jpx.co.jp"
MARGIN_PAGE = BASE + "/markets/statistics-equities/margin/04.html"
INVESTOR_PAGE = BASE + "/markets/statistics-equities/investor-type/index.html"
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_MARGIN_LINK = re.compile(r'href="([^"]+?/(\d{8})_mtcurrent\.xlsx)"')
_INV_LINK = re.compile(r'href="([^"]+?/stock_1_w_(\d{8})_(\d{8})\.xlsx)"')

# 投資部門別で拾う部門（見出しの名前 → キー）。差引（買い − 売り）の金額を億円で持つ
INVESTORS = {"海外投資家": "foreign", "個人": "indiv", "信託銀行": "trust", "事業法人": "corp", "投資信託": "fund", "自己": "prop"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(blob: bytes) -> list[list[list]]:
    """xlsx を [シート][行][列] の文字列（空は None）にする。読めなければ []。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        ss = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_NS + "si"):
                ss.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        files = sorted((n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                       key=lambda x: int(re.findall(r"\d+", x)[-1]))
        out = []
        for f in files:
            rows = []
            for r in ET.fromstring(z.read(f)).iter(_NS + "row"):
                row = {}
                for c in r.iter(_NS + "c"):
                    v = c.find(_NS + "v")
                    if v is None:
                        is_ = c.find(_NS + "is")
                        val = "".join(t.text or "" for t in is_.iter(_NS + "t")) if is_ is not None else None
                    else:
                        val = ss[int(v.text)] if c.get("t") == "s" else v.text
                    row[_col(c.get("r"))] = val
                rows.append([row.get(i) for i in range(max(row) + 1)] if row else [])
            out.append(rows)
        return out
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, IndexError):
        return []


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _iso(ymd: str) -> str:
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])).isoformat()


def parse_margin(blob: bytes, ymd: str) -> dict | None:
    """信用取引現在高（mtcurrent.xlsx）から、二市場の信用取引残高合計（一般＋制度、自己＋委託）を読む。

    行「信用取引残高合計」の株数の行と、その次の金額の行。列は [4]=売残（二市場） [6]=買残（二市場）。
    単位は千株・百万円（過去推移表と同じ定義。2026-09-25 の週で過去推移表の合計と一致することを確かめた）。"""
    for rows in read_xlsx(blob):
        for k, r in enumerate(rows):
            head = str((r[1] if len(r) > 1 else "") or "")
            if head.startswith("信用取引残高合計") and k + 1 < len(rows):
                sh, val = r, rows[k + 1]
                if len(sh) < 7 or len(val) < 7:
                    return None
                out = {"date": _iso(ymd), "sell_sh": _num(sh[4]), "sell": _num(val[4]),
                       "buy_sh": _num(sh[6]), "buy": _num(val[6])}
                return out if out["buy"] and out["sell"] else None
    return None


def _spans(row: list, lo: int, hi: int) -> list[tuple[str, int, int]]:
    """見出しの行で、名前の入った列から次の名前の列の手前までを1つの範囲にする。"""
    cols = [(i, str(row[i])) for i in range(lo, min(hi, len(row))) if row[i]]
    return [(name, c, (cols[j + 1][0] if j + 1 < len(cols) else hi)) for j, (c, name) in enumerate(cols)]


def parse_investors(blob: bytes, start: str, end: str, market: str = "東証プライム") -> dict | None:
    """投資部門別（週間）から、指定の市場の部門ごとの差引（買い − 売り。億円）を読む。

    見出しは3段（自己／委託 → 個人・海外投資家・証券会社・法人 → 現金・信用、法人の内訳…）。部門の名前を見出しの行から探し、
    その下の4列ずつの組（売・買・差引・合計）の差引を足す（海外投資家は法人＋個人、個人は現金＋信用、自己は現金＋信用）。"""
    for rows in read_xlsx(blob):
        if len(rows) < 8:
            continue
        val = None
        for k, r in enumerate(rows):
            if len(r) > 2 and r[1] and str(r[1]).startswith(market) and k + 1 < len(rows):
                val = rows[k + 1]                       # 株数の次の行が金額
                break
        if not val or "金額" not in str(val[2] or ""):
            continue
        width = len(val)
        found = {}
        for depth in range(1, min(6, len(rows))):       # 浅い段から探す（「個人」は海外投資家の内訳にもあるので、最初に見つけた段を使う）
            for name, lo, hi in _spans(rows[depth], 3, width):
                for label, key in INVESTORS.items():
                    if key in found or not name.startswith(label):
                        continue
                    s = 0.0
                    for c in range(lo, hi, 4):
                        v = _num(val[c + 2]) if c + 2 < len(val) else None
                        s += v or 0.0
                    found[key] = round(s / 1e5, 1)      # 千円 → 億円
        if "foreign" in found and "indiv" in found:
            return {"start": _iso(start), "end": _iso(end), **found}
    return None


def margin_weeks(have: set[str]) -> list[dict]:
    """JPX の信用取引現在高のページにある週（直近3週）のうち、まだ持っていない週を読む。"""
    html = get_text(MARGIN_PAGE, timeout=20, retries=1) or ""
    out = []
    for path, ymd in dict.fromkeys(_MARGIN_LINK.findall(html)):
        if _iso(ymd) in have:
            continue
        res = get(BASE + path if path.startswith("/") else path, timeout=30, retries=1)
        row = parse_margin(res.content, ymd) if res is not None else None
        if row:
            out.append(row)
    return out


def investor_weeks(have: set[str], limit: int = 12) -> list[dict]:
    """投資部門別のページにある週間の表のうち、まだ持っていない週を読む（新しい順に最大 limit）。"""
    html = get_text(INVESTOR_PAGE, timeout=20, retries=1) or ""
    out = []
    for path, s, e in list(dict.fromkeys(_INV_LINK.findall(html)))[:limit]:
        if _iso(e) in have:
            continue
        res = get(BASE + path if path.startswith("/") else path, timeout=30, retries=1)
        row = parse_investors(res.content, s, e) if res is not None else None
        if row:
            out.append(row)
    return out
