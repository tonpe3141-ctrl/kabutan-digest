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
    after = _quote({"symbol": "9502.T", "last": "2,835.50", "open": "2,839.00", "high": "2,868.00", "low": "2,813.50",
                    "volume": "4,479,100", "previous_day_closing": "2,839.00", "last_time": "2026-09-28T15:30:00.000+0900"})
    check("引け後のクォート: 出来高と最後の約定の時刻（当日の日足の代わりに使う）",
          (after["volume"], after["last_time"], after["asof"]), (4479100.0, "2026-09-28T15:30:00.000+0900", "2026-09-28"))

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

    from datetime import date

    from dashboard.build import insurance_target
    # 前の日の保険が深夜に発火した場合。今日の大引として保存しない（2026-09-30 00:41 の実測）
    check("深夜の大引の保険は前の日の大引", insurance_target("taibike", at(0, 41)), ("taibike", date(2026, 9, 6)))
    check("深夜の前場の保険も前の日の大引", insurance_target("zenba", at(2, 10)), ("taibike", date(2026, 9, 6)))
    check("夕方の大引の保険は今日", insurance_target("taibike", at(18, 5)), ("taibike", date(2026, 9, 7)))
    check("遅れた前場の保険は今日の大引", insurance_target("zenba", at(17, 59)), ("taibike", date(2026, 9, 7)))
    check("寄り前の保険は今日", insurance_target("preopen", at(7, 35)), ("preopen", date(2026, 9, 7)))

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


def test_rerun_carry_over():
    from dashboard.build import carry_over

    print("\n[同じ区分の取り直しで情報を減らさない]")
    before = {"sectors33": {"rows": [{"sector": "銀行業", "change_pct": 1.2}] * 33},
              "tables": {"value": {"rows": [{"code": "7203"}]}, "gainer": {"rows": [{"code": "1111"}]}},
              "kabutan": {"articles": [{"headline": "本日の【業種】騰落ランキング ＝ 大引け"}, {"headline": "A"}]}}
    now = {"sectors33": None, "tables": {"value": {"rows": [{"code": "9984"}]}, "gainer": {"rows": []}},
           "kabutan": {"articles": [{"headline": "A"}, {"headline": "B"}]}}
    kept = carry_over(now, before)
    check("東証33業種が消えたら前回を使う", len(now["sectors33"]["rows"]), 33)
    check("今回取れた表は上書きしない", now["tables"]["value"]["rows"][0]["code"], "9984")
    check("今回空だった表は前回を使う", now["tables"]["gainer"]["rows"][0]["code"], "1111")
    check("記事は前回にあって今回落ちたものを足す", [a["headline"] for a in now["kabutan"]["articles"]],
          ["A", "B", "本日の【業種】騰落ランキング ＝ 大引け"])
    check("引き継いだ区画を返す", kept, ["sectors33", "tables.gainer", "kabutan.articles+1"])
    check("前回が無ければ何もしない", carry_over({"sectors33": None}, None), [])
    now = {"press": {"headlines": [{"title": "新しい", "published": "2026-09-25T23:00+09:00"}], "official": [], "articles": []}}
    before = {"press": {"headlines": [{"title": "新しい", "published": "2026-09-25T23:00+09:00"},
                                      {"title": "窓から落ちた", "published": "2026-09-25T08:00+09:00"}],
                        "official": [{"title": "総裁会見", "published": "2026-09-25T15:30+09:00"}],
                        "articles": [{"headline": "〔東京株式〕続伸"}]}}
    kept = carry_over(now, before)
    check("報道・公的機関も取れなかった分を前回から引き継ぐ（重複させない）",
          ([h["title"] for h in now["press"]["headlines"]], len(now["press"]["official"]), len(now["press"]["articles"])),
          (["新しい", "窓から落ちた"], 1, 1))

    # 大引の保険: その日の大引が既にあっても、注文が前の営業日の引けのまま（当日の日足が未着だった）なら取り直す
    from dashboard import build as build_mod, commentary, store
    from dashboard.thermo_run import next_weekday
    tb = {"indices": {"nikkei": {"stale": False}}, "thermo": {"swing": {"asof": "2026-09-25"}}}
    orig = store.load_latest
    try:
        store.load_latest = lambda: {"date": "2026-09-28", "slots": {"taibike": {"data": tb}, "zenba": {"data": {}}}}
        check("大引の注文が前の営業日の引けのままなら、保険の実行で取り直す", build_mod.already_done("taibike", date(2026, 9, 28)), False)
        tb["thermo"]["swing"]["asof"] = "2026-09-28"
        check("当日の引けの注文が出ていれば、保険の実行は見送る", build_mod.already_done("taibike", date(2026, 9, 28)), True)
        tb["thermo"]["swing"]["asof"] = "2026-09-18"
        tb["indices"]["nikkei"]["stale"] = True
        check("休場日は取り直しても変わらないので見送る", build_mod.already_done("taibike", date(2026, 9, 28)), True)
        check("前場・寄り前は、その日の区分があれば見送る（従来どおり）",
              (build_mod.already_done("zenba", date(2026, 9, 28)), build_mod.already_done("preopen", date(2026, 9, 28))),
              (True, False))
    finally:
        store.load_latest = orig
    check("注文が有効な日は次の平日（金曜の引け → 月曜）", (next_weekday("2026-09-25"), next_weekday("2026-09-28")),
          ("2026-09-28", "2026-09-29"))
    th = {"temp": 50, "zone": "中立", "n": 8, "tailwind": 3, "headwind": 2, "guide": "中立の帯",
          "swing": {"asof": "2026-09-25", "orders": [{"code": "9502", "name": "中部電"}],
                    "verify": {"all": {"n": 10, "win": 80}, "account": {"cagr": 30.0, "dd": -10.0, "slot": 10.0}}}}
    stale_body = commentary.thermo_section(th, "taibike", "2026-09-28")["body"]
    fresh_body = commentary.thermo_section(th, "preopen", "2026-09-28")["body"]
    check("大引で注文が前の営業日の引けのままなら、注文として書かず「日足がそろい次第」と書く",
          ("日足がそろい次第" in stale_body, "中部電" in stale_body, "中部電" in fresh_body), (True, False, True))
    check("見立てに口座の伸び（資金の10%ずつ）も添える", "資金の10%ずつ本番どおりに置いた口座は年率 +30.0%" in fresh_body, True)


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
    check("買う側の分類は出さない（短期の押し目買いのルールに一本化）",
          T.stock_class({"rsi": 42, "dev25": -3, "r5": -4, "r60": 15, "ma25": 1100, "ma75": 1000, "d1": -0.5}), [])
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


def _swing_bars(n=260, start=1000.0, drift=0.003, vol_shares=2_000_000, dips=(), start_date="2025-01-06"):
    """上昇が続く日足（始値は前日終値、高安は ±1%）。dips は最後に足す日ごとの騰落率（%）。"""
    ds = _bdays(n + len(dips), start_date)
    out, c = [], start
    for k, d in enumerate(ds):
        prev = c
        c = c * (1 + drift) if k < n else c * (1 + dips[k - n] / 100)
        out.append((d, prev, max(prev, c) * 1.01, min(prev, c) * 0.99, c, vol_shares))
    return out


