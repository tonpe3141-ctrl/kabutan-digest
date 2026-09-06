"""Yahoo!ファイナンス（日本）からランキングと個別銘柄を取得する。

株探がクラウドIPを拒否するため、日本株の物色動向はここから取る。
ランキング表はセル内のテキストが連結されるので、
セルの子要素（stripped_strings）単位で分解して読む。
"""
import re

from bs4 import BeautifulSoup

from ..http import get_text

BASE = "https://finance.yahoo.co.jp"
CODE_RE = re.compile(r"^[0-9]{4}[A-Z0-9]?$")


def _num(text) -> float | None:
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("%", "").replace("+", "")
    s = s.replace("株", "").replace("円", "")
    if not s or s in ("---", "--", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_row(cells: list, rank: int) -> dict | None:
    """1 行を {code,name,market,price,change,change_pct,volume} に正規化する。

    セル構成（値上がり率など共通）:
      [順位] [銘柄名, コード, 市場, 掲示板] [取引値, 日付] [前日比, 前日比率, %] [出来高, 株]
    """
    parts = [[s.strip() for s in c.stripped_strings] for c in cells]
    code = name = market = None
    price = change = change_pct = volume = None

    for group in parts:
        for token in group:
            if code is None and CODE_RE.match(token):
                code = token
                # コードと同じセル内の、コード以外の長い文字列が銘柄名
                for other in group:
                    if other != token and len(other) > 1 and other != "掲示板":
                        if name is None or "証" in other:
                            if "証" in other and len(other) <= 8:
                                market = other
                            elif name is None:
                                name = other
                break
        if code:
            break

    if code is None:
        return None

    # 数値セルを左から順に拾う（取引値 → 前日比・前日比率 → 出来高）
    numeric_groups = [g for g in parts if any(_num(t) is not None for t in g)]
    for group in numeric_groups:
        nums = [_num(t) for t in group if _num(t) is not None]
        joined = "".join(group)
        if "%" in joined and change_pct is None:
            if len(nums) >= 2:
                change, change_pct = nums[0], nums[1]
            elif nums:
                change_pct = nums[0]
            # 符号は結合テキストから拾う（_num が符号を落とすため）
            m = re.search(r"([+\-])\s*[\d,.]+\s*%", joined)
            if m and m.group(1) == "-":
                change_pct = -abs(change_pct) if change_pct is not None else None
                change = -abs(change) if change is not None else None
        elif "株" in joined and volume is None:
            volume = nums[0] if nums else None
        elif price is None and nums:
            price = nums[0]

    return {"rank": rank, "code": code, "name": name, "market": market,
            "price": price, "change": change, "change_pct": change_pct,
            "volume": volume, "raw": ["".join(g) for g in parts]}


def fetch_ranking(page: dict) -> dict:
    """ランキングページを取得する。URL 候補を順に試し、最初に取れたものを使う。"""
    label = page["label"]
    rows: list[dict] = []
    used_url = None

    for url in page["urls"]:
        html = get_text(url, timeout=20)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            continue
        trs = table.find_all("tr")
        parsed = []
        for i, tr in enumerate(trs):
            cells = tr.find_all(["th", "td"])
            if len(cells) < 3:
                continue
            row = _parse_row(cells, len(parsed) + 1)
            if row:
                parsed.append(row)
        if parsed:
            rows = parsed[:page.get("max_rows", 20)]
            used_url = url
            break

    ok = bool(rows)
    print(f"    {'✅' if ok else '⚠️ '} {label}: {len(rows)} 行")
    return {"key": page["key"], "label": label, "url": used_url or page["urls"][0],
            "rows": rows, "ok": ok}


def fetch_stock(code: str) -> dict | None:
    """個別銘柄の株価を取得する。ページ構造の変更に弱いので防御的に読む。"""
    html = get_text(f"{BASE}/quote/{code}.T", timeout=20)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    name = None
    title = soup.find("title")
    if title:
        name = re.split(r"[【(]", title.get_text(strip=True))[0].strip()

    board = soup.find(attrs={"class": re.compile(r"PriceBoard__priceInfo")})
    price = change = change_pct = None
    if board:
        text = board.get_text(" ", strip=True)
        # 例: "3,120 前日比 +41 (+1.33%)"
        m = re.search(r"([\d,]+(?:\.\d+)?)\s*前日比\s*([+\-−][\d,.]+)\s*\(\s*([+\-−][\d.]+)%",
                      text.replace("▲", "-"))
        if m:
            price = _num(m.group(1))
            change = _num(m.group(2).replace("−", "-"))
            change_pct = _num(m.group(3).replace("−", "-"))
            if m.group(2).lstrip().startswith(("-", "−")):
                change = -abs(change) if change is not None else None
                change_pct = -abs(change_pct) if change_pct is not None else None
        else:
            nums = re.findall(r"[\d,]+(?:\.\d+)?", text)
            if nums:
                price = _num(nums[0])

    return {"code": code, "name": name, "price": price,
            "change": change, "change_pct": change_pct,
            "url": f"{BASE}/quote/{code}.T"}
