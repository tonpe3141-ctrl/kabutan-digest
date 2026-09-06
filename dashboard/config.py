"""ダッシュボードの定数定義。

スロット（tab）は 3 つ:
  preopen … 07:00 JST 実行。米国市場を振り返り「今日の日本株の寄り」を組み立てる
  zenba   … 12:00 JST 実行。前場を振り返る
  taibike … 17:30 JST 実行。大引けまでを振り返る

データ源について:
  GitHub Actions のランナーからは株探・stooq・Yahoo Finance(米) がいずれも
  クラウドIPを理由に拒否される（実測済み）。そのため
    相場データ … CNBC のクォート API
    日本株の物色 … Yahoo!ファイナンス（日本）のランキング
    決算・開示 … TDnet（適時開示、一次情報）
  の3つで構成している。詳細は README を参照。
"""

SLOTS = ["preopen", "zenba", "taibike"]

SLOT_LABEL = {
    "preopen": "寄り前",
    "zenba":   "前場",
    "taibike": "大引",
}

# ==================== 相場データ（CNBC） ====================
US_INDICES = [
    {"key": "spx",  "symbol": ".SPX",  "label": "S&P500",      "group": "index"},
    {"key": "ndq",  "symbol": ".IXIC", "label": "NASDAQ総合",   "group": "index"},
    {"key": "dji",  "symbol": ".DJI",  "label": "NYダウ",       "group": "index"},
    {"key": "sox",  "symbol": ".SOX",  "label": "SOX半導体",    "group": "index"},
    {"key": "rut",  "symbol": ".RUT",  "label": "ラッセル2000", "group": "index"},
    {"key": "vix",  "symbol": ".VIX",  "label": "VIX恐怖指数",  "group": "risk"},
]

MACRO_SYMBOLS = [
    {"key": "usdjpy", "symbol": "JPY=",  "label": "ドル/円",  "group": "fx",    "digits": 2},
    {"key": "us10y",  "symbol": "US10Y", "label": "米10年債", "group": "rate",  "digits": 3, "unit": "%"},
    {"key": "us2y",   "symbol": "US2Y",  "label": "米2年債",  "group": "rate",  "digits": 3, "unit": "%"},
    {"key": "wti",    "symbol": "@CL.1", "label": "WTI原油",  "group": "commo", "digits": 2},
    {"key": "gold",   "symbol": "@GC.1", "label": "金",       "group": "commo", "digits": 1},
]

# 米国セクター ETF（セクターローテーションの観測に使う）
US_SECTOR_ETFS = [
    {"key": "smh",  "symbol": "SMH",  "label": "半導体"},
    {"key": "xlk",  "symbol": "XLK",  "label": "テクノロジー"},
    {"key": "xlc",  "symbol": "XLC",  "label": "通信サービス"},
    {"key": "xly",  "symbol": "XLY",  "label": "一般消費財"},
    {"key": "xlf",  "symbol": "XLF",  "label": "金融"},
    {"key": "xli",  "symbol": "XLI",  "label": "資本財"},
    {"key": "xle",  "symbol": "XLE",  "label": "エネルギー"},
    {"key": "xlb",  "symbol": "XLB",  "label": "素材"},
    {"key": "xlv",  "symbol": "XLV",  "label": "ヘルスケア"},
    {"key": "xlp",  "symbol": "XLP",  "label": "生活必需品"},
    {"key": "xlu",  "symbol": "XLU",  "label": "公益"},
    {"key": "xlre", "symbol": "XLRE", "label": "不動産"},
]

# 日本の指数。日経平均先物は CNBC に無いため、想定オープンはモデル推計になる。
JP_INDICES = [
    {"key": "nikkei", "symbol": ".N225",     "label": "日経平均"},
    {"key": "topix",  "symbol": ".TOPX",     "label": "TOPIX"},
    {"key": "jpx400", "symbol": ".JPXNK400", "label": "JPX日経400"},
]

