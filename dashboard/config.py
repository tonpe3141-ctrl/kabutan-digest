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
        {"key": "value",  "label": "売買代金", "max_rows": 30,
         # tradingValueHigh が正しいスラッグ（tradingValue/turnover は 400 を返す）。
         # 出来高は代金の代替にならない（低位株が上位を占める）ので最後の手段。
         "urls": [{"url": f"{_Y}/tradingValueHigh?market=all&term=daily", "label": "売買代金"},
                  {"url": f"{_Y}/volume?market=all&term=daily",           "label": "出来高"}]},
        {"key": "gainer", "label": "上昇率", "max_rows": 20,
         "urls": [{"url": f"{_Y}/up?market=all&term=daily", "label": "上昇率"}]},
        {"key": "loser",  "label": "下落率", "max_rows": 20,
         "urls": [{"url": f"{_Y}/down?market=all&term=daily", "label": "下落率"}]},
        {"key": "ytd_high", "label": "年初来高値更新", "max_rows": 40, "layout": "ytd_high", "exclude_funds": True,
         "urls": [{"url": f"{_Y}/yearToDateHigh?market=all&term=daily", "label": "年初来高値更新"}]},
        {"key": "vol_surge", "label": "出来高急増", "max_rows": 30, "layout": "vol_surge", "exclude_funds": True,
         "urls": [{"url": f"{_Y}/volumeIncrease?market=all&term=daily", "label": "出来高急増"}]},
    ],
    "taibike": [
        {"key": "value",  "label": "売買代金", "max_rows": 30,
         # tradingValueHigh が正しいスラッグ（tradingValue/turnover は 400 を返す）。
         # 出来高は代金の代替にならない（低位株が上位を占める）ので最後の手段。
         "urls": [{"url": f"{_Y}/tradingValueHigh?market=all&term=daily", "label": "売買代金"},
                  {"url": f"{_Y}/volume?market=all&term=daily",           "label": "出来高"}]},
        {"key": "gainer", "label": "上昇率", "max_rows": 20,
         "urls": [{"url": f"{_Y}/up?market=all&term=daily", "label": "上昇率"}]},
        {"key": "loser",  "label": "下落率", "max_rows": 20,
         "urls": [{"url": f"{_Y}/down?market=all&term=daily", "label": "下落率"}]},
        {"key": "ytd_high", "label": "年初来高値更新", "max_rows": 40, "layout": "ytd_high", "exclude_funds": True,
         "urls": [{"url": f"{_Y}/yearToDateHigh?market=all&term=daily", "label": "年初来高値更新"}]},
        {"key": "vol_surge", "label": "出来高急増", "max_rows": 30, "layout": "vol_surge", "exclude_funds": True,
         "urls": [{"url": f"{_Y}/volumeIncrease?market=all&term=daily", "label": "出来高急増"}]},
    ],
    "preopen": [],
}

# 発掘台帳・テーマ辞書・週報
THEMES_PATH = "docs/data/themes.json"
LEDGER_PATH = "docs/data/ledger.json"
WEEKLY_PATH = "docs/data/weekly.json"
LEDGER_TRACK_DAYS = 20        # フラグから何営業日で追跡を終えるか
LEDGER_MIN_STREAK = 3         # 「資金流入の継続」とみなす連続ランクイン日数（下限）
LEDGER_MAX_STREAK = 6         # これを超える連続は常連（大型株）なので発掘の対象にしない

# 出力先
DOCS_DIR = "docs"
DATA_DIR = "docs/data"
HISTORY_DIR = "docs/data/history"
WATCHLIST_PATH = "docs/data/watchlist.json"
HISTORY_KEEP_DAYS = 120

# スパークラインは外部から履歴が取れないため、自分の履歴から積み上げる
SPARK_POINTS = 20

# ==================== 相場温度計（逆張りガード） ====================
# 詳しくは DESIGN.md 9章。日足は CNBC の bars API（Actions から取れることを実測済み）。
BARS_PATH = "docs/data/cache/bars.json"
PER_CACHE_PATH = "docs/data/cache/nikkei_per.json"
THERMO_PATH = "docs/data/thermo.json"
THERMO_TRACK_PATH = "docs/data/thermo_track.json"