def test_swing():
    from dashboard import swing as W

    print("\n[短期の押し目買い（作戦の中心のルール）]")
    check("呼値: 3000円以下は1円で切り下げ", W.tick_down(2792.7), 2792)
    check("呼値: 5000円以下は5円（売りは切り上げ）", (W.tick_down(3547), W.tick_up(3547)), (3545, 3550))
    check("呼値: 3万円以下は10円", W.tick_down(12345), 12340)
    check("キャッシュの行を詰める（欠けた日は飛ばす、出来高は100株単位）",
          W.compact(["2026-01-05", "2026-01-06", "2026-01-07"], [[10, 11, 9, 10.5, 3], None, [10, 12, 10, 11, 4]]),
          [("2026-01-05", 10.0, 11.0, 9.0, 10.5, 300), ("2026-01-07", 10.0, 12.0, 10.0, 11.0, 400)])

    # 入口: 上昇トレンド（終値>200日線、50日線>200日線）で 2日RSI<10、売買代金10億円以上、300円以上
    up = _swing_bars(dips=(-2.0, -2.0))
    ind = W.indicators(up)
    i = len(up) - 1
    check("上昇トレンド中に2日続けて押すと入口", (W.is_signal(ind, i), ind["rsi"][i] < 10), (True, True))
    check("押す前（上昇が続く日）は入口でない", W.is_signal(ind, i - 2), False)
    thin = W.indicators(_swing_bars(vol_shares=300_000, dips=(-2.0, -2.0)))
    check("売買代金が10億円に満たないと入口にしない", W.is_signal(thin, i), False)
    cheap = W.indicators(_swing_bars(start=100.0, vol_shares=50_000_000, dips=(-2.0, -2.0)))
    check("300円未満の低位株は入口にしない", W.is_signal(cheap, i), False)
    down = W.indicators(_swing_bars(drift=-0.003, dips=(-2.0, -2.0)))
    check("下落トレンドの押しは入口にしない", W.is_signal(down, i), False)
    check("200日線が引けるまでは判定しない", W.is_signal(W.indicators(up[:150]), 149), False)

    # 次の終値がいくら未満なら入口か（RSI の漸化式を解いた境目）
    base = _swing_bars()
    bi = W.indicators(base)
    p = W.trigger_price(bi, len(base) - 1)
    d_next = _bdays(1, "2026-12-01")[0]
    below = W.indicators(base + [(d_next, p, p, p * 0.999, p * 0.999, 2_000_000)])
    above = W.indicators(base + [(d_next, p, p * 1.001, p, p * 1.001, 2_000_000)])
    check("境目の少し下で引ければ 2日RSI<10", below["rsi"][-1] < 10 <= above["rsi"][-1], True)
    row = W.today_row(bi)
    check("まだ押していない銘柄は「待つ」と境目の価格", (row["state"], row["trig"] < row["price"]), ("wait", True))
    check("入口の日は「注文対象」", W.today_row(ind)["state"], "signal")

    # 注文: 翌日だけ有効の指値（終値−0.5ATR）、損切りは指値−3ATR、売りの目安は直近3日と指値の平均（下限は指値+0.2%）
    o = W.order_of(ind)
    c, atr = ind["c"][i], ind["atr"][i]
    check("指値は終値−0.5ATR（呼値で切り下げ）", o["limit"], W.tick_down(c - 0.5 * atr))
    check("損切りは指値−3ATR", o["stop"], W.tick_down(o["limit"] - 3 * atr))
    check("売りの下限は指値+0.2%（呼値で切り上げ）、売りの目安はその下限より下にしない",
          (o["floor"], o["sell"] >= o["floor"]), (W.tick_up(o["limit"] * 1.002), True))
    check("注文が出ない日は None", W.order_of(bi), None)

    # 約定と手仕舞いの再現
    limit = c - 0.5 * atr
    nd = _bdays(14, "2026-06-01")

    def after_bars(*bars):
        return up + [(nd[k], *b, 2_000_000) for k, b in enumerate(bars)]

    def after(*bars):
        return W.indicators(after_bars(*bars))

    check("翌日の安値が指値に届かなければ約定しない",
          W.simulate(after((c, c * 1.01, limit + 1, c)), i)["filled"], False)
    r = W.simulate(after((limit - 5, limit, limit - 8, limit - 2)), i)
    check("寄りが指値より下なら寄りで約定し、まだ結果が出ていない", (r["entry"], r.get("open")), (round(limit - 5, 2), True))
    sl = (sum(ind["c"][i - 2:i + 1]) + (limit - 2)) / 4          # 約定日の翌日の売り指値
    r = W.simulate(after((limit, limit + 1, limit - 1, limit - 2), (limit - 1, sl + 5, limit - 3, sl + 2)), i)
    check("翌日に高値が売り指値に届けば、売り指値で売る", (r["why"], r["exit"]), ("sell", round(sl, 2)))
    check("損益は売買コスト0.1%を引いた率", r["ret"], round((sl / limit - 1) * 100 - 0.1, 2))
    stop = limit - 3 * atr
    r = W.simulate(after((limit, limit + 1, limit - 1, limit - 2), (stop - 10, stop - 5, stop - 20, stop - 15)), i)
    check("寄りで損切りを割っていれば寄りで売る（窓開け）", (r["why"], r["exit"]), ("stop", round(stop - 10, 2)))
    r = W.simulate(after((limit, limit + 1, limit - 1, limit - 2), (limit - 2, sl + 5, stop - 1, limit)), i)
    check("同じ日に損切りと売り指値の両方に届いたら、損切りを先に数える", r["why"], "stop")
    drift = [(limit * (1 - 0.003 * k), limit * (1 - 0.003 * k) * 1.0005, limit * (1 - 0.003 * (k + 1)),
              limit * (1 - 0.003 * (k + 1))) for k in range(12)]
    r = W.simulate(after(*drift), i)
    check("10営業日で売れなければ引けで売る", (r["why"], r["days"]), ("time", 11))
    # 売り指値の下限: 直近4日の平均が約定値より下にあるうちは、平均に届いても売らない（小さな損を確定させない）
    L = limit
    dip = [(L, L + 1, L - 1, L - 2), (L * 0.98, L * 0.985, L * 0.97, L * 0.97), (L * 0.97, L * 0.975, L * 0.965, L * 0.97),
           (L * 0.97, L * 0.975, L * 0.965, L * 0.97), (L * 0.975, L * 0.99, L * 0.97, L * 0.985),
           (L * 0.99, L * 1.01, L * 0.985, L * 1.0)]
    di = after(*dip)
    check("4日の平均（約定値より下）に高値が届いた日は売らない",
          (W.sell_level(di, i + 5) < L, di["h"][i + 5] >= W.sell_level(di, i + 5), W.sell_level(di, i + 5, L) > di["h"][i + 5]),
          (True, True, True))
    r = W.simulate(di, i)
    check("約定値+0.2% に届いた日に、その値で売る（コストを引いて +0.1%）",
          (r["why"], r["exit"], r["ret"], r["days"]), ("sell", round(L * 1.002, 2), 0.1, 6))
    st = W.summarize([{"ret": 0.1, "days": 6, "why": "sell"}, {"ret": 2.0, "days": 3, "why": "sell"},
                      {"ret": -7.0, "days": 4, "why": "stop"}])
    check("成績: 小さな勝ち（+0.5%以下）と大きな負け（−5%以下）の割合", (st["win"], st["small"], st["big_loss"]), (67, 33, 33))
    check("成績: 翌日までに終わった割合と、1回の平均を保有日数で割った値", (st["d2"], st["per_day"]),
          (0, round((-4.9 / 3) / (13 / 3), 2)))

    # 口座の再現（本番どおりの置き方）: 引けで資金の10%ずつ注文を置き、約定しなければ資金は翌日に戻る
    ad = _bdays(6, "2026-07-01")
    win10 = {"filled": True, "entry": 100.0, "out": ad[3], "ret": 10.0}
    miss = {"filled": False}
    px = {("A", ad[1]): 105.0, ("A", ad[2]): 108.0, ("A", ad[3]): 110.1}
    acc = W.account(ad, {ad[0]: [{"code": "A", "res": win10}, {"code": "B", "res": miss}]}, lambda c, d: px.get((c, d)))
    check("口座: 約定した注文だけを持ち、約定しなかった注文の資金は翌日に現金へ戻る（10%で +10% → 資産 +1%）",
          (acc["final"], acc["n"], acc["win"], acc["slot"]), (1.01, 1, 100, 10.0))
    flat = {"filled": True, "entry": 100.0, "out": ad[2], "ret": 0.0}
    a2 = W.account(ad, {ad[0]: [{"code": str(k), "res": flat} for k in range(8)]}, lambda c, d: 100.0)
    check("口座: 1日5件まで", a2["n"], 5)
    a3 = W.account(ad, {ad[0]: [{"code": c, "res": flat} for c in ("1", "2", "3")]}, lambda c, d: 100.0,
                   {"1": "電力", "2": "電力", "3": "電力"})
    check("口座: 同じ業種は2銘柄まで", a3["n"], 2)
    hold = {"filled": True, "entry": 100.0, "out": ad[4], "ret": 5.0}
    a4 = W.account(ad, {ad[0]: [{"code": "1", "res": hold}], ad[1]: [{"code": "1", "res": flat}]}, lambda c, d: 100.0)
    check("口座: 保有中の銘柄には重ねて置かない", a4["n"], 1)
    trk = {"orders": [{"asof": ad[0], "code": "A", "done": True, "filled": True, "entry": 100.0, "out": ad[3], "ret": 10.0},
                      {"asof": ad[0], "code": "B", "done": True, "filled": False},
                      {"asof": ad[4], "code": "C", "done": False}]}
    pa = W.paper_account(trk, ad, lambda c, d: px.get((c, d)))
    check("注文の実績を口座に: 約定しなかった注文と結果が未確定の注文は資金を縛らない", (pa["final"], pa["n"], pa["from"]),
          (1.01, 1, ad[0]))

    # 並べ方: 25日線からの下離れが大きい順、1日5銘柄まで、同じ業種は2銘柄まで
    rows = [{"code": str(1000 + k), "dev25": -k} for k in range(8)]
    sector = {"1007": "電力", "1006": "電力", "1005": "電力"}
    picked, rest, skip = W.rank_orders(rows, sector)
    check("下離れの大きい順・同じ業種は2つまで", [r["code"] for r in picked], ["1007", "1006", "1004", "1003", "1002"])
    check("上限や業種で外れたものは次点", [r["code"] for r in rest][:2], ["1005", "1001"])

    # 業種の中での位置: 業種（自分を除いた中央値）の20日騰落と、業種との差で押しの形を決める
    check("業種ぐるみの押し（業種 −5% 以下）", W.peer_class(-6.0, -1.0), "dip")
    check("業種ぐるみの押しで、業種の中でも下げが大きい", W.peer_class(-6.0, -7.0), "dip_lag")
    check("出遅れの押し（業種は +3% 以上、自分は −5pt 以上遅れ）", W.peer_class(4.0, -6.0), "lag")
    check("業種の上げに沿った押しは見送り", W.peer_class(4.0, -2.0), "hot")
    check("業種がふつうなら、ふつうの押し", W.peer_class(0.0, -9.0), "plain")
    check("比べる業種が無ければ none", W.peer_class(None, None), "none")
    groups = W.peer_groups(["1", "2", "3", "4", "5", "6"],
                           {"1": {"themes": ["半導体", "AI"]}, "2": {"themes": ["半導体"]}, "3": {"themes": ["半導体"]},
                            "4": {"themes": ["半導体"]}, "5": {"themes": ["電線"]}},
                           {"5": "非鉄", "6": "機械"})
    check("グループ: 主テーマで束ね、4銘柄に満たないグループは使わない", groups,
          {"1": "半導体", "2": "半導体", "3": "半導体", "4": "半導体"})
    ctx = W.peer_context({"1": -20.0, "2": -8.0, "3": -6.0, "4": -10.0}, groups)
    check("業種は自分を除いた中央値・差は自分との差", (ctx["1"]["g20"], ctx["1"]["rel20"], ctx["1"]["cls"]),
          (-8.0, -12.0, "dip_lag"))
    check("自分を除くと3銘柄に満たない日は比べない", W.peer_context({"1": -20.0, "2": -8.0, "3": -6.0}, groups), {})
    board = W.peer_board(ctx, {"1": -20.0, "2": -8.0, "3": -6.0, "4": -10.0}, groups)
    check("業種の中の位置: 業種ぐるみの下げと、業種との差の順", (board[0]["kind"], board[0]["g20"],
                                                   [m["code"] for m in board[0]["members"]]),
          ("dip", -9.0, ["1", "4", "2", "3"]))
    rows = [{"code": "2001", "dev25": -3.0, "peer": {"cls": "hot", "rel20": 1.0}},
            {"code": "2002", "dev25": -3.0, "peer": {"cls": "lag", "rel20": -8.0}},
            {"code": "2003", "dev25": -6.0, "peer": None}]
    picked, rest, skip = W.rank_orders(rows, {})
    check("見送りの形は注文にも次点にも入れず、並べ方は下離れ＋業種より遅れている分",
          ([r["code"] for r in picked], [r["code"] for r in skip]), (["2002", "2003"], ["2001"]))

    # アプリが出した注文の実績: 記録して、結果が出たら凍結する
    done = after((limit, limit + 1, limit - 1, limit - 2), (limit - 1, sl + 5, limit - 3, sl + 2))
    orders = [{"asof": up[i][0], "code": "1111", "name": "A", "limit": W.tick_down(limit), "stop": 1}]
    tr = W.paper_update({}, nd[1], orders, lambda code: done)
    check("注文の実績: 売りまで済んだ1件", (tr["stats"]["n"], tr["stats"]["win"], tr["orders"][0]["why"]), (1, 100, "sell"))
    tr2 = W.paper_update(tr, nd[2], orders, lambda code: None)
    check("注文の実績: 同じ注文は二重に記録せず、結果は凍結", (len(tr2["orders"]), tr2["orders"][0]["ret"]),
          (1, tr["orders"][0]["ret"]))
    pend = W.paper_update({}, up[i][0], orders, lambda code: ind)
    check("注文の実績: 翌日の日足がまだ無ければ未確定", (pend["orders"][0]["done"], pend["stats"]["n"]), (False, 0))

    # ルールの検証: 1回ごとの成績と、比べる相手（翌日の寄りで買って5営業日後に売る）
    v = W.verify({"1111": after_bars((limit, limit + 1, limit - 1, limit - 2), (limit - 1, sl + 5, limit - 3, sl + 2)),
                  "2222": _swing_bars(drift=-0.003)})
    check("検証: 約定して結果が出た売買を数える", (v["all"]["n"], v["all"]["win"]), (1, 100))
    check("検証: 比べる相手がある", v["base"]["n"] > 0, True)
    check("検証: 同じ期間に本番どおりに置いた口座の再現も出す（資金の10%ずつ）",
          (v["account"]["n"], v["account"]["slot"], v["account"]["final"] > 1), (1, 10.0, True))
    # 業種と比べる検証: 同じ業種の3銘柄が20日で +4% ほど上げ、自分（+1% ほど）も業種並みの押しは見送りに数える
    sig_bars = after_bars((limit, limit + 1, limit - 1, limit - 2), (limit - 1, sl + 5, limit - 3, sl + 2))
    peers = {c: _swing_bars(n=len(sig_bars), drift=0.002) for c in ("3001", "3002", "3003")}
    grp = {c: "テスト" for c in ("1111", "3001", "3002", "3003")}
    vg = W.verify({"1111": sig_bars, **peers}, groups=grp)
    check("検証: 業種の上げに沿った押しは注文に数えず、見送りの成績に", (vg["all"].get("n", 0), vg["skipped"]["n"]), (0, 1))


