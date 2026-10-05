# 定時タスク手順書（Claude Routine 専用）

このファイルは、マーケットアプリの **時計・分析・通知** を担う Claude Routine の作業手順書です。
人間はその場にいません。判断に迷っても止まらず、ここに書かれた既定動作に従って最後まで実行してください。

呼び出し元（Routine）のプロンプトが対象を指定します。以下では `{SLOT}` と書きます。

| `{SLOT}` | 発火 (JST) | 役割 |
|---|---|---|
| `preopen` | 07:10 | 寄り前。収集の合図 → 待機 → 分析 → 通知。前日の点検も行う |
| `zenba` | 11:40 | 前場。合図 → 待機 → 分析 → 通知 |
| `taibike` | 16:45 | 大引。合図 → 待機 → 分析 → 台帳の理由づけ → 通知 |
| `weekly` | 金 17:00 | 週報。収集はせず、5営業日分の履歴と台帳から週次の総括を書く |

## なぜこの形か

- GitHub Actions の cron は毎日2〜7時間遅れる（実測）。Routine は時刻どおりに発火するので、
  **Routine が「合図」を push し、その push イベントで Actions を即時に起動する。**
- このセッションのネットワークは GitHub と api.anthropic.com 以外に出られない。
  数値やニュースの収集は Actions（`python -m dashboard.build`）がやる。**ここでは取得しない。**
- Routine の push は、このセッション固有の `claude/routine-*` ブランチに置き換えられることも、main に直接届くこともある。
  ブランチに置き換えられたときは、`docs/data/latest.json` など決められたファイルだけを変えたコミットを
  `.github/workflows/merge-ai-branch.yml` が自動で main にマージする。どちらの経路でも合図で収集が走る。

## 共通ルール

- **`data` に無い数値を書かない。** 前日比・順位・件数は必ず JSON から取る。
- 記事は「なぜ動いたか」「市場の受け止め」「次の注目点」を補うために使う。
  記事中の数値と手元のデータが食い違えば、データ側を優先する。
- 投資助言にしない。「買い」「売り」と断定せず、「〜という見方ができる」「〜に注意」に留める。
- 分からないことは「材料不足」「方向感に乏しい」と書く。物語を作らない。
- **日付は自分で計算しない。** このコンテナは UTC で、JST とは最大9時間ずれる。
  時刻が必要なときは必ず `TZ=Asia/Tokyo date` か、下記の Python を使う。
- 承認を求めない。完了まで自律的に進め、最後に短く報告する。
- **push したら終わり。** このブランチの PR を購読したり、`send_later` で定期チェックを予約したり、
  PR にコメントしたりしない。自動マージは Actions がやる。PR が自動で作られていても放置してよい
  （スロットごとに1本、開いたままにしておく設計）。

---

## STEP 0: リポジトリを最新にする

