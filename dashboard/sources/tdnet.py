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
PDF_BASE = "https://www.release.tdnet.info/inbs/"

# ETF・ETN・投資信託の開示は個別株の物色と関係がないので落とす。
# （収益分配金や決算短信が毎日まとめて出るため、これを混ぜると個別の材料が埋もれる）
#
# 判定は3つの手がかりを併用する。名称だけだと Global X のように
# 「ＧＸ超短期米国債」など ETF と分かる語を含まない銘柄を取りこぼすため。

# 1) 運用会社ごとのブランド名。先頭一致に限定して誤爆を避ける
#    （例: シンプレクス・ホールディングス(4373) は事業会社なので入れない）
FUND_BRAND_RE = re.compile(
    r"^(ＧＸ|GX|グローバルＸ"
    r"|ｉＦ|iF|ｉシェアーズ|ｉＳ"
    r"|ＭＡＸＩＳ|MAXIS|ＭＸＳ"
    r"|ＮＥＸＴ|NEXT|ＮＦ[・･]"
    r"|ダイワ上場|上場インデックス|上場ＩＮ"
    r"|Ｔｒａｃｅｒｓ|ＮＺＡＭ|ＳＭＤＡＭ|ＳＭＴ"
    r"|楽天ＥＴＦ|ＷＴ|ＷｉｓｄｏｍＴｒｅｅ)")

# 2) 名称・表題のどこかに現れる、ファンドであることを示す語
FUND_WORD_RE = re.compile(
    r"(ETF|ＥＴＦ|ETN|ＥＴＮ|上場投信|投信|投資信託|受益権|信託財産"
    r"|収益分配金|運用報告書|償還)")

# 3) ETF の決算短信に特有の、期間を日付範囲で書く形式
#    例: 2026年7月期（2026年1月25日～2026年7月24日）決算短信
#    事業会社は「第3四半期決算短信〔日本基準〕（連結）」と書くので衝突しない
FUND_PERIOD_RE = re.compile(
    r"（\s*\d{4}年\d{1,2}月\d{1,2}日\s*[～〜~－-]\s*\d{4}年\d{1,2}月\d{1,2}日\s*）")


def _is_fund(name: str, title: str) -> bool:
    """ETF・ETN・投資信託の開示かどうか。"""
    name, title = name or "", title or ""
    if FUND_BRAND_RE.match(name):
        return True
    if FUND_WORD_RE.search(name) or FUND_WORD_RE.search(title):
        return True
    if "決算短信" in title and FUND_PERIOD_RE.search(title):
        return True
    return False


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
                      only_material: bool = True,
                      exclude_funds: bool = True) -> dict:
    """指定日の開示一覧を取得する。戻り値は build がそのまま使える table 形式。

    exclude_funds: ETF・ETN・投資信託の開示を落とす（既定）。
                   収益分配金の告知が毎日大量に出るため、個別株の材料が埋もれる。
    """
    date_str = target_date.strftime("%Y%m%d")
    rows: list[dict] = []
    skipped_funds = 0

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
            link = tds[3].find("a", href=True)
            if not code or not title:
                continue
            page_rows += 1
            if exclude_funds and _is_fund(name, title):
                skipped_funds += 1
                continue
            category = _classify(title)
            if only_material and category is None:
                continue
            rows.append({
                "code": code, "name": name, "title": title,
                "time": time_txt, "category": category,
                "change_pct": None, "price": None,
                "pdf": (PDF_BASE + link["href"]) if link and link["href"].endswith(".pdf") else None,
            })
        if page_rows == 0:
            break

    # 重要度の高い順、同カテゴリ内は時刻の遅い順（引け後ほど翌日効きやすい）
    order = {label: i for i, (label, _) in enumerate(CATEGORIES)}
    rows.sort(key=lambda r: (order.get(r["category"], 99), r["time"]), reverse=False)

    note = f"（ETF等 {skipped_funds} 件を除外）" if skipped_funds else ""
    print(f"    {'✅' if rows else '⚠️ '} TDnet 適時開示: {len(rows)} 件{note}")
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


# ==================== 開示の PDF（会社自身の説明） ====================
# 決算短信の「経営成績の概況」と「業績予想の説明」、業績・配当の修正の「修正の理由」は、
# 会社が自分の言葉で事業環境（どの需要が強いか・何が重いか）を書いた一次情報。
# 業界の風向きと類似銘柄への連想は、ここを土台に読む（dashboard/earnings.py）。
PDF_MAX_BYTES = 6_000_000
PDF_PAGES = 6            # 決算短信の定性的情報は2〜4ページ目にある

_NOISE_LINE = re.compile(r"^(\d{1,3}|[-－―ー]\s*\d+\s*[-－―ー]|.{0,40}決算短信.{0,30}|.{0,30}株式会社\s*[(（]\d{3}[0-9A-Z][)）].{0,30})$")
_TOC = re.compile(r"…|・・・|\.{4,}")
_HEAD_LINE = re.compile(r"^([①-⑳]|[（(][0-9０-９一二三四五六七八九十ａ-ｚa-z]{1,2}[)）]|[0-9０-９]{1,2}[．.]|[・■●◆○※])")

