"""集めた指標から、相場の見立てを日本語の文章に組み立てる。

外部の生成AIは使わない。理由は3つ:
  - APIキーと課金なしで毎日確実に動くこと
  - 同じ数字からは必ず同じ文章が出ること（あとで検証できる）
  - 数字にない話を書かないこと

そのかわり、単なる穴埋めにならないよう「対比」と「含意」を作りにいく。
  指数の強弱そのものではなく、指数どうしのズレが何を意味するか
  金利の水準ではなく、2年と10年の動き方の違いが何を示すか
  ランキングの1位ではなく、上位の顔ぶれがどの物色テーマを示すか
"""
import re

# ==================== 文章づくりの小道具 ====================
def _clean_name(name: str | None) -> str:
    if not name:
        return ""
    n = re.sub(r"[(（]株[)）]|㈱|[(（].*?[)）]$", "", name).strip()
    return n or name


def _names(rows: list[dict], limit: int = 3) -> str:
    """銘柄リストを「トヨタ自動車(7203)、ソニーG(6758)」の形にする。"""
    parts, seen = [], set()
    for r in rows or []:
        code = r.get("code")
        key = code or r.get("name")
        if not key or key in seen:
            continue          # 同じ会社が1日に複数の開示を出すことがある
        seen.add(key)
        nm = _clean_name(r.get("name"))
        parts.append(f"{nm}({code})" if nm and code else (nm or code or ""))
        if len(parts) >= limit:
            break
    return "、".join(parts)


def _pct(v, digits=2) -> str:
    return "—" if v is None else f"{v:+.{digits}f}%"


def _join(sentences: list[str]) -> str:
    return "".join(s if s.endswith("。") else s + "。" for s in sentences if s)


def _section(title: str, sentences: list[str]) -> dict | None:
    body = _join(sentences)
    return {"title": title, "body": body} if body else None


# ==================== 寄り前 ====================
def _us_market(us: dict) -> str | None:
    spx, sox, rut = us.get("spx"), us.get("sox"), us.get("rut")
    ndq, vix = us.get("ndq"), us.get("vix")
    if not spx or spx.get("change_pct") is None:
        return None
    s, out = spx["change_pct"], []

    tone = "上昇" if s > 0.2 else ("下落" if s < -0.2 else "小動き")
    out.append(f"S&P500 は {_pct(s)} と{tone}")
    if ndq and ndq.get("change_pct") is not None:
        out.append(f"NASDAQ は {_pct(ndq['change_pct'])}")

    # 指数の強弱そのものより、どこに偏ったかを見る
    sox_gap = (sox["change_pct"] - s
               if sox and sox.get("change_pct") is not None else None)
    if sox_gap is not None and sox_gap > 1.5:
        out.append(f"目立つのは SOX指数の {_pct(sox['change_pct'])} で、"
                   "指数全体が冴えないなかでも半導体だけに資金が集まる一極集中の形")
    elif sox_gap is not None and sox_gap < -1.5:
        out.append(f"一方 SOX指数は {_pct(sox['change_pct'])} と逆行安で、"
                   "半導体が重しになっている")

    # 半導体への一極集中を指摘したあとに「裾野が広い」とは言わない（矛盾するため）
    if (rut and rut.get("change_pct") is not None
            and (sox_gap is None or abs(sox_gap) <= 1.5)):
        if rut["change_pct"] - s > 0.5:
            out.append("中小型のラッセル2000が大型を上回っており、物色の裾野は広い")
        elif s - rut["change_pct"] > 0.8:
            out.append("ラッセル2000は出遅れており、買いは大型に偏っている")

    if vix and vix.get("last") is not None:
        lvl, chg = vix["last"], vix.get("change_pct")
        if lvl < 16:
            out.append(f"VIX は {lvl:.1f} と低位で、リスクを取りにいける地合い")
        elif lvl > 25:
            out.append(f"VIX は {lvl:.1f} と高く、ヘッジ需要が強い状態")
        elif chg is not None and chg > 8:
            out.append(f"VIX が {_pct(chg)} と急伸しており、警戒感が戻りつつある")
    return _join(out)


