# 株探ダイジェスト スクレイパー

株探（kabutan.jp）の昼刊・夕刊記事を自動取得してGoogle Driveに保存するプロジェクト。

## 概要

| モード | 実行タイミング | 取得内容 | 保存先 |
|--------|--------------|---------|--------|
| `morning`（前場） | 昼休み（前場終了後） | 昼刊 + 前場指数 + 前場ランキング | Google Doc「株探ダイジェスト【前場】」 |
| `evening`（後場） | 夕方〜夜（後場終了後） | 昼刊 + 夕刊①②③ + 市況 + レーティング等 | Google Doc「株探ダイジェスト」 |

- 各モードで対応するドキュメントを毎回上書き
- evening モードは昼刊も含む1日分の完全ダイジェスト

## ファイル構成

```
kabutan-digest/
├── scraper.py      # メインスクリプト
├── config.json     # doc_id 等の設定（.gitignore対象）
└── requirements.txt
```

## 実行方法

```bash
cd ~/Projects/kabutan-digest

# 昼休みに（前場終了後・昼刊 + 前場ランキング）
python3 scraper.py --mode morning

# 夕方・夜に（後場終了後・全日ダイジェスト）← デフォルト
python3 scraper.py

# 特定日付の場合
python3 scraper.py 20260327 --mode morning
python3 scraper.py 20260327
```

## 設定

- **Google認証**: `~/Projects/garmin-to-notion/.env` の `GOOGLE_SERVICE_ACCOUNT_JSON` を流用
- **ランタイム設定**: `~/kabutan_digest/config.json`
  - `doc_id`: 「株探ダイジェスト」（後場用）のドキュメントID
  - `morning_doc_id`: 「株探ダイジェスト【前場】」（前場用）のドキュメントID ← **要設定**
  - サービスアカウント: `garmin-uploader@gen-lang-client-0369566303.iam.gserviceaccount.com`

### 前場ドキュメントの初回セットアップ

1. Google ドキュメントを新規作成（タイトル:「株探ダイジェスト【前場】」）
2. サービスアカウント `garmin-uploader@gen-lang-client-0369566303.iam.gserviceaccount.com` に編集権限を付与
3. ドキュメントURLの `/d/` と `/edit` の間の文字列が `morning_doc_id`
4. `~/kabutan_digest/config.json` に追記:
   ```json
   { "morning_doc_id": "ここにドキュメントIDを貼り付け" }
   ```

## 仕組み

1. 株探の記事番号（350〜1500）を順にスキャン
2. モードに応じたタイトルパターンに一致する記事を発見
3. 各記事の本文・指数データ・ランキングを取得
4. Google Docs APIで既存ドキュメントを全削除→新内容で上書き

## Claude活用フロー

```
【昼休み】
python3 scraper.py --mode morning
  ↓
Google Doc「株探ダイジェスト【前場】」更新
  ↓
Claudeデスクトップ: 「株探ダイジェスト【前場】を要約して」

【夕方・夜】
python3 scraper.py
  ↓
Google Doc「株探ダイジェスト」更新（昼刊 + 夕刊 + レーティング等）
  ↓
Claudeデスクトップ: 「株探ダイジェストを要約して」
```
