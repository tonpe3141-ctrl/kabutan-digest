"""
kabutan_scraper.py
株探の市況・昼刊・夕刊①②③などを取得してGoogleドライブに保存するスクリプト

記事URLの構造:
  https://kabutan.jp/news/marketnews/?b=n{YYYYMMDD}{NNNN}
  日次の記事ページには昼刊・夕刊が含まれないため、
  番号をスキャンして対象記事を探す。

モード:
  morning (前場): 昼刊 + 前場指数 + 前場ランキング → Google Doc「株探ダイジェスト【前場】」
  evening (後場): 昼刊 + 夕刊①②③ + 市況 + レーティング等 → Google Doc「株探ダイジェスト」

Googleドライブ:
  各モードの固定ドキュメントを実行のたびに上書き。
  Claudeデスクトップアプリからドライブ参照して要約・分析に利用する。
"""
import re
import sys
import json
import time
import os
import html as html_mod
import warnings
import argparse
import requests
from datetime import datetime, date, timezone, timedelta
from bs4 import BeautifulSoup
warnings.filterwarnings("ignore")

# ==================== Google Drive 設定 ====================
# garmin-to-notion プロジェクトと同じサービスアカウントを流用
_ENV_PATH = os.path.expanduser("~/Projects/garmin-to-notion/.env")