def _rates_fx(macro: dict) -> str | None:
    y10, y2, fx = macro.get("us10y"), macro.get("us2y"), macro.get("usdjpy")
    out = []

    bp10 = y10["change"] * 100 if y10 and y10.get("change") is not None else None
    bp2 = y2["change"] * 100 if y2 and y2.get("change") is not None else None

    if bp10 is not None:
        out.append(f"米10年金利は {bp10:+.0f}bp の{'上昇' if bp10 > 0 else '低下'}"
                   f"（{y10['last']:.2f}%）")
    # 2年と10年の動き方の差は、市場が何を織り込みにいったかを示す
    if bp10 is not None and bp2 is not None and abs(bp10 - bp2) >= 3:
        if bp2 > bp10 and bp2 > 0:
            out.append("2年が10年より大きく上昇するベア・フラットニングで、"
                       "利下げ期待の後退を織り込む形。グロース株には向かい風")
        elif bp10 > bp2 and bp10 > 0:
            out.append("10年主導で金利曲線が立っており、"
                       "財政・インフレ side の懸念が意識されている")
        elif bp2 < bp10 and bp2 < 0:
            out.append("2年主導の低下で、利下げ期待が前倒しされている形。"
                       "グロース株には追い風")

    if fx and fx.get("last") is not None and fx.get("change_pct") is not None:
        p = fx["change_pct"]
        if abs(p) >= 0.3:
            out.append(f"ドル円は {fx['last']:.2f}円（{_pct(p)}）と"
                       f"{'円安' if p > 0 else '円高'}に振れ、"
                       f"輸出関連には{'追い風' if p > 0 else '重し'}")
        else:
            out.append(f"ドル円は {fx['last']:.2f}円とほぼ横ばいで、為替の影響は中立")
    return _join(out)


def _sector_read(outlook: dict | None, sectors_us: dict) -> str | None:
    if not outlook or not outlook.get("tailwind"):
        return None
    tail = outlook["tailwind"][:3]
    head = (outlook.get("headwind") or [])[:2]
    out = [f"米国の動きをそのまま当てはめると、追い風が吹きやすいのは"
           f"{'、'.join(s['sector'] for s in tail)}"]
    if head:
        out.append(f"逆に重いのは{'、'.join(s['sector'] for s in head)}")

    if sectors_us:
        ranked = sorted((s for s in sectors_us.values() if s.get("change_pct") is not None),
                        key=lambda s: -s["change_pct"])
        if len(ranked) >= 3:
            spread = ranked[0]["change_pct"] - ranked[-1]["change_pct"]
            if spread > 2.5:
                out.append(f"米セクター間の騰落差は {spread:.1f}ポイントと大きく、"
                           f"{ranked[0]['label']}から{ranked[-1]['label']}へという"
                           "はっきりした資金の移動が起きている")
            elif spread < 1.0:
                out.append("米セクター間の騰落差は小さく、"
                           "テーマ性の乏しい全体相場だった")
    return _join(out)


def _carryover(carryover: dict) -> str | None:
    rows = (carryover or {}).get("after_hours_kessan") or []
    if not rows:
        return None
    revisions = [r for r in rows if r.get("category") == "業績予想の修正"]
    out = []
    if revisions:
        out.append(f"前営業日の引け後には業績予想の修正が {len(revisions)}件 出ている"
                   f"（{_names(revisions, 3)}など）。寄りで値が飛びやすい")
    else:
        out.append(f"前営業日の引け後の開示は {len(rows)}件。"
                   "業績予想の修正はなく、個別の材料は限定的")
    return _join(out)


def preopen_commentary(data: dict) -> dict | None:
    us = data.get("us") or {}
    macro = data.get("macro") or {}
    io = data.get("implied_open") or {}
    risk = data.get("risk") or {}

    sections = [
        _section("米国市場をどう見るか", [_us_market(us)]),
        _section("金利と為替", [_rates_fx(macro)]),
        _section("日本株への持ち込み", [
            (f"外部環境をそのまま当てはめた想定オープンは {_pct(io.get('gap_pct'))}"
             + (f"（およそ {io['gap']:+,.0f}円）" if io.get("gap") is not None else "")
             + f"。リスク環境は「{risk.get('label')}」" if io.get("gap_pct") is not None else None),
            _sector_read(data.get("sector_outlook"), data.get("sectors_us") or {}),
        ]),
        _section("今日の個別材料", [_carryover(data.get("carryover") or {})]),
    ]
    sections = [s for s in sections if s]
    if not sections:
        return None

    # 見出しは「方向 × 中身」の2軸で作る
    gap = io.get("gap_pct")
    sox = (us.get("sox") or {}).get("change_pct")
    spx = (us.get("spx") or {}).get("change_pct")
    if gap is None:
        headline = "外部環境の整理"
    elif gap > 0.8:
        headline = "米国発の追い風。寄り高スタートを想定"
    elif gap > 0.2:
        headline = "小幅高で始まる見込み"
    elif gap > -0.2:
        headline = "方向感に乏しい寄り付きを想定"
    elif gap > -0.8:
        headline = "小幅安で始まる見込み"
    else:
        headline = "米国発の逆風。寄り安スタートを想定"
    if sox is not None and spx is not None and sox - spx > 1.5:
        headline += "（半導体一極集中）"

    return {"headline": headline, "sections": sections, "method": "指標ベースの自動生成"}


# ==================== 前場・大引 ====================
def _index_read(indices: dict, divergence: dict | None, slot: str) -> str | None:
    nk = indices.get("nikkei")
    if not nk or nk.get("change_pct") is None:
        return None
    label = "前場" if slot == "zenba" else "大引け"
    p = nk["change_pct"]
    move = ("大幅高" if p > 1.5 else "上昇" if p > 0.3 else
            "小動き" if p > -0.3 else "下落" if p > -1.5 else "大幅安")
    out = [f"日経平均は{label}時点で {nk['close']:,.2f}円（{_pct(p)}）と{move}"]
    if divergence:
        out.append(divergence["comment"])
        if divergence["label"] == "値がさ主導":
            out.append("指数の数字ほど個別の体感は強くない点に注意")
    return _join(out)


