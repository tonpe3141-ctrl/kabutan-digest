"""Yahoo!ファイナンス（日本）からランキングと個別銘柄を取得する。

株探がクラウドIPを拒否するため、日本株の物色動向はここから取る。
ランキング表はセル内のテキストが連結されるので、
セルの子要素（stripped_strings）単位で分解して読む。
"""
import re

from bs4 import BeautifulSoup

from ..http import get_text

BASE = "https://finance.yahoo.co.jp"
CODE_RE = re.compile(r"^[0-9]{3}[0-9A-Z]$")


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


MARKET_RE = re.compile(r"^(東証|名証|福証|札証)")


def _parse_row(cells: list, rank: int) -> dict | None:
    """1 行を {code,name,market,price,change,change_pct,metric} に正規化する。

    metric は表によって中身が変わる（売買代金ランキングなら代金、
    出来高ランキングなら株数）。何なのかは table の label が持つ。

    列構成は実測で確認済み（値上がり率・値下がり率・出来高で共通）:
      [順位] [銘柄名, コード, 市場, 掲示板] [取引値, 日付] [前日比, 前日比率, %] [指標, 単位]
    順位セルの数字を株価と取り違えたり、社名の「(株)」を出来高セルと
    誤認したりしないよう、位置で読む。
    """
    parts = [[t.strip() for t in c.stripped_strings] for c in cells]

    # コードを含むセルを起点にする
    idx = None
    for i, group in enumerate(parts):
        if any(CODE_RE.match(t) for t in group):
            idx = i
            break
    if idx is None:
        return None

    group = parts[idx]
    code = next(t for t in group if CODE_RE.match(t))
    market = next((t for t in group if MARKET_RE.match(t)), None)
    name = next((t for t in group
                 if t != code and t != market and t != "掲示板" and len(t) > 1), None)

    rest = parts[idx + 1:]
    price = change = change_pct = metric = None

    if len(rest) > 0 and rest[0]:
        price = _num(rest[0][0])

    if len(rest) > 1:
        joined = "".join(rest[1])
        nums = [_num(t) for t in rest[1] if _num(t) is not None]
        if len(nums) >= 2:
            change, change_pct = nums[0], nums[1]
        elif nums:
            change_pct = nums[0]
        # _num は符号を落とすので、結合テキストから向きを取り直す
        if "-" in joined or "−" in joined or "▲" in joined:
            change = -abs(change) if change is not None else None
            change_pct = -abs(change_pct) if change_pct is not None else None

    if len(rest) > 2:
        nums = [_num(t) for t in rest[2] if _num(t) is not None]
        metric = nums[0] if nums else None

    return {"rank": rank, "code": code, "name": name, "market": market,
            "price": price, "change": change, "change_pct": change_pct,
            "metric": metric}


def fetch_ranking(page: dict) -> dict:
    """ランキングページを取得する。URL 候補を順に試し、最初に取れたものを使う。"""
    label = page["label"]
    rows: list[dict] = []
    used_url = None

    for candidate in page["urls"]:
        url = candidate["url"]
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
            # 第1候補が落ちて代替を使ったときは、表示名も実態に合わせる
            label = candidate["label"]
            break

    ok = bool(rows)
    print(f"    {'✅' if ok else '⚠️ '} {label}: {len(rows)} 行")
    return {"key": page["key"], "label": label,
            "url": used_url or page["urls"][0]["url"],
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