def _mid_bars(post=()):
    """1年上げた（+0.4%/日×250）あと、15日で −14% 調整し、8日戻して、2日続けて押した日足（最後が中期の押しの日）。"""
    return _swing_bars(n=0, dips=tuple([0.4] * 250 + [-1.0] * 15 + [0.5] * 8 + [-2.0, -2.0] + list(post)))


def test_mid():
    from dashboard import swing as W
    from dashboard import thermo_run as TR

    print("\n[中期の押し目（大きなトレンドの中の1〜3か月の調整）]")
    bars = _mid_bars()
    ind = W.indicators(bars)
    i = len(bars) - 1
    st = W.mid_state(ind, i)
    c = ind["c"]
    check("形の材料: 60日高値・高値からの営業日数・下げ・調整の安値・12か月の強さ",
          (st["hi"], st["age"], round(st["dd"], 1), st["low"], round(st["mom"], 1), st["up"]),
          (c[249], 25, round((c[i] / c[249] - 1) * 100, 1), min(c[249:]), round((c[i - 20] / c[i - 250] - 1) * 100, 1), True))
    check("250営業日に満たなければ材料を出さない", W.mid_state(ind, 240), None)
    check("強さの順位（0 = 最も弱い、1 = 最も強い）", W.mid_ranks({"A": 80.0, "B": 5.0, "C": -3.0, "D": None}),
          {"C": 0.0, "B": 0.5, "A": 1.0})
    check("中期の形: 強さ上位1/3・上昇トレンド・高値から20〜59営業日・−10%以下", W.is_mid_shape(st, 0.9), True)
    check("強さが上位1/3に入らなければ形にしない", W.is_mid_shape(st, 0.5), False)
    check("高値から20営業日に満たない（調整が短い）なら形にしない", W.is_mid_shape({**st, "age": 19}, 0.9), False)
    check("高値から60営業日を超えたら形にしない", W.is_mid_shape({**st, "age": 60}, 0.9), False)
    check("高値から −10% まで下げていなければ形にしない", W.is_mid_shape({**st, "dd": -9.0}, 0.9), False)
    check("200日線の下（大きなトレンドが崩れた）なら形にしない", W.is_mid_shape({**st, "up": False}, 0.9), False)
    check("中期の形で 2日RSI<10 まで押した日が入口", (ind["rsi"][i] < 10, W.is_mid_signal(ind, i, st, 0.9)), (True, True))
    check("押していない日は入口にしない", W.is_mid_signal(ind, i - 2, W.mid_state(ind, i - 2), 0.9), False)

    o = W.mid_order_of(ind, st)
    atr = ind["atr"][i]
    check("注文: 指値は短期と同じ（終値−0.5ATR）、損切りは min(調整の安値, 指値) − 1ATR",
          (o["limit"], o["stop"], o["hold"]), (W.tick_down(c[i] - 0.5 * atr), W.tick_down(min(st["low"], o["limit"]) - atr), 60))

    # 約定と手仕舞い: 売り指値は置かず、損切りか60営業日目の引け
    lim = c[i] - 0.5 * atr
    nd = _bdays(70, "2026-06-01")

    def after(*bs):
        return W.indicators(bars + [(nd[k], *b, 2_000_000) for k, b in enumerate(bs)])
    stop = min(st["low"], lim) - atr
    r = W.simulate_mid(after((lim, lim + 1, lim - 1, lim), *[(lim * 1.1, lim * 1.12, lim * 1.09, lim * 1.11)] * 3), i, st["low"])
    check("大きく上げても売り指値では売らない（まだ結果が出ていない）", (r["filled"], r.get("open"), round(r["stop"], 2)),
          (True, True, round(stop, 2)))
    r = W.simulate_mid(after((lim, lim + 1, lim - 1, lim), (stop - 5, stop - 2, stop - 9, stop - 3)), i, st["low"])
    check("寄りで損切りを割っていれば寄りで売る", (r["why"], r["exit"]), ("stop", round(stop - 5, 2)))
    r = W.simulate_mid(after((lim, lim + 1, lim - 1, lim), (lim, lim + 2, stop - 1, lim)), i, st["low"])
    check("場中に損切りを割ったら損切りの価格で", (r["why"], r["exit"]), ("stop", round(stop, 2)))
    flat = [(lim, lim * 1.01, lim * 0.995, lim * 1.005)] * 62
    r = W.simulate_mid(after(*flat), i, st["low"])
    check("60営業日たったら引けで売る", (r["why"], r["days"], r["exit"]), ("time", 61, round(lim * 1.005, 2)))
    check("中期の注文で翌日の安値が指値に届かなければ約定しない",
          W.simulate_mid(after((c[i], c[i] * 1.01, lim + 1, c[i])), i, st["low"])["filled"], False)
    check("押し待ちの価格でも大きなトレンドが保たれるか", (W.mid_holds_at(ind, i, c[i] * 0.99), W.mid_holds_at(ind, i, 1.0)),
          (True, False))

    # 口座: 中期は短期より先に、中期の保有と置いた注文が6件になるまで。業種の上限は短期だけで数える
    ad = _bdays(6, "2026-07-01")
    flat_res = {"filled": True, "entry": 100.0, "out": ad[4], "ret": 0.0}
    mid_on = {ad[0]: [{"code": f"M{k}", "res": flat_res} for k in range(8)]}
    a = W.account(ad, {ad[0]: [{"code": "M0", "res": flat_res}]}, lambda c_, d: 100.0, mid_on=mid_on)
    check("口座: 中期は6件まで・同じ銘柄に短期の注文を重ねない", (a["mid_n"], a["n"]), (6, 0))
    a = W.account(ad, {ad[0]: [{"code": "S1", "res": flat_res}, {"code": "S2", "res": flat_res}, {"code": "S3", "res": flat_res}]},
                  lambda c_, d: 100.0, {"M0": "電力", "M1": "電力", "S1": "電力", "S2": "電力", "S3": "電力"},
                  mid_on={ad[0]: [{"code": "M0", "res": flat_res}, {"code": "M1", "res": flat_res}]})
    check("口座: 業種の上限（2銘柄）は短期の保有だけで数える", (a["mid_n"], a["n"]), (2, 2))

    # 注文の実績: 調整の安値を覚えておき、同じ simulate_mid で測る
    done = after((lim, lim + 1, lim - 1, lim), (stop - 5, stop - 2, stop - 9, stop - 3))
    mo = [{"asof": bars[i][0], "code": "7777", "name": "M", "limit": W.tick_down(lim), "stop": 1, "low": st["low"]}]
    tr = W.paper_update({}, nd[1], mo, lambda code: done, mid=True)
    check("中期の注文の実績: 損切りまで済んだ1件", (tr["stats"]["n"], tr["orders"][0]["why"], tr["orders"][0]["low"]),
          (1, "stop", st["low"]))

    # 検証: 中期の形の押しは中期で数え、短期の成績と注文には入れない。比べる相手と、以前の置き方の口座も出す
    post = [-1.5] + [0.3] * 70                       # 翌日に指値まで下げて約定し、そのあと60営業日持つ
    strong = _mid_bars(post)
    weak = {k: _swing_bars(n=len(strong), drift=0.0) for k in ("8001", "8002")}
    v = W.verify({"7777": strong, **weak})
    sig_day = strong[i][0]
    check("検証: 中期の押しを中期の成績に数える（60営業日で手仕舞い）", (v["mid"]["all"]["n"], v["mid"]["all"]["times"]), (1, 100))
    check("検証: 比べる相手（調整の条件なしの強い銘柄の押し）も同じ出口で", v["mid"]["base"]["all"]["n"] >= 1, True)
    check("検証: 口座は短期と中期を合わせ、以前の置き方（短期だけ）も並べる",
          (v["account"]["mid_n"], v["account_prev"]["mid_n"], v["account_prev"]["n"] >= 1), (1, 0, True))

    # 売買タブの注文: 中期の形の押しは中期の注文にだけ出し、短期の注文・もうすぐには出さない
    rows = {}
    inds = {"7777": ind, **{k: W.indicators(b[:len(bars)]) for k, b in weak.items()}}
    for k, x in inds.items():
        rows[k] = W.today_row(x)
    blk = TR.swing_block({"dates": [b[0] for b in bars], "stocks": {}}, rows, lambda code: inds.get(code),
                         {"7777": "中期株"}, {})
    check("売買タブ: 中期の注文に出し、短期の注文には出さない",
          ([o["code"] for o in blk["mid"]["orders"]], [o["code"] for o in blk["orders"]], blk["mid"]["orders"][0]["asof"]),
          (["7777"], [], sig_day))
    check("銘柄ごとの位置に中期の形を書き足す", (rows["7777"]["mid"]["st"], rows["7777"]["mid"]["age"]), ("signal", 25))


