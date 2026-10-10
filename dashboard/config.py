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
NAMES_PATH = "docs/data/cache/names.json"    # 銘柄コード → 日本語の社名（CNBC の社名は英語なので。names.py）
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

# 短期の押し目買い（dashboard/swing.py、DESIGN.md 13章）。200日線と、それ以後の検証期間のために
# 個別株の四本値と出来高を約2年ぶん持つ。アプリは読まない（thermo.json に要約だけを載せる）。
OHLC_PATH = "docs/data/cache/ohlc.json"
STOCK_OHLC_KEEP = 500              # 営業日
STOCK_OHLC_CALENDAR_DAYS = 760     # 初回・取り直しのときに取る暦日数
SWING_TRACK_PATH = "docs/data/swing_track.json"   # アプリが出した注文と、その後の結果
MARGIN_PATH = "docs/data/margin.json"            # 銘柄ごとの信用残（週ごと。supply.py・sources/margin.py）
MARGIN_GIVE_UP = 5                             # 信用残のページに届かない応答がこれだけ続いたら、その日は打ち切る
MARGIN_FETCH_MAX = 60                          # 大引の1回で取りに行く銘柄の数の上限（週1回の更新なので、5営業日で約300銘柄が一巡。
                                               # 2026-10-10 の実測で、短時間に100回ほど続けて読むと届かなくなった）

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
    # 以下はマクロ環境（dashboard/macro.py）の時間軸と「約3年で最高」の判定に使う。温度計の軸には入れない
    {"key": "us2y",   "symbol": "US2Y",   "label": "米2年債",    "foreign": True},
    {"key": "us30y",  "symbol": "US30Y",  "label": "米30年債",   "foreign": True},
    {"key": "jp2y",   "symbol": "JP2Y",   "label": "日本2年債",  "foreign": True},
    {"key": "jp30y",  "symbol": "JP30Y",  "label": "日本30年債", "foreign": True},
    {"key": "gold",   "symbol": "@GC.1",  "label": "金",         "foreign": True},
    {"key": "copper", "symbol": "@HG.1",  "label": "銅",         "foreign": True},
]

# ==================== マクロ環境（dashboard/macro.py、DESIGN.md 20章） ====================
# 市況タブの「マクロ環境」に並べる系列。change は金利が bp、ほかは %。
# trend は「20営業日でこれだけ動いたら局面とみなす」公開の閾値（予測ではなく、いまの流れの大きさの目安）。
MACRO_VIEW = [
    {"key": "us10y",  "label": "米10年",   "group": "rate",  "unit": "%",  "digits": 3, "trend": 20, "family": "us_rate"},
    {"key": "us2y",   "label": "米2年",    "group": "rate",  "unit": "%",  "digits": 3, "trend": 20, "family": "us_rate"},
    {"key": "us30y",  "label": "米30年",   "group": "rate",  "unit": "%",  "digits": 3, "trend": 20, "family": "us_rate"},
    {"key": "jp10y",  "label": "日本10年", "group": "rate",  "unit": "%",  "digits": 3, "trend": 10, "family": "jp_rate"},
    {"key": "jp2y",   "label": "日本2年",  "group": "rate",  "unit": "%",  "digits": 3, "trend": 10, "family": "jp_rate"},
    {"key": "jp30y",  "label": "日本30年", "group": "rate",  "unit": "%",  "digits": 3, "trend": 15, "family": "jp_rate"},
    {"key": "usdjpy", "label": "ドル円",   "group": "fx",    "unit": "円", "digits": 2, "trend": 2.0, "family": "fx"},
    {"key": "wti",    "label": "WTI原油",  "group": "commo", "unit": "ドル", "digits": 2, "trend": 8.0, "family": "oil"},
    {"key": "gold",   "label": "金",       "group": "commo", "unit": "ドル", "digits": 1, "trend": 5.0, "family": "gold"},
    {"key": "copper", "label": "銅",       "group": "commo", "unit": "ドル", "digits": 3, "trend": 6.0, "family": "copper"},
]
MACRO_RANGE_DAYS = 250        # 「1年のレンジの位置」に使う営業日
MACRO_SPARK_DAYS = 60         # 画面の小さな折れ線

