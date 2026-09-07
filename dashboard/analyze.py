"""生データから「判断に使える形」へ変換する層。

ここが本ダッシュボードの中心。単に数値を並べるのではなく、
  - 米国市場 → 今日の日本株の寄りへの含意
  - 前場 → 後場で何が変わったか
  - 昨日 → 今日で資金の向き先がどう入れ替わったか
  - 寄り前に立てた想定は当たったのか
という「差分」と「含意」を出す。
"""
from .config import SECTOR_LINKS

# 想定オープンの簡易推計モデル。
# 日経平均の対米ベータと為替感応度を、説明可能な固定係数で表現する。
# （回帰で毎回係数を動かすとブラックボックス化するので、あえて固定・公開値）
OPEN_MODEL = {"spx_pct": 0.55, "sox_pct": 0.25, "usdjpy_pct": 0.35}

DRIVER_LABEL = {
    "spx_pct": "S&P500", "ndq_pct": "NASDAQ", "sox_pct": "SOX半導体",
    "rut_pct": "ラッセル2000", "vix_pct": "VIX",
    "smh_pct": "米半導体ETF", "xlk_pct": "米テック", "xlc_pct": "米通信",
    "xly_pct": "米一般消費財", "xlf_pct": "米金融", "xli_pct": "米資本財",
    "xle_pct": "米エネルギー", "xlb_pct": "米素材", "xlv_pct": "米ヘルスケア",
    "xlp_pct": "米生活必需品", "xlu_pct": "米公益", "xlre_pct": "米不動産",
    "us10y_bp": "米10年金利", "usdjpy_pct": "ドル円", "wti_pct": "WTI原油",
    "gold_pct": "金",
}


def driver_display(driver: str, raw: float) -> str:
    """ドライバーの生値を、その指標本来の単位で表示用に整形する。
    正規化の都合で圧縮している指標（金利 bp・VIX）は元の単位に戻す。"""
    if driver == "us10y_bp":
        return f"{raw * 10:+.1f}bp"
    if driver == "vix_pct":
        return f"{raw * 10:+.2f}%"
    return f"{raw:+.2f}%"


# ==================== ドライバーの正規化 ====================
def build_drivers(us: dict, macro: dict, sectors: dict) -> dict:
    """各データ源から「1.0 ≒ 1% 相当」に正規化したドライバー辞書を作る。"""
    d: dict[str, float] = {}
    for key, item in {**us, **sectors}.items():
        if item.get("change_pct") is not None:
            d[f"{key}_pct"] = item["change_pct"]

    for key, item in macro.items():
        if item.get("change_pct") is None:
            continue
        if key == "us10y":
            # 利回りは「率の変化率」ではなく bp 差で見る。10bp を 1.0 とする
            if item.get("change") is not None:
                d["us10y_bp"] = item["change"] * 100 / 10
        else:
            d[f"{key}_pct"] = item["change_pct"]

    # VIX は水準変化が大きすぎるので 1/10 に圧縮
    if "vix_pct" in d:
        d["vix_pct"] = d["vix_pct"] / 10
    return d


# ==================== 想定オープン ====================
def implied_open(drivers: dict, jp_prev_close: float | None,
                 futures: dict | None) -> dict | None:
    """今日の日経平均の寄りをどう見るか。

    先物が取れていればそれを最優先（実勢）。取れなければ公開係数モデルで推計する。
    どちらを使ったかは必ず method に残し、UI 側でも明示する。
    """
    if futures and jp_prev_close and futures.get("last"):
        gap = futures["last"] - jp_prev_close
        return {
            "method": "futures",
            "method_label": "日経平均先物ベース",
            "source": futures.get("symbol"),
            "futures_last": futures["last"],
            "prev_close": jp_prev_close,
            "gap": gap,
            "gap_pct": gap / jp_prev_close * 100,
            "asof": futures.get("asof"),
        }

    parts = {k: drivers[k] * w for k, w in OPEN_MODEL.items() if k in drivers}
    if not parts:
        return None
    gap_pct = sum(parts.values())
    return {
        "method": "model",
        "method_label": "簡易推計（米株×為替）",
        "formula": " + ".join(
            f"{w:g}×{DRIVER_LABEL.get(k, k)}" for k, w in OPEN_MODEL.items()
        ),
        "contributions": [
            {"driver": DRIVER_LABEL.get(k, k), "value": round(v, 3),
             "display": driver_display(k, drivers[k])}
            for k, v in sorted(parts.items(), key=lambda x: -abs(x[1]))
        ],
        "prev_close": jp_prev_close,
        "gap": (gap_pct / 100 * jp_prev_close) if jp_prev_close else None,
        "gap_pct": gap_pct,
    }


