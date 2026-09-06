"""ダッシュボードの定数定義。

スロット（tab）は 3 つ:
  preopen … 07:00 JST 実行。米国市場を振り返り「今日の日本株の寄り」を組み立てる
  zenba   … 12:00 JST 実行。前場を振り返る
  taibike … 17:30 JST 実行。大引けまでを振り返る
"""

SLOTS = ["preopen", "zenba", "taibike"]

SLOT_LABEL = {
    "preopen": "寄り前",
    "zenba":   "前場",
    "taibike": "大引",
}

# ==================== 米国・グローバル市場（stooq） ====================
# stooq のシンボル。日足 CSV を取得して直近終値と前日終値から騰落を出す。
US_INDICES = [
    {"key": "spx",  "symbol": "^spx", "label": "S&P500",      "group": "index"},
    {"key": "ndq",  "symbol": "^ndq", "label": "NASDAQ総合",   "group": "index"},
    {"key": "dji",  "symbol": "^dji", "label": "NYダウ",       "group": "index"},
    {"key": "sox",  "symbol": "^sox", "label": "SOX半導体",    "group": "index"},
    {"key": "rut",  "symbol": "^rut", "label": "ラッセル2000", "group": "index"},
    {"key": "vix",  "symbol": "^vix", "label": "VIX恐怖指数",  "group": "risk", "invert": True},
]

MACRO_SYMBOLS = [
    {"key": "usdjpy", "symbol": "usdjpy",  "label": "ドル/円",   "group": "fx",     "digits": 2},
    {"key": "eurjpy", "symbol": "eurjpy",  "label": "ユーロ/円", "group": "fx",     "digits": 2},
    {"key": "us10y",  "symbol": "10usy.b", "label": "米10年債",  "group": "rate",   "digits": 3, "unit": "%"},
    {"key": "wti",    "symbol": "cl.f",    "label": "WTI原油",   "group": "commo",  "digits": 2},
    {"key": "gold",   "symbol": "gc.f",    "label": "金",        "group": "commo",  "digits": 1},
]

# 日経平均先物。stooq 上の呼称が変わることがあるので候補を順に試す。
NIKKEI_FUTURES_CANDIDATES = ["nkd.f", "nk.f", "^nkx"]

# 米国セクター ETF（セクターローテーションの観測に使う）
US_SECTOR_ETFS = [
    {"key": "smh",  "symbol": "smh.us",  "label": "半導体"},
    {"key": "xlk",  "symbol": "xlk.us",  "label": "テクノロジー"},
    {"key": "xlc",  "symbol": "xlc.us",  "label": "通信サービス"},
    {"key": "xly",  "symbol": "xly.us",  "label": "一般消費財"},
    {"key": "xlf",  "symbol": "xlf.us",  "label": "金融"},
    {"key": "xli",  "symbol": "xli.us",  "label": "資本財"},
    {"key": "xle",  "symbol": "xle.us",  "label": "エネルギー"},
    {"key": "xlb",  "symbol": "xlb.us",  "label": "素材"},
    {"key": "xlv",  "symbol": "xlv.us",  "label": "ヘルスケア"},
    {"key": "xlp",  "symbol": "xlp.us",  "label": "生活必需品"},
    {"key": "xlu",  "symbol": "xlu.us",  "label": "公益"},
    {"key": "xlre", "symbol": "xlre.us", "label": "不動産"},
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

# ==================== 株探 ====================
KABUTAN = "https://kabutan.jp"
NEWS_ARTICLE_URL = "https://kabutan.jp/news/marketnews/?b=n{date}{num:04d}"

# 指数（株探の擬似コード）
JP_INDICES = [
    {"key": "nikkei", "code": "0000", "label": "日経平均"},
    {"key": "topix",  "code": "0010", "label": "TOPIX"},
    {"key": "growth", "code": "0516", "label": "グロース250"},
]

# 記事番号スキャンの範囲
SCAN_START = 300
SCAN_END = 1600

# スロットごとに探す記事
ARTICLE_PATTERNS = {
    "zenba": [
        ("昼刊", "話題株ピックアップ【昼刊】"),
    ],
    "taibike": [
        ("昼刊",              "話題株ピックアップ【昼刊】"),
        ("市況",              "株式相場に向けて"),
        ("イチオシ決算",       "イチオシ決算"),
        ("夕刊①",            "話題株ピックアップ【夕刊】（1）"),
        ("夕刊②",            "話題株ピックアップ【夕刊】（2）"),
        ("夕刊③",            "話題株ピックアップ【夕刊】（3）"),
        ("レーティング最上位", "レーティング日報【最上位を継続】"),
        ("レーティング新規",   "レーティング日報【新規格付け】"),
        ("レーティング弱気",   "レーティング日報【弱気継続】"),
    ],
    "preopen": [],
}

# スロットごとに取るランキング／注意報ページ
RANKING_PAGES = {
    "zenba": [
        {"key": "value",   "label": "売買代金",     "url": "https://kabutan.jp/warning/trading_value_ranking", "max_rows": 20},
        {"key": "gainer",  "label": "上昇率",       "url": "https://kabutan.jp/warning/?mode=2_1", "max_rows": 20},
        {"key": "loser",   "label": "下落率",       "url": "https://kabutan.jp/warning/?mode=2_2", "max_rows": 20},
        {"key": "sector",  "label": "業種別騰落",   "url": "https://kabutan.jp/warning/?mode=9_1", "max_rows": 40},
        {"key": "kessan_intraday", "label": "取引時間中の決算・修正", "url": "https://kabutan.jp/warning/?mode=4_2", "max_rows": 60},
    ],
    "taibike": [
        {"key": "value",   "label": "売買代金",     "url": "https://kabutan.jp/warning/trading_value_ranking", "max_rows": 20},
        {"key": "gainer",  "label": "上昇率",       "url": "https://kabutan.jp/warning/?mode=2_1", "max_rows": 20},
        {"key": "loser",   "label": "下落率",       "url": "https://kabutan.jp/warning/?mode=2_2", "max_rows": 20},
        {"key": "sector",  "label": "業種別騰落",   "url": "https://kabutan.jp/warning/?mode=9_1", "max_rows": 40},
        {"key": "kessan_intraday", "label": "取引時間中の決算・修正", "url": "https://kabutan.jp/warning/?mode=4_2", "max_rows": 60},
        {"key": "kessan_after",    "label": "取引終了後の決算・修正", "url": "https://kabutan.jp/warning/?mode=4_3", "max_rows": 120},
    ],
    "preopen": [],
}

# 出力先
DOCS_DIR = "docs"
DATA_DIR = "docs/data"
HISTORY_DIR = "docs/data/history"
WATCHLIST_PATH = "docs/data/watchlist.json"
HISTORY_KEEP_DAYS = 60
