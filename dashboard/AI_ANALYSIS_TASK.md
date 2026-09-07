# AI分析タスク（Claude Routine専用の作業手順書）

このファイルは、マーケットダッシュボードの数値データと相場振り返り記事から
実際のLLM分析を書いて `docs/data/latest.json` に書き戻す、**無人・定時実行**の
作業手順書です。人間はその場にいません。判断に迷っても止まらず、
このファイルに書かれた既定動作に従って最後まで実行してください。

呼び出し元（Routine）のプロンプトが対象スロット（`preopen` / `zenba` / `taibike`）を
指定します。以下では `{SLOT}` と書きます。

## なぜこの仕組みがあるか

数値データ（指数・ランキング・開示）と相場振り返り記事（ニュース本文）は
GitHub Actions がクラウドから収集して `docs/data/latest.json` に書き込み済みです
（この作業手順書を実行する時点で、それは既に終わっている前提）。

このタスクの仕事は、**その数値と記事を読んで、実際のLLMとしての分析・示唆を書くこと**
だけです。数値の再取得やスクレイピングはしません（する必要もできません。
このセッションのネットワークは api.anthropic.com / GitHub / パッケージレジストリ以外
遮断されているため、一般サイトへは出られません）。

以前はルールベース（`dashboard/commentary.py`、if文の組み合わせ）で機械的に
文章化していましたが、それでは「なぜその値動きになったか」というニュース由来の
文脈を汲めませんでした。ここでは実際に読んで、書いてください。

## STEP 0: リポジトリを最新にする

```bash
if [ -d ~/kabutan-digest/.git ]; then
  cd ~/kabutan-digest && git fetch origin main && git reset --hard origin/main
else
  git clone https://github.com/tonpe3141-ctrl/kabutan-digest.git ~/kabutan-digest
  cd ~/kabutan-digest
fi
```

## STEP 1: 対象データを読む

**「今日の日付」を自分で計算して比較しないこと。** このコンテナの既定タイムゾーンは
UTC で、JST とは日付が最大9時間ずれる（例: UTC 22:36 は JST では翌日 07:36）。
`date` コマンドをそのまま使うと日付判定を誤り、届いているデータを
「まだ届いていない」と誤判定してスキップしてしまう。

代わりに、次の Python を実行して**その出力の指示にそのまま従う**こと:

```bash
python3 <<'PYEOF'
import json
from datetime import datetime, timezone

with open("docs/data/latest.json", encoding="utf-8") as f:
    data = json.load(f)

slot = (data.get("slots") or {}).get("{SLOT}")
if not slot:
    print("SKIP: slots.{SLOT} が存在しません")
else:
    updated_at = datetime.fromisoformat(slot["updated_at"])
    age_h = (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_h > 20:
        print(f"SKIP: 最終更新から {age_h:.1f} 時間経過しており古すぎます（updated_at={slot['updated_at']}）")
    else:
        print(f"OK: 最終更新から {age_h:.1f} 時間（updated_at={slot['updated_at']}）。続行してください")
PYEOF
```

出力が `SKIP:` で始まった場合 → **データがまだ届いていない、または古すぎます。**
何も書き換えず、その理由をそのまま報告して終了してください。
次の定時実行を待てば十分で、無理に待機・再試行する必要はありません。

出力が `OK:` で始まった場合のみ、続けて `slots.{SLOT}.data` の中身、
特に以下を把握してください:

- 寄り前（preopen）: `us`（米国指数）, `macro`（為替・金利・商品）,
  `sectors_us`（米セクターETF）, `implied_open`（想定オープン）, `risk`,
  `sector_outlook`, `carryover.after_hours_kessan`（前営業日引け後の開示）,
  `news`（相場振り返り記事）
- 前場・大引（zenba/taibike）: `indices`（日経平均・TOPIX・JPX日経400）,
  `divergence`（日経とTOPIXのズレ）, `sectors_jp`（業種別騰落）,
  `breadth`（225銘柄中の上昇/下落数）, `constituents`（225銘柄の値動き。
  ヒートマップの元データ）, `tables.value/gainer/loser`（ランキング）,
  `tables.kessan_intraday/kessan_after`（適時開示）, `disclosure_summary`,
  `ranking_delta`, `streaks`, `news`（相場振り返り記事）