# ==================== 米国 → 日本セクターの連想マップ ====================
# 各日本業種を、どの米国ドライバーの動きで説明するか。
# 値は重み。ドライバーは正規化済み（≒「1.0 で 1% 相当のインパクト」）。
#   *_pct : そのシンボルの騰落率(%)
#   us10y_bp : 米10年債利回りの前日差(bp) を 10 で割った値（10bp=1.0）
#   usdjpy_pct : ドル円の騰落率(%)。プラス＝円安
#   vix_pct : VIX の騰落率(%) を 10 で割った値
SECTOR_LINKS = {
    "電気機器（半導体・電子部品）": {"smh_pct": 0.75, "xlk_pct": 0.25, "usdjpy_pct": 0.3},
    "精密機器":                   {"smh_pct": 0.35, "xlk_pct": 0.25, "usdjpy_pct": 0.3},
    "情報・通信業":               {"xlk_pct": 0.5, "xlc_pct": 0.4},
    "輸送用機器（自動車）":        {"usdjpy_pct": 0.8, "xly_pct": 0.4, "us10y_bp": 0.1},
    "機械":                       {"xli_pct": 0.6, "usdjpy_pct": 0.35, "smh_pct": 0.2},
    "銀行業":                     {"us10y_bp": 0.7, "xlf_pct": 0.5},
    "保険業":                     {"us10y_bp": 0.55, "xlf_pct": 0.45},
    "証券・商品先物取引業":        {"spx_pct": 0.6, "xlf_pct": 0.35, "vix_pct": -0.4},
    "鉱業・石油・石炭製品":        {"wti_pct": 0.8, "xle_pct": 0.4},
    "卸売業（商社）":             {"wti_pct": 0.45, "xlb_pct": 0.35, "usdjpy_pct": 0.25},
    "鉄鋼・非鉄金属":             {"xlb_pct": 0.7, "wti_pct": 0.2},
    "海運業":                     {"xlb_pct": 0.4, "wti_pct": 0.3, "usdjpy_pct": 0.25},
    "医薬品":                     {"xlv_pct": 0.8},
    "不動産業":                   {"us10y_bp": -0.6, "xlre_pct": 0.5},
    "建設業":                     {"xli_pct": 0.4, "us10y_bp": -0.2},
    "電気・ガス業":               {"xlu_pct": 0.6, "wti_pct": -0.25},
    "食料品":                     {"xlp_pct": 0.7, "vix_pct": 0.15},
    "小売業":                     {"xly_pct": 0.5, "xlp_pct": 0.25},
    "陸運業":                     {"xlp_pct": 0.35, "vix_pct": 0.15},
}

# ==================== 日本株ランキング（Yahoo!ファイナンス） ====================
# URL は候補を順に試す。Yahoo 側の仕様変更で片方が落ちても止まらないようにする。
_Y = "https://finance.yahoo.co.jp/stocks/ranking"

RANKING_PAGES = {
    "zenba": [
        {"key": "value",  "label": "売買代金", "max_rows": 20,
         "urls": [f"{_Y}/tradingValue?market=all&term=daily",
                  f"{_Y}/turnover?market=all&term=daily",
                  f"{_Y}/volume?market=all&term=daily"]},
        {"key": "gainer", "label": "上昇率", "max_rows": 20,
         "urls": [f"{_Y}/up?market=all&term=daily"]},
        {"key": "loser",  "label": "下落率", "max_rows": 20,
         "urls": [f"{_Y}/down?market=all&term=daily"]},
    ],
    "taibike": [
        {"key": "value",  "label": "売買代金", "max_rows": 20,
         "urls": [f"{_Y}/tradingValue?market=all&term=daily",
                  f"{_Y}/turnover?market=all&term=daily",
                  f"{_Y}/volume?market=all&term=daily"]},
        {"key": "gainer", "label": "上昇率", "max_rows": 20,
         "urls": [f"{_Y}/up?market=all&term=daily"]},
        {"key": "loser",  "label": "下落率", "max_rows": 20,
         "urls": [f"{_Y}/down?market=all&term=daily"]},
    ],
    "preopen": [],
}

# 出力先
DOCS_DIR = "docs"
DATA_DIR = "docs/data"
HISTORY_DIR = "docs/data/history"
WATCHLIST_PATH = "docs/data/watchlist.json"
HISTORY_KEEP_DAYS = 60

# スパークラインは外部から履歴が取れないため、自分の履歴から積み上げる
SPARK_POINTS = 20