# ニュースの論点: 信頼できる情報源（press.* と株探の見出し）を、マクロの話題ごとに数える。
# 見出しは NFKC で半角にそろえてから照合する。1本の見出しが複数の話題に入ってよい。
# AI 生成の要約（news[]）は数えない（信頼性の基準に合わない）。
MACRO_TOPICS = [
    {"key": "boj",      "label": "日銀・国内金利",
     "pattern": r"日銀|日本銀行|植田|氷見野|審議委員|決定会合|短観|展望リポート|展望レポート|(?<!米)長期金利|(?<!米)国債|JGB|"
                r"新発10年|BOJ|Bank of Japan"},
    {"key": "fed",      "label": "FRB・米金利",
     "pattern": r"FRB|FOMC|パウエル|米連銀|連銀総裁|米金融政策|米(長期)?金利|米国債|米\d+年債|米利上げ|米利下げ|利下げ観測|"
                r"利上げ観測|FF金利|\bFed\b|Treasury|Treasuries|yields?\b|Powell|rate cut|rate hike"},
    {"key": "fx",       "label": "為替・介入",
     "pattern": r"円相場|円安|円高|ドル円|ドル・円|円買い|円売り|外為|為替|介入|外国為替平衡|\byen\b|dollar"},
    {"key": "energy",   "label": "原油・資源",
     "pattern": r"原油|WTI|OPEC|石油|ガソリン|LNG|天然ガス|銅価格|銅相場|金価格|金相場|金先物|貴金属|\boil\b|crude|gold\b|copper"},
    {"key": "geo",      "label": "中東・地政学",
     "pattern": r"イラン|イスラエル|中東|ガザ|ウクライナ|ロシア|台湾有事|北朝鮮|地政学|ホルムズ|サウジ|フーシ|"
                r"Iran|Israel|Ukraine|Russia|Middle East|Gaza"},
    {"key": "trade",    "label": "通商・関税",
     "pattern": r"関税|通商|貿易交渉|貿易休戦|輸出規制|制裁|tariff|trade (war|deal|truce)|sanction"},
    {"key": "policy",   "label": "財政・政治",
     "pattern": r"財政|補正予算|予算|税収|減税|給付|政権|首相|総裁選|選挙|内閣|国会|財務相|財務大臣|経済財政|諮問会議|経済対策|"
                r"トランプ|ホワイトハウス|米議会|Trump|White House|Congress|shutdown|debt ceiling"},
    {"key": "data",     "label": "景気・物価指標",
     "pattern": r"雇用統計|雇用者数|雇用報告|失業率|求人|JOLTS|CPI|消費者物価|物価|インフレ|PCE|GDP|PMI|景況|景気|小売売上|"
                r"鉱工業|機械受注|賃金|貿易統計|国際収支|消費者信頼感|ISM|ADP|値上げ|payrolls?|jobs report|inflation|"
                r"consumer confidence|retail sales|unemployment"},
    {"key": "china",    "label": "中国",
     "pattern": r"中国|人民元|上海|香港|本土市場|\bChina\b|Chinese|Beijing|yuan"},
    {"key": "flow",     "label": "需給（投資主体・先物・空売り）",
     "pattern": r"投資部門別|海外投資家|外国人投資家|海外勢|空売り|信用残|信用買い残|裁定残|先物主導|ETF売買|手口|CFTC"},
]

# トレーダーズ・ウェブ（DZH）の短信。表題そのものが事実（経済指標の結果・発表予定・要人の発言）なので、表題を材料にする。
# 本文を読む記事は、1日の予定・FF 金利の織り込み・国内外の指標の一覧・市場のまとめ（1つの型につき最新の1本）。
PRESS_WIRE_KINDS = [
    ("result",   r"^【指標】"),
    ("schedule", r"^【指標発表予定】"),
    ("remarks",  r"^【要人発言】|^【日銀議事要旨】|^【日銀】|の主な要人発言"),
]
PRESS_WIRE_LIMIT = 40
PRESS_MACRO_ARTICLES = [r"イベントスケジュール", r"FF金利織り込み", r"主な経済指標", r"マーケットダイジェスト",
                        r"の主な要人発言", r"NY為替見通し|ロンドン為替見通し"]
PRESS_YAHOO_PAGES = {"fx": 3}    # 為替のカテゴリは短信が多いので3ページ目まで見る（既定は2ページ）

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