MACRO_BARS_CALENDAR_DAYS = 1100    # マクロ系列は約3年（バックテストの母数）
STOCK_BARS_KEEP = 130              # 個別株は130営業日（60日騰落・75日線・120日高値に足りる）
STOCK_BARS_CALENDAR_DAYS = 200     # 初回・取り直しのときに取る暦日数

# 温度計に使うマクロ系列。foreign=True は東証の引け後に確定する系列（バックテストでは前日までを使う）
MACRO_BAR_SYMBOLS = [
    {"key": "nikkei", "symbol": ".N225",  "label": "日経平均",   "foreign": False},
    {"key": "topix",  "symbol": ".TOPX",  "label": "TOPIX",      "foreign": False},
    {"key": "nkvi",   "symbol": ".JNIV",  "label": "日経VI",     "foreign": False},
    {"key": "jp10y",  "symbol": "JP10Y",  "label": "日本10年債", "foreign": True},
    {"key": "spx",    "symbol": ".SPX",   "label": "S&P500",     "foreign": True},
    {"key": "sox",    "symbol": ".SOX",   "label": "SOX半導体",  "foreign": True},
    {"key": "vix",    "symbol": ".VIX",   "label": "VIX",        "foreign": True},
    {"key": "usdjpy", "symbol": "JPY=",   "label": "ドル/円",    "foreign": True},
    {"key": "us10y",  "symbol": "US10Y",  "label": "米10年債",   "foreign": True},
    {"key": "wti",    "symbol": "@CL.1",  "label": "WTI原油",    "foreign": True},
]

# 業種（日経225の業種区分）ごとのマクロ感応度。−1〜+1。
# 「この20日のマクロの動きが、その業種に追い風か向かい風か」を出すための公開係数。
# ドライバー: us10y / jp10y（金利上昇）, yen（円安）, oil（原油高）, sox（米半導体高）, spx（米株高）
SECTOR_MACRO_SENS = {
    "電気機器":   {"sox": 0.8, "yen": 0.5, "spx": 0.3, "us10y": -0.2},
    "精密機器":   {"sox": 0.4, "yen": 0.5, "spx": 0.3},
    "機械":       {"yen": 0.5, "spx": 0.4, "sox": 0.3},
    "自動車":     {"yen": 0.9, "spx": 0.3},
    "造船":       {"yen": 0.4, "spx": 0.3},
    "その他製造": {"yen": 0.3, "spx": 0.3},
    "化学":       {"yen": 0.3, "oil": -0.3, "sox": 0.2},
    "ゴム":       {"yen": 0.4, "oil": -0.5},
    "窯業":       {"sox": 0.3, "spx": 0.3},
    "鉄鋼":       {"yen": 0.3, "spx": 0.3, "jp10y": 0.2},
    "非鉄・金属": {"sox": 0.4, "spx": 0.4, "yen": 0.3},
    "銀行":       {"jp10y": 0.9, "us10y": 0.4, "spx": 0.2},
    "保険":       {"jp10y": 0.8, "us10y": 0.4},
    "証券":       {"spx": 0.6, "jp10y": 0.2},
    "その他金融": {"spx": 0.3, "jp10y": -0.2},
    "不動産":     {"jp10y": -0.8, "us10y": -0.3},
    "建設":       {"jp10y": -0.3},
    "鉄道・バス": {"jp10y": -0.3, "yen": 0.2},
    "陸運":       {"oil": -0.4},
    "空運":       {"oil": -0.8, "yen": 0.2},
    "海運":       {"yen": 0.5, "spx": 0.3, "oil": 0.2},
    "商社":       {"oil": 0.6, "yen": 0.4},
    "石油":       {"oil": 0.9},
    "鉱業":       {"oil": 0.9},
    "電力":       {"oil": -0.6, "jp10y": -0.3},
    "ガス":       {"oil": -0.5, "jp10y": -0.2},
    "通信":       {"jp10y": -0.3, "spx": 0.1},
    "サービス":   {"jp10y": -0.3, "spx": 0.3},
    "小売業":     {"yen": -0.2, "jp10y": -0.2},
    "食品":       {"yen": -0.4, "oil": -0.2},
    "医薬品":     {"yen": 0.4, "spx": 0.2},
    "水産":       {"yen": -0.3},
    "繊維":       {"yen": -0.2, "oil": -0.3},
    "パルプ・紙": {"oil": -0.5, "yen": -0.4},
}