def test_ohlc_cache():
    from datetime import date as _date
    from dashboard import bars as B
    from dashboard.sources.cnbc import parse_ohlc

    print("\n[四本値の日足キャッシュ]")
    got = parse_ohlc({"barData": {"priceBars": [
        {"open": "10", "high": "12", "low": "9", "close": "11", "volume": 1200, "tradeTime": "20260105000000"},
        {"open": None, "high": None, "low": None, "close": "11.5", "volume": None, "tradeTime": "20260106000000"},
        {"close": "0", "tradeTime": "20260107000000"}]}})
    check("四本値: 欠けた始値・高安は終値で埋め、終値0の日は捨てる",
          got, [("2026-01-05", 10.0, 12.0, 9.0, 11.0, 1200), ("2026-01-06", 11.5, 11.5, 11.5, 11.5, 0)])

    ds = _bdays(20, "2026-03-02")
    data = {"macro": {"nikkei": {"d": ds, "c": [1.0] * 20}}, "dates": [], "stocks": {}}
    ohlc = {"dates": [], "stocks": {}}
    calls = []

    def fetch_ohlc(sym, start, end):
        calls.append(start)
        return [(d, 100.0, 101.0, 99.0, 100.0 + k, 1000) for k, d in enumerate(ds)]
    B.update_stocks(data, ["1111"], None, _date(2026, 3, 27), ohlc=ohlc, fetch_ohlc=fetch_ohlc)
    check("四本値から終値も作る（1回の取得で両方）", (data["stocks"]["1111"][-1], len(calls)), (119.0, 1))
    check("四本値は [始値, 高値, 安値, 終値, 出来高(100株)]", ohlc["stocks"]["1111"][-1], [100.0, 101.0, 99.0, 119.0, 10])

    def tail(sym, start, end):
        calls.append(start)
        return [(d, 100.0, 101.0, 99.0, 100.0 + k, 1000) for k, d in enumerate(ds)][-8:]

    def split(sym, start, end):
        calls.append(start)
        return [(d, 50.0, 51.0, 49.0, (100.0 + k) / 2, 1000) for k, d in enumerate(ds)]
    # 取り直し不要な長さ（600営業日）の履歴を持っている銘柄
    ohlc["dates"] = _bdays(600, "2023-12-01")[:-20] + ds
    ohlc["stocks"]["1111"] = [[100.0, 101.0, 99.0, 100.0, 10]] * 580 + [[100.0, 101.0, 99.0, 100.0 + k, 10] for k in range(20)]
    calls.clear()
    got, ok, full = B._fetch_ohlc_merged("1111", B.ohlc_map(ohlc, "1111"), tail, _date(2026, 3, 27))
    check("四本値: 分割が無ければ末尾だけ取ってつなぐ", (full, len(calls), calls[0].isoformat(), len(got)),
          (False, 1, ds[-8], 600))
    calls.clear()
    got, ok, full = B._fetch_ohlc_merged("1111", B.ohlc_map(ohlc, "1111"), split, _date(2026, 3, 27))
    check("四本値: 過去値が半分になっていたら（分割）全期間を取り直す", (full, len(calls), got[ds[-1]][3]), (True, 2, 59.5))

    # 大引の時点で日足に当日の分が無いとき、引け後のクォートから当日の日足を足す（その日の引けで翌営業日の注文を出す）
    dd = {"macro": {"nikkei": {"d": ["2026-09-24", "2026-09-25"], "c": [66000.0, 66364.2]}},
          "dates": ["2026-09-24", "2026-09-25"], "stocks": {"9502": [2869, 2839], "7203": [3000, 2989.5]}}
    oo = {"dates": ["2026-09-24", "2026-09-25"],
          "stocks": {"9502": [[2894.5, 2904, 2842, 2869, 35706], [2896.5, 2899, 2826.5, 2839, 36141]],
                     "7203": [[3010, 3020, 2990, 3000, 1000], [3000, 3005, 2980, 2989.5, 1000]]}}
    qs = {"9502": {"last": 2835.5, "open": 2839.0, "high": 2868.0, "low": 2813.5, "volume": 4479100.0,
                   "last_time": "2026-09-28T15:30:00.000+0900"},
          "7203": {"last": 2986.5, "open": 3003.0, "high": 3024.0, "low": 2986.5, "volume": 100.0,
                   "last_time": "2026-09-28T14:10:00.000+0900"}}
    n = B.append_today(dd, oo, "2026-09-28", 65877.62, qs)
    check("引け後のクォート（15:30 の約定）から当日の日足を足す（出来高は100株単位）",
          (n, oo["dates"][-1], oo["stocks"]["9502"][-1], dd["dates"][-1], dd["stocks"]["9502"][-1]),
          (1, "2026-09-28", [2839.0, 2868.0, 2813.5, 2835.5, 44791], "2026-09-28", 2835.5))
    check("引けより前で止まったクォートの銘柄は空け、日付の軸（日経平均）に当日の終値を足す",
          (oo["stocks"]["7203"][-1], dd["stocks"]["7203"][-1], dd["macro"]["nikkei"]["d"][-1], dd["macro"]["nikkei"]["c"][-1]),
          (None, None, "2026-09-28", 65877.62))
    check("日足に当日の分があれば何もしない", B.append_today(dd, oo, "2026-09-28", 65877.62, qs), 0)
    check("日経平均の当日の終値が無ければ何もしない",
          B.append_today({"macro": {"nikkei": {"d": ["2026-09-25"], "c": [1.0]}}}, {}, "2026-09-28", None, qs), 0)