# ==================== ニュースの情報源（株探以外） ====================
# 採用基準は「信頼性」。一次情報（中央銀行・官庁・取引所）、通信社・全国紙・公共放送、
# 市況の事実報道を業とする金融情報ベンダーだけを許可リストに載せる。
# コラム・論説が中心の媒体、まとめ・転載サイト、SNS は載せない。
# 配信元は取得時に必ず照合する（Yahoo は一覧の配信元表記、Google ニュースは見出し末尾の媒体名）。
# 実装は dashboard/sources/press.py。Actions から届くかは tools/probe_press.py で実測する。

# Yahoo!ファイナンスのニュース一覧に本文付きで配信されている他社（株探は kabutan_news.py が扱う）
# 時事通信の配信は有料会員向けで冒頭しか読めないことが多い。その場合は partial=True で渡す。
PRESS_YAHOO_PROVIDERS = {
    "時事通信":             "時事通信",
    "トレーダーズ・ウェブ": "トレーダーズ・ウェブ（DZH フィナンシャルリサーチ）",
    "ウエルスアドバイザー": "ウエルスアドバイザー（旧モーニングスター）",
}
PRESS_YAHOO_CATEGORIES = ("market", "stocks", "world", "fx")
# 本文を読む価値の高い記事の型（上ほど優先）。数表の羅列は後回し
PRESS_ARTICLE_PRIORITY = [
    r"〔東京株式|〔NY株式|〔ＮＹ株式|〔東京外為|〔NY外為|〔ＮＹ外為|〔債券|〔米国金融証券|〔金利",
    r"東証|日経平均|日本株|NY株|ＮＹ株|NYダウ|米国株|米株",
    r"ハイライト銘柄|注目銘柄|決算|上方修正|下方修正",
    r"為替|ドル|円相場|金利|原油",
]
PRESS_ARTICLE_LIMIT = 10          # 本文を取る本数
PRESS_BODY_MAX = 2500             # 1本あたりの本文の上限（文字）

_GN = "https://news.google.com/rss/search?hl=ja&gl=JP&ceid=JP:ja&q="
_MKT = ("(株価 OR 株式 OR 日経平均 OR 円相場 OR 円安 OR 円高 OR 日銀 OR 長期金利 OR 決算 OR "
        "米国株 OR 関税 OR 為替 OR 原油)")

