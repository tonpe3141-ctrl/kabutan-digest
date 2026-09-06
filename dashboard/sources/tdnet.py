"""TDnet（適時開示情報閲覧サービス）から、その日の開示を取得する。

株探の「決算発表・業績修正」テーブルの代替。むしろこちらが一次情報で、
決算短信・業績予想の修正・配当予想の修正などを漏れなく拾える。

注意: ページは UTF-8 だが Content-Type に charset が無く、requests が
      latin-1 と誤判定するため明示的に指定する。
"""
import re
from datetime import date

from bs4 import BeautifulSoup

from ..http import get

LIST_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{date}.html"

# 表題から「相場が動く開示」だけを拾うための分類
CATEGORIES = [
    ("業績予想の修正", re.compile(r"(業績予想|通期予想|連結業績予想).*(修正|変更)")),
    ("決算短信",       re.compile(r"決算短信")),
    ("配当予想の修正", re.compile(r"配当予想.*(修正|変更)")),
    ("自己株式取得",   re.compile(r"自己株式.*取得")),
    ("株式分割",       re.compile(r"株式分割")),
    ("公募・売出",     re.compile(r"(公募|売出|第三者割当|新株予約権)")),
    ("月次",           re.compile(r"月次")),
]


def _classify(title: str) -> str | None:
    for label, pattern in CATEGORIES:
        if pattern.search(title):
            return label
    return None


def _normalize_code(code: str) -> str:
    """TDnet は5桁（末尾0埋め）。証券コード4桁に直す。"""
    code = code.strip()
    if len(code) == 5 and code.endswith("0"):
        return code[:4]
    return code


def fetch_disclosures(target_date: date, max_pages: int = 4,
                      only_material: bool = True) -> dict:
    """指定日の開示一覧を取得する。戻り値は build がそのまま使える table 形式。"""
    date_str = target_date.strftime("%Y%m%d")
    rows: list[dict] = []

    for page in range(1, max_pages + 1):
        res = get(LIST_URL.format(page=page, date=date_str), timeout=20)
        if res is None:
            break
        res.encoding = "utf-8"          # charset 未指定のため明示する
        soup = BeautifulSoup(res.text, "html.parser")
        table = soup.find("table", id="main-list-table")
        if not table:
            break

        page_rows = 0
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            time_txt = tds[0].get_text(strip=True)
            code = _normalize_code(tds[1].get_text(strip=True))
            name = tds[2].get_text(strip=True)
            title = tds[3].get_text(strip=True)
            if not code or not title:
                continue
            page_rows += 1
            category = _classify(title)
            if only_material and category is None:
                continue
            rows.append({
                "code": code, "name": name, "title": title,
                "time": time_txt, "category": category,
                "change_pct": None, "price": None,
            })
        if page_rows == 0:
            break

    # 重要度の高い順、同カテゴリ内は時刻の遅い順（引け後ほど翌日効きやすい）
    order = {label: i for i, (label, _) in enumerate(CATEGORIES)}
    rows.sort(key=lambda r: (order.get(r["category"], 99), r["time"]), reverse=False)

    print(f"    {'✅' if rows else '⚠️ '} TDnet 適時開示: {len(rows)} 件")
    return {"key": "disclosures", "label": "適時開示（決算・業績修正）",
            "url": f"https://www.release.tdnet.info/inbs/I_main_00.html",
            "rows": rows, "ok": bool(rows)}


def split_by_session(rows: list[dict]) -> dict:
    """場中（〜15:30）と引け後（15:30〜）に分ける。引け後は翌日の寄りに効く。"""
    intraday, after = [], []
    for r in rows:
        t = r.get("time") or ""
        m = re.match(r"(\d{1,2}):(\d{2})", t)
        minutes = int(m.group(1)) * 60 + int(m.group(2)) if m else 0
        (after if minutes >= 15 * 60 + 30 else intraday).append(r)
    return {"intraday": intraday, "after": after}