def _load_env() -> dict:
    env = {}
    try:
        with open(_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass
    return env

_ENV = _load_env()
# 環境変数を優先（GitHub Actions用）、なければ .env ファイルから読む
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or _ENV.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_DRIVE_FOLDER_ID      = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")      or _ENV.get("GOOGLE_DRIVE_FOLDER_ID", "")
DRIVE_DOC_NAME         = "株探ダイジェスト"           # 後場（evening）用 Google Doc 名
DRIVE_MORNING_DOC_NAME = "株探ダイジェスト【前場】"    # 前場（morning）用 Google Doc 名

# ==================== 株探スクレイピング設定 ====================
BASE_URL         = "https://kabutan.jp"
NEWS_ARTICLE_URL = "https://kabutan.jp/news/marketnews/?b=n{date}{num:04d}"
CONFIG_PATH      = os.path.expanduser("~/kabutan_digest/config.json")

# 後場（evening）用パターン — 昼刊も含め1日分の完全ダイジェストにする
TARGET_PATTERNS = [
    ("昼刊",              "話題株ピックアップ【昼刊】"),        # 前場終了後 12:30 頃
    ("市況",              "株式相場に向けて"),                 # 月〜木:「明日の」、金・祝前日:「来週の」など
    ("イチオシ決算",       "イチオシ決算"),                    # 引け後決算まとめ記事
    ("夕刊①",            "話題株ピックアップ【夕刊】（1）"),
    ("夕刊②",            "話題株ピックアップ【夕刊】（2）"),
    ("夕刊③",            "話題株ピックアップ【夕刊】（3）"),
    ("レーティング最上位", "レーティング日報【最上位を継続】"),
    ("レーティング新規",   "レーティング日報【新規格付け】"),
    ("レーティング弱気",   "レーティング日報【弱気継続】"),
]

# 前場（morning）用パターン — 昼刊のみ
MORNING_PATTERNS = [
    ("昼刊", "話題株ピックアップ【昼刊】"),
]

# 市況は 17:30 頃、夕刊は大引け後（~07:00-09:50）に掲載される
SCAN_START = 350
SCAN_END   = 1500
SCAN_SLEEP = 0.05   # スキャン時のリクエスト間隔（秒）

# ==================== 市場概況設定 ====================
MARKET_INDICES = [
    {
        "label": "日経平均",
        "url":   "https://kabutan.jp/stock/?code=0000",
        "type":  "kabutan",
    },
    {
        "label": "TOPIX",
        "url":   "https://kabutan.jp/stock/?code=0010",
        "type":  "kabutan",
    },
    {
        "label": "米ドル/円",
        "url":   "https://kabutan.jp/stock/?code=0950",
        "type":  "kabutan",
    },
    {
        "label": "WTI原油",
        "url":   "https://fu.minkabu.jp/chart/wti",
        "type":  "wti",
    },
]

# ==================== ランキング・決算ページ設定 ====================
# 後場（evening）用: 全データ
RANKING_PAGES = [
    {
        "label":    "売買代金ランキング",
        "url":      "https://kabutan.jp/warning/trading_value_ranking",
        "max_rows": 30,
    },
    {
        "label":    "上昇率ランキング（今日）",
        "url":      "https://kabutan.jp/warning/?mode=2_1",
        "max_rows": 30,
    },
    {
        "label":    "下落率ランキング（今日）",
        "url":      "https://kabutan.jp/warning/?mode=2_2",
        "max_rows": 30,
    },
    {
        "label":    "東証【業種別】騰落ランキング",
        "url":      "https://kabutan.jp/warning/?mode=9_1",
        "max_rows": 40,
    },
    {
        "label":    "取引時間中 決算発表・業績修正",
        "url":      "https://kabutan.jp/warning/?mode=4_2",
        "max_rows": 200,
    },
    {
        "label":    "取引終了後 決算発表・業績修正",
        "url":      "https://kabutan.jp/warning/?mode=4_3",
        "max_rows": 200,
    },
]

# 前場（morning）用: 決算「取引終了後」を除く
MORNING_RANKING_PAGES = [
    {
        "label":    "売買代金ランキング（前場）",
        "url":      "https://kabutan.jp/warning/trading_value_ranking",
        "max_rows": 30,
    },
    {
        "label":    "上昇率ランキング（前場）",
        "url":      "https://kabutan.jp/warning/?mode=2_1",
        "max_rows": 30,
    },
    {
        "label":    "下落率ランキング（前場）",
        "url":      "https://kabutan.jp/warning/?mode=2_2",
        "max_rows": 30,
    },
    {
        "label":    "東証【業種別】騰落ランキング（前場）",
        "url":      "https://kabutan.jp/warning/?mode=9_1",
        "max_rows": 40,
    },
    {
        "label":    "取引時間中 決算発表・業績修正（前場）",
        "url":      "https://kabutan.jp/warning/?mode=4_2",
        "max_rows": 100,
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "ja,en;q=0.9",
}

# ==================== スクレイピング ====================
def fetch_article_urls(target_date: date, patterns: list = None) -> list[dict]:
    """
    指定日の記事番号をスキャンして対象記事のURLを取得する。
    昼刊・夕刊は日次一覧ページに載らないため、番号スキャン方式を使用。
    patterns: TARGET_PATTERNS または MORNING_PATTERNS（省略時は TARGET_PATTERNS）
    """
    if patterns is None:
        patterns = TARGET_PATTERNS
    date_str = target_date.strftime("%Y%m%d")
    print(f"[スキャン] {date_str} の記事を検索中（記事番号 {SCAN_START}-{SCAN_END}）...")
    found   = {label: None for label, _ in patterns}
    session = requests.Session()
    session.headers.update(HEADERS)

    for num in range(SCAN_START, SCAN_END + 1):
        if all(v is not None for v in found.values()):
            break
        time.sleep(SCAN_SLEEP)
        url = NEWS_ARTICLE_URL.format(date=date_str, num=num)
        try:
            res = session.get(url, timeout=8, allow_redirects=False)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            h1   = soup.find("h1")
            if not h1:
                continue
            clean_title = re.sub(r"^【注目】", "", h1.get_text(strip=True))
            for label, keyword in patterns:
                if found[label] is None and keyword in clean_title:
                    found[label] = url
                    print(f"  ✅ [{label}] {clean_title[:60]} (記事番号: {num:04d})")
                    break
        except Exception:
            pass

    for label, keyword in patterns:
        if found[label] is None:
            print(f"  ⚠️  [{label}] 記事が見つかりませんでした（未掲載の可能性）")

    return [{"label": label, "keyword": keyword, "url": found[label]}
            for label, keyword in patterns]


def fetch_article_content(url: str) -> dict:
    """記事URLから本文・タイトル・日時を取得する"""
    time.sleep(2)
    res  = requests.get(url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    h1    = soup.find("h1")
    title = re.sub(r"^【注目】", "", h1.get_text(strip=True)) if h1 else ""

    published_at = None
    for time_el in soup.find_all("time"):
        dt_attr = time_el.get("datetime", "")
        if "T" in dt_attr and len(dt_attr) > 10:
            published_at = dt_attr
            break

    article   = soup.find("article")
    body_text = article.get_text(separator="\n", strip=True) if article else ""
    lines = [l for l in body_text.split("\n") if l.strip()]
    if lines and re.match(r"\d{4}年\d{2}月\d{2}日", lines[0]):
        lines = lines[1:]
    if lines and lines[0].startswith("【注目】"):
        lines = lines[1:]

    return {
        "title":        title,
        "published_at": published_at,
        "body":         "\n".join(lines),
        "url":          url,
    }

# ==================== 市場概況スクレイピング ====================
def _fetch_kabutan_index(info: dict) -> dict:
    """kabutan.jp の指数・為替ページから終値・前日比・前日比率を取得"""
    res  = requests.get(info["url"], headers=HEADERS, timeout=15)
    soup = BeautifulSoup(res.text, "html.parser")
    box  = soup.find("div", id="stockinfo_i1")
    if not box:
        return {**info, "close": None, "change": None, "change_pct": None}

    close_el = box.find("span", class_="kabuka")
    close = close_el.get_text(strip=True) if close_el else None

    dds = box.find_all("dd")
    change     = dds[0].get_text(strip=True) if len(dds) > 0 else None
    change_pct = dds[1].get_text(strip=True).rstrip("%") if len(dds) > 1 else None
    if change_pct:
        change_pct = change_pct + "%"

    return {**info, "close": close, "change": change, "change_pct": change_pct}


def _fetch_wti(info: dict) -> dict:
    """fu.minkabu.jp/chart/wti の ng-init から WTI 終値・前日比・前日比率を取得"""
    res  = requests.get(info["url"], headers=HEADERS, timeout=15)
    soup = BeautifulSoup(res.text, "html.parser")

    for el in soup.find_all(attrs={"ng-init": True}):
        raw = el["ng-init"]
        if "2NMX" not in raw:
            continue
        # HTML エンティティをデコードしてから JSON を抽出
        decoded = html_mod.unescape(raw)
        # init([...], {}) のような multi-arg 形式 → 最初の '[' から始まる部分をパース
        bracket_pos = decoded.find("[")
        if bracket_pos == -1:
            continue
        try:
            items, _ = json.JSONDecoder().raw_decode(decoded, bracket_pos)
        except json.JSONDecodeError:
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            code = item.get("code", "")
            if "2NMX" in code and item.get("connect") == 1:
                close      = item.get("close")
                net_change = item.get("net_change")
                pct        = item.get("percent_change")
                def _fmt(v):
                    return f"{v:+.2f}" if isinstance(v, (int, float)) else str(v) if v is not None else None
                return {
                    **info,
                    "close":      str(close) if close is not None else None,
                    "change":     _fmt(net_change),
                    "change_pct": (f"{pct:+.2f}%" if isinstance(pct, (int, float)) else None),
                }
    return {**info, "close": None, "change": None, "change_pct": None}


def fetch_market_overview() -> list[dict]:
    """日経平均・TOPIX・米ドル/円・WTI原油の市場概況を取得する"""
    print("[市場概況] 指数・為替・商品データを取得中...")
    results = []
    for info in MARKET_INDICES:
        time.sleep(1)
        try:
            if info["type"] == "kabutan":
                r = _fetch_kabutan_index(info)
            else:
                r = _fetch_wti(info)
            status = r["close"] or "取得失敗"
            print(f"  ✅ {info['label']}: {status}")
            results.append(r)
        except Exception as e:
            print(f"  ❌ {info['label']}: {e}")
            results.append({**info, "close": None, "change": None, "change_pct": None})
    return results


# ==================== ランキング・決算スクレイピング ====================
def fetch_ranking_table(page_info: dict) -> dict:
    """株価注意報ページのテーブルを取得して行リストで返す"""
    label    = page_info["label"]
    url      = page_info["url"]
    max_rows = page_info.get("max_rows", 20)

    print(f"  [取得中] {label} ...")
    time.sleep(1)
    try:
        # shared_perpage=30 で30件表示（デフォルトは15件）
        cookies = {"shared_perpage": "30"}
        res = requests.get(url, headers=HEADERS, cookies=cookies, timeout=15,
                           allow_redirects=False)
        if res.status_code != 200:
            print(f"    ⚠️  ステータスコード {res.status_code}")
            return {"label": label, "url": url, "headers": [], "rows": []}

        soup  = BeautifulSoup(res.text, "html.parser")
        table = soup.find("table", class_="stock_table")
        if not table:
            print(f"    ⚠️  テーブルが見つかりません")
            return {"label": label, "url": url, "headers": [], "rows": []}

        all_rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
            cells = [c for c in cells if c]   # 空セル除去
            if cells:
                all_rows.append(cells)

        if not all_rows:
            return {"label": label, "url": url, "headers": [], "rows": []}

        headers   = all_rows[0]
        data_rows = all_rows[1 : max_rows + 1]
        print(f"    ✅ {len(data_rows)} 行取得")
        return {"label": label, "url": url, "headers": headers, "rows": data_rows}

    except Exception as e:
        print(f"    ❌ エラー: {e}")
        return {"label": label, "url": url, "headers": [], "rows": []}


# ==================== Google Drive / Docs 保存 ====================
def _get_services():
    """Drive・Docs サービスを返す（サービスアカウント認証）"""
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_SERVICE_ACCOUNT_JSON),
        scopes=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
        ],
    )
    drive = build("drive", "v3", credentials=creds)
    docs  = build("docs",  "v1", credentials=creds)
    return drive, docs


