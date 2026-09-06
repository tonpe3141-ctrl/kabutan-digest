# 株探ダイジェスト / マーケットダッシュボード

このリポジトリには2つの仕組みが同居している。

| | マーケットダッシュボード（新） | 株探ダイジェスト（従来） |
|---|---|---|
| 出力先 | GitHub Pages の固定URL | Google ドキュメント |
| 用途 | スマホでそのまま読む | Claude デスクトップから要約させる |
| 実行 | `python -m dashboard.build` | `python scraper.py` |
| 認証 | 不要（公開データのみ） | Google サービスアカウント |

新規の作業は基本的にダッシュボード側（`dashboard/`, `docs/`）で行う。
セットアップ・運用手順は `README.md` を参照。

---

## マーケットダッシュボード

日本株を **寄り前 / 前場 / 大引** の3タブで振り返る。GitHub Actions が
平日 07:00 / 12:00 / 12:40 / 17:30 JST に起動し、該当タブだけを更新する。

```
dashboard/config.py     定数・米→日セクター連想マップ・取得対象
dashboard/http.py       リトライ／レート制御。例外を投げず None を返す
dashboard/sources/      cnbc.py（相場）, yahoojp.py（ランキング）, tdnet.py（開示）
dashboard/analyze.py    想定オープン・リスク環境・セクター連想・上げの中身・差分
dashboard/store.py      latest.json のスロット単位マージ、履歴、ウォッチリスト
dashboard/build.py      スロット単位の実行エントリ
docs/                   Pages のルート（index.html / app.js / style.css / data/）
tools/probe.py          データ源の疎通診断（取得が壊れたときの切り分け）
tests/test_parsers.py   パーサの回帰テスト（更新の前に必ず走る）
```

### データ源についての重要な前提

**株探・stooq・Yahoo Finance(米) は GitHub Actions のランナーからは使えない。**
いずれもクラウドIPを理由に拒否する（株探は `405 Human Verification`）。実測済み。
自宅の回線からは取得できるので、ローカル実行と自動更新で挙動が変わる点に注意。

自動更新が使うのは次の3つだけ:
CNBC クォートAPI（相場）／Yahoo!ファイナンス（ランキング）／TDnet（適時開示）。

取得が壊れたら、まず Actions の「データ源の疎通診断」を手動実行して、
相手サイトの遮断なのかパーサの不具合なのかを切り分けること。

### 変更するときの約束

- **収集は必ず完走させる。** 取得失敗は例外ではなく欠損として扱い、
  ダッシュボード側は「データなし」を描画する。ここを壊すと1区画の失敗で全部が消える。
- **スロットは他スロットを上書きしない。** `store.save_slot()` は自分の区画だけ差し替える。
- **DOM 生成は `textContent` 経由。** 記事本文はスクレイピング由来なので `innerHTML` は使わない
  （`h()` の `html:` は自前で組み立てた SVG 専用）。
- **配色は日本市場の慣習に従う。** 上昇=赤（`--up`）、下落=青（`--down`）。
- **分析値は「予測」と言わない。** 想定オープンもセクター連想も、算出根拠を
  画面に併記したうえで相対的な並び順として提示する。
- **パーサは位置で読む。** ランキング表はセル内テキストが連結される
  （社名の「(株)」を出来高と誤認する等）。実測構造を `tests/` に固定してあるので、
  変更したら必ず `python tests/test_parsers.py` を通すこと。
- **ラベルは実態に合わせる。** 売買代金が取れず出来高にフォールバックしたら、
  表示名も「出来高」に変える。

### 動作確認

```bash
python tests/test_parsers.py                             # パーサの回帰テスト
python -m dashboard.build --slot zenba --date 20260904   # 実データで単発実行
python tools/probe.py                                    # データ源の疎通診断
python -m http.server 8000 --directory docs
```

---

## 株探ダイジェスト（従来のスクリプト）

株探の昼刊・夕刊記事を取得して Google ドキュメントに上書き保存する。

| モード | 実行タイミング | 取得内容 | 保存先 |
|--------|--------------|---------|--------|
| `morning`（前場） | 昼休み | 昼刊 + 前場指数 + 前場ランキング | Google Doc「株探ダイジェスト【前場】」 |
| `evening`（後場） | 夕方〜夜 | 昼刊 + 夕刊①②③ + 市況 + レーティング等 | Google Doc「株探ダイジェスト」 |

```bash
python3 scraper.py --mode morning     # 昼休みに
python3 scraper.py                    # 夕方・夜に（デフォルト）
python3 scraper.py 20260327           # 日付指定
```

### 設定

- **Google認証**: `~/Projects/garmin-to-notion/.env` の `GOOGLE_SERVICE_ACCOUNT_JSON` を流用
  （GitHub Actions では Secrets の同名変数）
- **ランタイム設定**: `~/kabutan_digest/config.json`
  - `doc_id`: 「株探ダイジェスト」（後場用）
  - `morning_doc_id`: 「株探ダイジェスト【前場】」（前場用）
  - サービスアカウント: `garmin-uploader@gen-lang-client-0369566303.iam.gserviceaccount.com`

### 仕組み

1. 株探の記事番号（350〜1500）を順にスキャン
2. モードに応じたタイトルパターンに一致する記事を発見
3. 各記事の本文・指数データ・ランキングを取得
4. Google Docs API で既存ドキュメントを全削除→新内容で上書き