def test_ledger_stats():
    print("\n[台帳の成績と価格の取り足し]")
    hist = [{"date": f"2026-09-{d:02d}", "taibike": {"value_rows": []}} for d in (16, 15, 14, 11, 10, 9)]
    payload = {"tables": {"kessan_after": {"rows": [
        {"code": "2788", "name": "アップル", "category": "業績予想の修正", "time": "15:30",
         "title": "業績予想の上方修正及び増配に関するお知らせ"}]}}, "streaks": [], "ranking_delta": {}}
    led = ledger_mod.update(date(2026, 9, 17), payload, hist, THEMES, None, lambda codes: {"2788": 500.0}, 64000.0,
                            ledger={"entries": [], "stats": {}})
    e = led["entries"][0]
    check("開示だけで載った銘柄も当日の価格を取り足す", e["price_at_flag"], 500.0)

    led = ledger_mod.update(date(2026, 9, 17), payload, hist, THEMES, None, lambda codes: {}, 64000.0,
                            ledger={"entries": [], "stats": {}})
    hist2 = [{"date": "2026-09-17", "taibike": {"value_rows": []}}] + hist
    led = ledger_mod.update(date(2026, 9, 18), {"tables": {}, "streaks": [], "ranking_delta": {}}, hist2, THEMES,
                            None, lambda codes: {"2788": 520.0}, 64500.0, ledger=led)
    e = led["entries"][0]
    check("価格が後から取れたら、その日を起点にし直す",
          (e["price_at_flag"], e.get("track_from"), e["track"]["d1"]), (520.0, "2026-09-18", None))

    # 休場日の実行は営業日に数えない（同じ価格で「1営業日たった」としない）
    hol = [{"date": "2026-09-22", "taibike": {"value_rows": [], "indices": {"nikkei": {"close": 1, "stale": True}}}},
           {"date": "2026-09-21", "taibike": {"value_rows": [], "indices": {"nikkei": {"close": 1, "stale": True}}}},
           {"date": "2026-09-18", "taibike": {"value_rows": [], "indices": {"nikkei": {"close": 1}}}}]
    led = ledger_mod.update(date(2026, 9, 18), payload, hol[2:] + hist, THEMES, None, lambda codes: {"2788": 500.0},
                            64000.0, ledger={"entries": [], "stats": {}})
    led = ledger_mod.update(date(2026, 9, 22), {"tables": {}, "streaks": [], "ranking_delta": {},
                                                "indices": {"nikkei": {"close": 1, "stale": True}}}, hol + hist, THEMES,
                            None, lambda codes: {"2788": 500.0}, 64000.0, ledger=led)
    check("休場日の実行では進めない", (led["entries"][0]["track"]["days"], led["entries"][0]["track"]["d1"]), (0, None))
    led = ledger_mod.update(date(2026, 9, 24), {"tables": {}, "streaks": [], "ranking_delta": {}}, hol + hist, THEMES,
                            None, lambda codes: {"2788": 510.0}, 64000.0, ledger=led)
    check("休場日を挟んでも翌営業日が d1", (led["entries"][0]["track"]["days"], led["entries"][0]["track"]["d1"]), (1, 2.0))

    # 日足での測り直し: 見つけた日の終値と、ちょうど h 営業日後の終値
    cal = _bdays(8, "2026-09-01")
    closes = {d: 100.0 + i * 2 for i, d in enumerate(cal)}               # 毎営業日 +2円
    nk = {d: 1000.0 + i * 10 for i, d in enumerate(cal)}                 # 毎営業日 +1%
    led = {"entries": [{"code": "1111", "first_seen": cal[1], "signals": ["A"], "status": "watching",
                        "track": {"d1": 0.0, "d5": -3.0}}]}
    ledger_mod.retrack(led, cal, lambda c, d: closes.get(d), nk.get)
    tr = led["entries"][0]["track"]
    check("測り直し: d1 は1営業日後の終値", tr["d1"], round((104 / 102 - 1) * 100, 2))
    check("測り直し: d5 は5営業日後の終値、日経比も同じ日", (tr["d5"], tr["x5"]),
          (round((112 / 102 - 1) * 100, 2), round((112 / 102 - 1) * 100 - (1060 / 1010 - 1) * 100, 2)))
    check("測り直し: 20営業日に満たなければ d20 は空・追跡中", (tr["d20"], led["entries"][0]["status"], tr["days"]), (None, "watching", 6))
    led["entries"][0]["first_seen"] = cal[5]
    ledger_mod.retrack(led, cal, lambda c, d: closes.get(d), nk.get)
    check("測り直し: 誤って埋まった d5 を消す", led["entries"][0]["track"]["d5"], None)

    entries = [{"signals": ["A"], "track": {"d5": 4.0, "x5": 2.0, "d20": 6.0, "x20": 1.0}},
               {"signals": ["A"], "track": {"d5": -2.0, "x5": -3.0}},
               {"signals": ["A", "B"], "track": {"d5": 1.0, "x5": 0.5}},
               {"signals": ["B"], "track": {"d5": None}}]
    st = ledger_mod.compute_stats(entries, "2026-09-25")
    a = next(r for r in st["by_signal"] if r["signal"] == "A")
    check("成績: 件数・d5中央値・日経比の中央値・日経に勝った割合",
          (a["count"], a["d5_median"], a["x5_median"], a["beat5"]), (3, 1.0, 0.5, 0.67))
    check("成績: 20日後", (a["n20"], a["d20_median"]), (1, 6.0))
    check("成績: 件数が少ないと enough=False", a["enough"], False)
    check("成績: 全体", st["overall"]["count"], 3)


# ==================== 報道・公的機関（株探以外の情報源） ====================
from dashboard.sources import press as press_mod  # noqa: E402
from dashboard.sources.kabutan_news import parse_yahoo_list  # noqa: E402

# Google ニュース RSS の実物の形（<source url> が配信元ドメイン。媒体名の表記は取得ごとに揺れる）
GN_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>東京株式市場・大引け＝5日続伸、半導体関連株けん引 - ロイター</title><link>https://news.google.com/rss/articles/A</link>
<pubDate>Fri, 25 Sep 2026 07:14:00 GMT</pubDate><description>&lt;a href="x"&gt;y&lt;/a&gt;</description>
<source url="https://jp.reuters.com">ロイター</source></item>
<item><title>ＮＹ外為市場＝円急伸 - jp.reuters.com</title><link>https://news.google.com/rss/articles/B</link>
<pubDate>Fri, 25 Sep 2026 19:21:00 GMT</pubDate><source url="https://jp.reuters.com">jp.reuters.com</source></item>
<item><title>634A.T - | Stock Price &amp; Latest News | Reuters - ロイター</title><link>https://news.google.com/rss/articles/C</link>
<pubDate>Fri, 25 Sep 2026 07:13:00 GMT</pubDate><source url="https://jp.reuters.com">ロイター</source></item>
<item><title>ゴルフ＝世界選抜が米国に5戦全勝 - ロイター</title><link>https://news.google.com/rss/articles/D</link>
<pubDate>Fri, 25 Sep 2026 09:31:00 GMT</pubDate><source url="https://jp.reuters.com">ロイター</source></item>
<item><title>ロイターによると円急伸 - 転載サイト</title><link>https://news.google.com/rss/articles/E</link>
<pubDate>Fri, 25 Sep 2026 10:00:00 GMT</pubDate><source url="https://example.com">転載サイト</source></item>
<item><title>【コラム】英国におにぎり旋風、本場の味にはまだ遠く - ロイター</title><link>https://news.google.com/rss/articles/G</link>
<pubDate>Fri, 25 Sep 2026 10:00:00 GMT</pubDate><source url="https://jp.reuters.com">ロイター</source></item>
<item><title>BNP PARIBAS EASY II WORLD AC - ロイター</title><link>https://news.google.com/rss/articles/H</link>
<pubDate>Fri, 25 Sep 2026 10:00:00 GMT</pubDate><source url="https://jp.reuters.com">ロイター</source></item>
<item><title>東京株式市場・前場＝古い記事 - ロイター</title><link>https://news.google.com/rss/articles/F</link>
<pubDate>Tue, 22 Sep 2026 03:00:00 GMT</pubDate><source url="https://jp.reuters.com">ロイター</source></item>
</channel></rss>"""

JIJI_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>◎〔米株式〕ダウ４日ぶり反発、４７８ドル高☆差替 - sp.m.jiji.com</title><link>https://news.google.com/a</link>
<pubDate>Fri, 25 Sep 2026 20:59:00 GMT</pubDate><source url="https://sp.m.jiji.com">sp.m.jiji.com</source></item>
<item><title>株式会社AITechと伏見工業株式会社、AI認識システムを共同開発 - 時事ドットコム</title><link>https://news.google.com/b</link>
<pubDate>Fri, 25 Sep 2026 21:16:00 GMT</pubDate><source url="https://www.jiji.com">時事ドットコム</source></item>
<item><title>米金利が高止まり＝早期利上げ観測 - 時事エクイティ</title><link>https://news.google.com/c</link>
<pubDate>Fri, 25 Sep 2026 15:17:00 GMT</pubDate><source url="https://equity.jiji.com">時事エクイティ</source></item>
</channel></rss>"""