_OVERVIEW_HEAD = re.compile(r"^[（(][1１][)）].{0,25}経営成績(等)?(の概況|に関する(説明|分析))")
_OUTLOOK_HEAD = re.compile(r"^[（(][2-6２-６][)）].{0,25}(将来予測情報に関する説明|今後の見通し|業績予想に関する説明)")
_REASON_HEAD = re.compile(r"^(?:[0-9０-９ⅠⅡⅢⅣⅤ]{1,2}[．.]\s*|[（(][0-9０-９]{1,2}[)）]\s*|[【＜<])?"
                          r"(?:.{0,14}?(?:修正|変更|差異|乖離)の?)?理由[)）】＞>]?\s*$")
_SUB_STOP = re.compile(r"^[（(][0-9０-９][)）]|^[0-9０-９][．.]")
_REASON_STOP = re.compile(r"^以\s*上\s*$|^[（(]\s*注|^※|^[0-9０-９ⅠⅡⅢⅣⅤ]{1,2}[．.]|^[（(][0-9０-９]{1,2}[)）]")
_RATE_LINE = re.compile(r"^増減率")
_RATE_NUM = re.compile(r"([△▲\-−－+＋])?\s*(\d[\d,]*(?:\.\d+)?)")


def clean_pdf_text(text: str) -> str:
    """pypdf の出力を読める形にする。日本語の字の間に入る空白（「営 業 利 益」）を詰め、空行を落とす。"""
    t = (text or "").replace("　", " ").replace("\xa0", " ")
    t = re.sub(r"(?<=[^\x00-\x7f]) +(?=[^\x00-\x7f])", "", t)
    t = re.sub(r"(?<=[0-9]) +(?=[^\x00-\x7f])|(?<=[^\x00-\x7f]) +(?=[0-9])", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    return "\n".join(l.strip() for l in t.split("\n") if l.strip())


def _join_prose(lines: list[str]) -> str:
    """PDF の行の折り返しをつなぐ。文の終わり（。）と見出し・箇条の頭でだけ改行する。"""
    out = ""
    for l in lines:
        if not out:
            out = l
        elif out.endswith("。") or _HEAD_LINE.match(l):
            out += "\n" + l
        else:
            out += l
    return out


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = head.rfind("。")
    return head[:end + 1] if end >= limit // 2 else head + "…"


def _section(lines: list[str], head: re.Pattern, stop: re.Pattern, limit: int) -> str | None:
    """見出しの次の行から、次の見出しまでの文章。目次の行と、文章（。）の無い節は飛ばす。"""
    for i, l in enumerate(lines):
        if len(l) > 40 or not head.search(l) or _TOC.search(l):
            continue
        body, size = [], 0
        for x in lines[i + 1:]:
            if stop.search(x):
                if body:
                    break
                continue        # 見出しの直後の小見出し（「１. 連結業績」）は節の中身。飛ばして読み続ける
            if _NOISE_LINE.match(x) or _TOC.search(x):
                continue
            body.append(x)
            size += len(x)
            if size > limit * 3:
                break
        # 表の行（数字と見出しの羅列）は文にならないので、文（。）を含む段落だけ残す
        prose = "\n".join(p for p in _join_prose(body).split("\n") if "。" in p)
        if prose:
            return _cut(prose, limit)
    return None


def revision_dir(text: str) -> str | None:
    """業績予想の修正の表の「増減率」の行から向きを読む（利益の列の符号の多数決）。読めなければ None。"""
    rows = [l for l in (text or "").split("\n") if _RATE_LINE.match(l)]
    for row in reversed(rows):
        nums = []
        for sign, num in _RATE_NUM.findall(row[3:]):
            try:
                v = float(num.replace(",", ""))
            except ValueError:
                continue
            nums.append(-v if sign in ("△", "▲", "-", "−", "－") else v)
        if not nums:
            continue
        profit = nums[1:4] if len(nums) >= 3 else nums
        pos, neg = sum(v > 0 for v in profit), sum(v < 0 for v in profit)
        if pos > neg:
            return "up"
        if neg > pos:
            return "down"
        return None
    return None


def extract_explanations(text: str, limits: dict | None = None) -> dict:
    """PDF の本文から、会社の説明（修正の理由・経営成績の概況・業績予想の説明）と修正の向きを取り出す。"""
    lim = {"reason": 600, "overview": 900, "outlook": 400, **(limits or {})}
    lines = clean_pdf_text(text).split("\n")
    out = {
        "reason": _section(lines, _REASON_HEAD, _REASON_STOP, lim["reason"]),
        "overview": _section(lines, _OVERVIEW_HEAD, _SUB_STOP, lim["overview"]),
        "outlook": _section(lines, _OUTLOOK_HEAD, _SUB_STOP, lim["outlook"]),
        "dir": revision_dir("\n".join(lines)),
    }
    return {k: v for k, v in out.items() if v}


def fetch_pdf_text(url: str, pages: int = PDF_PAGES) -> str | None:
    """開示の PDF の先頭数ページの文字。取れなければ None（pypdf が無い環境でも落ちない）。"""
    if not url:
        return None
    try:
        import io

        from pypdf import PdfReader
    except ImportError:
        return None
    res = get(url, timeout=30)
    if res is None or not res.content or len(res.content) > PDF_MAX_BYTES:
        return None
    try:
        reader = PdfReader(io.BytesIO(res.content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:pages])
    except Exception:                       # noqa: BLE001  壊れた PDF・暗号化は欠損として扱う
        return None