# ==================== リスク環境 ====================
def risk_regime(us: dict, macro: dict) -> dict:
    """リスクオン／オフをスコア化し、根拠を文章で残す。"""
    score = 0.0
    reasons = []

    vix = us.get("vix")
    if vix and vix.get("last") is not None:
        lvl = vix["last"]
        if lvl < 15:
            score += 1.5; reasons.append(f"VIX {lvl:.1f} と低位で、リスク許容度は高い")
        elif lvl < 20:
            score += 0.5; reasons.append(f"VIX {lvl:.1f} は平常域")
        elif lvl < 28:
            score -= 1.0; reasons.append(f"VIX {lvl:.1f} と警戒域に上昇")
        else:
            score -= 2.0; reasons.append(f"VIX {lvl:.1f} と高水準。リスク回避が優勢")

    spx = us.get("spx")
    if spx and spx.get("change_pct") is not None:
        p = spx["change_pct"]
        score += max(-2.0, min(2.0, p))
        reasons.append(f"S&P500 は {p:+.2f}%")

    rut, sox = us.get("rut"), us.get("sox")
    if rut and sox and rut.get("change_pct") is not None and sox.get("change_pct") is not None:
        if sox["change_pct"] - rut["change_pct"] > 1.0:
            reasons.append("値がさ半導体に資金が集中し、中小型は取り残されている")
        elif rut["change_pct"] - sox["change_pct"] > 1.0:
            reasons.append("中小型が優位で、物色の裾野が広い")

    usdjpy = macro.get("usdjpy")
    if usdjpy and usdjpy.get("change_pct") is not None:
        p = usdjpy["change_pct"]
        if abs(p) >= 0.4:
            reasons.append(
                f"ドル円は{'円安' if p > 0 else '円高'}方向に {abs(p):.2f}% 振れ、"
                f"輸出株の{'追い風' if p > 0 else '重し'}"
            )
            score += 0.4 if p > 0 else -0.4

    us10y = macro.get("us10y")
    if us10y and us10y.get("change") is not None:
        bp = us10y["change"] * 100
        if abs(bp) >= 5:
            reasons.append(
                f"米10年金利は {bp:+.0f}bp の{'上昇' if bp > 0 else '低下'}"
                f"（{'銀行に追い風／グロースに逆風' if bp > 0 else 'グロースに追い風／銀行に逆風'}）"
            )

    if score >= 1.5:
        label, tone = "リスクオン", "positive"
    elif score >= 0.3:
        label, tone = "やや強気", "positive"
    elif score > -0.3:
        label, tone = "中立", "neutral"
    elif score > -1.5:
        label, tone = "やや弱気", "negative"
    else:
        label, tone = "リスクオフ", "negative"

    return {"score": round(score, 2), "label": label, "tone": tone, "reasons": reasons}