def _flow_read(tables: dict, delta: dict | None, streaks: list | None) -> str | None:
    value = (tables or {}).get("value") or {}
    rows = value.get("rows") or []
    if not rows:
        return None
    label = value.get("label") or "売買代金"
    out = [f"{label}の上位は {_names(rows, 4)} が並ぶ"]

    ups = [r for r in rows[:10] if (r.get("change_pct") or 0) > 0]
    if len(ups) >= 7:
        out.append("上位10銘柄のうち7本以上が上昇しており、資金は買いに傾いている")
    elif len(ups) <= 3:
        out.append("上位10銘柄の大半が下落しており、売り需要が商いを膨らませている")

    if delta and delta.get("new"):
        out.append(f"前営業日から新たに上位入りしたのは {_names(delta['new'], 3)}")
    if streaks:
        top = streaks[0]
        out.append(f"{_clean_name(top['name'])}({top['code']}) は "
                   f"{top['days']}営業日続けて上位に残っており、資金の滞留が続いている")
    return _join(out)


def _extremes_read(tables: dict) -> str | None:
    g = ((tables or {}).get("gainer") or {}).get("rows") or []
    l = ((tables or {}).get("loser") or {}).get("rows") or []
    if not g and not l:
        return None
    out = []
    if g:
        big = [r for r in g if (r.get("change_pct") or 0) >= 20]
        out.append(f"上昇率トップは {_names(g, 2)}（{_pct(g[0].get('change_pct'))}）")
        if len(big) >= 3:
            out.append(f"20%超の急騰が {len(big)}銘柄と多く、短期資金の回転が効いている")
        elif not big:
            out.append("急騰銘柄は限られ、短期物色は落ち着いている")
    if l:
        out.append(f"下落率トップは {_names(l, 2)}（{_pct(l[0].get('change_pct'))}）")
    return _join(out)


def _disclosure_read(summary: dict | None, tables: dict, slot: str) -> str | None:
    if not summary:
        return None
    out = [summary["headline"]]
    key = "kessan_after" if slot == "taibike" else "kessan_intraday"
    rows = ((tables or {}).get(key) or {}).get("rows") or []
    revisions = [r for r in rows if r.get("category") == "業績予想の修正"]
    if revisions:
        when = "引け後" if slot == "taibike" else "場中"
        nxt = "翌営業日の寄りで反応しやすい" if slot == "taibike" else "後場の値動きに直結する"
        out.append(f"{when}の業績予想の修正は {len(revisions)}件"
                   f"（{_names(revisions, 3)}など）。{nxt}")
    return _join(out)


def session_commentary(data: dict, slot: str) -> dict | None:
    indices = data.get("indices") or {}
    tables = data.get("tables") or {}
    divergence = data.get("divergence")

    checks = []
    if slot == "zenba" and data.get("verify_open"):
        v = data["verify_open"]
        checks.append(f"寄り前に置いた想定は {_pct(v['expected_pct'])}、実際は {_pct(v['actual_pct'])}。"
                      f"{v['verdict']}")
    if slot == "taibike" and data.get("session_shift"):
        s = data["session_shift"]
        nk = next((i for i in s["indices"] if i["key"] == "nikkei"), None)
        checks.append(f"{s['verdict']}"
                      + (f"（前場終値比 {nk['diff']:+,.0f}円）" if nk else ""))

    sections = [
        _section("相場の総括", [_index_read(indices, divergence, slot)] + checks),
        _section("資金がどこに向かったか", [_flow_read(tables, data.get("ranking_delta"),
                                                data.get("streaks"))]),
        _section("値動きの両端", [_extremes_read(tables)]),
        _section("開示から見た次の一手",
                 [_disclosure_read(data.get("disclosure_summary"), tables, slot)]),
    ]
    sections = [s for s in sections if s]
    if not sections:
        return None

    nk = indices.get("nikkei") or {}
    p = nk.get("change_pct")
    label = "前場" if slot == "zenba" else "大引け"
    if p is None:
        headline = f"{label}の整理"
    elif p > 1.5:
        headline = f"{label}は大幅高"
    elif p > 0.3:
        headline = f"{label}は上昇"
    elif p > -0.3:
        headline = f"{label}はほぼ横ばい"
    elif p > -1.5:
        headline = f"{label}は下落"
    else:
        headline = f"{label}は大幅安"
    if divergence and divergence["label"] == "値がさ主導":
        headline += "、ただし中身は限定的"
    elif divergence and divergence["label"] == "広い物色":
        headline += "、物色は広い"

    return {"headline": headline, "sections": sections, "method": "指標ベースの自動生成"}
