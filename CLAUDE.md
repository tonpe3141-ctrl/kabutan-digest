# マーケット（日本株の日次振り返りアプリ）

日本株を **寄り前 / 前場 / 大引** で振り返り、注目銘柄を溜めて追う、スマートフォン向けアプリ。
GitHub Pages の固定 URL で読む。設計の経緯と採点は `DESIGN.md`、運用手順は `README.md`。

旧の「株探ダイジェスト（Google ドキュメント出力）」は撤去済み。出力はこのアプリのみ。

## 仕組み（3つの役者）

| 役者 | 担当 | 動く場所 |
|---|---|---|
| **Claude Routine**（時計・分析） | 時刻どおりに発火し、収集の合図を push → main の更新を待つ → 記事と数字を読んで見立て・台帳の理由づけ・テーマ辞書の追記を書く → push → PushNotification | claude.ai の Routine（`dashboard/AI_ANALYSIS_TASK.md` が手順書） |
| **GitHub Actions**（収集） | 合図の push で即時起動。CNBC / Yahoo!ファイナンス / TDnet / 日経 / 株探の配信先から取得し、分析値を付けて `docs/data/` に書く | `.github/workflows/dashboard.yml` |
| **GitHub Pages**（表示） | `docs/` をそのまま公開 | `docs/index.html app.js style.css sw.js` |

平日の発火時刻（JST）: 寄り前 07:10 / 前場 11:40 / 大引 16:45 / 週報 金 17:00。
GitHub の cron は毎日2〜7時間遅れる（実測）ので **時計は Routine**。cron は保険で1本ずつ残す。

```
dashboard/config.py       定数・米→日セクター連想マップ・取得対象・台帳の閾値
dashboard/http.py         リトライ／レート制御。例外を投げず None を返す
dashboard/sources/        cnbc.py（相場）, yahoojp.py（ランキング）, tdnet.py（開示）,
                          news.py（Yahoo 市況記事）, kabutan_news.py（株探の配信先）, nikkei225.py
dashboard/analyze.py      想定オープン・リスク環境・セクター連想・上げの中身・差分
dashboard/themes.py       銘柄→テーマ辞書でランキングを束ねる（辞書は docs/data/themes.json）
dashboard/ledger.py       発掘台帳: 入口は機械、理由は LLM、成績は週報（docs/data/ledger.json）
dashboard/trend.py        業種・指数の 5日／20日の時間軸
dashboard/bars.py         日足キャッシュ（docs/data/cache/bars.json。CNBC 日足API、分割は全期間取り直し）
dashboard/thermo.py       相場温度計（逆張りガード）: 8軸・温度・バックテスト・業種/銘柄の分類・出尽くし・成績
dashboard/thermo_run.py   温度計の実行。docs/data/thermo.json / thermo_track.json を書く
dashboard/commentary.py   ルールベースの見立て（LLM 分析が無い時の土台）
dashboard/store.py        latest.json のスロット単位マージ、履歴、ウォッチリスト
dashboard/build.py        スロット単位の実行エントリ
dashboard/AI_ANALYSIS_TASK.md  Routine の手順書（合図・待機・分析・通知・週報）
docs/                     Pages のルート（index.html / app.js / style.css / sw.js / data/）
tools/probe*.py           データ源の疎通診断（取得が壊れたときの切り分け）
tools/verify_ai_merge.py  Routine の push を main に自動マージしてよいかの検査
tests/test_parsers.py     パーサ・台帳・テーマ・時間軸の回帰テスト（更新の前に必ず走る）
```

### データ源についての重要な前提

**株探・stooq・Yahoo Finance(米) は GitHub Actions のランナーからは使えない**（クラウドIPを拒否）。
**Claude Routine のセッションからは GitHub と api.anthropic.com 以外に一切出られない**（実測）。
だから収集は Actions、分析は Routine、と分ける。

株探の記事は本体ではなく **配信先** から取る（`sources/kabutan_news.py`）:
Google ニュース RSS（見出し全部）と Yahoo!ファイナンスのニュース一覧（株探ニュースとして配信された本文。
大引け記事・マーケット日報・**東証33業種の騰落**・PTS・増資など）。自宅 Mac は不要。

取得が壊れたら、まず Actions の「データ源の疎通診断」を手動実行して、
相手サイトの遮断なのかパーサの不具合なのかを切り分けること。

### 変更するときの約束

- **収集は必ず完走させる。** 取得失敗は例外ではなく欠損として扱い、アプリ側は「データなし」を描画する。
- **スロットは他スロットを上書きしない。** `store.save_slot()` は自分の区画だけ差し替える。
- **DOM 生成は `textContent` 経由。** 記事本文はスクレイピング由来なので `innerHTML` は使わない
  （`h()` の `html:` は自前で組み立てた SVG 専用）。
- **配色は日本市場の慣習に従う。** 上昇=赤（`--up`）、下落=青（`--down`）。
- **分析値は「予測」と言わない。** 想定オープンもセクター連想もシグナルも、算出根拠を併記した相対的な並び順。
- **入口は機械、理由は LLM。** 台帳への候補の追加・削除とテーマの判定は Python が決定的に行う。
  LLM は理由づけ・見立て・未知銘柄のテーマ付け（辞書の育成）だけ。`data` に無い数値を書かない。
- **パーサは位置で読む。** ランキング表はセル内テキストが連結される。レイアウトが違う表は
  `layout` を指定する。実測構造を `tests/` に固定してあるので、変更したら必ず `python tests/test_parsers.py`。
- **Routine が書いてよいファイルは `tools/verify_ai_merge.py` の ALLOWED だけ。** 増やすときはそこも直す。
- **ラベルは実態に合わせる。** 売買代金が取れず出来高にフォールバックしたら、表示名も「出来高」に変える。
- **見立ての土台は外部の生成AIを使わない。** commentary.py はルールベース。矛盾する指摘を同時に出さない。
- **温度計は「予測」ではなく逆張りの物差し。** 帯の境目・判定条件は thermo.py の定数で公開し、
  過去の成績（backtest）と必ず並べて出す。都合の良い読みだけを出さない。バックテストは先読みしない
  （海外系列は判定日の前日まで）。温度計の数字は LLM が付け直さない。
- **型の成績は全銘柄比で、先読みせずに測る。** `setup_backtest` はその日までの終値だけで分類し直し、
  型に入った日だけを1件と数える。売買計画（`trade_plan`）の「計画上のR倍」は必ず過去の実績（平均R）と並べて出す。
- **成績は東証の営業日で数える。** 休場日の実行（日経が `stale`）では台帳も判定の記録も進めない。
  台帳の d1/d5/d20 は大引で日足から測り直す（`ledger.retrack`）。

### 動作確認

```bash
python tests/test_parsers.py                             # 回帰テスト（ネット不要）
python -m dashboard.build --slot zenba --date 20260904   # 実データで単発実行（自宅回線から）
python tools/probe.py                                    # データ源の疎通診断
python -m http.server 8000 --directory docs
```
