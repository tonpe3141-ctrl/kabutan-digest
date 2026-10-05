"""ニュースから読む: 報道・株探・開示を値動きと突き合わせる（DESIGN.md 25章）。

入口は機械、理由は LLM。ここは照合と数え上げだけで、文章は書かない（Routine が ai_news に書く）。

  動いた銘柄の材料（movers）
      売買代金の上位で大きく動いた銘柄・日経225で大きく動いた銘柄・値上がり／値下がりの上位・ウォッチの銘柄を、
      開示（コードで照合。前営業日の引け後の開示も、今日の値動きの材料として見る）と見出し（社名かコードで照合）に突き合わせ、
      「開示あり／報道あり／材料が見当たらない」に分ける。材料が見当たらない＝需給・連想・地合いで動いた可能性が高い
      （材料をこじつけない。見出しに名前が無いだけで、材料が無いとは限らないことも画面に書く）。
  話題の銘柄（buzz）
      社名かコードが入った見出しを銘柄ごとに数え、何媒体が報じたかで並べる。1媒体だけの話題と、各社がそろって報じた話題を分ける。
  テーマの話題（themes）
      見出しをテーマで束ね（テーマの語＝温度計の THEME_ALIASES と同じ、またはテーマ辞書の銘柄の名前）、過去5営業日の平均と比べる。

照合の約束（アプリの titleHas より少し厳しい）:
  NFKC で半角にそろえ、大文字にし、空白を消して照合する。社名は3文字以上だけ（2文字の社名はコードでだけ当てる）。
  社名の前後にカタカナ・英数字が続くときは当てない（「フリー」を「フリーランス」に、「クリエイト」を「クリエイトＳＤ」に当てない）。
  カタカナ・英字で終わる社名の後ろの漢字は「株」「系」などだけ許す。
  日経会社情報DIGITAL の見出し（適時開示の写し）は開示と重なるので、見出しとしては数えない。

すべて純関数。入力は build.py が集めた payload の区画。
"""
import re
import unicodedata
from collections import Counter

from .thermo import THEME_ALIASES

MIN_NAME = 3                 # 社名で照合する最小の文字数
MOVE_VALUE = 2.0             # 売買代金の上位で「大きく動いた」とみなす市場との差（%）
MOVE_225 = 3.0               # 日経225で「大きく動いた」とみなす市場との差（%）
MOVE_WATCH = 2.0             # ウォッチの銘柄で材料を探す市場との差（%）
GROUP_MOVE = 1.5             # 同じテーマ・業種の平均（自分を除く）がこれ以上同じ向きなら「業種ぐるみ」
GROUP_MIN = 3                # 業種ぐるみを判定する、値動きの分かる仲間の最小数
MIN_PRICE = 100              # 値上がり・値下がりの上位から拾う最低の株価（低位株の値幅は材料と無関係に大きい）
RANK_N = 15                  # 値上がり・値下がりの上位から見る数
MOVERS_MAX = 40              # 動いた銘柄の行の上限
BUZZ_MAX = 12                # 話題の銘柄の行の上限
THEME_MAX = 10               # テーマの話題の行の上限
HIST_DAYS = 5                # テーマの話題を比べる過去の営業日

_KANA = re.compile(r"[゠-ヿ]")
_ALNUM = re.compile(r"[A-Z0-9]")
_CODE_TAG = re.compile(r"[\[（(<＜]\s*([0-9]{3}[0-9A-Z])\s*[\]）)>＞]")
_SUFFIX = re.compile(r"(ホールディングス|HOLDINGS|HLDGS|グループ本社|グループ|コーポレーション|CORPORATION|CORP|HD)$")
_PREFIX = re.compile(r"^(G-|Ｇ－)")
_MIRROR = re.compile(r"日経会社情報DIGITAL|適時開示\)\s*$")
# 個別の銘柄の話ではない見出し（ランキング・テクニカルの一覧・相場全体）
_LISTING = re.compile(r"本日の【|ランキング|ETF売買動向|日経平均(大引け|前引け)|東京株式（|騰落|寄与度|PTS")
_ETF = re.compile(r"上場投信|ＥＴＦ|ETF|ETN|ＥＴＮ|インデックス|NEXT FUNDS|ＮＥＸＴ　ＦＵＮＤＳ|日経平均|ＴＯＰＩＸ|TOPIX|ブル|ベア")

