"""株探（kabutan.jp）からの取得。

取得対象:
  - 指数（日経平均・TOPIX・グロース250）
  - 株価注意報の各ランキング／決算発表テーブル
  - 昼刊・夕刊・市況などの記事本文
  - ウォッチリスト銘柄の株価

記事の探索について:
  昼刊・夕刊は日付一覧ページに出ないことがあるため、記事番号の総当たりが
  従来の確実な方法だった。ただし総当たりは数百リクエストになるので、
    1) ニュース一覧ページからの発見（数リクエスト）
    2) 過去に見つかった番号を起点にした近傍探索（数十リクエスト）
    3) 全域スキャン（最後の手段）
  の順に試す。
"""
import re
import time
from datetime import date

from bs4 import BeautifulSoup

from ..http import get, get_text
from ..config import (
    JP_INDICES, NEWS_ARTICLE_URL, SCAN_START, SCAN_END,
)

CODE_RE = re.compile(r"^[0-9]{4}[A-Z0-9]?$")
PCT_RE = re.compile(r"^[+\-−▲△]?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*%$")
NUM_RE = re.compile(r"^[+\-−▲△]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")


def _to_float(text: str | None):
    """「▲1.23」「+1,234.5」「-0.5%」などを float に。負号の表記ゆれを吸収する。"""
    if not text:
        return None
    s = text.strip().replace(",", "").replace("%", "").replace("円", "")
    neg = False
    for mark in ("▲", "△", "−", "-"):
        if s.startswith(mark):
            neg = True
            s = s[1:]
            break
    s = s.lstrip("+")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


# ==================== 指数 ====================
def fetch_index(code: str) -> dict | None:
    html = get_text(f"https://kabutan.jp/stock/?code={code}")
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    box = soup.find("div", id="stockinfo_i1")
    if not box:
        return None
    close_el = box.find("span", class_="kabuka")
    dds = box.find_all("dd")
    return {
        "close": _to_float(close_el.get_text(strip=True)) if close_el else None,
        "change": _to_float(dds[0].get_text(strip=True)) if len(dds) > 0 else None,
        "change_pct": _to_float(dds[1].get_text(strip=True)) if len(dds) > 1 else None,
    }


def fetch_indices() -> dict:
    print("  [株探] 指数を取得中...")
    out = {}
    for spec in JP_INDICES:
        data = fetch_index(spec["code"])
        if data is None:
            print(f"    ⚠️  {spec['label']} 取得失敗")
            continue
        out[spec["key"]] = {"label": spec["label"], "code": spec["code"], **data}
        print(f"    ✅ {spec['label']}: {data['close']} ({data['change_pct']}%)")
    return out


# ==================== ランキング・決算テーブル ====================
def _normalize_row(cells: list[str]) -> dict:
    """テーブル 1 行を {code,name,price,change_pct,raw} に正規化する。

    ページごとに列構成が違うので、位置ではなく「見た目」で判定する。
    """
    code = name = None
    price = change_pct = change = None

    for i, c in enumerate(cells):
        if code is None and CODE_RE.match(c):
            code = c
            # コードの次に来る文字列セルが銘柄名
            for nxt in cells[i + 1:]:
                if not NUM_RE.match(nxt) and not PCT_RE.match(nxt) and len(nxt) > 1:
                    name = nxt
                    break
            break

    if code is None:
        # 業種別ランキングなど、コードを持たない表
        for c in cells:
            if not NUM_RE.match(c) and not PCT_RE.match(c) and len(c) > 1:
                name = c
                break

    pcts = [_to_float(c) for c in cells if PCT_RE.match(c)]
    if pcts:
        change_pct = pcts[0]

    # 株価は「名前より後ろにある、%でない数値」の最初のもの
    start = cells.index(name) + 1 if name in cells else 0
    nums = [_to_float(c) for c in cells[start:] if NUM_RE.match(c)]
    nums = [n for n in nums if n is not None]
    if nums:
        price = nums[0]
        if len(nums) > 1:
            change = nums[1]

    return {"code": code, "name": name, "price": price,
            "change": change, "change_pct": change_pct, "raw": cells}


def fetch_table(page: dict) -> dict:
    """株価注意報系ページのテーブルを取得して行リストで返す。"""
    label = page["label"]
    res = get(page["url"], cookies={"shared_perpage": "30"}, allow_redirects=False)
    if res is None:
        print(f"    ⚠️  {label} 取得失敗")
        return {"key": page["key"], "label": label, "url": page["url"],
                "headers": [], "rows": [], "ok": False}

    soup = BeautifulSoup(res.text, "html.parser")
    table = soup.find("table", class_="stock_table")
    if not table:
        print(f"    ⚠️  {label} テーブルなし")
        return {"key": page["key"], "label": label, "url": page["url"],
                "headers": [], "rows": [], "ok": False}

    all_rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if cells:
            all_rows.append(cells)
    if not all_rows:
        return {"key": page["key"], "label": label, "url": page["url"],
                "headers": [], "rows": [], "ok": False}

    headers = all_rows[0]
    rows = [_normalize_row(r) for r in all_rows[1:page.get("max_rows", 20) + 1]]
    print(f"    ✅ {label}: {len(rows)} 行")
    return {"key": page["key"], "label": label, "url": page["url"],
            "headers": headers, "rows": rows, "ok": True}


# ==================== 記事 ====================
def _clean_title(text: str) -> str:
    return re.sub(r"^【注目】", "", text.strip())


