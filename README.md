# マーケットダッシュボード

日本株を **寄り前 / 前場 / 大引** の3つの視点で振り返る、スマートフォン最適化ダッシュボード。
GitHub Actions が1日3回データを取得し、GitHub Pages の固定 URL を更新する。

**公開URL（Pages 有効化後）**
<https://tonpe3141-ctrl.github.io/kabutan-digest/>

---

## 1. 更新スケジュール

| 時刻（JST） | タブ | 内容 |
|---|---|---|
| 平日 07:00 | **寄り前** | 米国市場の終値・セクターローテーション・為替金利から、今日の日本株の寄りを組み立てる |
| 平日 12:00 | **前場** | 前場の指数・業種別・売買代金・値動き上位。寄り前想定との答え合わせ |
| 平日 12:40 | 前場（追い取り） | 昼刊は12:30頃掲載のため、記事を拾い直す |
| 平日 17:30 | **大引** | 大引けまでの全体像。後場の変化、引け後決算、夕刊・市況・レーティング |

各実行は**自分のタブだけ**を更新する。12:00 の実行で朝の「寄り前」タブが消えることはない。

## 2. このダッシュボードが普通と違うところ

数値を並べるだけでなく、**差分と含意**を出すことに寄せている。

- **想定オープン** — 日経平均先物が取れればそれを、取れなければ公開係数モデル
  （`0.55×S&P500 + 0.25×SOX + 0.35×ドル円`）で寄り付きのギャップを推計する。
  どちらを使ったかは画面に必ず明示する。
- **米国 → 日本セクターの連想** — 米セクターETF・米10年金利・ドル円・原油の前日変化から、
  今日どの日本の業種に追い風／向かい風が吹くかを並べる。根拠となった指標も一緒に表示する。
- **答え合わせ** — 朝の想定と実際の前場を突き合わせる。当てにいくためではなく、
  「今日は外部環境で説明できる動きか、日本市場固有の材料があるか」を切り分けるため。
- **前場 → 後場の変化** — 前場終値のスナップショットを保存しているので、
  後場に買われたのか売られたのかを大引タブで出せる。
- **市場の広がり（breadth）** — 東証33業種のうち何業種が上昇したか。
  指数がプラスでも上昇業種が半分以下なら「値がさ株主導の見かけの上げ」と指摘する。
- **資金の向き先の入れ替わり** — 売買代金上位の顔ぶれを前営業日と比較し、
  新規ランクイン・順位急上昇・連続ランクイン日数を出す。

## 3. 初回セットアップ（2ステップ）

このダッシュボードは公開市場データしか使わないため、**Secrets の登録は不要**。

1. **リポジトリを public にする**
   Settings → General → 最下部 Danger Zone → *Change repository visibility* → Public
2. **GitHub Pages を有効化する**
   Settings → Pages → Source: **Deploy from a branch** → Branch: `main` / フォルダ: `/docs` → Save

数分後に `https://tonpe3141-ctrl.github.io/kabutan-digest/` が開く。
初回はデータが空なので、Actions タブから **マーケットダッシュボード更新** を
`workflow_dispatch` で手動実行するとすぐ中身が入る。

> スマホでは Safari/Chrome の共有メニューから「ホーム画面に追加」しておくと、
> アプリのように全画面で開ける（`manifest.webmanifest` 対応済み）。

## 4. ウォッチリストの入れ替え

銘柄は頻繁に入れ替える前提で、3通りの方法を用意している。

### A. ダッシュボード上で編集（推奨・最速）
画面の「ウォッチリスト」カード右上の **編集** → コードを追加／削除 → **保存して反映**。

初回だけ **連携設定** から GitHub トークンを登録すると、保存がそのまま
`docs/data/watchlist.json` へのコミットになり、株価の再取得まで自動で走る（数分で反映）。

- 推奨トークン: **Fine-grained personal access token**
- Repository access: `kabutan-digest` のみ
- Permissions: **Contents: Read and write** だけ（他は不要）
- トークンはその端末のブラウザ内（localStorage）にのみ保存され、リポジトリには入らない

> トークンを登録しない場合でも編集内容はその端末に保存される。ただしサーバ側は
> 銘柄を知らないため、株価は「次回更新後に反映」と表示される。

### B. チャットから頼む
Claude に「ウォッチリストに 6501 を追加して」と言えば `docs/data/watchlist.json` を
更新してプッシュする。トークン登録は不要。

### C. ファイルを直接編集
`docs/data/watchlist.json` の `codes` を書き換えてコミットする。
このファイルへの push は自動的にデータ再取得のトリガーになる。

```json
{ "codes": ["7203", "8035", "6501"] }
```

## 5. ローカル実行

```bash
pip install -r requirements.txt

python -m dashboard.build --slot preopen        # 寄り前
python -m dashboard.build --slot zenba          # 前場
python -m dashboard.build --slot taibike        # 大引
python -m dashboard.build --slot auto           # 現在時刻から判定
python -m dashboard.build --slot zenba --date 20260904   # 日付指定

# ダッシュボードをローカルで開く
python -m http.server 8000 --directory docs     # → http://localhost:8000

# ネットワークを使わずに UI だけ確認する
python tools/gen_sample.py /tmp/md/data && cp docs/*.html docs/*.js docs/*.css /tmp/md/
python -m http.server 8000 --directory /tmp/md
```

## 6. 構成

```
dashboard/
  config.py          定数・米→日セクター連想マップ・取得対象ページ
  http.py            リトライとホスト単位のレート制御（例外を投げない）
  sources/
    kabutan.py       指数・ランキング・決算・記事・個別銘柄
    stooq.py         米国指数・セクターETF・為替・金利・商品（日足CSV）
  analyze.py         想定オープン／リスク環境／セクター連想／breadth／差分
  store.py           latest.json のスロット単位マージと履歴管理
  build.py           スロット単位の実行エントリ
docs/                GitHub Pages のルート（そのまま公開される）
  index.html app.js style.css
  data/latest.json   3タブぶんの最新状態
  data/history/      日次スナップショット（前日比較・連続ランクインに使用）
  data/watchlist.json
tools/gen_sample.py  UI 確認用のサンプルデータ生成
scraper.py           旧: 株探ダイジェストを Google Docs に保存（従来どおり動作）
```

### 設計上の約束

- **収集は必ず完走する** — どこかが取れなくても他の区画は出す。`http.py` は例外を投げず
  `None` を返し、`build.py` は区画ごとに握り潰す。
- **記事探索は段階的** — ニュース一覧 → 過去に当たった番号の近傍 → 全域スキャン、の順。
  件数と時間の両方に上限があり、株探が応答しない場合は早期に打ち切る。
- **履歴は差分のためだけに残す** — 前日比較に必要な列だけを保存し、60日で自動削除する。

## 7. データの出どころと注意

- 株探（kabutan.jp）: 指数・ランキング・決算発表・記事本文
- stooq.com: 米国指数・セクターETF・為替・米金利・商品の日足

いずれも公開ページを自動取得したもので、正確性・即時性は保証されない。
特に「想定オープン」と「セクター連想」は将来の株価の予測ではなく、
**外部環境の並び順を機械的に整理したもの**として扱うこと。投資判断は自己責任で。