# 材料とみなさない定例の開示（取得の途中経過・月次・ガバナンス・総会の手続き）
_ROUTINE = re.compile(r"取得状況|取得結果|月次|定款の定め|ガバナンス|独立役員|招集|株主総会|役員の異動|人事異動|"
                      r"サステナビリティ|ESG|統合報告|説明会資料|補足資料|訂正|延期")
# 何銘柄もまとめて挙げる見出し（株探の話題株ピックアップなど）。名前が入っていても理由は本文にある
_ROUNDUP = re.compile(r"ピックアップ|注目銘柄|話題株|材料株|動意株|物色")

MEDIA = {"株探": "株探", "株探（見出し）": "株探", "株探ニュース（Yahoo!ファイナンス配信）": "株探"}


def norm(s: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")).upper())


_KANJI = re.compile(r"[\u3400-\u9fff]")
_NAME_TAIL_OK = "株社系製側氏"       # カタカナ・英字の社名の後ろに続いてよい漢字（「トヨタ株」「ソニー系」）


def _word_char(c: str) -> bool:
    return bool(c and (_KANA.match(c) or _ALNUM.match(c)))


def _bad_before(c: str, first: str) -> bool:
    """前の字が社名とつながって別の語になるか（「ジェフリーズ」の中の「フリー」）。"""
    return _word_char(c) and _word_char(first)


def _bad_after(c: str, last: str) -> bool:
    """後ろの字が社名とつながって別の語になるか。カタカナ・英数字が続けば別の社名（「クリエイト」と「クリエイトＳＤ」）。
    カタカナ・英字で終わる社名に漢字が続くときは「株」「系」などだけ許す（「フリー」と「フリー素材」）。"""
    if not c:
        return False
    if _word_char(c):
        return True
    return bool(_word_char(last) and _KANJI.match(c) and c not in _NAME_TAIL_OK)


def has_word(text: str, key: str, strict: bool = True) -> bool:
    """text（norm 済み）に key（norm 済み）が語として入っているか。

    strict=False（テーマの語）は、前後に同じ字種（カタカナどうし・英数字どうし）が続くときだけ外す（「AI関連」は AI に数える）。"""
    if not key:
        return False
    i = text.find(key)
    while i >= 0:
        before = text[i - 1] if i > 0 else ""
        after = text[i + len(key)] if i + len(key) < len(text) else ""
        if strict:
            ok = not _bad_before(before, key[0]) and not _bad_after(after, key[-1])
        else:
            ok = not _same_kind(before, key[0]) and not _same_kind(after, key[-1])
        if ok:
            return True
        i = text.find(key, i + 1)
    return False


def _same_kind(a: str, b: str) -> bool:
    return bool(a and b and ((_KANA.match(a) and _KANA.match(b)) or (_ALNUM.match(a) and _ALNUM.match(b))))


def aliases(name: str | None) -> list[str]:
    """社名の照合に使う表記（norm 済み・3文字以上）。「(株)」「ホールディングス」「Ｇ－」を外した形も足す。"""
    base = norm(re.sub(r"\(株\)|（株）|株式会社", "", str(name or "")))
    out = []
    for a in (base, _PREFIX.sub("", base)):
        b = _SUFFIX.sub("", a)
        for x in (a, b):
            if len(x) >= MIN_NAME and x not in out:
                out.append(x)
    return out


def name_index(names: dict[str, list[str] | str]) -> list[tuple[str, str]]:
    """{コード: 社名（の一覧）} → 照合用の [(表記, コード)]。長い表記を先に（「三菱電機」を「三菱電」より先に当てる）。"""
    pairs = {}
    for code, ns in names.items():
        for n in ([ns] if isinstance(ns, str) else ns or []):
            for a in aliases(n):
                pairs.setdefault(a, code)
    return sorted(pairs.items(), key=lambda kv: -len(kv[0]))


def codes_in(title: str, index: list[tuple[str, str]], known: set[str]) -> list[str]:
    """見出しに名前かコードが入っている銘柄。"""
    t = norm(title)
    found = [c for c in _CODE_TAG.findall(t) if c in known]
    for key, code in index:
        if code not in found and has_word(t, key):
            found.append(code)
    return found


# ==================== 見出しを集める ====================
def gather(kabutan: dict | None, press: dict | None) -> list[dict]:
    """株探の見出し・記事、報道の見出し・本文つきの記事を1つにする（同じ見出しは1本）。{title, media, url, t}"""
    items, seen = [], set()

    def add(title, media, url, t):
        k = norm(title)
        if not title or k in seen or _MIRROR.search(title):
            return
        seen.add(k)
        items.append({"title": title, "media": MEDIA.get(media, media), "url": url, "t": t})

    kb, pr = kabutan or {}, press or {}
    for a in kb.get("articles") or []:
        add(a.get("headline"), "株探", a.get("url"), a.get("timestamp"))
    for x in kb.get("headlines") or []:
        add(x.get("title"), "株探", x.get("url"), x.get("published"))
    for x in pr.get("headlines") or []:
        add(x.get("title"), x.get("source") or "報道", x.get("url"), x.get("published"))
    for x in pr.get("articles") or []:
        add(x.get("headline"), x.get("provider") or x.get("category") or "報道", x.get("url"), x.get("timestamp"))
    return items


def match_all(items: list[dict], index, known: set[str]) -> dict[str, list[int]]:
    """{コード: [見出しの番号]}。一覧・ランキング型の見出しは数えない。"""
    by: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        if _LISTING.search(it["title"]):
            continue
        for c in codes_in(it["title"], index, known):
            by.setdefault(c, []).append(i)
    return by


# ==================== 動いた銘柄の材料 ====================
def market_move(constituents: list[dict]):
    """市場の前日比（日経225採用銘柄の前日比の中央値）。値がさ株に引っ張られる日経平均より、ふつうの銘柄の動きに近い。"""
    xs = sorted(c["change_pct"] for c in constituents or [] if c.get("change_pct") is not None)
    if len(xs) < 50:
        return None
    n = len(xs)
    return round(xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2, 2)


def pick_movers(tables: dict, constituents: list[dict], watch: list[dict], mkt=None) -> list[dict]:
    """材料を探す銘柄。売買代金の上位で大きく動いた → 225で大きく動いた → 値上がり・値下がりの上位 → ウォッチ の順。

    「大きく動いた」は市場（mkt）との差で見る（相場全体が +2% の日に +3% の銘柄は材料を探すほどではない）。"""
    out, seen = [], set()
    m0 = mkt or 0.0

    def add(code, name, pct, src):
        if not code or code in seen or pct is None or _ETF.search(str(name or "")):
            return
        seen.add(code)
        out.append({"code": str(code), "name": re.sub(r"\(株\)|（株）", "", str(name or code)).strip(), "pct": round(pct, 2),
                    "ex": round(pct - m0, 2) if mkt is not None else None, "src": src})

    for i, r in enumerate(((tables or {}).get("value") or {}).get("rows") or []):
        if r.get("change_pct") is not None and abs(r["change_pct"] - m0) >= MOVE_VALUE:
            add(r.get("code"), r.get("name"), r.get("change_pct"), f"売買代金{i + 1}位")
    for c in sorted(constituents or [], key=lambda x: -abs((x.get("change_pct") or 0) - m0)):
        if c.get("change_pct") is not None and abs(c["change_pct"] - m0) >= MOVE_225:
            add(c.get("code"), c.get("name"), c.get("change_pct"), "日経225")
    for key, label in (("gainer", "値上がり"), ("loser", "値下がり")):
        for i, r in enumerate((((tables or {}).get(key) or {}).get("rows") or [])[:RANK_N]):
            if (r.get("price") or 0) < MIN_PRICE or "東証" not in str(r.get("market") or "東証"):
                continue
            add(r.get("code"), r.get("name"), r.get("change_pct"), f"{label}{i + 1}位")
    for w in watch or []:
        if w.get("change_pct") is not None and abs(w["change_pct"] - m0) >= MOVE_WATCH:
            add(w.get("code"), w.get("name"), w.get("change_pct"), "ウォッチ")
    return out[:MOVERS_MAX]


def group_moves(pct_of: dict, group_of: dict) -> dict[str, tuple[float, int, list]]:
    """グループ → (前日比の合計, 数, コード)。業種ぐるみの判定で自分を除いた平均を出すため。"""
    agg: dict[str, list] = {}
    for c, g in group_of.items():
        if pct_of.get(c) is not None:
            a = agg.setdefault(g, [0.0, 0, []])
            a[0] += pct_of[c]
            a[1] += 1
            a[2].append(c)
    return {g: (a[0], a[1], a[2]) for g, a in agg.items()}


def movers(cands: list[dict], items: list[dict], by: dict, disclosures: list[dict], themes_of: dict,
           pct_of: dict | None = None, group_of: dict | None = None, names: dict | None = None, mkt=None,
           arts: list[dict] | None = None, keys_of: dict | None = None) -> dict:
    """動いた銘柄ごとに、開示と見出しを貼る。どちらも無く、同じテーマ・業種が同じ向きに動いていれば「業種ぐるみ」。

    kind: disc（開示あり）／news（報道あり）／group（材料は見当たらないが業種ぐるみ）／none（材料が見当たらない）"""
    disc_by: dict[str, list[dict]] = {}
    for d in disclosures or []:
        if _ROUTINE.search(d.get("title") or ""):
            continue
        disc_by.setdefault(str(d.get("code")), []).append(d)
    gm = group_moves(pct_of or {}, group_of or {})
    m0 = mkt or 0.0
    rows = []
    for m in cands:
        ds = disc_by.get(m["code"]) or []
        ns = [items[i] for i in by.get(m["code"]) or []]
        ns.sort(key=lambda x: bool(_ROUNDUP.search(x["title"])))       # 1社の見出しを、まとめ記事より先に
        g = (group_of or {}).get(m["code"])
        grp = None
        if g and g in gm:
            tot, n, codes = gm[g]
            if m["code"] in codes:
                tot, n = tot - (pct_of or {})[m["code"]], n - 1
            if n >= GROUP_MIN:
                avg = tot / n - m0
                if abs(avg) >= GROUP_MOVE and (avg > 0) == ((m.get("ex") if m.get("ex") is not None else m["pct"]) > 0):
                    lead = sorted((c for c in codes if c != m["code"]), key=lambda c: -abs((pct_of or {})[c]))[:3]
                    grp = {"g": g.replace("（業種）", ""), "avg": round(avg, 1), "n": n,
                           "names": [(names or {}).get(c, c) for c in lead]}
        bm = body_mentions(m["code"], (keys_of or {}).get(m["code"]) or aliases(m["name"]), arts or [])
        bm.sort(key=lambda x: x["list"])
        kind = "disc" if ds else "news" if (ns or any(not x["list"] for x in bm)) else "group" if grp else "none"
        rows.append({**m, "kind": kind, "th": (themes_of.get(m["code"]) or [])[:2], "grp": grp, "said": bm,
                     "disc": [{"title": d.get("title"), "category": d.get("category") or "その他", "time": d.get("time"),
                               "when": d.get("when") or "今日", "pdf": d.get("pdf")} for d in ds[:3]],
                     "news": [{"title": x["title"], "media": x["media"], "url": x["url"], "t": x["t"],
                               "roundup": bool(_ROUNDUP.search(x["title"]))} for x in ns[:3]]})
    cnt = Counter(r["kind"] for r in rows)
    up = [r for r in rows if r["pct"] > 0]
    down = [r for r in rows if r["pct"] < 0]
    return {"rows": rows, "n": len(rows), "mkt": mkt, "disc": cnt["disc"], "news": cnt["news"], "group": cnt["group"],
            "none": cnt["none"], "up_none": sum(1 for r in up if r["kind"] == "none"), "n_up": len(up),
            "down_none": sum(1 for r in down if r["kind"] == "none"), "n_down": len(down),
            "move": {"value": MOVE_VALUE, "n225": MOVE_225, "watch": MOVE_WATCH, "group": GROUP_MOVE}}


# 記事の本文で銘柄に触れた文（大引け概況・マーケット日報などは、動いた銘柄の理由を1文で書くことが多い）
SNIP = 90                    # 切り出す文の長さの上限


def articles_of(kabutan: dict | None, press: dict | None) -> list[dict]:
    out = []
    for a in (kabutan or {}).get("articles") or []:
        out.append({"title": a.get("headline"), "media": "株探", "url": a.get("url"), "body": a.get("body") or ""})
    for a in (press or {}).get("articles") or []:
        out.append({"title": a.get("headline"), "media": a.get("provider") or a.get("category") or "報道",
                    "url": a.get("url"), "body": a.get("body") or ""})
    return [a for a in out if a["body"]]


def _snip(sent: str, key: str) -> str:
    sent = sent.strip("　 ")
    if len(sent) <= SNIP:
        return sent
    i = max(0, norm(sent).find(key) - 20)
    return ("…" if i else "") + sent[i:i + SNIP] + "…"


_BULLET = re.compile(r"^[\s　]*[△▲▽▼■□◆◇●○★☆・]")


def body_mentions(code: str, keys: list[str], arts: list[dict], limit: int = 2) -> list[dict]:
    """記事の本文のうち、その銘柄の名前かコードが入った文（1記事1文）。理由を書いた文を、名前が並んだだけの文より先に。"""
    out = []
    tag = f"<{code}>"
    for a in arts:
        if _LISTING.search(a["title"] or "") and "ストップ高" not in (a["title"] or ""):
            continue
        found = None
        for sent in re.split(r"(?<=。)|\n", a["body"]):
            t = norm(sent)
            key = next((k for k in keys if has_word(t, k)), None) or (code if tag in sent else None)
            if not key:
                continue
            # 何銘柄も並べただけの文（「Ａ<1>、Ｂ<2>などが上昇」）や一覧の行（「△ティアフォー<593A>[東証Ｇ]」）は理由ではない
            listed = (len(re.findall(r"<[0-9]{3}[0-9A-Z]>", sent)) >= 3 or bool(re.search(r"など(が|の|も)", sent))
                      or bool(_BULLET.match(sent)) or (len(sent.strip()) < 30 and "。" not in sent))
            if found is None or (found["list"] and not listed):
                found = {"title": a["title"], "media": a["media"], "url": a["url"], "text": _snip(sent, key), "list": listed}
            if not listed:
                break
        if found:
            out.append(found)
    out.sort(key=lambda x: x["list"])
    return out[:limit]


# ==================== 話題の銘柄・テーマ ====================
def buzz(items: list[dict], by: dict, names: dict, pct_of: dict, themes_of: dict) -> list[dict]:
    """見出しの数と、何媒体が報じたか。2媒体以上か、3本以上の銘柄だけ。"""
    rows = []
    for code, idx in by.items():
        media = list(dict.fromkeys(items[i]["media"] for i in idx))
        if len(media) < 2 and len(idx) < 3:
            continue
        idx = sorted(idx, key=lambda i: bool(_ROUNDUP.search(items[i]["title"])))     # 1社の見出しを、まとめ記事より先に
        rows.append({"code": code, "name": names.get(code) or code, "n": len(idx), "media": media,
                     "pct": pct_of.get(code), "th": (themes_of.get(code) or [])[:2],
                     "items": [{"title": items[i]["title"], "media": items[i]["media"], "url": items[i]["url"]} for i in idx[:4]]})
    rows.sort(key=lambda r: (-len(r["media"]), -r["n"], r["code"]))
    return rows[:BUZZ_MAX]


def theme_counts(items: list[dict], by: dict, themes_of: dict, theme_names: list[str]) -> dict[str, int]:
    """テーマごとの見出しの本数（テーマの語か、そのテーマの銘柄の名前が入った見出し。1本は1テーマに1回）。"""
    keys = {t: [norm(w) for w in THEME_ALIASES.get(t, [t])] for t in theme_names if len(t) >= 2}
    hit: dict[str, set] = {}
    for code, idx in by.items():
        for th in themes_of.get(code) or []:
            hit.setdefault(th, set()).update(idx)
    for i, it in enumerate(items):
        t = norm(it["title"])
        for th, words in keys.items():
            if any(has_word(t, w, strict=False) for w in words):
                hit.setdefault(th, set()).add(i)
    return {th: len(ix) for th, ix in hit.items() if ix}


def theme_rows(counts: dict[str, int], history: list[dict], theme_move: dict) -> list[dict]:
    """テーマの話題の行。過去の営業日（history: 新しい順の {テーマ: 本数}）の平均と比べる。"""
    past = [h for h in history if h][:HIST_DAYS]
    rows = []
    for th, n in counts.items():
        if n < 2:
            continue
        avg = (sum(h.get(th, 0) for h in past) / len(past)) if len(past) >= 3 else None
        rows.append({"theme": th, "n": n, "avg": round(avg, 1) if avg is not None else None,
                     "ratio": round(n / avg, 1) if avg else None, "move": theme_move.get(th)})
    rows.sort(key=lambda r: (-(r["ratio"] or 0) if r["avg"] is not None else 0, -r["n"], r["theme"]))
    return rows[:THEME_MAX]


# ==================== まとめ ====================
def build(slot: str, payload: dict, *, names: dict, themes: dict, disclosures: list[dict],
          history_counts: list[dict]) -> dict | None:
    """latest.json の slots.{SLOT}.data.newsflow。

    names: {コード: 社名か社名の一覧}（照合用）。disclosures: 照合に使う開示（今日の分と前営業日の引け後。when を付けておく）。
    history_counts: 過去の営業日のテーマの本数（新しい順）。"""
    items = gather(payload.get("kabutan"), payload.get("press"))
    if not items:
        return None
    stocks = (themes or {}).get("stocks") or {}
    themes_of = {c: e.get("themes") or [] for c, e in stocks.items()}
    names = dict(names)
    tables = payload.get("tables") or {}
    for t in tables.values():
        for r in (t or {}).get("rows") or []:
            if r.get("code") and r.get("name"):
                ns = names.get(r["code"])
                names[r["code"]] = list(dict.fromkeys(([ns] if isinstance(ns, str) else ns or []) + [r["name"]]))
    for d in disclosures or []:
        if d.get("code") and d.get("name"):
            ns = names.get(d["code"])
            names[d["code"]] = list(dict.fromkeys(([ns] if isinstance(ns, str) else ns or []) + [d["name"]]))
    index = name_index(names)
    by = match_all(items, index, set(names))
    first = {c: (ns if isinstance(ns, str) else (ns or [c])[0]) for c, ns in names.items()}

    pct_of = {}
    for t in tables.values():
        for r in (t or {}).get("rows") or []:
            if r.get("code") and r.get("change_pct") is not None:
                pct_of.setdefault(r["code"], r["change_pct"])
    for c in payload.get("constituents") or []:
        if c.get("change_pct") is not None:
            pct_of[c["code"]] = c["change_pct"]
    for w in payload.get("watchlist") or []:
        if w.get("change_pct") is not None:
            pct_of[w["code"]] = w["change_pct"]

    out = {"slot": slot, "n_items": len(items), "n_matched": sum(1 for _ in by)}
    if slot != "preopen":
        cons = payload.get("constituents") or []
        mkt = market_move(cons)
        group_of = {c: ts[0] for c, ts in themes_of.items() if ts}
        for c in cons:
            if c.get("sector") and c["code"] not in group_of:
                group_of[c["code"]] = f"{c['sector']}（業種）"
        cands = pick_movers(tables, cons, payload.get("watchlist") or [], mkt)
        keys_of: dict[str, list[str]] = {}
        for key, code in index:
            keys_of.setdefault(code, []).append(key)
        out["movers"] = movers(cands, items, by, disclosures, themes_of, pct_of, group_of, first, mkt,
                               articles_of(payload.get("kabutan"), payload.get("press")), keys_of)
    out["buzz"] = buzz(items, by, first, {k: round(v, 2) for k, v in pct_of.items()}, themes_of)
    counts = theme_counts(items, by, themes_of, list((themes or {}).get("themes") or {}) or
                          sorted({t for ts in themes_of.values() for t in ts}))
    move = {t["theme"]: t.get("avg_pct") for t in ((payload.get("theme_flow") or {}).get("top") or [])
            + ((payload.get("theme_flow") or {}).get("bottom") or [])}
    out["themes"] = theme_rows(counts, history_counts, move)
    out["counts"] = counts
    out["hist_days"] = min(len([h for h in history_counts if h]), HIST_DAYS)
    return out