# ==================== 米国 → 日本セクター ====================
def sector_outlook(drivers: dict, top_n: int = 6) -> dict:
    """米国の値動きから、今日追い風／向かい風になりそうな日本の業種を並べる。"""
    out = []
    for jp_sector, links in SECTOR_LINKS.items():
        contribs, covered, total_w = [], 0.0, 0.0
        for driver, weight in links.items():
            total_w += abs(weight)
            if driver not in drivers:
                continue
            covered += abs(weight)
            contribs.append({
                "driver": DRIVER_LABEL.get(driver, driver),
                "value": round(drivers[driver] * weight, 3),
                "raw": round(drivers[driver], 3),
                "display": driver_display(driver, drivers[driver]),
            })
        if not contribs or total_w == 0 or covered / total_w < 0.5:
            continue  # 根拠データが半分も揃わない業種は出さない
        score = sum(c["value"] for c in contribs)
        contribs.sort(key=lambda c: -abs(c["value"]))
        out.append({
            "sector": jp_sector,
            "score": round(score, 3),
            "coverage": round(covered / total_w, 2),
            "drivers": contribs[:3],
        })

    out.sort(key=lambda s: -s["score"])
    tail = out[-top_n:] if len(out) > top_n else []
    return {"tailwind": out[:top_n], "headwind": list(reversed(tail)), "all": out}


# ==================== 日経225の構成銘柄から見る中身 ====================
def constituent_breadth(rows: list[dict] | None) -> dict | None:
    """日経225の何銘柄が上がったか。指数の数字だけでは見えない広がりを測る。"""
    vals = [r["change_pct"] for r in (rows or []) if r.get("change_pct") is not None]
    if len(vals) < 50:
        return None
    up = sum(1 for v in vals if v > 0)
    down = sum(1 for v in vals if v < 0)
    flat = len(vals) - up - down
    ratio = up / len(vals) * 100
    if ratio >= 75:
        comment = "採用銘柄の4分の3以上が上昇。広く買われている"
    elif ratio >= 55:
        comment = "上昇銘柄が優勢"
    elif ratio > 45:
        comment = "上昇と下落がほぼ拮抗。物色は選別的"
    elif ratio > 25:
        comment = "下落銘柄が優勢"
    else:
        comment = "採用銘柄の4分の3以上が下落。売りが広い"
    return {"up": up, "down": down, "flat": flat, "total": len(vals),
            "up_ratio": round(ratio, 1), "comment": comment}