このセッションにはリポジトリ（kabutan-digest）がチェックアウト済みのはず。**そのチェックアウトを使う**
（push の認可はこのセッションに紐づいているため、別の場所に clone し直さない）。

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
[ -f dashboard/AI_ANALYSIS_TASK.md ] || { cd ~ && [ -d kabutan-digest/.git ] || git clone https://github.com/tonpe3141-ctrl/kabutan-digest.git; cd ~/kabutan-digest; }
git fetch origin main && git reset --hard origin/main
git config user.name  "claude-routine[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
```

このセッションは定時に何度も起こされる（前回の会話の続きになる）。**毎回この STEP 0 から**やり直し、
前回の記憶にある数値やファイル内容を使い回さない。

`{SLOT}` が `weekly` のときは STEP 1・2 を飛ばして **STEP W** へ。

## STEP 1: 収集の合図を push する

```bash
SIGNAL_AT=$(TZ=Asia/Tokyo date '+%Y-%m-%dT%H:%M:%S+09:00')
mkdir -p docs/data/trigger
echo "$SIGNAL_AT" > "docs/data/trigger/{SLOT}.txt"
git add docs/data/trigger/{SLOT}.txt
git commit -m "収集の合図: {SLOT} ($SIGNAL_AT)"
git push origin HEAD:main
echo "SIGNAL_AT=$SIGNAL_AT"
```

push 先は `claude/*` ブランチに置き換えられることも、main に直接届くこともある（2026-09-28 は main に直接届いた）。
どちらでも正しい。`docs/data/trigger/*.txt` の push で **マーケットダッシュボード更新** の
ワークフローが数秒以内に起動し、main にデータを書き込む。

## STEP 2: main にデータが届くのを待つ

最大 20 分。30 秒ごとに確認する。

```bash
python3 - "$SIGNAL_AT" <<'PYEOF'
import json, subprocess, sys, time
from datetime import datetime

signal_at = datetime.fromisoformat(sys.argv[1])
deadline = time.time() + 20 * 60
while True:
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False)
    raw = subprocess.run(["git", "show", "origin/main:docs/data/latest.json"],
                         capture_output=True, text=True).stdout
    try:
        slot = (json.loads(raw).get("slots") or {}).get("{SLOT}")
        upd = datetime.fromisoformat(slot["updated_at"]) if slot else None
    except Exception:
        upd = None
    if upd and upd >= signal_at:
        print(f"OK: 収集完了 updated_at={slot['updated_at']}")
        break
    if time.time() > deadline:
        print("TIMEOUT: 20分待っても届きませんでした")
        break
    time.sleep(30)
PYEOF
```

- `OK:` → STEP 3 へ。
- `TIMEOUT:` → Actions が動かなかった可能性が高い。**それでも止まらない。**
  `origin/main` の `slots.{SLOT}.updated_at` が今日（JST）の、その区分の時間帯
  （寄り前 06:00 以降・前場 11:30 以降・大引 15:30 以降）なら、その既存データで STEP 3 以降を続ける。
  日付だけで判断しない（深夜 00:43 に前の日の引けで作られた大引を今日の大引とみなした実測が 2026-09-30 にある）。
  今日のデータが無ければ STEP 6 の通知だけを
  「{SLOT} の収集が届きませんでした（Actions を確認）」という内容で送って終了する。

## STEP 3: main に追随してからデータを読む

```bash
git pull --rebase origin main
```

（合図のコミットが main に取り込まれていれば自動で消え、まだなら main の上に載る。どちらでもよい。）

読むもの（すべて `docs/data/latest.json` の `slots.{SLOT}.data`）:

- **寄り前**: `us`（米国指数）, `macro`（為替・金利・商品）, `sectors_us`, `implied_open`, `risk`,
  `sector_outlook`, `carryover.after_hours_kessan`（前営業日引け後の開示）, `news`, `kabutan`, `press`
- **前場・大引**: `indices`, `divergence`, `sectors_jp`, `breadth`, `constituents`,
  `tables.value/gainer/loser/ytd_high/vol_surge`, `tables.kessan_intraday/kessan_after`,
  `disclosure_summary`, `ranking_delta`, `streaks`, `theme_flow`（テーマ別の資金の向き）,
  `news`, `kabutan`, `press`
- **`kabutan`**: `headlines[]`（株探の見出し。`{title, published}`。本文なし）と
  `articles[]`（Yahoo!ファイナンス配信の株探記事。`{headline, timestamp, body, url}`）。
  見出しは「話題株ピックアップ【夕刊】（1）：Ａ、Ｂ、Ｃ」のように、それ自体が要約になっている。
- **`news[]`**: Yahoo!ファイナンス マーケットAIトピックス。`{headline, category, timestamp, body, url, source}`。
  **AI が生成した要約なので、事実の裏付けには使わない**（他の情報源で確かめられた範囲で参考にする）。
- **`press`**（株探以外の信頼できる情報源。配信元はドメインで照合済み）:
  - `official[]` … 日本銀行・財務省・日本取引所グループ・FRB の公式発表の表題 `{title, published, url, source}`。**一次情報**
  - `headlines[]` … ロイター・ブルームバーグ・日本経済新聞・時事通信・NHK の見出し `{title, published, url, source}`（本文なし）
  - `articles[]` … 時事通信・トレーダーズ・ウェブ・ウエルスアドバイザーの本文 `{headline, timestamp, body, url, provider, partial}`。
    `partial: true` は有料部分の手前までしか無い（書かれていない続きを推測しない）
  - `overseas[]` … CNBC（英語）の見出しと要約 `{title, summary, published, url, source}`
  - `wire[]` … トレーダーズ・ウェブの短信の表題 `{title, kind, published, url}`。`kind` は `result`（経済指標の結果。
    「【指標】9月中国製造業PMI 50.1、予想 50.1」のように結果と予想が表題にある）・`schedule`（発表予定）・
    `remarks`（要人発言。「【要人発言】経済財政相「…」」）。**表題そのものが事実**なので、そのまま引いてよい
  - `macro_articles[]` … マクロの材料の本文 `{headline, body, url, provider}`。1日の **イベントスケジュール**（重要度 ☆◎◇ と市場予想つき）・
    **FF金利織り込み度**（次回・次々回の FOMC で各金利水準を織り込む確率。現在・1週間前・1カ月前）・国内外の **主な経済指標**の一覧・
    **マーケットダイジェスト**（為替・株・債券のまとめ）・**為替見通し**
  - `status[]` … 情報源ごとの取得結果。`ok: false` の情報源は「取れなかった」のであって「何も無かった」ではない
- **`macro_view`**（全スロット。マクロ環境のカード）: `rows[]` … 米2・10・30年、日本2・10・30年、ドル円、WTI・金・銅の
  `last`（水準）・`d1`/`d5`/`d20`/`d60`（金利は bp、ほかは %）・`pos`（1年のレンジの中の位置 0〜100）・`record`（取れている約3年での
  `high`/`low`）・`trend`（20日の変化が公開の閾値を超えた向き）。`spreads[]` … 米国の長短差（10年−2年）と日米の10年金利差の水準と20日の変化（bp）。
  `topics[]` … マクロの話題（日銀・FRB・為替・原油・地政学・通商・財政・指標・中国・需給）ごとに、何媒体・何本が報じたかと代表の見出し。
  `commentary` … 数字だけで組み立てた文章（なぞるだけでは意味がない）
- **`thermo`**（全スロット）: 相場温度計の要約と、売買タブの注文。`temp`（0〜100）, `zone`（総悲観／悲観／中立／楽観／過熱）,
  `consensus`（全面追い風／全面向かい風）, `factors[]`（8軸の `score` −2〜+2 と `change` 改善／悪化、`text` に根拠の数字）,
  `turning`（底打ち／天井打ちの兆し）, `sector_picks`（押し目・下げ止まりの業種）, `sector_hot`, `hot`（高値掴み注意）/
  `bad_out` / `good_out`（出尽くし）, `themes`（テーマの論調×値動き）, `watch_guard`（ウォッチリストの注意書き）,
  **`swing`**（短期の押し目買い。買う候補を出すのはこれと `swing.mid` の2つのルールだけ）: `asof`（この日の引けで出た注文。次の営業日だけ有効）,
  `orders[]`（`code`・`name`・買いの指値 `limit`・損切り `stop`・売りの目安 `sell`（毎朝の売り指値＝直近4日の終値の平均。
  ただし買値 +0.2% の `floor` より下には置かない）とそれぞれの率、`peer` = 業種の中での位置
  `{label（押しの形）, group, g20（業種の20日騰落）, rel20（業種との差）}`）, `more`（上限で外れた次点の数）,
  `skip[]`（押したが「業種の上げに沿った押し」で見送った銘柄）,
  `near[]`（あと少しの下げで注文対象になる銘柄と、その終値 `trig`）, `verify`（このルールを日足キャッシュの期間に当てた
  `all`/`recent` の回数 `n`・勝率 `win`・平均 `avg`・PF `pf`・平均の保有日数 `days` と、比べる相手 `base`。勝率を書くときは、+0.5% 以下の小さな勝ちの割合
  `small` と −5% 以下の大きな負けの割合 `big_loss` も読んで、「勝率は高いが負けは1回が大きい」ことを落とさない。
  `verify.account` は同じ期間に **本番どおりに置いた口座**（引けで上から1日5件まで・資金の `slot`%ずつ・約定しなければ資金はその日遊ぶ）の
  年率 `cagr`・最大の目減り `dd`・稼働率 `util`・月でプラスだった割合 `m_up`・資産の倍率 `final`。「どれくらいの期間で増えるか」はこちらで書く）,
  `paper`（アプリが出した注文の実績。結果が出てから。`paper.account` は出した注文どおりに資金の10%ずつ置いた口座）, `slot_pct`（1件の金額＝資金の%）,
  **`mid`**（中期の押し目。1年で強い銘柄＝12か月の強さが上位1/3 が、60日高値から1〜3か月・−10% 以上調整している中で短く押した日の注文。
  短期とは別の計画で、同じ銘柄に両方は出ない）: `orders[]`（`code`・`name`・買いの指値 `limit`・損切り `stop`（調整の安値−1ATR）と `stop_pct`・
  60日高値 `hi` と指値からの距離 `to_hi`・高値からの営業日数 `age`・下げ `dd`・強さの順位 `rank`（%）・`hold`＝60営業日目の引けで売る）,
  `slots`（中期の保有の上限6銘柄）, `n_shape`（調整中の銘柄の数）, `near[]`（押し待ちの銘柄と終値 `trig`）,
  `verify`（`all`＝中期の1回ごと、`base`＝比べる相手＝調整の条件なしで強い銘柄の押しを同じ出口で、`account`＝短期と中期を合わせた口座、
  `account_prev`＝以前の置き方＝短期だけの口座）, `paper`（アプリが出した中期の注文の実績）。勝率は3割前後で損切りが多く、少数の大きく伸びた銘柄が
  平均を作る形なので、勝率だけを書かない。`verify.account` は短期と中期を合わせた口座。
  **`strength`**（業種の強弱。業種タブ）: `top[]`・`bottom[]`（強い順・弱い順に5業種。`g` 業種、`rank` 順位、`score` 強さ 0〜100、
  `quad` 先行／一服／改善／出遅れ、`rs60`・`rs120`・`rs20` 市場との差%、`br50` 50日線より上の銘柄%、`rank20` 20営業日前の順位）,
  `rising[]`（20営業日で順位を5以上上げた業種）, `verify`（直近の検証: 強さの上位1/5・下位1/5 の次の20日の市場との差 `avg` と
  市場に勝った割合 `pos`、上位が下位を上回った回 `beat`）。
  **`crowd`**（急騰して混み合った銘柄の印と、主役の入れ替わり。DESIGN.md 24章）: `rule`（印の条件＝3日で +10% 以上かつ出来高が20日平均の1.5倍以上）,
  `verify.hot`・`verify.all`（印の付いた銘柄／全銘柄の、翌日に市場より3%以上弱くなった割合 `weak`・強くなった割合 `strong`・上回った割合 `up`。
  `[前半, 後半]` の2つ）, `watch[]`（ウォッチリストの銘柄で、印が付いた `cw`・または主役の入れ替わりに入ったもの `rot`＝`out`置き去り／`into`買われた と
  対市場の差の前日 `prev`→今日 `now`）, `rotation`（`mkt` 対市場の基準＝全銘柄の中央値の前日・今日、`out[]` 前の営業日に市場を大きく上回り今日は下回った銘柄・
  `into[]` 前の営業日は市場以下で今日大きく買われた銘柄・`themes[]` テーマ別の件数）。
  詳細は `docs/data/thermo.json`（業種の全表・バックテスト `backtest.zones[]`・警告の成績 `track[]`・`swing` の全体）、
  注文の記録は `docs/data/swing_track.json`
- **`earnings`**（全スロット。決算・修正の開示が無い日は無い）: 寄り前は **前営業日の引け後**、前場・大引は **その日** の決算・業績修正・配当修正を、
  修正を先に最大25社（前場10社）読んだ材料。`asof`（開示の日）, `scope`, `n_total`（決算・修正を出した会社の数）, `n_read`（読んだ数）,
  `more[]`（読んでいない会社）。`items[]` は1社ずつ:
  `code`・`name`・`time`・`kinds`（業績予想の修正／決算短信／配当予想の修正）・`titles`（TDnet の表題）・`industry`（東証33業種）・`themes`（テーマ辞書）・
  `dir`（`up`/`down`/`mixed`。株探の見出しの語、無ければ修正の PDF の増減率の符号から機械が読んだ向き）・
  `flash`（株探【決算速報】の `headline` と `body`＝数字の要約）・**`reason`**（修正の PDF の「修正の理由」）・**`overview`**（決算短信の「経営成績の概況」）・
  **`outlook`**（決算短信の「業績予想の説明」）… この3つが **会社自身の言葉の事業環境**（どの需要が強いか・何が重いか）で一次情報・
  `press[]`（時事通信 DZH 個別株情報・ウエルスアドバイザーなどその銘柄の見出し）・`move`（`pct` 直近の値動き。寄り前は開示の日の値動きで、引け後の開示への反応はまだ入っていない）・
  `peers[]`（類似銘柄。`src` が「株探の比較銘柄」→「テーマ: …」→「日経225の同じ業種: …」の順。`pct` 直近の値動き・`r20` 20日の騰落・`next` 次の決算予定日・
  `today` 同じ回に読んだ決算の向き・`last` 最近読んだ決算 `{date, dir, head}`）。
  `wind`（風向き）: 直近 `days` 営業日に **読んだ** 開示の向きを `industries[]`・`themes[]` ごとに数えたもの（`key`・`up`・`down`・`mixed`・`n`・`lean`・`names[]`）。
  全開示の集計ではない（修正と追っている銘柄を先に読んでいる）。株探の「サプライズ決算」「明日の好悪材料」「明日注目すべき【好決算】」の本文は `kabutan.articles` にある
- 参考: `commentary`（ルールベースの見立て。なぞるだけでは意味がない）

あわせて、あれば読む:

- `docs/data/ledger.json` … 発掘台帳。`entries[]` のうち `first_seen` が今日で `notes` が空のもの
- `docs/data/themes.json` … 銘柄→テーマ辞書。`slots.{SLOT}.data.theme_flow.unknown_codes[]` が
  辞書に無い銘柄（`{code, name}`）

## STEP 4: 書く

### 4a. 相場の見立て（必須）

市況全体（今日の相場がどう動き、なぜ動き、資金がどこへ向かい、次に何を見るか）を書く。マクロ（金利・為替・商品・政策）の
詳しい話は 4b の `ai_macro` に書き、ここでは相場の値動きとのつながりを1〜2文で触れるだけにする（同じ話を2か所に書かない）。

`slots.{SLOT}.data.ai_commentary` に次の形を設定する:

```json
{
  "headline": "一言見出し（15〜25字）",
  "sections": [
    {"title": "セクション見出し", "body": "本文（200〜400字。読みやすい文章で）"}
  ],
  "method": "Claude による分析",
  "generated_at": "実行時の ISO8601 日時（JST, +09:00）",
  "sources": [{"title": "参照した記事の見出し", "url": "記事URL"}]
}
```

セクションは 4〜5 個、1つ 250〜450字。次の骨組みで書く（見出しは内容に合わせて具体的に。例:「後場に一段高、全33業種が上昇」）:

1. **相場の総括** … 指数の水準と騰落、日経と TOPIX の乖離（`divergence`）、値上がり・値下がりの広がり（`breadth`・株探の東証33業種）、
   前場からの変化（`session_shift`）や寄り前の想定との差（`verify_open`）。数字を並べるだけでなく「値がさ主導か、広い買いか」を言い切る
2. **なぜ動いたか** … 株探の相場記事（「日経平均 大引け／前引け」「マーケット日報」「明日の株式相場に向けて」「前場／後場に注目すべき3つのポイント」）の
   説明を土台に、報道（時事「東京株式」・ロイター・日経・トレーダーズ・ウェブ「大引け概況」「明日の戦略」）で裏付ける。
   海外（米株・SOX・為替）、国内の材料（日銀・財務省・政治）、需給（先物主導・海外勢・月末や四半期末・配当落ち）のどれが主因かを分けて書く。
   各社の説明が割れていれば両論を書く
3. **物色と需給** … 売買代金の上位と新顔（`tables.value`・`ranking_delta`・`streaks`）、テーマの資金の向き（`theme_flow`）、
   業種の上位・下位、急騰・急落の顔ぶれ、空売り比率（トレーダーズ・ウェブ「空売り集計」）・投資部門別・先物の手口の記事があればその数字。
   「どこに資金が集まり、どこから抜けたか」を1つの絵にする
4. **逆張りの視点**（`thermo` があれば必須。下の決まりどおり）
5. **次の注目点** … 引け後の開示（`tables.kessan_after`・`disclosure_summary`。個々の決算の読みは 4e の `ai_earnings` に書き、ここでは1〜2文と
   `ai_earnings.headline` の要旨だけにする）、明日の決算予定（株探の見出し）、
   今夜〜明日の重要イベント（`press.macro_articles` のイベントスケジュールの ☆）。寄り前は今日の日中のイベントと前日引け後の材料

寄り前は 1〜3 を「夜間の米国市場と金利・為替」「前日の東京市場と今日の想定」「今日の材料」に読み替える（`us`・`implied_open`・`carryover`）。
**`thermo` があれば、そのうち1つを必ず「逆張りの視点」にする。** 書くこと:
- 温度と帯、どの軸が追い風／向かい風か（`factors` の数字をそのまま使う。温度やスコアを自分で付け直さない）
- ニュースの論調と値動きのずれ（例: 好材料の見出しが続くのに `good_out` に出ている＝好材料出尽くし、
  悪材料の見出しが続くのに `bad_out` や `themes` で「下げ止まり」＝悪材料出尽くしの兆し）。記事で裏付けが取れた範囲で
- `sector_picks` / `hot` / `good_out` のうち記事で背景が分かるもの。「買い」「売り」と断定せず、
  「押しを待つ」「追いかけは控えめに」の言い方にとどめる
- `backtest` で温度帯の過去の成績が逆張りの読みと食い違っているときは、そのことも書く（都合の良い読みだけを書かない）
- `swing.orders` があれば、銘柄名と指値をそのまま伝える（価格や勝率を自分で計算し直さない。`verify` の数字を使う）。
  成績に触れるときは、1回ごとの勝率（`verify.all.win`）だけでなく口座の伸び（`verify.account.cagr` と `dd`）も並べる
  （置いた注文の約定は4割弱で、資金の半分以上は遊ぶ。1回ごとの勝率から想像するより口座の伸びは遅い）。
  記事で背景（押した理由）が分かるものは一言添えてよいが、「買い」と断定せず「注文の条件に入った」の言い方にとどめる。
  `peer.label` が「業種ぐるみの押し」「出遅れの押し」なら、その形と業種（`group` と `g20`）をそのまま添えてよい（数字は付け直さない）。
  `skip` の銘柄を「買い場」と書かない（業種の上げに沿った押しは検証で弱かったので見送っている）。
  大引で `swing.asof` が今日より前なら、その注文は今日で期限切れ（次の営業日の注文ではない）なので、注文として書かない。
  注文が無い日は「押し目の注文なし。待つのも作戦」と書いてよい。温度で注文を増やす・減らすとは書かない
  （このルールは温度で絞らない。検証では弱い地合いの押しのほうが成績が良かった）
- `swing.mid.orders` があれば、「中期の押し目」として銘柄名・指値・損切りをそのまま伝え、60営業日持つ計画であること、
  1年の強さと調整（`age` 営業日・`dd`%）を添えてよい。記事で調整の理由（業種ぐるみの下げ・材料）が分かるものは一言添えてよいが、
  「底打ち」「上昇再開」と断定しない（調整明けは予測できない。押した日に指値を置く規則）。成績は `mid.verify` の数字を比べる相手と並べる
- `strength` があれば、追い風の業種（`top` の上位）と向かい風の業種（`bottom`）を名前と `rs60`（市場との差）で一言添えてよい。
  記事で背景（なぜ強いか）が分かるものだけ理由を書く。「この業種を買い」とは書かない（強さは注文の条件ではない）。
  `quad` が「改善」の業種を「反転」「買い場」と書かない（弱い業種の戻りは、検証では次の20日も市場に負けがちだった）。
  強さや順位を自分で付け直さない。

- **`crowd`**（`thermo.crowd`）があれば、次の2つを「物色と需給」か「逆張りの視点」に1〜2文で書く（事実の並べ替えで、予測ではない）:
  - **資金の移り先**（大引・前場）: `rotation.out`（前の営業日の主役が今日は置き去り）と `rotation.into`（出遅れが今日買われた）を、`themes[]` の
    テーマで束ねて「どのテーマからどのテーマへ資金が移ったか」を1つの絵にする（例: 電線・光ファイバーから半導体製造装置へ）。
    `watch[]` に `rot` が付いた銘柄（持ち株の候補）があれば、必ず名前と `prev`→`now` を挙げ、その銘柄に個別の材料（`disclosure_summary`・
    `earnings`・`press`・`news` の見出し）があったかを確かめて、あれば書き、無ければ「個別の材料は見当たらない＝資金の移り先の問題」と書く。
    **相場全体が大きく上がった日に持ち株だけ下がった理由を、材料のない銘柄で「悪材料」とこじつけない。**
  - **明日の振れ幅**（寄り前・大引）: `watch[]` に `cw` が付いた銘柄（3日で急騰して出来高が膨らんだ）があれば、名前・3日の騰落 `r3`・出来高の倍数 `vr` を挙げ、
    `verify.hot` と `verify.all` の `weak`／`strong` を並べて「市場と違う動きをしやすい（弱くなる側にも強くなる側にも）。向きは `up` が五分で読めない」と書く。
    **「天井」「売り」「過熱だから下がる」と書かない**（検証では急騰した銘柄は翌日も平均では市場を上回り、売りのサインにならなかった）。
    書いてよい行動は「市場とずれても耐えられる株数か・撤退ラインを決めているかの確認」まで
- 印も入れ替わりも `watch[]` が空なら、`rotation` の全体の話だけ書く。数字（`prev`・`now`・`weak`・`strong`）は自分で計算し直さない

**情報源を突き合わせる。** 株探だけに寄らず、次の順で重みを付ける:
1. 一次情報（`press.official`、TDnet の開示）… 事実として書いてよい
2. 通信社・全国紙・公共放送（`press.headlines` / `press.articles` / `press.overseas`）… 2社以上が同じ事実を報じていれば事実扱い。
   1社だけなら「〇〇（媒体名）によると」と出所を明記する
3. 株探（`kabutan`）… 市場の受け止め・話題株の把握に使う。背景の説明は 1・2 で裏付けが取れたときに書く
4. `news`（AI 生成の要約）… 補助。これだけを根拠にしない

媒体によって見方が割れていれば（例: ある社は「円高警戒」、別の社は「利上げ観測」を主因に挙げる）、どちらか一方に寄せず
両論として書く。寄り前は `press.overseas` と `press.headlines` の米国市況・為替で夜間の材料を、前場・大引は
`press.official`（日銀・財務省の発言や介入）と国内報道で場中の材料を確かめる。

複数の事実を組み合わせた解釈を書く（例: 日経は上昇したが TOPIX との乖離と上昇銘柄数から
値がさ株主導であり、株探の夕刊が挙げる AI 関連への資金集中と整合する）。
`sources` には実際に使った記事だけを入れる（`kabutan.articles` / `press.*` / `news` の URL）。
見出しだけで使った記事（`press.headlines`・`kabutan.headlines`）も、根拠にしたなら入れてよい。`title` には媒体名を添える
（例: `"ロイター: 東京株式市場・大引け＝5日続伸"`）。

### 4b. マクロ環境の見立て（必須。全スロット）

`slots.{SLOT}.data.ai_macro` に 4a と同じ形（`headline` / `sections` / `method` / `generated_at` / `sources`）を設定する。
市況タブの「マクロ環境」のカードに、`macro_view` の表の下に出る。**表の数字を文章で読み上げるのではなく、「なぜそう動いているか」
「市場は何を織り込みにいっているか」「株式のどこに効くか」を、報道と一次情報で裏付けて書く。**

`headline` は 15〜25字で、いまのマクロの主題を言い切る（例:「米長期金利5.2%台、利上げ観測が円と株の重し」）。
セクションは 3〜5 個、1つ 200〜400字。材料が無いセクションは作らない:

1. **金利と金融政策** … 米国は `macro_view.rows` の 2年・10年・30年（前日・20日・`record`）と長短差（`spreads`）、
   FRB の次の一手を市場がどう織り込んでいるか（`macro_articles` の **FF金利織り込み度**の数字を、現在・1週間前・1カ月前の変化として書く）、
   FRB 当局者の発言（`press.official` の FRB・`wire` の要人発言・報道）。日本は 2年・10年・30年と、日銀の政策（`press.official` の日銀・
   議事要旨・国債買入れの予定、要人発言、報道の「利上げ観測」）、国債入札の結果（財務省）。**「2年が主導か10年が主導か」から、
   利上げ観測なのか財政・インフレへの警戒なのかを区別する**（`macro_view.commentary` の曲線の読みは参考。報道で裏付けてから書く）
2. **為替** … ドル円の水準と5日・20日の流れ、日米の金利差（`spreads`）と為替の向きが合っているか、介入（財務省「外国為替平衡操作の実施状況」・
   報道の「介入警戒」）、要人発言、月末・期末などの需給（マーケットダイジェスト・為替見通し）
3. **商品と地政学** … 原油・金・銅の水準と流れ（`record` があれば必ず）、中東・ロシアなどの報道、供給の材料（産油国・在庫統計）
4. **政治・財政・通商**（`topics` にあるときだけ）… 予算・税・政権・関税・米国の政治。一次情報（財務省・官邸の発表）と報道を分ける
5. **株式への波及と次の注目イベント** … 上の動きがどの業種に効きやすいか（`macro_view.commentary` の「業種の感応度」の文と、
   `thermo.factors` の金利・為替・原油の軸の数字をそのまま使う。点数は付け直さない）、今日の経済指標の結果（`wire` の `result`。
   予想との差を書く）、今夜〜明日の重要イベント（イベントスケジュールの ☆ を時刻つきで。書いていない予定を足さない）

書き方の決まり:
- **数字は `macro_view` と記事に書かれたものだけ。** 水準・変化幅を自分で計算し直さない（記事と `macro_view` が食い違えば `macro_view` を優先し、
  記事の数字は「〇〇（媒体名）によると」と出所をつける）
- **何社が報じたかで書き方を変える。** `topics[].media` が2つ以上の話題は事実として、1社だけなら出所を明記する。一次情報（`press.official`）は事実
- **市場の織り込みと自分の見方を混ぜない。** 「FF金利先物は12月の利上げを42%織り込む（トレーダーズ・ウェブ）」は事実、
  「利上げが近い」とは書かない。「〜の形」「〜に読まれやすい」にとどめ、予測しない
- 見方が割れている材料（例: 円高の主因を「日銀の利上げ観測」とする社と「実需の売り」とする社）は両論で書く
- 寄り前は夜間の米国（金利・FRB 発言・米指標）と東京の今日の予定、前場は場中の日銀・財務省・国債入札・要人発言、
  大引は1日のまとめと今夜の欧米の予定に重心を置く
- `sources` には使った記事だけ（`press.*`・`kabutan.*`・`macro_articles`・`wire` の URL）。`title` に媒体名を添える

### 4c. 台帳の理由づけ（大引のみ。台帳があれば）

`docs/data/ledger.json` の `entries[]` で `first_seen` が今日かつ `notes` が空の各行に、
`notes` を 1〜2 文で書く。**候補の追加・削除・シグナルの変更はしない**（機械が決める）。
書くのは「なぜ数字に引っかかったか」に、記事（`kabutan` / `press`）で裏付けが取れれば「何が材料か」を添えるだけ。
裏付けが無ければ「記事での裏付けなし」と書く。裏付けた記事の URL を `note_url` に入れる。

### 4d. テーマ辞書の育成（前場・大引。未知銘柄があれば）

`theme_flow.unknown_codes[]` の各銘柄について、記事・開示・自分の知識から
テーマが分かるものだけ `docs/data/themes.json` の `stocks` に追記する:

```json
"5803": {"name": "フジクラ", "themes": ["電線", "データセンター"]}
```

- テーマ名は辞書の `themes` に既にあるものを優先して使う。新しいテーマ名は本当に必要なときだけ。
- 1銘柄 1〜3 テーマ。分からない銘柄は書かない（空で埋めない）。
- 既存の行は変更しない。

### 4e. 決算から読む（全スロット。`earnings` があれば必須）

`slots.{SLOT}.data.ai_earnings` に書く。ニュースタブの「決算から読む」のカードに、機械が集めた材料（会社の説明・決算速報・類似銘柄の値動きと決算予定・
業種の風向き）と並んで出る。**決算の数字を読み上げるのではなく、「その決算から何が読めるか」＝業界の風向き・類似銘柄への連想・次の投資のヒントを書く。**

```json
{
  "headline": "いちばん大事な読みを一言（15〜28字。例: 設備投資向けの上方修正相次ぐ、FA・制御機器に追い風）",
  "winds": [
    {"title": "業界の風向きの見出し（例: AI向け設備投資がFA部品まで波及）",
     "body": "200〜400字。どの会社の説明（reason/overview/outlook）と数字（flash）が根拠か、wind の上向き・下向きの数、報道での裏付け",
     "codes": ["6652", "6504"]}
  ],
  "items": [
    {"code": "6652",
     "read": "何が分かったか（80〜160字）。数字は flash のまま、会社が挙げた理由（どの需要・どの地域・どの製品）を添える",
     "wind": "業界への含意（50〜120字）。この1社だけの事情か、業界に広がる話か",
     "peers": [{"code": "6504", "why": "連想の理由（30〜80字）。同じ需要・同じ顧客・同じコストを受けるか、すでに動いているか、決算がいつか"}],
     "next": "次に確かめること（40〜120字）。類似銘柄の決算日（peers[].next）、会社の業績予想（outlook）・受注残・月次など"}
  ],
  "method": "Claude による分析",
  "generated_at": "実行時の ISO8601 日時（JST, +09:00）",
  "sources": [{"title": "媒体名: 見出し", "url": "URL"}]
}
```

- **`winds`** は 1〜3 個。複数の会社の説明に共通する需要・コストの話（例: 「AI・データセンター向けの設備投資」「建設コストの上昇と住宅ローン金利」
  「インバウンドの鈍化」）を、**会社の説明の文言を根拠に** 書く。`wind.industries` の数（例: 電気機器 上3・下0）はそのまま使い、
  「読んだ開示の中で」と断る（全開示の集計ではない）。1社だけの説明なら「1社の説明」と書く。下向きの風（減益・下方修正の共通の理由）も同じ重さで書く
- **`items`** は、読みどころのある会社だけ 3〜8 社（修正、会社の説明に業界の話がある決算、類似銘柄が多く動いた・決算を控える会社を優先）。全社は書かない
- **類似銘柄は `items[].peers` か、`items`・`wind.names` に出た銘柄からだけ選ぶ**（自分の知識で銘柄を足さない。辞書にない連想先は書かない）。
  理由は「同じ需要を受ける」「同じ顧客」「原材料・金利・為替が同じ向きに効く」の形で書き、`peers[].pct`・`r20` で **すでに動いているか**、
  `next` で **いつ決算で確かめられるか**、`last` で **最近の決算と矛盾しないか**（同業が下向きなら両論）を添える
- **数字は材料にあるものだけ**（`flash`・`reason`・`overview`・`outlook`・`peers` の数字。計算し直さない）。会社の説明にない理由を作らない。
  時事・ウエルスアドバイザーの見出し（`press`）は「〇〇（媒体名）によると」と出所をつける
- **買う候補・注文・目標株価は書かない。** 「買い」「狙い目」「上がる」と書かず、「連想が向かいやすい」「次の決算で確かめたい」「すでに織り込みが進んでいる可能性」の言い方にとどめる
  （買う候補は `thermo.swing` の2つのルールだけ。類似銘柄は連想の材料）
- 寄り前は前営業日の引け後の決算が中心（`move` は開示の日の値動きで、反応はこれから）。大引は 16:45 までの開示しか読めていない
  （夜の開示は翌朝の寄り前で読む）。前場は朝の開示だけ
- `earnings` が無い・`items` が空の日は `ai_earnings` を書かない（null のまま）

### 書き込み方

キーの一部だけを差し替え、他には触れない:

```python
import json
path = "docs/data/latest.json"
with open(path, encoding="utf-8") as f:
    data = json.load(f)
data["slots"]["{SLOT}"]["data"]["ai_commentary"] = { ... }
data["slots"]["{SLOT}"]["data"]["ai_macro"] = { ... }
data["slots"]["{SLOT}"]["data"]["ai_earnings"] = { ... }   # earnings があるときだけ
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
```

`ledger.json` / `themes.json` も同様に読み込んで該当キーだけ更新し、
`ensure_ascii=False, indent=1` で書く。

## STEP 5: コミットして push する

```bash
python tools/stamp_assets.py
git add docs/data/latest.json docs/data/ledger.json docs/data/themes.json docs/index.html 2>/dev/null
git commit -m "AI分析: {SLOT} ($(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M'))"
for i in 1 2 3 4; do
  git pull --rebase origin main && git push origin HEAD:main && break
  echo "push に失敗。$((2 ** i)) 秒待って再試行します。"
  sleep $((2 ** i))
done
# 4回とも拒否されたとき（前回の push が自動マージされず、このセッションのブランチが main と食い違っている）は
# このセッションのブランチ（claude/routine-{SLOT}）だけを今の HEAD で上書きしてよい。
# push が main に直接届くこともあるので、宛先は必ずブランチ名で書く。main には絶対に force push しない
git push --force origin "HEAD:refs/heads/claude/routine-{SLOT}" || echo "force push も拒否。報告して終わる"
```

衝突したら: `docs/data/latest.json` の衝突で相手側が同じスロットの `ai_commentary`・`ai_macro`・`ai_earnings` 以外を
変えているだけなら、こちらの `ai_commentary`・`ai_macro`・`ai_earnings` を活かして解決する。それ以外の込み入った衝突は
諦めて「衝突のため見送り」と報告して STEP 6 へ（次回の定時実行で再度書き込まれる）。

## STEP 6: 通知する

`PushNotification` ツールで 1 通だけ送る。

- タイトル: `{SLOT} の日本語名`（寄り前 / 前場 / 大引）＋ 日付
- 本文: 4a の `headline`。大引で台帳に今日の新規候補があれば銘柄名を 3 つまで添える。
  `thermo.zone` が「過熱」「総悲観」のとき、`thermo.consensus` があるとき、または前回の通知から帯が変わったときは、
  本文の先頭に `温度{temp}・{zone}` を付ける（例: 「温度84・過熱｜…」）。大引と寄り前は、`thermo.swing.orders` があれば本文の末尾に
  「注文: {銘柄名 指値}」を3つまで（例: 「注文: 中部電 2,792・関西電 2,713・吉野家HD 3,545」。指値は `limit` をそのまま）、
  無ければ「注文なし」を1行添える（通知だけ見て、寄りの前に次の営業日の注文を置けるように）。`thermo.swing.mid.orders` があれば
  「中期: {銘柄名 指値}」を2つまで別の1行で添える（無ければ書かない）。
  ただし大引で `thermo.swing.asof` が今日より前なら（CNBC の日足に今日の分がまだ無く、注文が前の営業日の引けのまま）、
  注文は載せず「注文は日足がそろい次第（夜の更新か明朝の寄り前）」と1行添える。寄り前はそのまま載せてよい
  （寄り前の収集は、日足が遅れていれば取り直してから注文を出す）。`watch_guard` があれば「ウォッチ: {銘柄名}に注意書き」を1行添える。
  寄り前と大引は、`ai_earnings` を書いたら「決算: {ai_earnings.headline}」を1行添える。
  寄り前は STEP 7 の点検結果に欠けがあれば 1 行添える。
- URL: `https://tonpe3141-ctrl.github.io/kabutan-digest/`

`PushNotification` が使えない環境なら、報告文に同じ内容を書いて終わる。

## STEP 7: 前日の点検（寄り前のみ）

`docs/data/history/` の直近の営業日ファイル（今日より前で最新）を読み、
`preopen` / `zenba` / `taibike` の 3 つが揃っているかを見る。欠けていれば
STEP 6 の通知に「昨日の {欠けた区分} が未取得」と添える。修復は試みない。

---

## STEP W: 週報（`{SLOT}` = `weekly`）

1. `docs/data/history/` から直近 5 営業日分を読む（各日の `taibike.indices`, `sectors`,
   `value_rows`, `after_hours`）。`docs/data/ledger.json` の `entries[]` と `stats` も読む。
   `docs/data/latest.json` の当日データも参考にする。
2. `docs/data/weekly.json` に書く:

```json
{
  "week_end": "YYYY-MM-DD",
  "headline": "今週を一言で（20字前後）",
  "sections": [
    {"title": "指数と物色", "body": "5営業日の日経・TOPIX の動きと、強かった業種・テーマ、弱かったもの"},
    {"title": "マクロの1週間", "body": "latest.json の macro_view.rows の d5（金利は bp、ほかは %）で今週の米・日の金利、ドル円、原油・金・銅の動きを並べ、各日の history の taibike.macro_headline で流れの変化を追う。FRB・日銀・財務省の発表と要人発言、今週の主な経済指標の結果（予想との差）を、報道で裏付けて書く"},
    {"title": "候補の成績", "body": "台帳: 今週フラグした候補の d5 中央値、シグナル別の当たり外れ（stats から）"},
    {"title": "想定と実際", "body": "寄り前想定（implied_open）と実際の寄りの当たり具合"},
    {"title": "注文の成績と温度", "body": "swing_track.json の今週結果が出た注文（勝率・平均・損切りで終わった数）を thermo.json の swing.verify（検証の勝率）と比べる。口座としての実績（swing.paper.account の資産の倍率・最大の目減り）を検証の口座（swing.verify.account）と並べる。今週の温度の推移（history の thermo.temp）と、track[]（高値掴み注意・出尽くしの5日後）"},
    {"title": "決算の風向き", "body": "earnings.json の log（今週の分。業種・テーマ・向き・見出し）と、latest.json の earnings.wind から、上向き・下向きが多かった業種と、各日の ai_earnings の winds に共通する需要・コストの話。読んだ開示の数であって全開示の集計ではないと断る"},
    {"title": "来週の注目", "body": "決算・イベント・持ち越し材料。earnings.json の schedule（決算発表予定）のうち、今週の決算と同じ業種・類似銘柄に挙がった会社"}
  ],
  "method": "Claude による週次総括",
  "generated_at": "ISO8601 (JST)"
}
```

3. STEP 5 と同じ手順でコミット・push（`git add docs/data/weekly.json`）。
4. STEP 6 と同じく通知（タイトル「週報」）。

---

## 最後に

実行した内容を短く報告する（対象、見立てとマクロと決算の見出し、決算を読んだ社数、参照した記事数と媒体の数、台帳に書いた件数、
辞書に足した銘柄数、コミットできたか、通知を送ったか）。この作業に承認は不要。