- 参考: `commentary`（従来のルールベース分析。書き方の参考にしてよいが、
  内容をなぞるだけでは意味がない）

`news` 配列の各要素は `{headline, category, timestamp, body, url, source}`。
`body` には見出し直後の「影響銘柄」タグと本文が混在したテキストが入っています
（Yahoo!ファイナンス マーケットAIトピックスの記事）。

## STEP 2: 分析を書く

**絶対に守ること: `data` に無い数値を書かない。** 前日比・水準・順位などの
数値は必ず `slots.{SLOT}.data` から取ってください。ニュース記事は
「なぜそう動いたか」「市場の受け止め方」「今後の注目点」を補うために使い、
記事中の数値と手元のデータが食い違う場合はデータ側を優先してください。

書くべき内容（アナリストとして）:

- 単なる数値の言い換えではなく、**複数の事実を組み合わせた解釈**を書く。
  例: 「日経平均は上昇したが、TOPIXとの乖離と225銘柄の上昇/下落数から見て
  値がさ株主導であり、ニュース記事が指すAI関連テーマへの資金集中と整合する」
- ニュース記事が触れている銘柄・テーマと、手元のランキング・業種別データを
  突き合わせて、裏付けが取れるか確認したうえで書く（記事の言い分を鵜呑みにしない）。
- 意味のある含意（示唆）を必ず1つ以上書く。ただし**投資助言にはしない**
  （「買い」「売り」と断定しない。「〜という見方ができる」「〜に注意が必要」
  という書き方に留める）。
- 分からないこと・データが薄いことは「材料不足」「方向感に乏しい」と正直に書く。
  無理に物語を作らない。

## STEP 3: 出力を書き込む

`docs/data/latest.json` の `slots.{SLOT}.data.ai_commentary` に、次の形の
オブジェクトを設定してください（既存の `commentary` と同じ形）:

```json
{
  "headline": "一言見出し（15〜25字程度）",
  "sections": [
    {"title": "セクション見出し", "body": "本文（200〜400字程度、である調ではなく、ですます調でなくてよい。読みやすい文章で）"}
  ],
  "method": "Claude Sonnet 5 による分析",
  "generated_at": "実行時のISO8601日時（JST, +09:00）",
  "sources": [{"title": "参照した記事の見出し", "url": "記事URL"}]
}
```

セクションは2〜4個程度。見出しは自由に付けてよいが、例:
「相場の総括」「ニュースから読む背景」「注目点」など。
`sources` には STEP 2 で実際に参照した `news` 記事のうち、分析に使ったものだけを
入れる（使わなかった記事は入れない。全部入れる必要はない）。

書き込みは Python で行うこと（キーの一部だけを差し替え、他のキーには触れない）:

```python
import json

path = "docs/data/latest.json"
with open(path, encoding="utf-8") as f:
    data = json.load(f)

data["slots"]["{SLOT}"]["data"]["ai_commentary"] = { ... }  # STEP 3 の内容

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
```

**`ai_commentary` 以外のキーは一切変更しないこと。** 数値データ・ランキング・
ニュース原文などは触らない。

## STEP 4: 静的ファイルのハッシュを打ち直す

```bash
python tools/stamp_assets.py
```

## STEP 5: コミットして push する

```bash
git add docs/data/latest.json docs/index.html
git -c user.name="claude-routine[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -m "AI分析を追加: {SLOT} ($(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M'))"

for i in 1 2 3 4; do
  git pull --rebase origin main && git push origin HEAD:main && break
  echo "push に失敗。$((2 ** i)) 秒待って再試行します。"
  sleep $((2 ** i))
done
```

`git pull --rebase` で衝突した場合（GitHub Actions の定時収集と時間が重なった等）:
- 衝突が `docs/data/latest.json` のみで、かつ相手側の変更が同じスロットの
  `ai_commentary` 以外のフィールド（数値データの更新など）であれば、
  こちらの `ai_commentary` の変更を活かしつつ相手の変更を取り込んで解決する。
- それ以外の込み入った衝突は、無理に解決しようとせず、変更を諦めて
  「衝突のため今回は見送りました」と報告して終了してよい（次回の定時実行で
  再度書き込みが試みられる）。

## 最後に

実行した内容を短く報告してください（対象スロット、見出し、参照した記事数、
コミットできたか）。この作業に承認は不要です。完了まで自律的に進めてください。