def sector_performance(rows: list[dict] | None, min_count: int = 2) -> list[dict]:
    """業種ごとの平均騰落率。日経の業種区分で構成銘柄を束ねて単純平均する。

    東証33業種の指数そのものはクラウドから取得できないため、
    日経225採用銘柄を業種区分でまとめた代替指標として出す。
    時価総額加重ではないので、指数の騰落率とは一致しない。
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows or []:
        sector = r.get("sector")
        if not sector or r.get("change_pct") is None:
            continue
        buckets.setdefault(sector, []).append(r)

    out = []
    for sector, items in buckets.items():
        if len(items) < min_count:
            continue
        vals = [i["change_pct"] for i in items]
        best = max(items, key=lambda i: i["change_pct"])
        worst = min(items, key=lambda i: i["change_pct"])
        out.append({
            "sector": sector,
            "avg_pct": round(sum(vals) / len(vals), 2),
            "count": len(items),
            "up": sum(1 for v in vals if v > 0),
            "down": sum(1 for v in vals if v < 0),
            "best": {"code": best["code"], "name": best.get("name"),
                     "change_pct": best["change_pct"]},
            "worst": {"code": worst["code"], "name": worst.get("name"),
                      "change_pct": worst["change_pct"]},
        })
    out.sort(key=lambda s: -s["avg_pct"])
    return out


# ==================== 市場の広がり ====================
def index_divergence(indices: dict | None) -> dict | None:
    """日経平均と TOPIX のズレから「上げの中身」を読む。

    33業種別の騰落データがクラウドから取れないため、breadth の代わりに
    採用銘柄数と加重方式の違う 2 指数の差で物色の広がりを測る。
    日経平均は値がさ株の影響が大きく、TOPIX は時価総額加重で全銘柄が対象。
    """
    if not indices:
        return None
    nk = (indices.get("nikkei") or {}).get("change_pct")
    tp = (indices.get("topix") or {}).get("change_pct")
    if nk is None or tp is None:
        return None

    gap = nk - tp
    if gap > 0.4:
        label, tone = "値がさ主導", "warn"
        comment = ("日経平均が TOPIX を大きく上回った。指数寄与度の高い一部の値がさ株が"
                   "押し上げており、市場全体は見た目ほど強くない")
    elif gap < -0.4:
        label, tone = "広い物色", "neutral"
        comment = ("TOPIX が日経平均を上回った。値がさ株以外にも買いが広がっており、"
                   "内需・バリュー中心の相場")
    else:
        label, tone = "素直", "neutral"
        comment = "日経平均と TOPIX がほぼ揃って動いており、物色に偏りは小さい"

    return {"nikkei_pct": round(nk, 2), "topix_pct": round(tp, 2),
            "gap": round(gap, 2), "label": label, "tone": tone, "comment": comment}


def disclosure_summary(rows: list[dict] | None) -> dict | None:
    """適時開示の内訳。決算シーズンかどうか、修正が多い日かが一目で分かる。"""
    if not rows:
        return None
    counts: dict[str, int] = {}
    for r in rows:
        cat = r.get("category") or "その他"
        counts[cat] = counts.get(cat, 0) + 1
    items = sorted(counts.items(), key=lambda kv: -kv[1])

    revisions = counts.get("業績予想の修正", 0)
    if revisions >= 15:
        headline = f"業績予想の修正が {revisions} 件と多い。決算シーズンのピーク圏"
    elif revisions >= 5:
        headline = f"業績予想の修正が {revisions} 件。個別に材料が出ている"
    else:
        headline = f"開示 {sum(counts.values())} 件。大きな業績修正は少ない"

    return {"total": sum(counts.values()),
            "items": [{"label": k, "count": v} for k, v in items],
            "headline": headline}


# ==================== 前場 → 後場 ====================
def session_shift(zenba_indices: dict | None, taibike_indices: dict | None) -> dict | None:
    """前場終値と大引けを比べ、後場に買われたのか売られたのかを出す。"""
    if not zenba_indices or not taibike_indices:
        return None
    out = []
    for key in ("nikkei", "topix", "growth"):
        z, t = zenba_indices.get(key), taibike_indices.get(key)
        if not z or not t or z.get("close") is None or t.get("close") is None:
            continue
        diff = t["close"] - z["close"]
        pct = diff / z["close"] * 100 if z["close"] else None
        out.append({"key": key, "label": t.get("label", key),
                    "zenba_close": z["close"], "close": t["close"],
                    "diff": diff, "diff_pct": pct})
    if not out:
        return None

    nk = next((o for o in out if o["key"] == "nikkei"), out[0])
    d = nk["diff_pct"] or 0
    if d > 0.4:
        verdict, tone = "後場に買われ、大引けにかけて水準を切り上げた", "positive"
    elif d > 0.1:
        verdict, tone = "後場はじり高", "positive"
    elif d > -0.1:
        verdict, tone = "後場はほぼ横ばい。前場のレンジを引き継いだ", "neutral"
    elif d > -0.4:
        verdict, tone = "後場はじり安", "negative"
    else:
        verdict, tone = "後場に売られ、大引けにかけて水準を切り下げた", "negative"
    return {"indices": out, "verdict": verdict, "tone": tone}


# ==================== 寄り前想定の答え合わせ ====================
def verify_open(implied: dict | None, actual_pct: float | None) -> dict | None:
    """朝に立てた想定と実際のズレを記録する。当てに行くのではなく、
    「今日の地合いが想定より強いのか弱いのか」を測る物差しとして使う。"""
    if not implied or implied.get("gap_pct") is None or actual_pct is None:
        return None
    diff = actual_pct - implied["gap_pct"]
    if diff > 0.6:
        verdict, tone = "想定よりはっきり強い。日本市場側に買い材料がある", "positive"
    elif diff > 0.2:
        verdict, tone = "想定よりやや強い", "positive"
    elif diff > -0.2:
        verdict, tone = "ほぼ想定通り。外部環境で説明できる動き", "neutral"
    elif diff > -0.6:
        verdict, tone = "想定よりやや弱い", "negative"
    else:
        verdict, tone = "想定よりはっきり弱い。日本市場固有の売り材料が疑われる", "negative"
    return {"expected_pct": round(implied["gap_pct"], 2),
            "actual_pct": round(actual_pct, 2),
            "diff": round(diff, 2), "method": implied.get("method_label"),
            "verdict": verdict, "tone": tone}


# ==================== 資金の向き先の入れ替わり ====================
def _codes(rows: list[dict]) -> list[str]:
    return [r["code"] for r in rows if r.get("code")]


def ranking_delta(today_rows: list[dict], prev_rows: list[dict] | None) -> dict:
    """売買代金上位の顔ぶれの入れ替わりを出す。「今日から資金が入った銘柄」が分かる。"""
    if not prev_rows:
        return {"new": [], "dropped": [], "rank_up": []}
    prev_rank = {c: i for i, c in enumerate(_codes(prev_rows))}
    today_codes = _codes(today_rows)
    new, rank_up = [], []
    for i, row in enumerate(today_rows):
        code = row.get("code")
        if not code:
            continue
        if code not in prev_rank:
            new.append({"code": code, "name": row.get("name"),
                        "rank": i + 1, "change_pct": row.get("change_pct")})
        else:
            jump = prev_rank[code] - i
            if jump >= 5:
                rank_up.append({"code": code, "name": row.get("name"),
                                "rank": i + 1, "prev_rank": prev_rank[code] + 1,
                                "jump": jump, "change_pct": row.get("change_pct")})
    dropped = [{"code": c} for c in prev_rank if c not in today_codes]
    rank_up.sort(key=lambda r: -r["jump"])
    return {"new": new[:10], "dropped": dropped[:10], "rank_up": rank_up[:5]}


def streaks(today_rows: list[dict], history: list[list[dict]],
            min_days: int = 3) -> list[dict]:
    """売買代金上位に何営業日連続で居座っているか。継続的な資金流入の検出。

    history は「新しい日 → 古い日」の順に並んだ過去の行リスト。
    """
    if not history:
        return []
    past = [set(_codes(rows)) for rows in history]
    out = []
    for row in today_rows:
        code = row.get("code")
        if not code:
            continue
        n = 1
        for day in past:
            if code in day:
                n += 1
            else:
                break
        if n >= min_days:
            out.append({"code": code, "name": row.get("name"), "days": n,
                        "change_pct": row.get("change_pct")})
    out.sort(key=lambda r: -r["days"])
    return out[:8]


# ==================== ウォッチリスト ====================
def enrich_watchlist(quotes: list[dict], tables: dict,
                     articles: list[dict]) -> list[dict]:
    """保有・注目銘柄に「今日この銘柄に何が起きたか」を貼り付ける。"""
    hits: dict[str, list[str]] = {}

    for key, table in (tables or {}).items():
        if not table or not table.get("rows"):
            continue
        for i, row in enumerate(table["rows"]):
            code = row.get("code")
            if not code:
                continue
            hits.setdefault(code, []).append(f"{table['label']} {i + 1}位")

    body_all = "\n".join(a.get("body", "") for a in (articles or []))
    titles = {a.get("label"): a.get("body", "") for a in (articles or [])}

    out = []
    for q in quotes:
        code = q.get("code")
        tags = list(hits.get(code, []))
        mentions = []
        name = (q.get("name") or "").strip()
        for label, body in titles.items():
            if code and code in body:
                mentions.append(label)
            elif name and len(name) >= 3 and name in body:
                mentions.append(label)
        out.append({**q, "tags": tags, "mentions": sorted(set(mentions)),
                    "in_news": bool(mentions) or (code in body_all if code else False)})
    return out