# FRB の RSS は先頭に BOM があり、値が CDATA
FED_RSS = ("\ufeff<?xml version=\"1.0\" encoding=\"utf-8\" ?><rss version=\"2.0\"><channel>"
           "<item><title>Federal Reserve issues FOMC statement</title>"
           "<link><![CDATA[https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm]]></link>"
           "<pubDate><![CDATA[Wed, 16 Sep 2026 18:00:00 GMT]]></pubDate></item></channel></rss>").encode("utf-8")

BOJ_RSS = """<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel>
<item><title>共通担保資金供給オペレーションの運用について</title><pubDate>Fri, 25 Sep 2026 17:00:00 +0900</pubDate>
<link>http://www.boj.or.jp/a.pdf</link></item>
<item><title>【記者会見】植田総裁（9月18日分）</title><pubDate>Thu, 24 Sep 2026 15:20:00 +0900</pubDate>
<link>http://www.boj.or.jp/b.htm</link></item>
<item><title>【対談】現代美術家 vs 審議委員（広報誌「にちぎん」）</title><pubDate>Fri, 25 Sep 2026 14:00:00 +0900</pubDate>
<link>http://www.boj.or.jp/c.htm</link></item>
</channel></rss>"""

YAHOO_LIST_HTML = """<ul>
<li><a href="/news/detail/aaa">〔NY外為〕円急伸、一時156円台＝再度の日米協調介入警戒（25日） 9/26 時事通信</a></li>
<li><a href="/news/detail/bbb">「検証！ハイライト銘柄」山一電機：２４日に急伸 8:56 ウエルスアドバイザー</a></li>
<li><a href="/news/detail/ccc">二面性持つ今年のスイスフラン【フィスコ・コラム】 9:00 フィスコ</a></li>
<li><a href="/news/detail/ddd">時事通信の報道を受けて 9:10 サーチナ</a></li>
</ul>"""

JIJI_PAYWALL_HTML = """<html><body><article>
<h1>〔米国金融証券週報・展望〕住宅ローン金利7％超え＝不動産市場のさらなる逆風に</h1>
<p>【ニューヨーク時事＝岩崎万季】ニューヨーク金融市場は波乱に満ちた1週間となった。...</p>
<p>続きをお読みいただくには、VIP倶楽部の登録が必要です。</p>
<p>初月無料＋1,500円相当プレゼント</p>
</article></body></html>"""

WA_HTML = """<html><body><article>
<h1>「検証！ハイライト銘柄」山一電機：２４日に急伸し１カ月ぶり高値</h1>
<time>8:56</time>
<p>半導体検査用ソケットの山一電機<6941>が２４日に大幅続伸し、１カ月ぶりの水準を回復した。</p>
<p>提供：ウエルスアドバイザー社</p>
<p>ウエルスアドバイザー</p>
<div>関連ニュース</div>
</article></body></html>"""


def test_press():
    print("\n報道・公的機関（株探以外）")
    jst = timezone(timedelta(hours=9))
    now = datetime(2026, 9, 26, 7, 10, tzinfo=jst)    # 土曜の朝
    feed = {"key": "reuters", "label": "ロイター", "kind": "press", "max": 15, "hosts": ["jp.reuters.com"]}
    items = press_mod.parse_rss(GN_RSS)
    check("Google ニュース RSS を読める", len(items), 8)
    check("配信元ドメインを取れる", items[0]["source_host"], "jp.reuters.com")
    check("description の HTML は要約にしない", items[0]["summary"], "")
    rows = press_mod.select_feed_items(items, feed, now)
    check("媒体名の表記ゆれ（ロイター / jp.reuters.com）の両方を採り、株価ページ・スポーツ・論説・転載・古い記事を落とす",
          [r["title"] for r in rows], ["ＮＹ外為市場＝円急伸", "東京株式市場・大引け＝5日続伸、半導体関連株けん引"])
    check("時刻は JST", rows[0]["published"], "2026-09-26T04:21+09:00")

    jiji = {"key": "jiji", "label": "時事通信", "kind": "press", "max": 12, "hosts": ["equity.jiji.com", "sp.m.jiji.com"]}
    rows = press_mod.select_feed_items(press_mod.parse_rss(JIJI_RSS), jiji, now)
    check("時事: www.jiji.com のプレスリリース転載を落とし、速報記号・差替を外す",
          [r["title"] for r in rows], ["〔米株式〕ダウ４日ぶり反発、４７８ドル高", "米金利が高止まり＝早期利上げ観測"])

    fed = {"key": "fed", "label": "FRB", "kind": "official", "max": 6}
    fitems = press_mod.parse_rss(FED_RSS)
    check("BOM 付き・CDATA の RSS を読める", (len(fitems), fitems[0]["url"].endswith("/monetary20260916a.htm")), (1, True))
    check("公的機関の窓は 72 時間（10日前の発表は落とす）", press_mod.select_feed_items(fitems, fed, now), [])
    check("月曜は週末をまたぐので窓を 48 時間延ばす",
          press_mod.window_hours("press", datetime(2026, 9, 28, 7, 10, tzinfo=jst)), 78)

    boj = {"key": "boj", "label": "日本銀行", "kind": "official", "max": 8,
           "include": r"総裁|審議委員|決定会合", "exclude": r"にちぎん|【対談】"}
    rows = press_mod.select_feed_items(press_mod.parse_rss(BOJ_RSS), boj, now)
    check("日銀: 定例の事務連絡と広報誌を落とし、総裁会見を残す", [r["title"] for r in rows], ["【記者会見】植田総裁（9月18日分）"])

    providers = ("時事通信", "トレーダーズ・ウェブ", "ウエルスアドバイザー")
    lst = parse_yahoo_list(YAHOO_LIST_HTML, providers)
    check("Yahoo 一覧: 許可リストの配信元だけ（末尾で照合。見出し中の媒体名では拾わない）",
          [(r["provider"], r["time"]) for r in lst], [("時事通信", "9/26"), ("ウエルスアドバイザー", "8:56")])
    check("Yahoo 一覧: 見出しから日付を外す", lst[0]["title"], "〔NY外為〕円急伸、一時156円台＝再度の日米協調介入警戒（25日）")

    art = parse_yahoo_article(JIJI_PAYWALL_HTML, "u", provider="時事通信")
    check("有料記事は続きの案内の手前で切って partial", (art["body"].startswith("【ニューヨーク時事"), art.get("partial"),
                                                   "VIP" in art["body"]), (True, True, False))
    art = parse_yahoo_article(WA_HTML, "u", provider="ウエルスアドバイザー", source="ウエルスアドバイザー（旧モーニングスター）")
    check("末尾の「提供：〇〇社」と配信元名を落とす", art["body"].endswith("回復した。"), True)
    check("配信元の表示名", art["source"], "ウエルスアドバイザー（旧モーニングスター）（Yahoo!ファイナンス配信）")

    many = [{"title": f"〔東京株式〕記事{i}", "provider": "時事通信", "url": str(i)} for i in range(8)]
    many += [{"title": "香港大引け", "provider": "トレーダーズ・ウェブ", "url": "h"}]
    picked = press_mod.pick_articles(many, 4)
    check("1つの配信元に偏らない（上限は limit の半分）", sum(r["provider"] == "時事通信" for r in picked), 2)

    pr = {"headlines": [{"title": "日経平均が続伸", "source_key": "nikkei"}, {"title": "円相場が上昇", "source_key": "nhk"}],
          "official": [{"title": "総裁記者会見"}], "articles": [{"headline": "〔NY株式〕反発"}]}
    check("論調に数えるのは日本語の報道だけ（NHK・公的機関は数えない）", press_mod.tone_titles(pr), ["日経平均が続伸", "〔NY株式〕反発"])


def test_names():
    import tempfile
    from dashboard import names as N
    from dashboard.sources.yahoojp import stock_name_from_title

    print("\n[社名（日本語）]")
    check("銘柄ページの題名から社名だけ（(株) を外す）",
          stock_name_from_title("(株)アシックス【7936】：株価・株式情報（夜間PTS含む） - Yahoo!ファイナンス"), "アシックス")
    check("（株）が後ろ・全角英数は半角に", stock_name_from_title("ＳＷＣＣ（株）【5805】：株価"), "SWCC")
    check("英語の社名は日本語ではない", (N.has_japanese("ASICS Corporation"), N.has_japanese("スクウェア・エニックス")), (False, True))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "names.json")
        calls = []
        got = N.resolve(["999A", "998A"], fetch=lambda c: calls.append(c) or ("テスト社" if c == "999A" else None), path=path)
        check("手元に無い社名だけ取りに行き、取れたものを返す", (got, calls), ({"999A": "テスト社"}, ["999A", "998A"]))
        calls.clear()
        again = N.resolve(["999A"], fetch=lambda c: calls.append(c), path=path)
        check("取れた社名はキャッシュから（二度取りに行かない）", (again, calls), ({"999A": "テスト社"}, []))
        boom = N.resolve(["997A"], fetch=lambda c: 1 / 0, path=path)
        check("取得で例外が出ても止めない", boom, {})


