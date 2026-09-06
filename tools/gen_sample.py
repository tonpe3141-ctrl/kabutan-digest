"""ダッシュボードの見た目を確認するためのサンプルデータ生成。

外部ネットワークを使わずに UI を検証・調整するために使う。
  python3 tools/gen_sample.py [出力先ディレクトリ]
"""
import json
import os
import random
import sys

random.seed(20260906)

SECTORS = [
    "電気機器", "情報・通信業", "輸送用機器", "機械", "銀行業", "保険業",
    "証券商品先物", "医薬品", "小売業", "食料品", "化学", "その他金融業",
    "卸売業", "サービス業", "不動産業", "建設業", "鉄鋼", "非鉄金属",
    "海運業", "陸運業", "空運業", "電気・ガス業", "精密機器", "ガラス土石",
    "石油石炭製品", "鉱業", "繊維製品", "パルプ・紙", "ゴム製品", "金属製品",
    "その他製品", "水産・農林業", "倉庫運輸関連",
]

NAMES = [
    ("7203", "トヨタ自動車"), ("6758", "ソニーグループ"), ("8035", "東京エレクトロン"),
    ("6857", "アドバンテスト"), ("9984", "ソフトバンクグループ"), ("6501", "日立製作所"),
    ("8306", "三菱UFJフィナンシャル・グループ"), ("6098", "リクルートホールディングス"),
    ("4063", "信越化学工業"), ("9433", "KDDI"), ("6367", "ダイキン工業"),
    ("7974", "任天堂"), ("8058", "三菱商事"), ("4661", "オリエンタルランド"),
    ("6902", "デンソー"), ("7011", "三菱重工業"), ("5401", "日本製鉄"),
    ("9432", "日本電信電話"), ("4568", "第一三共"), ("8766", "東京海上ホールディングス"),
]


def _series(base, n=20, vol=0.012):
    out, v = [], base
    for _ in range(n):
        v *= 1 + random.gauss(0, vol)
        out.append(round(v, 2))
    return out


def _quote(key, label, last, pct, **extra):
    prev = last / (1 + pct / 100)
    return {"key": key, "label": label, "last": round(last, 2), "prev": round(prev, 2),
            "change": round(last - prev, 2), "change_pct": round(pct, 2),
            "asof": "2026-09-05", "series": _series(last), **extra}


def _rows(n, base_pct=0.0, spread=3.0):
    rows = []
    picks = random.sample(NAMES, min(n, len(NAMES)))
    while len(picks) < n:
        picks.append(random.choice(NAMES))
    for code, name in picks[:n]:
        pct = round(random.gauss(base_pct, spread), 2)
        price = round(random.uniform(800, 9000), 1)
        rows.append({"code": code, "name": name, "price": price,
                     "change": round(price * pct / 100, 1), "change_pct": pct,
                     "raw": [code, name, str(price), f"{pct}%"]})
    return rows


def _sector_rows():
    return [{"code": None, "name": s, "price": None, "change": None,
             "change_pct": round(random.gauss(0.35, 1.1), 2), "raw": [s]}
            for s in SECTORS]