def _format_content(articles: list[dict], target_date: date,
                    rankings: list[dict] = None,
                    market_overview: list[dict] = None,
                    mode: str = "evening") -> str:
    """全記事＋ランキングデータを1つのテキストにまとめる（Claude参照用）"""
    JST = timezone(timedelta(hours=9))
    date_label = target_date.strftime("%Y年%m月%d日")
    now_str    = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    if mode == "morning":
        doc_title = f"株探ダイジェスト【前場速報】— {date_label}"
    else:
        doc_title = f"株探ダイジェスト — {date_label}"

    lines = [
        doc_title,
        f"最終更新: {now_str}  |  取得記事数: {sum(1 for a in articles if a.get('body'))}件",
        "=" * 60,
        "",
    ]

    # ── 市場概況 ──────────────────────────────────────
    if market_overview:
        lines += [
            "■ 市場概況",
            "",
        ]
        for m in market_overview:
            close  = m.get("close")      or "—"
            change = m.get("change")     or "—"
            pct    = m.get("change_pct") or "—"
            lines.append(f"  {m['label']:<12}  終値: {close:<12}  前日比: {change:<10}  ({pct})")
        lines += ["", "=" * 60, ""]

    # ── 昼刊・夕刊・市況記事 ──────────────────────────────────
    for art in articles:
        if not art.get("body"):
            continue
        pub = (art["published_at"][:16].replace("T", " ")
               if art["published_at"] else "不明")
        lines += [
            f"【{art['label']}】{art['title']}",
            f"掲載日時: {pub}",
            f"URL: {art['url']}",
            "",
            art["body"],
            "",
            "-" * 60,
            "",
        ]

    # ── ランキング・決算データ ────────────────────────────
    if rankings:
        lines += [
            "",
            "=" * 60,
            "■ 市場データ（ランキング・決算）",
            "=" * 60,
            "",
        ]
        for r in rankings:
            if not r.get("rows"):
                lines += [f"【{r['label']}】（データなし）", ""]
                continue
            lines.append(f"【{r['label']}】")
            lines.append(f"URL: {r['url']}")
            lines.append("")
            if r.get("headers"):
                lines.append("  ".join(r["headers"]))
                lines.append("-" * 40)
            for row in r["rows"]:
                lines.append("  ".join(row))
            lines += ["", "-" * 60, ""]

    return "\n".join(lines)


