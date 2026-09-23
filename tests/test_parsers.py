"""パーサとスロット判定の回帰テスト（ネットワーク不要）。

  python tests/test_parsers.py

HTML は Actions 上で実測した構造をそのまま写したもの。
取得元のページ構造が変わったときは、まず tools/probe.py で実物を見てから
ここの期待値を更新する。
"""
import os
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.sources.cnbc import _num, _quote            # noqa: E402
from dashboard.sources.tdnet import (  # noqa: E402
    _classify, _is_fund, _normalize_code,
)
from dashboard.sources.news import _parse_article          # noqa: E402
from dashboard.sources.yahoojp import _parse_row           # noqa: E402

failures: list[str] = []


def check(label, got, want):
    if got == want:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}\n       得: {got}\n       期: {want}")
        failures.append(label)


# ==================== Yahoo!ファイナンス ランキング ====================
RANKING_HTML = """<table>
<tr><th>順位</th><th>名称・コード・市場</th><th>取引値</th><th>前日比</th><th>出来高</th></tr>
<tr><td>1</td>
 <td><a>(株)ランド</a><span>8918</span><span>東証STD</span><span>掲示板</span></td>
 <td><span>11</span><span>09/04</span></td>
 <td><span>0</span><span>0.00</span><span>%</span></td>
 <td><span>371,104,600</span><span>株</span></td></tr>
<tr><td>2</td>
 <td><a>ブレインズテクノロジー(株)</a><span>4075</span><span>東証GRT</span><span>掲示板</span></td>
 <td><span>1,394</span><span>09/04</span></td>
 <td><span>-386</span><span>-21.69</span><span>%</span></td>
 <td><span>977,400</span><span>株</span></td></tr>
<tr><td>3</td>
 <td><a>(株)エブリー</a><span>607A</span><span>東証GRT</span><span>掲示板</span></td>
 <td><span>317</span><span>09/04</span></td>
 <td><span>+80</span><span>+33.76</span><span>%</span></td>
 <td><span>14,561,700</span><span>株</span></td></tr>
</table>"""