def build():
    us = {
        "spx": _quote("spx", "S&P500", 6412.5, 0.62, group="index"),
        "ndq": _quote("ndq", "NASDAQ総合", 21388.0, 1.05, group="index"),
        "dji": _quote("dji", "NYダウ", 45120.0, 0.21, group="index"),
        "sox": _quote("sox", "SOX半導体", 5980.0, 2.31, group="index"),
        "rut": _quote("rut", "ラッセル2000", 2380.0, -0.18, group="index"),
        "vix": _quote("vix", "VIX恐怖指数", 14.2, -4.8, group="risk"),
    }
    macro = {
        "usdjpy": _quote("usdjpy", "ドル/円", 152.35, 0.44, digits=2),
        "eurjpy": _quote("eurjpy", "ユーロ/円", 165.10, 0.21, digits=2),
        "us10y": _quote("us10y", "米10年債", 4.182, 1.9, digits=3, unit="%"),
        "wti": _quote("wti", "WTI原油", 68.4, -1.35, digits=2),
        "gold": _quote("gold", "金", 3480.0, 0.35, digits=1),
    }
    macro["us10y"]["change"] = 0.078          # +7.8bp
    macro["us10y"]["prev"] = 4.104

    sectors_us = {
        k: _quote(k, lbl, random.uniform(40, 260), pct)
        for k, lbl, pct in [
            ("smh", "半導体", 2.65), ("xlk", "テクノロジー", 1.12), ("xlc", "通信サービス", 0.74),
            ("xly", "一般消費財", 0.35), ("xlf", "金融", 0.58), ("xli", "資本財", 0.11),
            ("xle", "エネルギー", -1.02), ("xlb", "素材", -0.24), ("xlv", "ヘルスケア", -0.41),
            ("xlp", "生活必需品", -0.33), ("xlu", "公益", -0.62), ("xlre", "不動産", -0.85),
        ]
    }

    sys.path.insert(0, os.getcwd())
    from dashboard import analyze

    drivers = analyze.build_drivers(us, macro, sectors_us)
    implied = analyze.implied_open(drivers, 42180.0, None)
    risk = analyze.risk_regime(us, macro)
    outlook = analyze.sector_outlook(drivers)

    watchlist = [
        {"code": "7203", "name": "トヨタ自動車", "sector": "輸送用機器", "price": 3120.0,
         "change": 41.0, "change_pct": 1.33, "tags": ["売買代金 6位"], "mentions": ["昼刊"]},
        {"code": "8035", "name": "東京エレクトロン", "sector": "電気機器", "price": 28450.0,
         "change": 890.0, "change_pct": 3.23, "tags": ["売買代金 2位", "上昇率 8位"], "mentions": []},
        {"code": "8306", "name": "三菱UFJ", "sector": "銀行業", "price": 2088.5,
         "change": -12.5, "change_pct": -0.59, "tags": [], "mentions": []},
    ]

    preopen = {
        "us": us, "macro": macro, "sectors_us": sectors_us, "futures": None,
        "implied_open": implied, "risk": risk, "sector_outlook": outlook,
        "carryover": {
            "prev_session": {"date": "2026-09-05",
                             "session_shift": {"verdict": "後場に買われ、大引けにかけて水準を切り上げた",
                                               "tone": "positive"}},
            "after_hours_kessan": _rows(12, 0.0, 4.0),
        },
        "watchlist": watchlist,
        "freshness": {"us_asof": "2026-09-05", "us_stale_days": 1},
    }

    sector_table = {"key": "sector", "label": "業種別騰落", "url": "", "headers": [], "ok": True,
                    "rows": _sector_rows()}
    zenba_indices = {
        "nikkei": {"label": "日経平均", "code": "0000", "close": 42461.2, "change": 281.2, "change_pct": 0.67},
        "topix": {"label": "TOPIX", "code": "0010", "close": 3042.8, "change": 12.4, "change_pct": 0.41},
        "growth": {"label": "グロース250", "code": "0516", "close": 742.1, "change": -3.2, "change_pct": -0.43},
    }
    breadth = analyze.market_breadth(sector_table)
    zenba = {
        "indices": zenba_indices,
        "tables": {
            "value": {"key": "value", "label": "売買代金", "url": "", "headers": [], "ok": True, "rows": _rows(15, 0.6, 2.2)},
            "gainer": {"key": "gainer", "label": "上昇率", "url": "", "headers": [], "ok": True, "rows": _rows(12, 12.0, 4.0)},
            "loser": {"key": "loser", "label": "下落率", "url": "", "headers": [], "ok": True, "rows": _rows(12, -11.0, 3.0)},
            "sector": sector_table,
            "kessan_intraday": {"key": "kessan_intraday", "label": "取引時間中の決算・修正", "url": "",
                                "headers": [], "ok": True, "rows": _rows(8, 1.0, 5.0)},
        },
        "breadth": breadth,
        "breadth_note": analyze.breadth_vs_index(breadth, 0.67),
        "ranking_delta": {"new": [{"code": "7011", "name": "三菱重工業", "rank": 4, "change_pct": 4.2}],
                          "dropped": [], "rank_up": [{"code": "6857", "name": "アドバンテスト", "rank": 3,
                                                      "prev_rank": 11, "jump": 8, "change_pct": 3.9}]},
        "streaks": [{"code": "8035", "name": "東京エレクトロン", "days": 5, "change_pct": 3.2}],
        "articles": [{"label": "昼刊", "title": "話題株ピックアップ【昼刊】：三菱重、アドテスト、トヨタ",
                      "published_at": "2026-09-06T12:31:00+09:00",
                      "body": "（サンプル本文）三菱重工業は大幅高。防衛関連の受注増が…\n\nアドバンテストは続伸。前日の米SOX指数上昇を好感し…",
                      "url": "https://kabutan.jp/news/marketnews/"}],
        "verify_open": analyze.verify_open(implied, 0.67),
        "watchlist": watchlist,
    }

    taibike_indices = {
        "nikkei": {"label": "日経平均", "code": "0000", "close": 42610.5, "change": 430.5, "change_pct": 1.02},
        "topix": {"label": "TOPIX", "code": "0010", "close": 3055.1, "change": 24.7, "change_pct": 0.82},
        "growth": {"label": "グロース250", "code": "0516", "close": 738.4, "change": -6.9, "change_pct": -0.93},
    }
    t_sector = {**sector_table, "rows": _sector_rows()}
    t_breadth = analyze.market_breadth(t_sector)
    taibike = {
        "indices": taibike_indices,
        "tables": {
            "value": {"key": "value", "label": "売買代金", "url": "", "headers": [], "ok": True, "rows": _rows(15, 0.9, 2.4)},
            "gainer": {"key": "gainer", "label": "上昇率", "url": "", "headers": [], "ok": True, "rows": _rows(12, 14.0, 4.0)},
            "loser": {"key": "loser", "label": "下落率", "url": "", "headers": [], "ok": True, "rows": _rows(12, -12.0, 3.0)},
            "sector": t_sector,
            "kessan_after": {"key": "kessan_after", "label": "取引終了後の決算・修正", "url": "",
                             "headers": [], "ok": True, "rows": _rows(18, 0.0, 3.0)},
        },
        "breadth": t_breadth,
        "breadth_note": analyze.breadth_vs_index(t_breadth, 1.02),
        "session_shift": analyze.session_shift(zenba_indices, taibike_indices),
        "ranking_delta": {"new": [{"code": "5401", "name": "日本製鉄", "rank": 9, "change_pct": 2.1}],
                          "dropped": [], "rank_up": []},
        "streaks": [{"code": "8035", "name": "東京エレクトロン", "days": 5, "change_pct": 4.1}],
        "articles": [
            {"label": "市況", "title": "明日の株式相場に向けて＝半導体主導の上昇が続くか",
             "published_at": "2026-09-06T17:35:00+09:00",
             "body": "（サンプル本文）きょうの東京株式市場では日経平均が3日続伸した。…",
             "url": "https://kabutan.jp/news/marketnews/"},
            {"label": "夕刊①", "title": "話題株ピックアップ【夕刊】（1）",
             "published_at": "2026-09-06T18:02:00+09:00",
             "body": "（サンプル本文）■ソニーG…\n■任天堂…", "url": "https://kabutan.jp/news/marketnews/"},
        ],
        "watchlist": watchlist,
    }

    return {
        "date": "2026-09-06",
        "generated_at": "2026-09-06T17:35:12+09:00",
        "available_slots": ["preopen", "zenba", "taibike"],
        "slots": {
            "preopen": {"updated_at": "2026-09-06T07:02:41+09:00", "data": preopen},
            "zenba":   {"updated_at": "2026-09-06T12:03:55+09:00", "data": zenba},
            "taibike": {"updated_at": "2026-09-06T17:35:12+09:00", "data": taibike},
        },
    }


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp/md-sample"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "latest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build(), f, ensure_ascii=False, indent=1)
    print(f"サンプルを書き出しました: {path}")
