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
- Routine の push は main に直接入らず、このセッション固有の `claude/routine-*` ブランチに置き換えられる。
  `docs/data/latest.json` など決められたファイルだけを変えたコミットは
  `.github/workflows/merge-ai-branch.yml` が自動で main にマージし、ブランチを消す
  （次の push でまた作られる。それで正しい）。

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

push 先は main と書いても `claude/*` ブランチに置き換えられる（仕様）。
それで正しい。`docs/data/trigger/*.txt` の push で **マーケットダッシュボード更新** の
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
  `origin/main` の `slots.{SLOT}.updated_at` が今日（JST）の日付なら、その既存データで
  STEP 3 以降を続ける。今日のデータが無ければ STEP 6 の通知だけを
  「{SLOT} の収集が届きませんでした（Actions を確認）」という内容で送って終了する。

## STEP 3: main に追随してからデータを読む

```bash
git pull --rebase origin main
```

（合図のコミットが main に取り込まれていれば自動で消え、まだなら main の上に載る。どちらでもよい。）

読むもの（すべて `docs/data/latest.json` の `slots.{SLOT}.data`）:

- **寄り前**: `us`（米国指数）, `macro`（為替・金利・商品）, `sectors_us`, `implied_open`, `risk`,
  `sector_outlook`, `carryover.after_hours_kessan`（前営業日引け後の開示）, `news`, `kabutan`
- **前場・大引**: `indices`, `divergence`, `sectors_jp`, `breadth`, `constituents`,
  `tables.value/gainer/loser/ytd_high/vol_surge`, `tables.kessan_intraday/kessan_after`,
  `disclosure_summary`, `ranking_delta`, `streaks`, `theme_flow`（テーマ別の資金の向き）,
  `news`, `kabutan`
- **`kabutan`**: `headlines[]`（株探の見出し。`{title, published}`。本文なし）と
  `articles[]`（Yahoo!ファイナンス配信の株探記事。`{headline, timestamp, body, url}`）。
  見出しは「話題株ピックアップ【夕刊】（1）：Ａ、Ｂ、Ｃ」のように、それ自体が要約になっている。
- **`news[]`**: Yahoo!ファイナンス マーケットAIトピックス。`{headline, category, timestamp, body, url, source}`。
- **`thermo`**（全スロット）: 相場温度計の要約。`stance`（今日のスタンス: `label` 攻め／選んで小さく／守り、
  `risk` は株数に掛ける倍率、`reasons[]` は地合い・効いている型・温度帯の過去の成績の ＋1／±0／－1 と根拠の文）, `temp`（0〜100）, `zone`（総悲観／悲観／中立／楽観／過熱）,
  `consensus`（全面追い風／全面向かい風）, `factors[]`（8軸の `score` −2〜+2 と `change` 改善／悪化、`text` に根拠の数字）,
  `turning`（底打ち／天井打ちの兆し）, `sector_picks`（押し目・下げ止まりの業種）, `sector_hot`, `dip` / `oversold` / `hot` /
  `bad_out` / `good_out` / `deep`（深押し: 上昇トレンド中の急落）/ `turn`（上向き転換: 25日線が上を向いた初動）（銘柄）, `themes`（テーマの論調×値動き）, `watch_guard`（ウォッチリストの注意書き）,
  `setups[]`（型ごとの直近成績: `verdict` 効いている／効いていない／はっきりしない、`x5`・`x20` は全銘柄比、`avg_r` は計画どおりに売買した場合の平均R）,
  `board[]`（作戦ボードの上位: 型・買う目安 `entry`・損切り `stop`・利確の目安 `target`・`rr`）。
  詳細は `docs/data/thermo.json`（業種の全表・バックテスト `backtest.zones[]`・判定の成績 `track[]`・型の成績 `setups.setups[]`）
- 参考: `commentary`（ルールベースの見立て。なぞるだけでは意味がない）

あわせて、あれば読む:

- `docs/data/ledger.json` … 発掘台帳。`entries[]` のうち `first_seen` が今日で `notes` が空のもの
- `docs/data/themes.json` … 銘柄→テーマ辞書。`slots.{SLOT}.data.theme_flow.unknown_codes[]` が
  辞書に無い銘柄（`{code, name}`）

## STEP 4: 書く

### 4a. 相場の見立て（必須）

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

セクションは 2〜4 個。例: 「相場の総括」「ニュースから読む背景」「テーマと資金の向き」「注目点」。
**`thermo` があれば、そのうち1つを必ず「逆張りの視点」にする。** 書くこと:
- 温度と帯、どの軸が追い風／向かい風か（`factors` の数字をそのまま使う。温度やスコアを自分で付け直さない）
- ニュースの論調と値動きのずれ（例: 好材料の見出しが続くのに `good_out` に出ている＝好材料出尽くし、
  悪材料の見出しが続くのに `bad_out` や `themes` で「下げ止まり」＝悪材料出尽くしの兆し）。記事で裏付けが取れた範囲で