def _article_from_html(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = _clean_title(h1.get_text(strip=True)) if h1 else ""

    published_at = None
    for t in soup.find_all("time"):
        dt = t.get("datetime", "")
        if "T" in dt and len(dt) > 10:
            published_at = dt
            break

    article = soup.find("article")
    body = article.get_text(separator="\n", strip=True) if article else ""
    lines = [l for l in body.split("\n") if l.strip()]
    if lines and re.match(r"\d{4}年\d{2}月\d{2}日", lines[0]):
        lines = lines[1:]
    if lines and lines[0].startswith("【注目】"):
        lines = lines[1:]
    return {"title": title, "published_at": published_at,
            "body": "\n".join(lines), "url": url}


def _try_news_list(target_date: date, patterns: list[tuple]) -> dict:
    """ニュース一覧ページから記事 URL を拾えるだけ拾う（高速パス）。"""
    date_str = target_date.strftime("%Y%m%d")
    found: dict[str, str] = {}
    for page_no in range(1, 6):
        if len(found) == len(patterns):
            break
        html = get_text(
            f"https://kabutan.jp/news/marketnews/?date={date_str}&page={page_no}",
            retries=1,
        )
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if "b=n" not in a["href"]:
                continue
            title = _clean_title(a.get_text(strip=True))
            for label, keyword in patterns:
                if label not in found and keyword in title:
                    href = a["href"]
                    found[label] = href if href.startswith("http") else "https://kabutan.jp" + href
    return found


def _scan_order(hints: list[int]) -> list[int]:
    """探索する記事番号の順序。過去に当たった番号の近傍を先に見る。"""
    seen, order = set(), []

    def push(n):
        if SCAN_START <= n <= SCAN_END and n not in seen:
            seen.add(n)
            order.append(n)

    for hint in hints:
        for delta in range(0, 30):
            push(hint + delta)
            push(hint - delta)
    for n in range(SCAN_START, SCAN_END + 1):
        push(n)
    return order


def fetch_articles(target_date: date, patterns: list[tuple],
                   hints: list[int] | None = None,
                   max_scan: int = 1400,
                   budget_sec: float = 420.0) -> tuple[list[dict], list[int]]:
    """対象記事を取得する。戻り値は (記事リスト, 当たった記事番号リスト)。

    スキャンには件数と時間の両方で上限を設ける。株探が落ちている・
    レスポンスが極端に遅いといった状況でジョブ全体を巻き込まないため。
    """
    if not patterns:
        return [], []

    date_str = target_date.strftime("%Y%m%d")
    found: dict[str, str] = {}
    hit_numbers: list[int] = []

    print(f"  [株探] 記事を検索中（{date_str}）...")
    found.update(_try_news_list(target_date, patterns))
    for label in found:
        print(f"    ✅ 一覧から発見: {label}")
        m = re.search(r"b=n\d{8}(\d{4})", found[label])
        if m:
            hit_numbers.append(int(m.group(1)))

    remaining = [(l, k) for l, k in patterns if l not in found]
    if remaining:
        scanned, hits, misses = 0, 0, 0
        deadline = time.monotonic() + budget_sec
        for num in _scan_order(hints or []):
            if not remaining or scanned >= max_scan:
                break
            if time.monotonic() > deadline:
                print(f"    ⏱ スキャン時間の上限に達したため打ち切ります（{scanned} 件）")
                break
            url = NEWS_ARTICLE_URL.format(date=date_str, num=num)
            res = get(url, timeout=8, retries=0, allow_redirects=False)
            scanned += 1
            if res is None:
                misses += 1
                # 一度も 200 が返らないまま連続失敗が続く = サイト側が応答していない
                if hits == 0 and misses >= 150:
                    print("    ⚠️  株探から応答が得られないためスキャンを中止します")
                    break
                continue
            hits += 1
            soup = BeautifulSoup(res.text, "html.parser")
            h1 = soup.find("h1")
            if not h1:
                continue
            title = _clean_title(h1.get_text(strip=True))
            for label, keyword in list(remaining):
                if keyword in title:
                    found[label] = url
                    hit_numbers.append(num)
                    remaining.remove((label, keyword))
                    print(f"    ✅ [{label}] {title[:50]} (No.{num:04d})")
                    break
        print(f"    ↳ スキャン {scanned} 件")

    articles = []
    for label, _ in patterns:
        url = found.get(label)
        if not url:
            print(f"    ⚠️  [{label}] 未掲載")
            continue
        html = get_text(url)
        if not html:
            continue
        art = _article_from_html(html, url)
        art["label"] = label
        articles.append(art)
    return articles, sorted(set(hit_numbers))


# ==================== 個別銘柄（ウォッチリスト） ====================
def fetch_stock(code: str) -> dict | None:
    html = get_text(f"https://kabutan.jp/stock/?code={code}")
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    box = soup.find("div", id="stockinfo_i1")
    if not box:
        return None

    name = None
    h2 = soup.find("h2")
    if h2:
        name = h2.get_text(strip=True)
    if not h2 or not name:
        title = soup.find("title")
        if title:
            name = title.get_text(strip=True).split("【")[0]

    close_el = box.find("span", class_="kabuka")
    dds = box.find_all("dd")

    market = None
    mk = soup.select_one("#stockinfo_i1 .market")
    if mk:
        market = mk.get_text(strip=True)

    sector = None
    sec = soup.select_one("#stockinfo_i2 .stock_sector")
    if sec:
        sector = sec.get_text(strip=True)

    return {
        "code": code,
        "name": name,
        "market": market,
        "sector": sector,
        "price": _to_float(close_el.get_text(strip=True)) if close_el else None,
        "change": _to_float(dds[0].get_text(strip=True)) if len(dds) > 0 else None,
        "change_pct": _to_float(dds[1].get_text(strip=True)) if len(dds) > 1 else None,
        "url": f"https://kabutan.jp/stock/?code={code}",
    }