def save_to_drive(articles: list[dict], target_date: date,
                  rankings: list[dict] = None,
                  market_overview: list[dict] = None,
                  mode: str = "evening") -> str:
    """
    Google Doc の内容を全削除して最新記事で上書き。
    morning モード: config の morning_doc_id を使用（「株探ダイジェスト【前場】」）
    evening モード: config の doc_id を使用（「株探ダイジェスト」）
    """
    _, docs = _get_services()

    config = load_config()
    if mode == "morning":
        doc_id   = config.get("morning_doc_id")
        doc_name = DRIVE_MORNING_DOC_NAME
        if not doc_id:
            raise ValueError(
                "config.json に morning_doc_id が設定されていません。\n"
                "Google ドキュメント「株探ダイジェスト【前場】」を作成してサービスアカウント "
                "(garmin-uploader@gen-lang-client-0369566303.iam.gserviceaccount.com) "
                "に編集権限を付与し、~/kabutan_digest/config.json の morning_doc_id に "
                "ドキュメントIDを設定してください。"
            )
    else:
        doc_id   = config.get("doc_id")
        doc_name = DRIVE_DOC_NAME
        if not doc_id:
            raise ValueError(
                "config.json に doc_id が設定されていません。\n"
                "Google ドキュメントを作成してサービスアカウント "
                "(garmin-uploader@gen-lang-client-0369566303.iam.gserviceaccount.com) "
                "に編集権限を付与し、doc_id を config.json に保存してください。"
            )

    print(f"  📄 ドキュメントを更新 (ID: {doc_id})")

    # ── 既存テキストを全削除 → 新内容を挿入 ────────────
    doc     = docs.documents().get(documentId=doc_id).execute()
    content = doc.get("body", {}).get("content", [])
    end_idx = max((el.get("endIndex", 1) for el in content), default=1)

    reqs = []
    if end_idx > 2:
        reqs.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_idx - 1}
            }
        })
    reqs.append({
        "insertText": {
            "location": {"index": 1},
            "text": _format_content(articles, target_date, rankings, market_overview, mode),
        }
    })
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": reqs},
    ).execute()

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    print(f"  ✅ Google Drive 保存完了")
    print(f"     ドキュメント名: {doc_name}")
    print(f"     URL: {doc_url}")
    return doc_url