def test_ranking():
    print("Yahoo!ファイナンス ランキング")
    soup = BeautifulSoup(RANKING_HTML, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 3:
            continue
        r = _parse_row(cells, len(rows) + 1)
        if r:
            rows.append(r)

    check("ヘッダ行を除いて3行", len(rows), 3)
    # 順位を株価と、社名の「(株)」を出来高と取り違えないこと
    check("社名に(株)を含む行", (rows[0]["code"], rows[0]["name"], rows[0]["price"],
                          rows[0]["metric"]),
          ("8918", "(株)ランド", 11.0, 371104600.0))
    check("マイナスの符号", (rows[1]["change"], rows[1]["change_pct"]), (-386.0, -21.69))
    check("英字を含む新形式コード", (rows[2]["code"], rows[2]["change_pct"]), ("607A", 33.76))
    check("市場区分", [r["market"] for r in rows], ["東証STD", "東証GRT", "東証GRT"])


# ==================== CNBC ====================
def test_cnbc():
    print("\nCNBC クォート")
    check("カンマ付き", _num("7,718.60"), 7718.60)
    check("パーセント", _num("-0.38%"), -0.38)
    check("UNCH は欠損", _num("UNCH"), None)

    # 市場が閉じていると change が UNCH になるので前日終値から補う
    closed = _quote({"symbol": ".N225", "last": "65,020.94", "change": "UNCH",
                     "change_pct": "UNCH", "previous_day_closing": "64,800.00"})
    check("UNCH を前日終値から補完", (round(closed["change"], 2), round(closed["change_pct"], 3)),
          (220.94, 0.341))

    # CNBC の change_pct は基準がずれることがあるので必ず再計算する
    odd = _quote({"symbol": "US2Y", "last": "4.374%", "change": "+0.04",
                  "change_pct": "-0.0781%", "previous_day_closing": "4.334%"})
    check("矛盾した change_pct を再計算", round(odd["change_pct"], 3), 0.923)


# ==================== TDnet ====================
def test_tdnet():
    print("\nTDnet 適時開示")
    check("5桁コードを4桁に", _normalize_code("72030"), "7203")
    check("英字入りコード", _normalize_code("607A0"), "607A")
    check("業績予想の修正",
          _classify("2027年１月期第２四半期および通期連結業績予想の修正に関するお知らせ"),
          "業績予想の修正")
    check("決算短信", _classify("2026年10月期 第3四半期決算短信〔日本基準〕（連結）"), "決算短信")
    check("自己株式取得", _classify("自己株式の取得に関するお知らせ"), "自己株式取得")
    check("対象外は None", _classify("代表取締役の異動に関するお知らせ"), None)

    # ETF は名称にも表題にも ETF の語が無いことがある（Global X など）ので
    # ブランド名の先頭一致と、期間を日付範囲で書く決算短信の形式でも判定する
    period = "2026年7月期（2026年1月25日～2026年7月24日）決算短信"
    check("Global X（名称に ETF の語なし）", _is_fund("ＧＸウラニウム", period), True)
    check("期間が日付範囲の決算短信", _is_fund("なんとかファンド", period), True)
    check("iFree の分配金", _is_fund("ｉＦＦリート", "iFreeETFの収益分配金分配のお知らせ"), True)
    check("NEXT FUNDS", _is_fund("ＮＦ・日経225", "決算短信"), True)
    check("事業会社の決算短信は残す",
          _is_fund("萩原工業", "2026年10月期 第3四半期決算短信〔日本基準〕（連結）"), False)
    check("事業会社の業績修正は残す",
          _is_fund("旭コンクリ", "2027年3月期第２四半期（中間期）及び通期業績予想の修正に関するお知らせ"),
          False)
    check("紛らわしい社名は落とさない",
          _is_fund("シンプレクスＨＤ", "2026年3月期 第2四半期決算短信〔日本基準〕（連結）"), False)


# ==================== スロットの補正 ====================
def test_slot():
    from datetime import datetime, timedelta, timezone

    from dashboard.build import adjust_slot, resolve_slot

    JST = timezone(timedelta(hours=9))
    at = lambda h, m: datetime(2026, 9, 7, h, m, tzinfo=JST)   # noqa: E731

    print("\nスロットの補正")
    check("定刻の前場はそのまま", adjust_slot("zenba", at(12, 5)), "zenba")
    check("12:40 の追い取りもそのまま", adjust_slot("zenba", at(12, 40)), "zenba")
    check("14:59 まではまだ前場", adjust_slot("zenba", at(14, 59)), "zenba")
    # GitHub の cron が数時間遅れた場合。大引けの値を前場タブに入れない
    check("15:00 以降の前場は大引に倒す", adjust_slot("zenba", at(17, 36)), "taibike")
    check("寄り前は遅れても寄り前", adjust_slot("preopen", at(10, 30)), "preopen")
    check("大引はそのまま", adjust_slot("taibike", at(18, 5)), "taibike")

    check("auto 07:05", resolve_slot(at(7, 5)), "preopen")
    check("auto 12:05", resolve_slot(at(12, 5)), "zenba")
    check("auto 17:35", resolve_slot(at(17, 35)), "taibike")


# ==================== 相場振り返り記事（Yahoo AIマーケット） ====================
ARTICLE_HTML = """<html><body>
<article>
<div>マーケットAIトピックス</div>
<div>市場心理</div>
<h1>日経平均+2.21%でAI関連主導の戻り</h1>
<span>9/7 11:55更新</span>
<div>影響銘柄</div>
<span>アドテスト</span><span>レーザテク</span><span>東エレク</span>
<p>東京市場は指数高とAIテーマが相場を牽引。</p>
<p>日経平均は+2.21%（66,460.11円）と大幅高で、TOPIX（+0.62%）を上回る伸び。</p>
</article>
</body></html>"""


def test_news():
    print("\nYahoo AIマーケット記事")
    art = _parse_article(ARTICLE_HTML, "https://finance.yahoo.co.jp/news/ai-market/detail/2956")
    check("先頭のボイラープレートを除去", art is not None and "マーケットAIトピックス" not in art["body"],
          True)
    check("category", art["category"], "市場心理")
    check("headline", art["headline"], "日経平均+2.21%でAI関連主導の戻り")
    check("timestamp", art["timestamp"], "9/7 11:55更新")
    check("本文に影響銘柄と数値が両方入る",
          ("影響銘柄" in art["body"] and "66,460.11円" in art["body"]), True)


# ==================== 株探ニュース（配信先経由） ====================
from datetime import date, datetime, timedelta, timezone  # noqa: E402

from dashboard.sources.kabutan_news import (  # noqa: E402
    parse_sector_ranking, parse_yahoo_article, select_headlines,
)
from dashboard import ledger as ledger_mod, themes as themes_mod, trend  # noqa: E402

# Actions 上で実測した Yahoo!ファイナンス記事ページの <article> 構造を写したもの
KABUTAN_ARTICLE_HTML = """<html><body><article>
<h1>本日の【増資・売り出し】銘柄　(17日大引け後 発表分)</h1>
<time>18:40</time><span>配信</span>
<div>現在値</div>
<dl><dt>ＮＡＮＯＨＤ</dt><dd>99</dd><dd>+3</dd></dl>
<p>Cyntecを割当先とする313万9700株の第三者割当増資を実施する。発行価格は99円。</p>
<p>[2026年9月17日]</p>
<p>株探ニュース（minkabu PRESS）</p>
<p>株探ニュース</p>
<div>関連ニュース</div><a>ＮＡＮＯＨＤの【トレンドシグナル】はこちらから</a>
<p>最終更新: 9/17(木) 18:40</p>
</article></body></html>"""

SECTOR_BODY = (
    "・15時35分現在の東証プライム市場における業種別の騰落率ランキング\n"
    "●東証33業種　　　　　　　　値上がり：　28 業種　　値下がり：　 5 業種\n"
    "東証プライム：1548銘柄　　値上がり：1285 銘柄　　値下がり： 233 銘柄　　変わらず他：　30 銘柄\n"
    "東証33業種　　　 前日比率　 【株価】上昇率／下落率　上位3銘柄\n"
    "海運業　　　　　 　 +3.09 　商船三井<9104>、川崎汽<9107>、郵船<9101>"
    "その他製品　　　 　 +2.88 　任天堂<7974>、タカラトミー<7867>、ミズノ<8022>"
    "医薬品　　　　　 　 +2.61 　ネクセラ<4565>、住友ファーマ<4506>、東和薬品<4553>"
    "鉄鋼　　　　　　 　 +2.26 　菱製鋼<5632>、合同鉄<5410>、日本製鉄<5401>"
    "保険業　　　　　 　 +2.12 　東京海上<8766>、アニコムＨＤ<8715>、ＳＯＭＰＯ<8630>"
    "銀行業　　　　　 　 +1.90 　ちゅうぎん<5832>、めぶきＦＧ<7167>、ひろぎん<7337>"
    "電気・ガス業　　 　 +1.75 　北海電<9509>、東電ＨＤ<9501>、九州電<9508>"
    "建設業　　　　　 　 +1.60 　誠建設<8995>、大末建<1814>、東急建<1720>"
    "小売業　　　　　 　 +1.55 　ＧＥＮＤＡ<9166>、ヨネックス<7906>、しまむら<8227>"
    "食料品　　　　　 　 +1.40 　神戸物産<3038>、明治ＨＤ<2269>、味の素<2802>"
    "陸運業　　　　　 　 +1.30 　東急<9005>、京成<9009>、ＪＲ東日本<9020>"
    "不動産業　　　　 　 +1.25 　霞ヶ関Ｃ<3498>、レオパレス<8848>、三井不<8801>"
    "化学　　　　　　 　 +1.10 　三菱ケミＧ<4188>、レゾナック<4004>、信越化<4063>"
    "機械　　　　　　 　 +1.00 　クボテック<7709>、芝浦機<6104>、ダイキン<6367>"
    "情報・通信業　　 　 +0.95 　ＮＴＴ<9432>、ＫＤＤＩ<9433>、ＳＢ<9434>"
    "サービス業　　　 　 +0.90 　ベイカレント<6532>、リクルート<6098>、ＳＭＳ<2175>"
    "卸売業　　　　　 　 +0.85 　三菱商<8058>、三井物<8031>、伊藤忠<8001>"
    "輸送用機器　　　 　 +0.60 　トヨタ<7203>、ホンダ<7267>、三菱自<7211>"
    "精密機器　　　　 　 +0.50 　ＨＯＹＡ<7741>、テルモ<4543>、オリンパス<7733>"
    "その他金融業　　 　 +0.40 　オリックス<8591>、三菱ＨＣ<8593>、ＪＰＸ<8697>"
    "証券、商品先物取引業 　 -0.30 　野村<8604>、大和<8601>、ＳＢＩ<8473>"
    "電気機器　　　　 　 -0.55 　太陽誘電<6976>、イビデン<4062>、ＴＤＫ<6762>"
    "非鉄金属　　　　 　 -1.94 　三井金属<5706>、ＪＸ金属<5016>、住友鉱<5713>"
    "鉱業　　　　　　 　 -2.10 　ＩＮＰＥＸ<1605>、石油資源<1662>、三井松島<1518>"
)


def test_kabutan():
    print("\n株探ニュース（配信先経由）")
    art = parse_yahoo_article(KABUTAN_ARTICLE_HTML, "https://finance.yahoo.co.jp/news/detail/x")
    check("見出し", art["headline"], "本日の【増資・売り出し】銘柄　(17日大引け後 発表分)")
    check("時刻", art["timestamp"], "18:40")
    check("本文は株価引用ブロックを飛ばして始まる", art["body"].startswith("Cyntec"), True)
    check("末尾の定型（日付・配信元）を落とす", ("株探ニュース" in art["body"] or "[2026年" in art["body"]), False)

    sec = parse_sector_ranking(SECTOR_BODY)
    check("33業種を全部読める（サンプルは24業種）", sec is not None and len(sec["rows"]), 24)
    check("上昇トップ", sec["rows"][0]["sector"], "海運業")
    check("上昇トップの率", sec["rows"][0]["change_pct"], 3.09)
    check("上位銘柄", sec["rows"][0]["leaders"][0], {"name": "商船三井", "code": "9104"})
    check("下落トップ", sec["rows"][-1]["sector"], "鉱業")
    check("証券の表記ゆれを正規化", any(r["sector"] == "証券・商品先物取引業" for r in sec["rows"]), True)
    check("値上がり/値下がり業種数", (sec.get("up"), sec.get("down")), (28, 5))
    check("プライム全体の上昇/下落", sec.get("prime"), {"total": 1548, "up": 1285, "down": 233})

    jst = timezone(timedelta(hours=9))
    now = datetime(2026, 9, 17, 16, 45, tzinfo=jst)
    heads = [
        {"title": "話題株ピックアップ【夕刊】（1）：ＧＥＮＤＡ、任天堂、三菱重", "published": "2026-09-17T15:43+09:00"},
        {"title": "話題株ピックアップ【昼刊】：ＧＥＮＤＡ、ヨネックス、みずほＦＧ", "published": "2026-09-17T11:37+09:00"},
        {"title": "【↑】日経平均 大引け｜ 続伸、半導体失速もバリュー株が買われる (9月17日)", "published": "2026-09-17T16:33+09:00"},
        {"title": "話題株ピックアップ【夕刊】（1）：古い", "published": "2026-09-10T15:43+09:00"},
        {"title": "関係ない記事", "published": "2026-09-17T12:00+09:00"},
    ]
    picked = select_headlines(heads, "taibike", now=now)
    check("大引: 夕刊が先頭、古いものと無関係は落ちる",
          [h["title"].startswith("話題株ピックアップ【夕刊】") or h["title"].startswith("【↑】日経平均") for h in picked]
          + [len(picked)], [True, True, 2])
    picked = select_headlines(heads, "zenba", now=now)
    check("前場: 昼刊が先頭", picked[0]["title"].startswith("話題株ピックアップ【昼刊】"), True)


# ==================== ランキングの別レイアウト ====================
YTD_HTML = """<table><tr><th>順位</th><th>名称</th><th>取引値</th><th>前営業日までの年初来高値</th><th>高値</th></tr>
<tr><td>1</td><td><a>金下建設(株)</a><span>1897</span><span>東証STD</span><span>掲示板</span></td>
<td><span>3,700</span><span>15:30</span></td><td><span>3,575</span><span>2026/08/07</span></td><td><span>3,700</span></td></tr>
<tr><td>2</td><td><a>(NEXT FUNDS)情報通信</a><span>1626</span><span>東証ETF</span><span>掲示板</span></td>
<td><span>50,200</span><span>15:30</span></td><td><span>49,520</span><span>2026/09/15</span></td><td><span>50,200</span></td></tr>
</table>"""
VOL_HTML = """<table><tr><th>順位</th><th>名称</th><th>取引値</th><th>出来高</th><th>前日出来高</th><th>出来高増加率</th></tr>
<tr><td>1</td><td><a>(株)テスト</a><span>7777</span><span>東証PRM</span><span>掲示板</span></td>
<td><span>1,234</span><span>15:30</span></td><td><span>120,049</span><span>株</span></td><td><span>147</span><span>株</span></td><td><span>816.660</span><span>倍</span></td></tr>
</table>"""


def test_ranking_layouts():
    print("\nランキングの別レイアウト")
    rows = [tr.find_all(["th", "td"]) for tr in BeautifulSoup(YTD_HTML, "html.parser").find_all("tr")]
    r = _parse_row(rows[1], 1, "ytd_high")
    check("年初来高値: 取引値", r["price"], 3700.0)
    check("年初来高値: 前営業日までの高値を metric に", r["metric"], 3575.0)
    check("年初来高値: 上抜け幅を change_pct に", r["change_pct"], 3.5)
    r2 = _parse_row(rows[2], 2, "ytd_high")
    check("ETF は market で識別できる", r2["market"], "東証ETF")
    rows = [tr.find_all(["th", "td"]) for tr in BeautifulSoup(VOL_HTML, "html.parser").find_all("tr")]
    r = _parse_row(rows[1], 1, "vol_surge")
    check("出来高急増: 倍率", r["metric"], 816.66)
    check("出来高急増: 出来高と前日出来高", (r["volume"], r["prev_volume"]), (120049.0, 147.0))


# ==================== テーマ・台帳・時間軸（合成データ） ====================
THEMES = {"themes": ["電線", "半導体"], "stocks": {
    "5803": {"name": "フジクラ", "themes": ["電線"]},
    "5802": {"name": "住友電気工業", "themes": ["電線"]},
    "8035": {"name": "東京エレクトロン", "themes": ["半導体"]},
}}


def _payload():
    return {
        "tables": {
            "value": {"rows": [
                {"rank": 1, "code": "5803", "name": "フジクラ(株)", "price": 8120.0, "change_pct": 4.0},
                {"rank": 2, "code": "5802", "name": "住友電気工業(株)", "price": 3000.0, "change_pct": 2.0},
                {"rank": 3, "code": "8035", "name": "東京エレクトロン(株)", "price": 30000.0, "change_pct": -1.0},
                {"rank": 4, "code": "9999", "name": "(株)未知", "price": 500.0, "change_pct": 9.0},
            ]},
            "gainer": {"rows": [
                {"rank": 1, "code": "9999", "name": "(株)未知", "price": 500.0, "change_pct": 9.0},
                {"rank": 2, "code": "5803", "name": "フジクラ(株)", "price": 8120.0, "change_pct": 4.0},
            ]},
            "kessan_after": {"rows": [
                {"code": "2788", "name": "アップル", "title": "通期業績予想の修正（増配）に関するお知らせ",
                 "time": "15:30", "category": "業績予想の修正"},
            ]},
        },
        "streaks": [{"code": "5803", "name": "フジクラ(株)", "days": 4, "change_pct": 4.0}],
        "ranking_delta": {"new": [{"code": "9999", "name": "(株)未知", "rank": 4, "change_pct": 9.0}], "rank_up": []},
    }


def test_themes_ledger_trend():
    print("\nテーマ・台帳・時間軸")
    flow = themes_mod.theme_flow(_payload()["tables"], THEMES, prev_top=[{"theme": "半導体"}])
    check("テーマ上位の先頭は電線（2銘柄）", (flow["top"][0]["theme"], flow["top"][0]["count"]), ("電線", 2))
    check("電線の平均騰落", flow["top"][0]["avg_pct"], 3.0)
    check("電線は昨日の上位に無かった（初動）", flow["top"][0]["was_top"], False)
    check("履歴が無い日は初動と言わない", themes_mod.theme_flow(_payload()["tables"], THEMES, None)["top"][0]["was_top"], True)
    check("辞書に無い銘柄を unknown_codes に残す", [u["code"] for u in flow["unknown_codes"]], ["9999"])
    streaks = themes_mod.theme_streaks(flow["top"], [[{"theme": "電線"}], [{"theme": "電線"}], [{"theme": "半導体"}]])
    check("電線は3日連続（今日含む）", streaks[0], {"theme": "電線", "days": 3})

    hist = [
        {"date": "2026-09-16", "taibike": {"value_rows": [{"code": "2788", "name": "アップル", "change_pct": 3.0}],
                                          "after_hours": [], "sectors": [{"sector": "電気機器", "avg_pct": 1.0}]}},
        {"date": "2026-09-15", "taibike": {"value_rows": [], "sectors": [{"sector": "電気機器", "avg_pct": -0.5}],
                                          "after_hours": [{"code": "2788", "name": "アップル",
                                                           "title": "業績予想の上方修正", "category": "業績予想の修正"}]}},
    ]
    sig = ledger_mod.detect_signals(_payload(), hist, THEMES, flow)
    by = {c["code"]: c for c in sig}
    check("資金流入の継続 + 上昇率×売買代金 + テーマ初動 が重なる",
          sorted(by["5803"]["signals"]), sorted(["資金流入の継続", "上昇率×売買代金", "テーマ初動"]))
    check("上方修正 + 修正後に資金流入", sorted(by["2788"]["signals"]), sorted(["上方修正・増配", "修正後に資金流入"]))
    check("新規流入 + 上昇率×売買代金", sorted(by["9999"]["signals"]), sorted(["新規の資金流入", "上昇率×売買代金"]))

    led = ledger_mod.update(date(2026, 9, 17), _payload(), hist, THEMES, flow, lambda codes: {}, 64000.0,
                            ledger={"entries": [], "stats": {}})
    codes = {e["code"] for e in led["entries"]}
    check("スコア2以上の候補が台帳に載る", {"5803", "2788", "9999"} <= codes, True)
    e = next(x for x in led["entries"] if x["code"] == "5803")
    check("フラグ時点の株価と日経を記録", (e["price_at_flag"], e["nikkei_at_flag"]), (8120.0, 64000.0))
    # 翌営業日以降の追跡: 5営業日後に d1/d5 が埋まり、20日で閉じる
    hist2 = [{"date": f"2026-09-{d:02d}", "taibike": {"value_rows": []}} for d in (24, 23, 22, 21, 18, 17, 16, 15)]
    led2 = ledger_mod.update(date(2026, 9, 25), {"tables": {}, "streaks": [], "ranking_delta": {}}, hist2, THEMES,
                             None, lambda codes: {"5803": 8932.0}, 65280.0, ledger=led)
    e = next(x for x in led2["entries"] if x["code"] == "5803")
    check("6営業日後: d1 と d5 が埋まる（+10%）", (e["track"]["d1"], e["track"]["d5"]), (10.0, 10.0))
    check("日経比の超過リターン", e["track"]["excess"], 8.0)
    check("成績: シグナル別の d5 中央値", led2["stats"]["by_signal"][0]["d5_median"], 10.0)

    tr = trend.sector_trend([{"sector": "電気機器", "avg_pct": 2.0}], hist)
    check("業種の5日累積（当日+履歴2日）", tr["rows"][0]["d5"], 2.5)
    check("反発の読み（当日プラス・20日マイナス→なし、20日プラス）", tr["rows"][0]["label"], "続伸（トレンド）")

    # 指数の時間軸: 履歴は新しい順。日経と TOPIX を別々に積む
    ihist = [
        {"date": "2026-09-17", "taibike": {"indices": {"nikkei": {"close": 64000.0}, "topix": {"close": 4000.0}}}},
        {"date": "2026-09-16", "zenba": {"indices": {"nikkei": {"close": 63000.0}, "topix": {"close": 3900.0}}}},
        {"date": "2026-09-15", "taibike": {"indices": {"nikkei": {"close": 62000.0}, "topix": {"close": 3800.0}}}},
    ]
    itr = trend.index_trend(ihist, 65000.0, {"nikkei": {"close": 65000.0}, "topix": {"close": 4100.0}})
    check("系列は古い順（当日を末尾に足す）", itr["series"], [62000.0, 63000.0, 64000.0, 65000.0])
    check("トップレベルは従来どおり日経", itr["d5"], None)
    check("日経と TOPIX が並ぶ", [r["key"] for r in itr["indices"]], ["nikkei", "topix"])
    check("TOPIX の系列も当日まで", itr["indices"][1]["series"], [3800.0, 3900.0, 4000.0, 4100.0])


# ==================== 相場温度計（逆張りガード） ====================
def _bdays(n, start="2026-01-05"):
    from datetime import date, timedelta
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _path(dates, start, end, wiggle=0.0):
    """start から end へ等比で動く系列。wiggle は交互の揺れ（RSI を 100 に張り付かせない）。"""
    n = len(dates)
    out = []
    for i, d in enumerate(dates):
        v = start * (end / start) ** (i / (n - 1))
        out.append((d, round(v * (1 + (wiggle if i % 2 else -wiggle)), 4)))
    return out


def _macro(hot: bool):
    ds = _bdays(160)
    up = hot
    return {
        "nikkei": _path(ds, 50000, 65000 if up else 38000, 0.002),
        "nkvi": [(d, 16.0 if up else 40.0) for d in ds],
        "vix": [(d, 12.0 if up else 33.0) for d in ds],
        "spx": _path(ds, 6000, 7500 if up else 4800),
        "sox": _path(ds, 8000, 12000 if up else 5200),
        "usdjpy": _path(ds, 140, 160 if up else 125),
        "us10y": _path(ds, 5.5 if up else 3.5, 3.5 if up else 5.5),
        "jp10y": _path(ds, 2.5 if up else 1.0, 1.0 if up else 2.5),
        "wti": _path(ds, 110, 60) if up else _path(ds, 60, 110),
    }


def test_thermo():
    from dashboard import thermo as T
    from dashboard import bars as B

    print("\n[相場温度計]")
    check("帯: 高いほど+（順向き）", T.band(80, (25, 35, 65, 75))["score"], 2)
    check("帯: 低いほど+（逆向き、VIX 12）", T.band(12, (13, 16, 22, 30), -1)["score"], 2)
    check("帯: 中立", T.band(50, (25, 35, 65, 75))["score"], 0)
    check("RSI: 一方的な上昇は100", T.rsi([float(i) for i in range(1, 40)]), 100.0)
    check("RSI: 交互の上下は50付近", 45 <= T.rsi([100 + (1 if i % 2 else -1) for i in range(60)]) <= 55, True)

    for hot in (True, False):
        m = _macro(hot)
        ds = [d for d, _ in m["nikkei"]]
        extras = {
            "eps": [(d, 3000 * (1.0015 if hot else 0.9985) ** i) for i, d in enumerate(ds[-30:])],
            "revisions": [(d, 3 if hot else 0, 0 if hot else 3) for d in ds[-20:]],
            "news": [(d, 20 if hot else 1, 1 if hot else 20) for d in ds[-3:]],
            "breadth": [(d, 160 if hot else 50, 60 if hot else 170) for d in ds[-25:]],
        }
        mt = T.market_thermo(m, extras=extras)
        name = "全部追い風" if hot else "全部向かい風"
        check(f"{name}: 8軸すべて採点", mt["n"], 8)
        check(f"{name}: 一致度", mt["consensus"], "全面追い風" if hot else "全面向かい風")
        check(f"{name}: 帯", mt["zone"], "過熱" if hot else "総悲観")

    # 材料が欠けた軸は温度から外し、5軸未満なら温度を出さない
    thin = {k: v for k, v in _macro(True).items() if k in ("nikkei", "wti")}
    mt = T.market_thermo(thin)
    check("5軸未満は温度なし", (mt["temp"], mt["zone"]), (None, "材料不足"))

    # 先読みしない: 海外系列は判定日の当日を使わない
    m = {"nikkei": [("2026-01-05", 100.0), ("2026-01-06", 101.0)],
         "wti": [("2026-01-05", 60.0), ("2026-01-06", 999.0)]}
    check("upto strict は当日を除く", T.upto(m["wti"], "2026-01-06", strict=True), [60.0])
    check("upto は当日を含む", T.upto(m["wti"], "2026-01-06"), [60.0, 999.0])

    # バックテスト: 温度帯ごとの日数の合計 = 全日数
    import math
    ds = _bdays(260)
    base = _macro(True)
    long = {}
    for k, s in base.items():
        vals = [v for _, v in s]
        long[k] = [(d, vals[i % len(vals)] * (1 + 0.05 * math.sin(i / 11))) for i, d in enumerate(ds)]
    bt = T.backtest(long, horizons=(20, 60), warmup=80)
    check("バックテスト: 帯の日数の合計が全日数", sum(z["days"] for z in bt["zones"]), bt["all"]["days"])
    check("バックテスト: 60日後が無い末尾は数えない", bt["all"]["n60"] <= bt["all"]["days"] - 60, True)

    # 開示の方向
    check("方向: 上方修正", T.disclosure_dir({"category": "業績予想の修正", "title": "通期業績予想の上方修正に関するお知らせ"}), "up")
    check("方向: 表題に無い", T.disclosure_dir({"category": "業績予想の修正", "title": "業績予想の修正に関するお知らせ"}), None)
    check("方向: 両方書いてある", T.disclosure_dir({"category": "業績予想の修正", "title": "上方修正及び下方修正"}), None)
    check("方向: 増配", T.disclosure_dir({"category": "配当予想の修正", "title": "配当予想の修正（増配）"}), "up")
    ev = T.material_events([{"code": "1111", "name": "A", "category": "業績予想の修正", "title": "上方修正", "time": "15:30"},
                            {"code": "2222", "name": "B", "category": "業績予想の修正", "title": "下方修正", "time": "13:00"}],
                           "2026-09-10")
    check("引け後かどうか", [e["after"] for e in ev], [True, False])

    # 出尽くし: 引け後の上方修正の翌日から下げた／場中の下方修正の前日比で下げ止まった
    prices = {("1111", "2026-09-10", False): 1000, ("1111", "2026-09-12", False): 960,
              ("2222", "2026-09-10", True): 500, ("2222", "2026-09-12", False): 505}
    got = T.exhaustion(ev, lambda c, d, b: prices.get((c, d, b)), "2026-09-12")
    check("好材料出尽くし", got[0]["label"], "好材料出尽くし")
    check("悪材料出尽くし（アク抜け）", got[1]["label"], "悪材料出尽くし（アク抜け）")
    prices[("2222", "2026-09-12", False)] = 700      # 下方修正のあと +40%（TOB など別の材料）
    got = T.exhaustion(ev, lambda c, d, b: prices.get((c, d, b)), "2026-09-12")
    check("開示後の急騰は出尽くしと呼ばない", got[1]["label"], "開示後に急変（別の材料の可能性）")

    # 個別株の分類
    check("高値掴み注意（RSI 80）", "高値掴み注意" in T.stock_class({"rsi": 80, "dev25": 5, "r5": 3}), True)
    check("押し目", T.stock_class({"rsi": 42, "dev25": -3, "r5": -4, "r60": 15, "ma25": 1100, "ma75": 1000, "d1": -0.5}),
          ["押し目"])
    check("売られすぎ・下げ止まり", T.stock_class({"rsi": 25, "dev25": -12, "d1": 1.2})[0], "売られすぎ・下げ止まり")

    # 見出しの論調（1見出しで強気・弱気は1回ずつ）
    tone = T.headline_tone(["半導体株が急騰、最高値を更新", "銀行株が急落", "日経平均は小動き"], ["半導体", "銀行"])
    check("論調: 強気1・弱気1", (tone["pos"], tone["neg"]), (1, 1))
    check("論調: テーマ別", (tone["themes"]["半導体"], tone["themes"]["銀行"]), ([1, 0], [0, 1]))

    # 日足のつなぎ目: 分割で過去値が変われば取り直す
    old = [("2026-09-01", 1000.0), ("2026-09-02", 1010.0)]
    merged, refetch = B.merge_series(old, [("2026-09-02", 1010.0), ("2026-09-03", 1020.0)])
    check("日足: 末尾をつなぐ", (merged[-1], refetch), (("2026-09-03", 1020.0), False))
    merged, refetch = B.merge_series(old, [("2026-09-02", 505.0), ("2026-09-03", 510.0)])
    check("日足: 分割（過去値が半分）を検出", refetch, True)

    # 提案の記録: 5営業日たったら d5 が埋まる。同じ提案は20営業日のあいだ記録し直さない
    track = {}
    ds = _bdays(7, "2026-09-01")
    track = T.track_update(track, ds[0], [{"kind": "押し目候補", "key": "1111", "name": "A", "price": 100}],
                           lambda e: None, 1000)
    for d in ds[1:6]:
        track = T.track_update(track, d, [{"kind": "押し目候補", "key": "1111", "name": "A", "price": 100}],
                               lambda e: 5.0, 1010)
    check("記録は1件（20営業日は記録し直さない）", len(track["entries"]), 1)
    check("d5 と日経比", (track["entries"][0]["d5"], track["entries"][0]["x5"]), (5.0, 4.0))
    check("種類別の成績", track["stats"][0]["d5_median"], 5.0)

    # 業種: 中期上昇＋足元の調整は「押し目」、短期急騰は「過熱」
    check("業種: 押し目", T.sector_class({"rsi": 45, "dev25": -3, "dev75": 4, "r20": -2, "r60": 8, "heat": -1.2, "d1": -0.3}), "押し目")
    check("業種: 過熱", T.sector_class({"rsi": 78, "dev25": 9, "dev75": 12, "r20": 10, "r60": 20, "heat": 2.5}), "過熱")
    fit = T.macro_fit("銀行", {"jp10y": {"raw": 20, "unit": "bp", "norm": 1.33}})
    check("マクロ感応度: 国内金利上昇は銀行に追い風", fit["label"], "追い風")


if __name__ == "__main__":
    test_ranking()
    test_cnbc()
    test_tdnet()
    test_slot()
    test_news()
    test_kabutan()
    test_ranking_layouts()
    test_themes_ledger_trend()
    test_thermo()
    print()
    if failures:
        print(f"❌ {len(failures)} 件失敗: {', '.join(failures)}")
        sys.exit(1)
    print("✅ すべて通過")
