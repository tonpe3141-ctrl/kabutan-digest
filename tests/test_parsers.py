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
from dashboard.sources.tdnet import _classify, _normalize_code  # noqa: E402
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


if __name__ == "__main__":
    test_ranking()
    test_cnbc()
    test_tdnet()
    test_slot()
    print()
    if failures:
        print(f"❌ {len(failures)} 件失敗: {', '.join(failures)}")
        sys.exit(1)
    print("✅ すべて通過")