# ==================== メイン ====================
def run(target_date: date = None, mode: str = "evening"):
    if target_date is None:
        target_date = date.today()

    mode_label    = "前場速報" if mode == "morning" else "後場ダイジェスト"
    patterns      = MORNING_PATTERNS      if mode == "morning" else TARGET_PATTERNS
    ranking_pages = MORNING_RANKING_PAGES if mode == "morning" else RANKING_PAGES
    doc_name      = DRIVE_MORNING_DOC_NAME if mode == "morning" else DRIVE_DOC_NAME

    print(f"\n{'='*50}")
    print(f"  株探ダイジェスト取得 [{mode_label}]: {target_date.strftime('%Y年%m月%d日')}")
    print(f"{'='*50}\n")

    # 記事 URL スキャン
    articles_meta = fetch_article_urls(target_date, patterns)

    # 各記事の本文を取得
    articles = []
    for meta in articles_meta:
        if meta["url"] is None:
            continue
        print(f"\n[取得中] {meta['label']} ...")
        try:
            art = fetch_article_content(meta["url"])
            art["label"] = meta["label"]
            articles.append(art)
            print(f"  📄 本文取得完了: {art['title'][:50]}")
        except Exception as e:
            print(f"  ❌ エラー: {e}")

    if not articles:
        print("\n⚠️  取得できた記事がありません。終了します。")
        return

    # 市場概況データ取得
    market_overview = fetch_market_overview()

    # ランキング・決算データ取得
    print(f"\n[ランキング] 市場データを取得中...")
    rankings = [fetch_ranking_table(p) for p in ranking_pages]

    # Google Drive に保存
    print(f"\n[Google Drive] {doc_name} を保存中...")
    try:
        drive_url = save_to_drive(articles, target_date, rankings, market_overview, mode)
        if mode == "morning":
            save_config({"morning_last_drive_url": drive_url,
                         "morning_last_date": target_date.isoformat()})
        else:
            save_config({"last_drive_url": drive_url, "last_date": target_date.isoformat()})
    except Exception as e:
        print(f"  ❌ Google Drive 保存エラー: {e}")
        raise

    print(f"\n✅ 完了！ ({len(articles)} 件)")
    if mode == "morning":
        print(f"   Claude デスクトップから「株探ダイジェスト【前場】」を Google Drive で検索して参照してください。")
    else:
        print(f"   Claude デスクトップから「株探ダイジェスト」を Google Drive で検索して参照してください。")


def save_config(data: dict):
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        config = {}
    config.update(data)
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        pass
    # 環境変数を優先（GitHub Actions用）
    if os.environ.get("KABUTAN_DOC_ID"):
        config["doc_id"] = os.environ["KABUTAN_DOC_ID"]
    if os.environ.get("KABUTAN_MORNING_DOC_ID"):
        config["morning_doc_id"] = os.environ["KABUTAN_MORNING_DOC_ID"]
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="株探ダイジェスト取得スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python3 scraper.py                          # 今日の後場ダイジェスト（デフォルト）
  python3 scraper.py --mode morning           # 今日の前場速報
  python3 scraper.py 20260408                 # 指定日の後場ダイジェスト
  python3 scraper.py 20260408 --mode morning  # 指定日の前場速報
        """,
    )
    parser.add_argument(
        "date",
        nargs="?",
        help="取得日付 YYYYMMDD 形式（省略時は今日）",
    )
    parser.add_argument(
        "--mode",
        choices=["morning", "evening"],
        default="evening",
        help="取得モード: morning=前場速報, evening=後場ダイジェスト（デフォルト）",
    )
    args = parser.parse_args()

    target_date = date.today()
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y%m%d").date()
        except ValueError:
            print(f"日付形式エラー: {args.date}（例: 20260408）")
            sys.exit(1)

    run(target_date=target_date, mode=args.mode)
