# 株探ダイジェスト スクレイパー

株探（kabutan.jp）の昼刊・夕刊記事を自動取得してGoogle Driveに保存するプロジェクト。

## 概要

- **対象記事**: 話題株ピックアップ【昼刊】【夕刊①②③】（計4記事/日）
- **保存先**: Google Document「株探ダイジェスト」（毎回上書き）
- **利用方法**: Claude デスクトップからGoogle Driveで「株探ダイジェスト」を検索して参照・分析

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

# 今日の記事を取得
python3 scraper.py

# 特定日付の記事を取得（例: 2026年3月27日）
python3 scraper.py 20260327
```

## 設定

- **Google認証**: `~/Projects/garmin-to-notion/.env` の `GOOGLE_SERVICE_ACCOUNT_JSON` を流用
- **Google Doc ID**: `config.json` の `doc_id` に設定
  - ドキュメント「株探ダイジェスト」はユーザーが作成し、サービスアカウントに編集権限を付与済み
  - サービスアカウント: `garmin-uploader@gen-lang-client-0369566303.iam.gserviceaccount.com`

## 仕組み

1. 株探の記事番号（350〜1000）を順にスキャン
2. タイトルに「昼刊」「夕刊①②③」が含まれる記事を発見
3. 各記事の本文を取得
4. Google Docs APIで既存ドキュメントを全削除→新内容で上書き

## Claude活用フロー

```
python3 scraper.py
  ↓
Google Doc「株探ダイジェスト」更新
  ↓
Claudeデスクトップ: 「株探ダイジェストを要約して」
```