# ==================== 業種の強弱（sectors.py） ====================
def test_sectors():
    from dashboard import sectors as X
    from dashboard import thermo_run as R

    print("\n[業種の強弱]")
    check("順位は同じ値を平均の順位に（pandas の rank(pct=True) と同じ）",
          X.pct_rank({"a": 1.0, "b": 1.0, "c": 2.0}), {"a": 0.5, "b": 0.5, "c": 1.0})
    check("4象限（強さ・勢いが真ん中より上か下か）",
          [X.quad_of(0.9, 0.9), X.quad_of(0.9, 0.1), X.quad_of(0.1, 0.9), X.quad_of(0.1, 0.1), X.quad_of(None, 0.5)],
          ["lead", "fade", "turn", "lag", None])
    check("段階（上位1/5 は 0.8 より上、下位1/5 は 0.2 以下）",
          [X.tier_of(0.85), X.tier_of(0.8), X.tier_of(0.5), X.tier_of(0.21), X.tier_of(0.2)],
          ["strong", "up", "mid", "down", "weak"])

    # 10業種 × 4銘柄。業種 i は毎日 (i−5)×0.05% ずつ動く（強さの順が決まっている）。銘柄ごとに小さな揺れ
    n, G = 320, 10
    dates = [f"d{t:03d}" for t in range(n)]
    closes, vols, groups = {}, {}, {}
    for g in range(G):
        for k in range(4):
            code = f"S{g}_{k}"
            px, arr = 1000.0, []
            for t in range(n):
                px *= 1 + (g - 5) * 0.0005 + (0.004 if (t + k + g) % 3 == 0 else -0.002)
                arr.append(px)
            closes[code] = arr
            vols[code] = [1000 + (500 if (g == 9 and t >= n - 5) else 0) for t in range(n)]
            groups[code] = f"G{g}"
    P = X.Panel(dates, closes, groups)
    check("200日線が引けるまでは順位を付けない", P.at(150), {})
    now = P.at(n - 1)
    order = sorted(now, key=lambda g: now[g]["rank"])
    check("強い順（毎日上げる業種が1位、毎日下げる業種が最下位）", (order[0], order[-1]), ("G9", "G0"))
    check("強さは4つの順位の平均（最も強い業種が最大、0〜1）",
          (max(now, key=lambda g: now[g]["score"]), all(0 < v["score"] <= 1 for v in now.values())), ("G9", True))

    # 先読みしない: t より後の値を変えても、t の強さは変わらない
    t = 260
    cl2 = {c: a[:t + 1] + [x * (3.0 if c.startswith("S0") else 0.3) for x in a[t + 1:]] for c, a in closes.items()}
    P2 = X.Panel(dates, cl2, groups)
    check("t 日目の強さは t 日目の引けまでで決まる",
          {g: round(v["score"], 6) for g, v in P2.at(t).items()} == {g: round(v["score"], 6) for g, v in P.at(t).items()}, True)
    check("先の成績は翌営業日の引けから20日（最後の21日は測れない）", (P.fwd("G9", n - 21), P.fwd("G9", n - 22) is not None),
          (None, True))

    # 分割の取りこぼし（1日で10倍）は捨て、その日の業種の値動きはほかの銘柄で測る
    cl3 = {c: list(a) for c, a in closes.items()}
    cl3["S3_0"] = [x * (10 if i >= 100 else 1) for i, x in enumerate(cl3["S3_0"])]
    P3 = X.Panel(dates, cl3, groups)
    others = [(cl3[f"S3_{k}"][100] / cl3[f"S3_{k}"][99] - 1) * 100 for k in (1, 2, 3)]
    got = (P3.idx["G3"][100] / P3.idx["G3"][99] - 1) * 100
    check("1日の騰落が50%を超える値は捨てる", round(got, 6), round(sum(others) / 3, 6))

    v = X.verify(P)
    check("検証: 強さの上位1/5 は下位1/5 より先の成績が良い（強さが続く作り）",
          (v["top"]["avg"] > v["bottom"]["avg"], v["beat"], v["dates"] > 5), (True, 100, True))
    events = [{"code": "S9_0", "dir": "up"}, {"code": "S9_1", "dir": "down"}, {"code": "S9_2", "dir": "flat"},
              {"code": "ZZZ", "dir": "up"}]
    b = X.board(dates, closes, vols, groups, {"S9_0": "電気機器", "S9_1": "電気機器", "S9_2": "機械"}, events, panel=P)
    top = b["rows"][0]
    check("一覧は強い順・1位は先行か一服", ([r["rank"] for r in b["rows"]] == list(range(1, G + 1)), top["g"], top["tier"]),
          (True, "G9", "strong"))
    check("業種の中の多数派の日経の業種と、米国の連想の業種名", (top["sector"], top["link"]), ("電気機器", "電気機器（半導体・電子部品）"))
    check("業績修正は上方・下方だけ数える", top["rev"], [1, 1])
    check("売買代金の増え方（直近5日だけ増えた業種は市場より多い）", top["flow"] > 1.0, True)
    check("構成銘柄は20日の騰落の順", [m["r20"] for m in top["members"]] == sorted([m["r20"] for m in top["members"]], reverse=True), True)
    check("軌跡は直近10営業日", len(top["trail"]), X.TRAIL)
    brief = R._strength_brief({**b, "verify": v})
    check("要約: 上位5と下位5（下位は弱い順）", (brief["top"][0]["g"], brief["bottom"][0]["g"], len(brief["top"])), ("G9", "G0", 5))
    check("日足がそろわなければ一覧を出さない", X.board(dates[:50], {c: a[:50] for c, a in closes.items()}, {}, groups), None)
def test_hold():
    from dashboard import hold as HD, swing as W
    from dashboard.thermo_run import _hd_brief, hold_block

    print("\n[保有株の判定（持つ・減らす・売り方）]")
    check("20日 +12% は勝ち → 持つ", HD.classify(12.0, 50.0), ("win", "hold"))
    check("20日 −25% は急落 → 今は売らない（2日RSI によらない）", HD.classify(-25.0, 5.0), ("crash", "wait"))
    check("20日 −8%・2日RSI 5 は負けの押した日 → 売り指値で", HD.classify(-8.0, 5.0), ("lose", "trim_limit"))
    check("20日 −8%・2日RSI 80 は負けの戻った日 → 戻った今", HD.classify(-8.0, 80.0), ("lose", "trim_now"))
    check("20日 −8%・2日RSI 40 は負け", HD.classify(-8.0, 40.0), ("lose", "trim"))
    check("20日 +3% は中立（形の差なし）", HD.classify(3.0, 40.0), ("flat", "flat"))
    check("境目: 20日 −5% ちょうどは負け、+10% ちょうどは勝ち",
          (HD.classify(-5.0, 40.0)[0], HD.classify(10.0, 40.0)[0]), ("lose", "win"))
    check("20日の騰落が無ければ判定しない", HD.classify(None, 40.0), (None, None))

    # 上昇が続いたあと、最後に下げる日足
    bars = _swing_bars(n=60, drift=0.004, dips=(-3.0, -3.0, -3.0, -3.0))
    ind = W.indicators(bars)
    row = HD.today_row(ind)
    i = len(bars) - 1
    want_sell = W.tick_up(sum(b[4] for b in bars[-4:]) / 4)
    check("売り指値 = 直近4日の終値の平均（呼値で切り上げ）", (row["sell"], HD.sell_limit(ind, i)), (want_sell, want_sell))
    check("判定は20日の騰落と2日RSI から", (row["cls"], row["v"]), HD.classify(HD.ret_n(ind, i), ind["rsi"][i]))
    check("最新の日付と終値", (row["asof"], row["c"]), (bars[-1][0], bars[-1][4]))
    check("日足が短すぎれば判定しない", HD.today_row(W.indicators(bars[:15])), None)
    check("thermo.json の hd は必要な項目だけ", sorted(_hd_brief(row)),
          sorted(["asof", "c", "cls", "v", "r20", "rsi2", "r5", "dd60", "atr", "sell"]))

    am = HD.intraday(1000, 960, 940, nk_prev=40000, nk_last=39600)
    check("前場: 窓・前日比・日経・日経との差", (am["gap"], am["move"], am["nk"], am["ex"]), (-4.0, -6.0, -1.0, -5.0))
    check("前場: 前日の終値が無ければ出さない", HD.intraday(None, 960, 940), None)

    # 検証: 先読みしない（最後の20日は数えない）・前半と後半に分ける
    flat = _swing_bars(n=120, drift=0.0)
    up = _swing_bars(n=120, drift=0.01)
    inds = {"1111": W.indicators(flat), "2222": W.indicators(up)}
    nk = {b[0]: 30000.0 for b in flat}
    v = HD.verify(inds, nk, min_tv=0)
    check("検証: 最後の20営業日は結果が無いので数えない（20日後の引けがある日まで）", v["to"], flat[-1 - HD.H][0])
    n_all = v["cls"]["all"][0]["n"] + v["cls"]["all"][1]["n"]
    check("検証: 形ごとの件数の合計 = 全体", sum(v["cls"][k][0]["n"] + v["cls"][k][1]["n"] for k in HD.CLASSES), n_all)
    check("検証: 日経が横ばいなら、上げ続けた銘柄（勝ち）の日経との差はプラス", v["cls"]["win"][1]["avg"] > 0, True)
    check("検証: 前半と後半を split の日で分ける", v["split"] > v["from"] and v["split"] <= v["to"], True)
    blk = hold_block(inds, nk, {"2222": HD.today_row(inds["2222"])})
    check("hold ブロック: 判定の一言・検証・研究の値", sorted(blk), sorted(["rules", "verdicts", "classes", "verify", "research", "exits", "pm"]))
    check("研究の値は前半・後半の2つずつ", all(len(r["avg"]) == 2 for r in blk["exits"]["rows"]), True)