# kind: press（報道・日本語）/ official（公的機関の一次情報）/ overseas（海外報道・英語）
# hosts: Google ニュースの各記事の配信元ドメイン（<source url>）がこれに一致したものだけを採る。
#        媒体名の表記は取得ごとに揺れる（「ロイター」「jp.reuters.com」）のでドメインで照合する。
#        時事は www.jiji.com にプレスリリースの転載が混ざるため、編集記事だけの
#        時事エクイティ（equity）とモバイル版（sp.m）に限る。
# include / exclude: 定例の事務連絡が多い公的機関は、相場に関係する表題だけに絞る
# tone: 温度計の「見出しの論調」に数えるか（日本語の報道だけ。公的機関と英語は数えない）
PRESS_FEEDS = [
    {"key": "reuters",   "label": "ロイター",       "kind": "press", "tone": True, "max": 15,
     "url": _GN + "site:jp.reuters.com " + _MKT + " when:2d", "hosts": ["jp.reuters.com"]},
    {"key": "bloomberg", "label": "ブルームバーグ", "kind": "press", "tone": True, "max": 15,
     "url": _GN + "site:bloomberg.com/jp when:2d", "hosts": ["www.bloomberg.com", "bloomberg.com"]},
    {"key": "nikkei",    "label": "日本経済新聞",   "kind": "press", "tone": True, "max": 15,
     "url": _GN + "site:nikkei.com " + _MKT + " when:2d", "hosts": ["www.nikkei.com"]},
    {"key": "jiji",      "label": "時事通信",       "kind": "press", "tone": True, "max": 12,
     "url": _GN + "site:jiji.com " + _MKT + " when:2d", "hosts": ["equity.jiji.com", "sp.m.jiji.com"]},
    {"key": "nhk",       "label": "NHK",            "kind": "press", "tone": False, "max": 12,
     "url": "https://www.nhk.or.jp/rss/news/cat5.xml",
     "include": r"株|円相場|円安|円高|日銀|金利|物価|景気|為替|決算|関税|財務|GDP|原油|賃金|輸出|輸入|貿易|"
                r"銀行|市場|利上げ|利下げ|介入|値上げ|業績|投資|半導体|経済対策|予算"},
    {"key": "boj",       "label": "日本銀行",       "kind": "official", "max": 8,
     "url": "https://www.boj.or.jp/rss/whatsnew.xml",
     "include": r"金融政策|決定会合|主な意見|議事要旨|総裁|副総裁|審議委員|講演|記者会見|短観|展望レポート|"
                r"経済・物価|地域経済報告|さくらレポート|国債買入|声明|公表文",
     "exclude": r"にちぎん|【対談】|広報誌"},
    {"key": "mof",       "label": "財務省",         "kind": "official", "max": 8,
     "url": "https://www.mof.go.jp/news.rss",
     "include": r"大臣|会見|会談|外国為替平衡操作|国際収支|貿易統計|法人企業統計|利付国債.*入札結果|G7|G20"},
    {"key": "jpx",       "label": "日本取引所グループ", "kind": "official", "max": 6,
     "url": "https://www.jpx.co.jp/rss/jpx-news.xml"},
    {"key": "fed",       "label": "FRB（米連邦準備制度理事会）", "kind": "official", "max": 6,
     "url": "https://www.federalreserve.gov/feeds/press_monetary.xml"},
    {"key": "cnbc_mkt",  "label": "CNBC Markets",   "kind": "overseas", "max": 10,
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20409666"},
    {"key": "cnbc_econ", "label": "CNBC Economy",   "kind": "overseas", "max": 8,
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"},
    {"key": "cnbc_earn", "label": "CNBC Earnings",  "kind": "overseas", "max": 8,
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135"},
]
# 報道の見出しから常に外すもの（Google ニュースの検索語は本文にも当たるため、スポーツ等が紛れる）
PRESS_EXCLUDE = (r"^(ゴルフ|テニス|サッカー|野球|大リーグ|ＭＬＢ|MLB|ＮＢＡ|NBA|ＮＦＬ|NFL|ＮＨＬ|Ｆ１|F1|ラグビー|"
                 r"陸上|競泳|水泳|五輪|相撲|ボクシング|フィギュア|スキー|バスケット|アイスホッケー|自転車|"
                 r"格闘技|競馬|モーター|卓球|バレー|柔道|体操)[＝=]|^画像・写真[：:]|^写真特集|"
                 r"^【コラム】|^コラム[：:]|^アクセスランキング$|^ランキング$")   # 論説と、記事ではないページ
# 見出しの鮮度（時間）。月曜は週末をまたぐので +48 時間。公的機関は発表が疎なので長め
PRESS_WINDOW_HOURS = {"press": 30, "overseas": 30, "official": 72}

# ==================== 決算から読む（dashboard/earnings.py。DESIGN.md 22章） ====================
# 決算・業績修正の開示から、会社の説明（TDnet の PDF）・株探の決算速報・類似銘柄を集め、LLM が業界の風向きと
# 類似銘柄への連想を読む。買う候補は出さない（買う候補は swing.py の2つのルールだけ）。
EARN_KINDS = ("業績予想の修正", "決算短信", "配当予想の修正")
EARN_READ_MAX = {"preopen": 25, "zenba": 10, "taibike": 25}   # 1回に読む会社の数（修正を先に）
EARN_PEERS_MAX = 6                                           # 1社に添える類似銘柄の数
EARN_TEXT = {"reason": 600, "overview": 600, "outlook": 350, "flash": 400}   # 会社の説明・決算速報の字数の上限
EARN_WIND_DAYS = 20       # 業種の風向きを数える営業日
EARN_WIND_MIN = 3         # 風向きとして出す、1業種で読んだ開示の最小件数
EARN_LOG_DAYS = 70        # earnings.json に残す記録の日数（暦日）
# 銘柄ごとのニュース欄で拾う配信元（許可リストの媒体だけ。株探は決算速報、他社は見出し）
EARN_PROVIDERS = ("株探ニュース", "時事通信", "トレーダーズ・ウェブ", "ウエルスアドバイザー")
