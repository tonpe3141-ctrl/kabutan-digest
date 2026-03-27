"""
kabutan_scraper.py
株探の市況（明日の株式相場に向けて）・夕刊①②③を取得してGoogleドライブに保存するスクリプト

記事URLの構造:
  https://kabutan.jp/news/marketnews/?b=n{YYYYMMDD}{NNNN}
  日次の記事ページには昼刊・夕刊が含まれないため、
  記事番号をスキャンして対象記事を探す。

Googleドライブ:
  固定ファイル名「株探ダイジェスト.txt」に全記事を保存。
  実行のたびに前回ファイルを削除して最新内容で上書き。
  Claudeデスクトップアプリからドライブ参照して要約・分析に利用する。
"""
import re
import sys
import json
import time
import os
import warnings
import requests
from datetime import datetime, date
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
DRIVE_DOC_NAME = "株探ダイジェスト"   # Google Doc名（拡張子なし）

# ==================== 株探スクレイピング設定 ====================
BASE_URL        = "https://kabutan.jp"
NEWS_ARTICLE_URL = "https://kabutan.jp/news/marketnews/?b=n{date}{num:04d}"
CONFIG_PATH     = os.path.expanduser("~/kabutan_digest/config.json")

TARGET_PATTERNS = [
    ("市況",  "明日の株式相場に向けて"),
    ("夕刊①", "話題株ピックアップ【夕刊】（1）"),
    ("夕刊②", "話題株ピックアップ【夕刊】（2）"),
    ("夕刊③", "話題株ピックアップ【夕刊】（3）"),
]
# 市況は17:30頃、夕刊は大引け後（~0700-0950）に掲載される
SCAN_START = 350
SCAN_END   = 1500
SCAN_SLEEP = 0.05   # スキャン時のリクエスト間隔（秒）

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "ja,en;q=0.9",
}

# ==================== スクレイピング ====================
def fetch_article_urls(target_date: date) -> list[dict]:
    """
    指定日の記事番号をスキャンして対象4記事のURLを取得する。
    昼刊・夕刊は日次一覧ページに載らないため、番号スキャン方式を使用。
    """
    date_str = target_date.strftime("%Y%m%d")
    print(f"[スキャン] {date_str} の記事を検索中（記事番号 {SCAN_START}-{SCAN_END}）...")
    found   = {label: None for label, _ in TARGET_PATTERNS}
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
            for label, keyword in TARGET_PATTERNS:
                if found[label] is None and keyword in clean_title:
                    found[label] = url
                    print(f"  ✅ [{label}] {clean_title[:60]} (記事番号: {num:04d})")
                    break
        except Exception:
            pass

    for label, keyword in TARGET_PATTERNS:
        if found[label] is None:
            print(f"  ⚠️  [{label}] 記事が見つかりませんでした（未掲載の可能性）")

    return [{"label": label, "keyword": keyword, "url": found[label]}
            for label, keyword in TARGET_PATTERNS]


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


def _format_content(articles: list[dict], target_date: date) -> str:
    """全記事を1つのテキストにまとめる（Claude参照用）"""
    date_label = target_date.strftime("%Y年%m月%d日")
    now_str    = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"株探ダイジェスト — {date_label}",
        f"最終更新: {now_str}  |  取得記事数: {sum(1 for a in articles if a.get('body'))}件",
        "=" * 60,
        "",
    ]
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
    return "\n".join(lines)


def save_to_drive(articles: list[dict], target_date: date) -> str:
    """
    Google Doc「株探ダイジェスト」の内容を全削除して最新記事で上書き。
    config.json に doc_id が保存されている場合はそのドキュメントを使用する。
    （ドキュメントはユーザーが作成しサービスアカウントに編集権限を付与済み）
    戻り値: ドキュメントの URL
    """
    _, docs = _get_services()

    # config.json から doc_id を取得
    config = load_config()
    doc_id = config.get("doc_id")
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
            "text": _format_content(articles, target_date),
        }
    })
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": reqs},
    ).execute()

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    print(f"  ✅ Google Drive 保存完了")
    print(f"     ドキュメント名: {DRIVE_DOC_NAME}")
    print(f"     URL: {doc_url}")
    return doc_url


# ==================== メイン ====================
def run(target_date: date = None):
    if target_date is None:
        target_date = date.today()

    print(f"\n{'='*50}")
    print(f"  株探ダイジェスト取得: {target_date.strftime('%Y年%m月%d日')}")
    print(f"{'='*50}\n")

    # 記事 URL スキャン
    articles_meta = fetch_article_urls(target_date)

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

    # Google Drive に保存
    print(f"\n[Google Drive] {DRIVE_DOC_NAME} を保存中...")
    try:
        drive_url = save_to_drive(articles, target_date)
        save_config({"last_drive_url": drive_url, "last_date": target_date.isoformat()})
    except Exception as e:
        print(f"  ❌ Google Drive 保存エラー: {e}")
        raise

    print(f"\n✅ 完了！ ({len(articles)} 件)")
    print(f"   Claude デスクトップから「株探ダイジェスト」を Google Drive で検索して参照してください。")


def save_config(data: dict):
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except Exception:
        config = {}
    config.update(data)
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
    return config


if __name__ == "__main__":
    target_date = date.today()
    if len(sys.argv) > 1:
        try:
            target_date = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        except ValueError:
            print(f"日付形式エラー: {sys.argv[1]}（例: 20260325）")
            sys.exit(1)
    run(target_date=target_date)