- `sector_picks` / `dip` / `hot` のうち記事で背景が分かるもの。「買い」「売り」と断定せず、
  「押し目を待つ」「追いかけは控えめに」「分割で」の言い方にとどめる
- `backtest` で温度帯の過去の成績が逆張りの読みと食い違っているときは、そのことも書く（都合の良い読みだけを書かない）
- `stance` があれば、その `label` を見出しか冒頭で1回だけ伝える（例:「今日は『選んで小さく』」）。
  スタンスを自分で付け直さない。`reasons` の数字はそのまま使う
- `setups` で「効いていない」型があれば、その型の候補（例: `oversold`）を勧める書き方をしない。効いている型が無い日は
  「見送る・小さく」も選択肢として書く。`board` の価格・R倍は数字をそのまま使い、自分で計算し直さない

複数の事実を組み合わせた解釈を書く（例: 日経は上昇したが TOPIX との乖離と上昇銘柄数から
値がさ株主導であり、株探の夕刊が挙げる AI 関連への資金集中と整合する）。
`sources` には実際に使った記事だけを入れる（`kabutan.articles` と `news` の URL）。

### 4b. 台帳の理由づけ（大引のみ。台帳があれば）

`docs/data/ledger.json` の `entries[]` で `first_seen` が今日かつ `notes` が空の各行に、
`notes` を 1〜2 文で書く。**候補の追加・削除・シグナルの変更はしない**（機械が決める）。
書くのは「なぜ数字に引っかかったか」に、記事で裏付けが取れれば「何が材料か」を添えるだけ。
裏付けが無ければ「記事での裏付けなし」と書く。裏付けた記事の URL を `note_url` に入れる。

### 4c. テーマ辞書の育成（前場・大引。未知銘柄があれば）

`theme_flow.unknown_codes[]` の各銘柄について、記事・開示・自分の知識から
テーマが分かるものだけ `docs/data/themes.json` の `stocks` に追記する:

```json
"5803": {"name": "フジクラ", "themes": ["電線", "データセンター"]}
```

- テーマ名は辞書の `themes` に既にあるものを優先して使う。新しいテーマ名は本当に必要なときだけ。
- 1銘柄 1〜3 テーマ。分からない銘柄は書かない（空で埋めない）。
- 既存の行は変更しない。

### 書き込み方

キーの一部だけを差し替え、他には触れない:

```python
import json
path = "docs/data/latest.json"
with open(path, encoding="utf-8") as f:
    data = json.load(f)
data["slots"]["{SLOT}"]["data"]["ai_commentary"] = { ... }
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
# ブランチを今の HEAD で上書きしてよい。main には触れない
git push --force origin HEAD:main || echo "force push も拒否。報告して終わる"
```

衝突したら: `docs/data/latest.json` の衝突で相手側が同じスロットの `ai_commentary` 以外を
変えているだけなら、こちらの `ai_commentary` を活かして解決する。それ以外の込み入った衝突は
諦めて「衝突のため見送り」と報告して STEP 6 へ（次回の定時実行で再度書き込まれる）。

## STEP 6: 通知する

`PushNotification` ツールで 1 通だけ送る。

- タイトル: `{SLOT} の日本語名`（寄り前 / 前場 / 大引）＋ 日付
- 本文: 4a の `headline`。大引で台帳に今日の新規候補があれば銘柄名を 3 つまで添える。
  `thermo.zone` が「過熱」「総悲観」のとき、`thermo.consensus` があるとき、または前回の通知から帯が変わったときは、
  本文の先頭に `温度{temp}・{zone}` を付ける（例: 「温度84・過熱｜…」）。`thermo.stance` があれば本文の末尾に
  「スタンス: {label}」を1行添える（通知だけ見て今日の構えが分かるように）。`watch_guard` があれば「ウォッチ: {銘柄名}に注意書き」を1行添える。
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
    {"title": "候補の成績", "body": "台帳: 今週フラグした候補の d5 中央値、シグナル別の当たり外れ（stats から）"},
    {"title": "想定と実際", "body": "寄り前想定（implied_open）と実際の寄りの当たり具合"},
    {"title": "温度計と逆張りの成績", "body": "今週の温度の推移（history の thermo.temp）と、thermo.json の track[]（押し目・売られすぎ・出尽くし・高値掴み注意の5日後）を台帳の順張りシグナルと比べる"},
    {"title": "来週の注目", "body": "決算・イベント・持ち越し材料"}
  ],
  "method": "Claude による週次総括",
  "generated_at": "ISO8601 (JST)"
}
```

3. STEP 5 と同じ手順でコミット・push（`git add docs/data/weekly.json`）。
4. STEP 6 と同じく通知（タイトル「週報」）。

---

## 最後に

実行した内容を短く報告する（対象、見出し、参照した記事数、台帳に書いた件数、
辞書に足した銘柄数、コミットできたか、通知を送ったか）。この作業に承認は不要。