# ==================== マクロ環境 ====================
TW_TABLE_HTML = """<html><body><article>
<h1>FF金利織り込み度＝日本時間30日現在（10月、12月開催分）</h1>
<time>13:15</time><span>配信</span>
<p>FF金利誘導目標レンジ 3.75－4.00％</p>
<p>■FOMC FF金利公表予定日 2026年10月28日</p>
<p>現在　1週間前　1カ月前</p>
<p>3.75-4.00%織り込み度　49.6　 44.6%　 52.7%</p>
<p>※数字は四捨五入をしているため、若干のずれが生じる場合がございます。</p>
<p>越後</p>
<div>関連ニュース</div>
</article></body></html>"""


def test_macro():
    print("\nマクロ環境")
    from dashboard import macro as M, build as B

    days = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)][:300]
    rate = {"d": days, "c": [4.0 + i * 0.002 for i in range(300)]}              # じり高（最後が最高）
    yen = {"d": days, "c": [150.0] * 299 + [151.5]}
    bm = {"us10y": rate, "usdjpy": yen}

    xs = M.series(bm, "us10y", {"us10y": {"last": 5.0, "asof": days[-1]}})
    check("クォートは同じ日付なら置き換える", (len(xs), xs[-1][1]), (300, 5.0))
    xs = M.series(bm, "us10y", {"us10y": {"last": 5.0, "asof": "2026-01-05"}})
    check("クォートの日付が新しければ足す", (len(xs), xs[-1]), (301, ("2026-01-05", 5.0)))

    spec = next(s for s in M.MACRO_VIEW if s["key"] == "us10y")
    r = M.view_row(spec, M.series(bm, "us10y"))
    check("金利の変化は bp（20日 +4bp）", (r["d1"], r["d20"]), (0, 4))
    check("約3年の最高は1年以上の系列だけ・最後が最高なら high", r["record"], "high")
    check("20日 +4bp は閾値 20bp に届かない（局面にしない）", r["trend"], None)
    short = M.view_row(spec, list(zip(days[:30], rate["c"][:30])))
    check("1年に満たない系列では最高と言わない", short["record"], None)
    fx = M.view_row(next(s for s in M.MACRO_VIEW if s["key"] == "usdjpy"), M.series(bm, "usdjpy"))
    check("為替は %（前日 +1.0%）", fx["d1"], 1.0)

    steep = {"us2y": {"d": days, "c": [3.5] * 279 + [3.5 + i * 0.03 for i in range(21)]},
             "us10y": {"d": days, "c": [4.0] * 279 + [4.0 + i * 0.01 for i in range(21)]}}
    rows = [M.view_row(s, M.series(steep, s["key"])) for s in M.MACRO_VIEW if s["key"] in steep]
    by = {x["key"]: x for x in rows}
    check("2年 +60bp・10年 +20bp", (by["us2y"]["d20"], by["us10y"]["d20"]), (60, 20))
    check("2年主導の上昇はベア・フラット化と読む", "ベア・フラット" in (M._curve_read(60, 20) or ""), True)
    check("差が小さければ曲線の読みを書かない", M._curve_read(30, 25), None)
    check("見出しは流れの大きい順（米金利上昇）", M.headline(rows).startswith("米金利上昇"), True)
    sp = {x["key"]: x for x in M.spreads(steep, None)}
    check("長短差の20日の変化（bp）", sp["us_curve"]["d20"], -40)

    press = {"official": [{"title": "外国為替平衡操作の実施状況", "source": "財務省", "published": "2026-09-30T19:00+09:00"},
                          {"title": "[JPX総研]J-LENS の機能追加", "source": "日本取引所グループ", "published": "2026-09-30T18:00+09:00"}],
             "headlines": [{"title": "円相場、反発 米長期金利が低下", "source": "日本経済新聞", "published": "2026-09-30T17:00+09:00"},
                           {"title": "ＮＹ外為市場＝ドル上昇、介入警戒", "source": "ロイター", "published": "2026-09-30T06:00+09:00"},
                           {"title": "日銀、利上げ観測", "source": "ブルームバーグ", "published": "2026-09-30T08:00+09:00"}],
             "wire": [{"title": "【指標】8月南アフリカPPI（前月比） -0.4％、予想 +0.2％ほか", "kind": "result", "published": "2026-09-30T18:30"},
                      {"title": "【指標】9月中国製造業PMI 50.1、予想 50.1", "kind": "result", "published": "2026-09-30T10:31"}]}
    items = M.source_items(press, {"headlines": [{"title": "円相場、反発 米長期金利が低下", "source": "株探"}]})
    check("同じ見出しは1本に数える（公的機関2・報道3・短信2、株探の重複1本は落とす）", len(items), 7)
    tps = {t["key"]: t for t in M.topics(items)}
    check("全角の「ＮＹ外為」も半角にそろえて為替に数える", tps["fx"]["n"], 3)
    check("「米長期金利」は国内金利（日銀）に数えない", [x["title"] for x in tps["boj"]["_all"]], ["日銀、利上げ観測"])
    check("公的機関の発表を先頭に", tps["fx"]["items"][0]["source"], "財務省")
    news = M._news_section(M.topics(items), press)
    check("公的機関の発表はマクロの話題に当たるものだけ（取引所の案内は挙げない）", "J-LENS" in "".join(news), False)
    check("指標は日米・中国・欧州を先に（新興国は後ろ）、末尾の「ほか」を重ねない",
          [x for x in news if x.startswith("発表された")][0],
          "発表された経済指標（トレーダーズ・ウェブの短信）は 9月中国製造業PMI 50.1、予想 50.1、8月南アフリカPPI（前月比） -0.4％、予想 +0.2％")

    view = M.build(steep, None, press, None)
    check("文章は金利・ニュースの論点（為替・商品の系列が無ければその節を出さない）",
          [x["title"] for x in view["commentary"]["sections"]], ["金利", "ニュースの論点"])
    check("話題の内部用の全件は出力に入れない", any("_all" in t for t in view["topics"]), False)

    jst = timezone(timedelta(hours=9))
    now = datetime(2026, 9, 30, 19, 55, tzinfo=jst)
    check("一覧の時刻: 当日の時:分", press_mod.list_time("19:50", now), "2026-09-30T19:50+09:00")
    check("一覧の時刻: いまより先の時:分は前日", press_mod.list_time("23:30", now), "2026-09-29T23:30+09:00")
    check("一覧の時刻: 月/日", press_mod.list_time("9/29", now), "2026-09-29")
    found = [{"title": "【指標】9月中国製造業PMI 50.1、予想 50.1", "provider": "トレーダーズ・ウェブ", "time": "10:31", "url": "a"},
             {"title": "【要人発言】経済財政相「認識の齟齬はない」", "provider": "トレーダーズ・ウェブ", "time": "18:53", "url": "b"},
             {"title": "【指標発表予定】20:00　米MBA住宅ローン申請指数", "provider": "トレーダーズ・ウェブ", "time": "19:45", "url": "c"},
             {"title": "【指標】古い指標", "provider": "トレーダーズ・ウェブ", "time": "9/20", "url": "d"},
             {"title": "〔東京外為〕ドル、156円台後半", "provider": "時事通信", "time": "17:11", "url": "e"}]
    wire = press_mod.pick_wire(found, now)
    check("短信: 型を分け、新しい順、鮮度の窓の外と他社・短信以外を落とす",
          [(w["kind"], w["url"]) for w in wire], [("schedule", "c"), ("remarks", "b"), ("result", "a")])

    art = parse_yahoo_article(TW_TABLE_HTML, "u", provider="トレーダーズ・ウェブ")
    check("トレーダーズ・ウェブの表は「配信」の次の行から読む（表の頭を落とさない）",
          art["body"].split("\n")[0], "FF金利誘導目標レンジ 3.75－4.00％")
    check("末尾の担当者の名字の行を落とす", art["body"].endswith("場合がございます。"), True)
    art = parse_yahoo_article(KABUTAN_ARTICLE_HTML, "u")
    check("株探の記事は従来どおり（現在値の引用ブロックを飛ばす）", art["body"].startswith("Cyntecを割当先"), True)

    before = {"press": {"wire": [{"title": "【指標】A", "published": "2026-09-30T10:00"}],
                        "macro_articles": [{"headline": "30日の欧米イベントスケジュール", "body": "…"}]}}
    now_p = {"press": {"wire": [], "macro_articles": []}}
    kept = B.carry_over(now_p, before)
    check("取り直しで短信とマクロの本文を減らさない", (len(now_p["press"]["wire"]), len(now_p["press"]["macro_articles"]),
                                              sorted(kept)), (1, 1, ["press.macro_articles+1", "press.wire+1"]))


if __name__ == "__main__":
    test_names()
    test_ranking()
    test_cnbc()
    test_tdnet()
    test_slot()
    test_rerun_carry_over()
    test_news()
    test_kabutan()
    test_ranking_layouts()
    test_themes_ledger_trend()
    test_thermo()
    test_swing()
    test_mid()
    test_hold()
    test_ohlc_cache()
    test_ledger_stats()
    test_press()
    test_macro()
    test_sectors()
    print()
    if failures:
        print(f"❌ {len(failures)} 件失敗: {', '.join(failures)}")
        sys.exit(1)
    print("✅ すべて通過")
