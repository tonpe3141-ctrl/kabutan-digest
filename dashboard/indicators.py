"""経済指標: その日の指標の結果・予想・前回と、発表の前後に市場がどう動いたか（DESIGN.md 31章）。

依頼（2026-10-26）: その日の指標がどうだったか、なぜその値動きになったかを、情報収集と分析の中心に。
それまでの課題: 指標の結果はトレーダーズ・ウェブの短信の表題（「〜 56.0、予想 55.5ほか」）と一覧の本文に入っていたが、
構造になっておらず、寄り前の見立てが夜の米国の指標（10/23 の PMI・ミシガン）に一言も触れない日があった。
発表のあとにドル円・金利・先物がどう動いたかは、どこにも測っていなかった。

素材（どれもトレーダーズ・ウェブ＝DZH の配信を press.fetch_all が読む。株探の週の予定だけは kabutan_news）:
  【指標】の短信        … 結果と予想。本文に同時発表の指標と、詳細の行（〔予想〕（前回発表値・改））。発表の時刻に配信される
  【指標発表予定】の短信 … 発表の15分前。時刻・前回値・予想・重要度
  イベントスケジュール   … その日の予定（時刻・重要度 ☆◎◇・予想）。朝 6:45 ごろ
  主な経済指標の一覧     … 発表値と前回（短信が一覧から落ちた指標を補う。予想は無い）
  株探「今週の【重要イベント】」… 1週間の予定（★が重要）。週の初めにしか一覧に無いので、indicators.json に残して週の間使う

反応（react）: CNBC の5分足で、発表時刻の値から15分後・60分後の動き。ドル円（円）・米10年（bp）・S&P500 先物（%）・
日経平均先物（CME、%）。同じ時刻の発表はまとめて1つの反応にする（どの指標の分かは分けられない）。
「ふだんの何倍か」（ratio）は、直前10営業日の同じ時間帯（発表時刻の前後1時間）の15分の動きの中央値との比。
発表の時刻に重なったほかのニュースの分も入るので、指標だけの反応とは言えない（画面にそう書く）。

向き（tone）は公開の表（DIRECTIONS）で決める: 景気の指標の上振れ＝「景気が強い方向」、物価の上振れ＝「物価が高い方向」、
失業率・失業保険の上振れ＝「景気が弱い方向」。市場の受け止め（株高か株安か）は反応の数字で見る。
機械は「読む・そろえる・測る」だけで、何を意味するかの読みは Routine（ai_macro・ai_commentary）。
"""
from __future__ import annotations

import bisect
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median

from .config import INDICATORS_PATH

JST = timezone(timedelta(hours=9))

# 反応を測り、画面の先頭に並べる国・地域（ほかは畳んで出す。ドル円・日本株にほとんど響かない）
MAJOR = ("米", "日本", "ユーロ圏", "独", "英", "中国")
# 反応を測る値段（CNBC の5分足）。scale: 値の差に掛ける（米10年は % → bp）。pct: 変化率で出す
INSTR = [
    {"key": "usdjpy", "symbol": "JPY=",  "label": "ドル円",       "unit": "円", "scale": 1.0,   "digits": 2},
    {"key": "us10y",  "symbol": "US10Y", "label": "米10年",       "unit": "bp", "scale": 100.0, "digits": 1},
    {"key": "es",     "symbol": "@SP.1", "label": "S&P500先物",   "unit": "%",  "pct": True,    "digits": 2},
    {"key": "nk",     "symbol": "@NK.1", "label": "日経平均先物", "unit": "%",  "pct": True,    "digits": 2},
]
AFTER = (15, 60)          # 発表から何分後の動きを測るか
BASE_DAYS = 10            # 「ふだん」に使う直前の営業日の数
BASE_WIN = 60             # 「ふだん」の時間帯: 発表時刻の前後（分）
BASE_MIN = 30             # 「ふだん」の標本がこれ未満なら倍率を出さない
BIG_RATIO, MOVE_RATIO = 3.0, 1.5   # ふだんの15分の動きの何倍で「大きく反応」「反応」とするか
STALE_MIN = 30            # 値段の足がこれより古ければ、その時刻の値段は無いとする（分）
KEEP_DAYS = 250           # indicators.json に残す日数（暦日）
REACT_DAYS = 10           # 反応が未計算の記録を、何日前まで測り直すか
STATS_DAYS = 180          # 検証（記録の集計）に使う日数
SMALL = 20                # 件数がこれ未満なら「少ない」と書く

# 指標の向き。上から順に当てる（「PCEデフレーター」を「個人消費」より先に、「失業率」を「雇用」より先に）。
#   cat: growth（景気）/ inflation（物価）/ labor_inv（上がると景気が弱い）/ policy（政策金利）/ other（向きを決めない）
DIRECTIONS = [
    ("policy",    r"政策金利|FFレート|誘導目標|預金ファシリティ|リファイナンス金利"),
    ("inflation", r"CPI|消費者物価|物価指数|PCE.{0,6}(デフレーター|価格|物価)|PPI|生産者物価|卸売物価|企業物価|輸入物価|輸出物価|"
                  r"平均時給|時間当たり賃金|賃金|雇用コスト|インフレ期待|期待インフレ|デフレーター|サービス価格"),
    ("labor_inv", r"失業率|失業保険|失業者|失業手当|継続受給"),
    ("other",     r"貿易収支|経常収支|財政収支|在庫|マネーストック|マネーサプライ|外貨準備|対内証券|対外証券|国債入札|入札"),
    ("growth",    r"PMI|購買担当者|ISM|景況|景気|業況判断|短観|小売|売上高|販売|鉱工業|生産|GDP|雇用者|雇用統計|新規雇用|ADP|"
                  r"求人|JOLTS|消費者信頼感|消費者態度|消費者マインド|消費動向|信頼感|住宅着工|建築許可|住宅販売|住宅価格|中古住宅|"
                  r"新築住宅|耐久財|製造業受注|機械受注|設備稼働率|個人消費|個人所得|個人支出|消費支出|先行指数|一致指数|ZEW|Ifo|IFO|"
                  r"Gfk|GfK|フィラデルフィア|NY連銀|連銀製造業|製造業指数|サービス業|非製造業|商業販売|有効求人|工業企業利益|企業利益"),
]
TONE_LABEL = {"strong": "景気が強い方向", "weak": "景気が弱い方向", "hot": "物価が高い方向", "cool": "物価が低い方向"}
SURPRISE_LABEL = {"up": "上振れ", "down": "下振れ", "inline": "予想どおり"}

# 国・地域の判定（表題の頭の「10月米」「8月カナダ」「7-9月期中国」「前週分の米」。中銀の名前から）。国名の無い指標は日本
_COUNTRY = [
    ("ユーロ圏", r"ユーロ圏|ECB|欧州中銀|欧州中央銀行"), ("米", r"米|FOMC|FRB|連邦準備"), ("独", r"独|ドイツ"), ("仏", r"仏|フランス"),
    ("英", r"英|BOE|イングランド銀"), ("伊", r"伊|イタリア"), ("中国", r"中国"), ("豪", r"豪|RBA"), ("NZ", r"NZ|ニュージーランド|RBNZ"),
    ("カナダ", r"カナダ"), ("スイス", r"スイス"), ("日本", r"日銀|日本"), ("韓国", r"韓国"), ("香港", r"香港"), ("台湾", r"台湾"),
    ("インド", r"インド(?!ネシア)"), ("インドネシア", r"インドネシア"), ("シンガポール", r"シンガポール"), ("タイ", r"タイ"),
    ("ブラジル", r"ブラジル"), ("メキシコ", r"メキシコ"), ("南ア", r"南ア"), ("トルコ", r"トルコ"), ("ロシア", r"ロシア"),
    ("スウェーデン", r"スウェーデン"), ("ノルウェー", r"ノルウェー"), ("ハンガリー", r"ハンガリー"), ("ポーランド", r"ポーランド"),
    ("チェコ", r"チェコ"), ("フィリピン", r"フィリピン"), ("マレーシア", r"マレーシア"), ("チリ", r"チリ"),
]
# 「10月米」「7-9月期中国」「前週分の米」「5－7月英」のように、期間の直後に国名が来る。期間が無ければ表題の頭（中銀の名前）
_PERIOD = r"(?:\d{1,2}\s*[-－~～]\s*)?\d{1,2}月(?:期)?(?:分)?|前週分の|\d{1,2}月\d{1,2}日(?:分)?の|第\d四半期"

_NUM = r"[+\-−－▲±]?\d[\d,]*(?:\.\d+)?(?:\s*[-－～〜]\s*\d[\d,]*(?:\.\d+)?)?"
_UNIT = r"(?:％|%|倍|pt|ポイント|[万億兆]?(?:[A-Za-z]{0,3}ドル|ユーロ|ポンド|ランド|元|円|ウォン|ルピー|レアル|人|件|戸|台|フラン)|[万億兆])?"
_VAL = _NUM + r"\s*" + _UNIT
_RES = re.compile(rf"^(?P<name>.+?)(?:\s+|(?<=[）)\]]))(?P<actual>{_VAL})\s*[、,]\s*(?P<kind>予想|前回)\s*(?P<ref>{_VAL}|[-－]|なし)?\s*(?P<more>ほか)?\s*$")
_POLICY = re.compile(r"^(?P<bank>[^、,]{1,24}?)\s*[、,]\s*(?P<what>[^、,]{0,16}?(?:金利|レート|誘導目標|ファシリティ))を?\s*"
                     r"(?P<rate>\d[\d.]*(?:\s*[-－～〜]\s*\d[\d.]*)?\s*[％%])\s*(?P<act>で据え置き|に引き上げ|に引き下げ)\s*[、,]?\s*(?P<exp>.*)$")
_DETAIL = re.compile(r"^(?P<name>.+?)\s*〔\s*(?:予想)?\s*(?P<fc>[^〕]*)〕\s*[（(]\s*前回発表値\s*(?P<prior>[^）)]*)[）)]\s*$")
_EV = re.compile(r"^[○〇]\s*(?:(?P<day>\d{1,2})日)?\s*(?P<hm>\d{1,2}:\d{2})?\s*(?P<imp>[☆◎◇])?\s*(?P<name>.+?)\s*"
                 r"(?:[（(]\s*予想\s*[：:]\s*(?P<fc>[^）)]*)[）)])?\s*$")
_SCHED_HEAD = re.compile(r"^[○〇]\s*(?P<hm>\d{1,2}:\d{2})\s*(?P<name>.+?)\s*$")
_IMP = {"☆": "高", "◎": "中", "◇": "低"}


def r1(v):
    return None if v is None else round(v, 1)


def r2(v):
    return None if v is None else round(v, 2)


def norm(s: str) -> str:
    """照合用の名前: NFKC・空白と記号を落とす・「（PMI）」のような略称の括弧は残す。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"^【[^】]*】", "", s)
    return re.sub(r"[\s・、,。:：〔〕\[\]「」『』]", "", s)


def _bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def similar(a: str, b: str) -> float:
    """名前の似かた（文字の2つ組の重なり）。片方がもう片方を含めば 1。"""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / max(1, min(len(x), len(y)))


def to_num(text) -> float | None:
    """「+1.2％」「▲0.4％」「±0.0％」「19.4万件」「-886億ドル」→ 数（単位は落とす）。範囲（3.75-4.00）は上の端。読めなければ None。"""
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", str(text)).strip()
    m = re.match(r"^([+\-−▲±]?)(\d[\d,]*(?:\.\d+)?)(?:\s*[-~〜]\s*(\d[\d,]*(?:\.\d+)?))?", s)
    if not m:
        return None
    v = float((m.group(3) or m.group(2)).replace(",", ""))
    return -v if m.group(1) in ("-", "−", "▲") else v


def unit_of(text) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).strip()
    m = re.match(r"^[+\-−▲±]?\d[\d,]*(?:\.\d+)?(?:\s*[-~〜]\s*\d[\d,]*(?:\.\d+)?)?\s*(.*)$", s)
    return (m.group(1) if m else "").strip()


def clean_val(text) -> str | None:
    """表示用の値: NFKC・▲ をマイナスに・前後の空白を落とす。「-」「なし」は None。"""
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", str(text)).strip()
    if s in ("", "-", "なし", "—"):
        return None
    return s.replace("▲", "-")


def country_of(name: str) -> str:
    """指標の名前から国・地域。期間（10月・7-9月期・前週分の）の直後の国名か、表題の頭の中銀の名前。国名が無ければ日本。"""
    s = unicodedata.normalize("NFKC", str(name or "")).strip()
    m = re.match(rf"^(?:{_PERIOD})\s*(.*)$", s)
    head = (m.group(1) if m else s)[:8]
    for c, pat in _COUNTRY:
        if re.match(rf"(?:{pat})", head):
            return c
    if not m:          # 中銀の名前（「ロシア中銀、」「FOMC、」「英中銀MPC、」）は頭にある
        for c, pat in _COUNTRY:
            if re.search(rf"^(?:{pat})", s):
                return c
    return "日本"


def classify(name: str) -> str:
    s = unicodedata.normalize("NFKC", str(name or ""))
    for cat, pat in DIRECTIONS:
        if re.search(pat, s):
            return cat
    return "other"


def tone_of(cat: str, surprise: str | None) -> str | None:
    if surprise not in ("up", "down"):
        return None
    if cat == "growth":
        return "strong" if surprise == "up" else "weak"
    if cat == "inflation":
        return "hot" if surprise == "up" else "cool"
    if cat == "labor_inv":
        return "weak" if surprise == "up" else "strong"
    return None


def _surprise(actual: float | None, forecast: float | None) -> tuple[float | None, str | None]:
    if actual is None or forecast is None:
        return None, None
    d = round(actual - forecast, 6)
    return d, ("up" if d > 0 else "down" if d < 0 else "inline")


def _floor5(iso: str | None) -> str | None:
    if not iso or "T" not in iso:
        return None
    try:
        t = datetime.fromisoformat(iso[:16])
    except ValueError:
        return None
    return t.replace(minute=t.minute - t.minute % 5).strftime("%Y-%m-%dT%H:%M")


def make_row(name: str, actual: str | None, ref: str | None, kind: str, at: str | None, src: dict,
             full: str | None = None, prior: str | None = None, revised: bool = False, source: str = "wire") -> dict:
    """1本の指標の記録（純関数）。kind は予想か前回（予想が無い指標は「前回」と並べて出る）。"""
    actual, ref = clean_val(actual), clean_val(ref)
    forecast = ref if kind == "予想" else None
    if kind == "前回" and not prior:
        prior = ref
    a, f = to_num(actual), to_num(forecast)
    diff, sup = _surprise(a, f)
    cat = classify(full or name)
    day = (at or "")[:10] or None
    row = {"id": f"{day or '-'}|{norm(name)}", "at": at, "date": day, "country": country_of(name), "name": name,
           "actual": actual, "forecast": forecast, "prior": clean_val(prior), "revised": bool(revised),
           "unit": unit_of(actual), "diff": r2(diff) if diff is not None else None, "surprise": sup,
           "cat": cat, "tone": tone_of(cat, sup), "src": src, "source": source}
    if full and norm(full) != norm(name):
        row["full"] = full
    return row


def parse_policy(title: str, at: str | None, src: dict) -> dict | None:
    """「ロシア中銀、政策金利を14.00％で据え置き 予想通り」「FOMC、FFレート誘導目標3.75-4.00％に引き上げ、 予想通り」。"""
    t = unicodedata.normalize("NFKC", re.sub(r"^【[^】]*】\s*", "", title)).strip()
    m = _POLICY.match(t)
    if not m:
        return None
    bank = m.group("bank").strip()
    act = {"で据え置き": "据え置き", "に引き上げ": "引き上げ", "に引き下げ": "引き下げ"}[m.group("act")]
    exp = (m.group("exp") or "").strip()
    sup = "inline" if exp.startswith("予想通り") else ("surprise" if "予想外" in exp else None)
    name = f"{bank} {m.group('what').strip()}"
    day = (at or "")[:10] or None
    return {"id": f"{day or '-'}|{norm(name)}", "at": at, "date": day, "country": country_of(bank), "name": name,
            "actual": re.sub(r"\s+", "", m.group("rate")), "decision": act, "forecast": "予想通り" if sup == "inline" else None,
            "prior": None, "revised": False, "unit": "%", "diff": None, "surprise": "inline" if sup == "inline" else None,
            "unexpected": sup == "surprise", "cat": "policy", "tone": None, "src": src, "source": "wire"}


def parse_result_wire(w: dict) -> list[dict]:
    """【指標】の短信1本 → 指標の記録の列。表題と本文の頭の行が「名前 結果、予想 X」、※の後ろが詳細（予想・前回・改）。"""
    title = unicodedata.normalize("NFKC", re.sub(r"^【[^】]*】\s*", "", w.get("title") or "")).strip()
    at = _floor5(w.get("published_at"))
    src = {"title": w.get("title"), "url": w.get("url")}
    pol = parse_policy(title, at, src)
    if pol:
        return [pol]
    heads, details = [title], []
    after = False
    for line in (w.get("body") or "").split("\n"):
        line = unicodedata.normalize("NFKC", line).strip()
        if not line:
            continue
        if line.startswith("※"):
            after = True
            continue
        if after:
            m = _DETAIL.match(line)
            if m:
                details.append(m)
        elif line != title:
            heads.append(line)
    out = []
    for i, h in enumerate(heads):
        m = _RES.match(h)
        if not m:
            continue
        name = m.group("name").strip()
        d = details[i] if i < len(details) else next((x for x in details if similar(x.group("name"), name) >= 0.6), None)
        prior = revised = None
        full = None
        if d:
            full = d.group("name").strip()
            p = d.group("prior").strip()
            revised = "改" in p
            prior = re.sub(r"[・\s]*改.*$", "", p)
        out.append(make_row(name, m.group("actual"), m.group("ref"), m.group("kind"), at, src,
                            full=full, prior=prior, revised=bool(revised)))
    return out


def parse_schedule_wire(w: dict) -> list[dict]:
    """【指標発表予定】の短信 → 予定の列（時刻・名前・前回値・予想・重要度）。"""
    day = (w.get("published_at") or "")[:10]
    if not day:
        return []
    out, cur = [], None
    for line in (w.get("body") or "").split("\n"):
        line = unicodedata.normalize("NFKC", line).strip()
        m = _SCHED_HEAD.match(line)
        if m:
            cur = {"at": _at(day, m.group("hm"), w.get("published_at")), "name": m.group("name"), "imp": None,
                   "forecast": None, "prior": None, "src": {"title": w.get("title"), "url": w.get("url")}}
            out.append(cur)
            continue
        if not cur:
            continue
        kv = re.match(r"^(前回値|予想|重要度)\s*(.*)$", line)
        if kv:
            v = kv.group(2).strip()
            if kv.group(1) == "前回値":
                cur["prior"] = clean_val(v)
            elif kv.group(1) == "予想":
                cur["forecast"] = clean_val(v)
            else:
                cur["imp"] = v[:1] if v[:1] in ("高", "中", "低") else None
    for x in out:
        x["country"] = country_of(x["name"])
    return out


def _at(day: str, hm: str, pub: str | None = None, next_day: bool = False) -> str:
    """予定の時刻。配信より前の時刻（深夜の発表を前日の夕方に予告）なら翌日にする。"""
    h, mi = (int(x) for x in hm.split(":"))
    d = date.fromisoformat(day)
    if next_day or h >= 24:
        d += timedelta(days=1)
        h %= 24
    at = f"{d.isoformat()}T{h:02d}:{mi:02d}"
    if pub and len(pub) >= 16 and at < pub[:16] and not next_day:
        # 予定の短信は発表の15分前に出る。配信より前の時刻なら日付をまたいでいる
        at = f"{(d + timedelta(days=1)).isoformat()}T{h:02d}:{mi:02d}"
    return at


def parse_event_schedule(art: dict, now: datetime) -> list[dict]:
    """「26日のイベントスケジュール（時間は日本時間）」の本文 → その日の予定（時刻・重要度・予想）。休場の行は note に。"""
    head = unicodedata.normalize("NFKC", art.get("headline") or "")
    m = re.search(r"(\d{1,2})日の", head)
    if not m:
        return []
    base = _day_of(int(m.group(1)), now)
    if not base:
        return []
    out, area = [], None
    for line in (art.get("body") or "").split("\n"):
        line = unicodedata.normalize("NFKC", line).strip()
        if line.startswith("<国内>") or line.startswith("＜国内＞"):
            area = "国内"
            continue
        if line.startswith("<海外>") or line.startswith("＜海外＞"):
            area = "海外"
            continue
        e = _EV.match(line)
        if not e:
            continue
        name = e.group("name").strip()
        if not e.group("hm"):
            if "休場" in name or "短縮" in name:
                out.append({"at": None, "date": base.isoformat(), "name": name, "note": True, "area": area})
            continue
        d = base
        if e.group("day") and int(e.group("day")) != base.day:
            d = base + timedelta(days=1)
        h, mi = (int(x) for x in e.group("hm").split(":"))
        out.append({"at": f"{d.isoformat()}T{h:02d}:{mi:02d}", "date": d.isoformat(), "name": name,
                    "imp": _IMP.get(e.group("imp") or ""), "forecast": clean_val(e.group("fc")), "area": area,
                    "country": "日本" if area == "国内" else country_of(name),
                    "src": {"title": art.get("headline"), "url": art.get("url")}})
    return out


def _day_of(day: int, now: datetime) -> date | None:
    """「26日」を、今日にいちばん近いその日の日付にする（月をまたぐ前後3週間）。"""
    cands = [(now + timedelta(days=k)).date() for k in range(-21, 22)]
    cands = [d for d in cands if d.day == day]
    return min(cands, key=lambda d: abs((d - now.date()).days)) if cands else None


def parse_result_list(art: dict, now: datetime) -> list[dict]:
    """「欧米市場の主な経済指標・23日」「日本、オセアニア時間の主な経済指標・26日」→ 発表値と前回（予想は無い）。

    名前の行のあとに「（前月比） ▲0.4％ 0.7％・改」「先行指数 118.0 118.0」「49.3 50.6」の値の行が続く。"""
    head = unicodedata.normalize("NFKC", art.get("headline") or "")
    m = re.search(r"・(\d{1,2})日", head)
    base = _day_of(int(m.group(1)), now) if m else None
    if not base:
        return []
    out, name, area = [], None, ("国内" if "日本" in head else None)
    val = re.compile(rf"^(?P<label>.*?)\s*(?P<a>{_NUM}\s*{_UNIT}(?:で据え置き|に引き上げ|に引き下げ)?)\s+(?P<p>{_NUM}\s*{_UNIT})(?P<rev>[・\s]*改)?\s*$")
    for line in (art.get("body") or "").split("\n"):
        line = unicodedata.normalize("NFKC", line).strip()
        if not line or line.startswith("※") or "発表値" in line:
            if line.startswith("<国内>"):
                area = "国内"
            continue
        if line.startswith("<海外>"):
            area = "海外"
            continue
        if line in ("特になし",):
            continue
        v = val.match(line)
        if v and name:
            label = v.group("label").strip()
            full = name + (label if label.startswith(("(", "（")) else (f"・{label}" if label else ""))
            a = v.group("a").strip()
            dec = re.search(r"(で据え置き|に引き上げ|に引き下げ)$", a)
            row = make_row(full, re.sub(r"(で据え置き|に引き上げ|に引き下げ)$", "", a), None, "予想", None,
                           {"title": art.get("headline"), "url": art.get("url")}, prior=v.group("p"),
                           revised=bool(v.group("rev")), source="list")
            row["date"] = base.isoformat()
            row["id"] = f"{base.isoformat()}|{norm(full)}"
            if area == "国内":
                row["country"] = "日本"
            if dec:
                row["decision"] = {"で据え置き": "据え置き", "に引き上げ": "引き上げ", "に引き下げ": "引き下げ"}[dec.group(1)]
                row["cat"] = "policy"
            out.append(row)
        elif not v:
            name = line
    return out


_WK_DAY = re.compile(r"(\d{1,2})月(\d{1,2})日\s*[(（](.)[)）]")
_WK_TIME = re.compile(r"[(（]\s*(?:(\d{1,2})日\s*)?(\d{1,2}:\d{2})\s*[)）]\s*$")


def parse_week(art: dict, now: datetime) -> dict | None:
    """株探「今週の【重要イベント】…（10月26日～11月1日）」→ 日ごとの予定（★が重要。海外決算は★の会社だけ）。"""
    body = art.get("body") or ""
    days, cur, area = [], None, None
    for raw in body.split("\n"):
        line = unicodedata.normalize("NFKC", raw).strip()
        m = _WK_DAY.search(line)
        if m and "―" in raw or (m and line.startswith("-")):
            d = _month_day(int(m.group(1)), int(m.group(2)), now)
            cur = {"date": d.isoformat(), "items": []}
            days.append(cur)
            area = None
            continue
        if not cur or not line:
            continue
        if line.startswith("◆国内"):
            area = "国内"
            continue
        if line.startswith("◆国際") or line.startswith("◆海外"):
            area = "海外"
            continue
        if line.startswith("【海外決算】"):
            area = "決算"
            continue
        if line.startswith("◆"):
            area = None          # 新規上場・市場変更などは載せない
            continue
        if area == "決算":
            for part in re.split(r"/", line):
                mm = re.match(r"^\[(.+?)\](.*)$", part.strip())
                if not mm:
                    continue
                stars = [x.strip()[1:] for x in mm.group(2).split("、") if x.strip().startswith("★")]
                if stars:
                    cur["items"].append({"area": "決算", "star": True, "name": f"[{mm.group(1)}] " + "、".join(stars)})
            continue
        if area and line[:1] in ("・", "★"):
            name = line[1:].strip()
            t = _WK_TIME.search(name)
            hm = None
            if t:
                hm = t.group(2) if not t.group(1) else f"{t.group(1)}日{t.group(2)}"
                name = name[:t.start()].strip()
            cur["items"].append({"area": area, "star": line[0] == "★", "name": name, "time": hm})
    if not days:
        return None
    return {"from": days[0]["date"], "to": days[-1]["date"], "days": days,
            "src": {"title": art.get("headline"), "url": art.get("url")}}


def _month_day(mo: int, dy: int, now: datetime) -> date:
    y = now.year
    d = date(y, mo, dy)
    if (d - now.date()).days > 200:
        d = date(y - 1, mo, dy)
    elif (now.date() - d).days > 200:
        d = date(y + 1, mo, dy)
    return d


# ==================== そろえる ====================
def attach_schedule(rows: list[dict], sched: list[dict]) -> None:
    """結果の記録に、予定（時刻・重要度・予想・前回）を名前と時刻で突き合わせて足す。発表の時刻は予定の時刻を優先する。"""
    for r in rows:
        best, score = None, 0.0
        for s in sched:
            if not s.get("at"):
                continue
            if r.get("at"):
                gap = abs((datetime.fromisoformat(s["at"]) - datetime.fromisoformat(r["at"])).total_seconds()) / 60
                if gap > 30:
                    continue
            elif s["at"][:10] != r.get("date"):
                continue
            sim = max(similar(s["name"], r["name"]), similar(s["name"], r.get("full") or ""))
            if sim >= 0.55 and sim > score:
                best, score = s, sim
        if not best:
            continue
        if best.get("imp") and not r.get("imp"):
            r["imp"] = best["imp"]
        if not r.get("at") or abs((datetime.fromisoformat(best["at"]) - datetime.fromisoformat(r["at"])).total_seconds()) <= 1800:
            r["at"] = best["at"]
            r["date"] = best["at"][:10]
        if not r.get("forecast") and best.get("forecast") and r.get("cat") != "policy":
            r["forecast"] = best["forecast"]
            diff, sup = _surprise(to_num(r.get("actual")), to_num(best["forecast"]))
            r["diff"], r["surprise"] = (r2(diff) if diff is not None else None), sup
            r["tone"] = tone_of(r["cat"], sup)
        if not r.get("prior") and best.get("prior"):
            r["prior"] = best["prior"]


def merge_rows(old: dict, new: list[dict]) -> dict:
    """id ごとに記録をまとめる。新しい記録で欠けている項目は古い記録から残す（取り直しで情報を減らさない）。
    一覧（予想が無い）の記録は、同じ日に名前の似た短信の記録があれば足さない。"""
    out = dict(old)
    for r in new:
        if r.get("source") == "list":
            twin = next((x for x in out.values() if x.get("date") == r.get("date") and x.get("source") != "list"
                         and max(similar(x.get("full") or x["name"], r["name"]), similar(x["name"], r["name"])) >= 0.7), None)
            if twin:
                if not twin.get("prior") and r.get("prior"):
                    twin["prior"], twin["revised"] = r["prior"], r.get("revised")
                continue
        prev = out.get(r["id"])
        if prev:
            for k, v in prev.items():
                if r.get(k) in (None, "", False) and v not in (None, "", False):
                    r[k] = v
        out[r["id"]] = r
    return out


# ==================== 測る（5分足） ====================
def price_at(bars: list[tuple], starts: list[int], t: int, step: int = 300) -> float | None:
    """時刻 t（epoch 秒）の値段: 終わりが t 以前の最後の足の終値。STALE_MIN より古ければ None。"""
    i = bisect.bisect_right(starts, t - step) - 1
    if i < 0:
        return None
    if t - (starts[i] + step) > STALE_MIN * 60:
        return None
    return bars[i][4]


def _chg(p0, p1, spec) -> float | None:
    if p0 is None or p1 is None:
        return None
    if spec.get("pct"):
        return (p1 / p0 - 1) * 100 if p0 else None
    return (p1 - p0) * spec.get("scale", 1.0)


def _epoch(at: str) -> int:
    return int(datetime.fromisoformat(at).replace(tzinfo=JST).timestamp())


def baseline(bars, starts, t: int, spec: dict) -> float | None:
    """ふだんの15分の動き（絶対値）の中央値: 直前 BASE_DAYS 営業日の、同じ時刻の前後 BASE_WIN 分。"""
    vals, days = [], 0
    for k in range(1, BASE_DAYS * 3):
        if days >= BASE_DAYS:
            break
        base_t = t - k * 86400
        got = []
        for off in range(-BASE_WIN, BASE_WIN + 1, 5):
            t0 = base_t + off * 60
            c = _chg(price_at(bars, starts, t0), price_at(bars, starts, t0 + AFTER[0] * 60), spec)
            if c is not None:
                got.append(abs(c))
        if len(got) >= 6:
            vals += got
            days += 1
    return median(vals) if len(vals) >= BASE_MIN else None


def react_at(at: str, series: dict, now: datetime) -> dict | None:
    """発表時刻 at の反応（純関数）。series: {key: 5分足の列}。15分後がまだ来ていなければ None。"""
    t = _epoch(at)
    if now.timestamp() < t + AFTER[0] * 60:
        return None
    moves = {}
    for spec in INSTR:
        bars = series.get(spec["key"]) or []
        if not bars:
            continue
        starts = [b[0] for b in bars]
        p0 = price_at(bars, starts, t)
        if p0 is None:
            continue
        mv = {}
        for m in AFTER:
            if now.timestamp() >= t + m * 60:
                c = _chg(p0, price_at(bars, starts, t + m * 60), spec)
                if c is not None:
                    mv[f"d{m}"] = round(c, spec["digits"])
        if "d15" not in mv:
            continue
        base = baseline(bars, starts, t, spec)
        if base:
            mv["ratio"] = round(abs(mv["d15"]) / base, 1)
        moves[spec["key"]] = mv
    if not moves:
        return None
    ratios = [m.get("ratio") for m in moves.values() if m.get("ratio") is not None]
    top = max(ratios) if ratios else None
    level = "big" if top is not None and top >= BIG_RATIO else "move" if top is not None and top >= MOVE_RATIO else "quiet"
    return {"at": at, "moves": moves, "level": level, "max_ratio": top}


def reaction_targets(rows: dict, now: datetime) -> list[str]:
    """反応を測る発表時刻: 主要国・時刻の分かる記録で、まだ測っていないか60分後がまだだったもの（REACT_DAYS 日以内）。"""
    cut = (now - timedelta(days=REACT_DAYS)).strftime("%Y-%m-%dT%H:%M")
    return sorted({r["at"] for r in rows.values() if r.get("at") and r["at"] >= cut and r.get("country") in MAJOR
                   and r["at"] <= now.strftime("%Y-%m-%dT%H:%M")})


# ==================== まとめ ====================
def known_urls(hist: dict) -> set:
    return set((hist or {}).get("seen") or [])


def load() -> dict:
    try:
        with open(INDICATORS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save(hist: dict) -> None:
    os.makedirs(os.path.dirname(INDICATORS_PATH), exist_ok=True)
    tmp = INDICATORS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, INDICATORS_PATH)


def update(hist: dict, press: dict, kabutan_articles: list[dict], now: datetime) -> dict:
    """短信・一覧・予定を読み、indicators.json の中身（rows・schedule・week・seen）を更新して返す（純関数。保存は呼び出し側）。"""
    hist = dict(hist or {})
    wires = (press or {}).get("wire_bodies") or []
    results, sched = [], []
    for w in wires:
        if w.get("kind") == "result":
            results += parse_result_wire(w)
        elif w.get("kind") == "schedule":
            sched += parse_schedule_wire(w)
    for a in (press or {}).get("macro_articles") or []:
        h = a.get("headline") or ""
        if "イベントスケジュール" in h:
            sched += [s for s in parse_event_schedule(a, now)]
        elif "主な経済指標" in h:
            results += parse_result_list(a, now)
    # 予定は日付ごとに残す（朝のイベントスケジュールを大引でも使う。短信の予定は前回値・重要度が細かいので上書きしない）
    store = {k: list(v) for k, v in (hist.get("schedule") or {}).items()}
    for s in sched:
        day = (s.get("at") or s.get("date") or "")[:10]
        if not day:
            continue
        lst = store.setdefault(day, [])
        twin = next((x for x in lst if x.get("at") == s.get("at") and similar(x["name"], s["name"]) >= 0.6), None)
        if twin:
            for k, v in s.items():
                if twin.get(k) in (None, "") and v not in (None, ""):
                    twin[k] = v
        else:
            lst.append(s)
    all_sched = [s for v in store.values() for s in v]
    attach_schedule(results, all_sched)
    rows = merge_rows(hist.get("rows") or {}, results)
    # 結果が出た予定に印を付ける（画面の「今日の予定」から発表済みを外すため）
    for s in all_sched:
        if s.get("note") or not s.get("at"):
            continue
        s["done"] = any(r.get("at") == s["at"] and max(similar(r["name"], s["name"]), similar(r.get("full") or "", s["name"])) >= 0.55
                        for r in rows.values())
    for a in kabutan_articles or []:
        if "重要イベント" in (a.get("headline") or ""):
            wk = parse_week(a, now)
            if wk and (not hist.get("week") or wk["from"] >= (hist["week"].get("from") or "")):
                hist["week"] = wk
    seen = set(hist.get("seen") or []) | {w["url"] for w in wires if w.get("url")}
    keep_from = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    hist["rows"] = {k: v for k, v in rows.items() if (v.get("date") or "9") >= keep_from}
    sched_from = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    hist["schedule"] = {k: v for k, v in store.items() if k >= sched_from}
    # 読んだ短信の URL は一覧に載っている間（数日）だけ要る。古いものから捨てる
    hist["seen"] = sorted(seen)[-600:]
    hist["updated_at"] = now.isoformat(timespec="seconds")
    return hist


def measure(hist: dict, fetch_series, now: datetime) -> dict:
    """反応の未計算・途中の発表時刻について、5分足を取って反応を測る（hist["react"] を更新して返す）。
    fetch_series(symbol, start_date, end_date) → 5分足の列（cnbc.fetch_intraday）。"""
    react = dict(hist.get("react") or {})
    todo = [at for at in reaction_targets(hist.get("rows") or {}, now)
            if not (react.get(at) or {}).get("done")]
    if not todo:
        hist["react"] = react
        return hist
    first = datetime.fromisoformat(min(todo)) - timedelta(days=BASE_DAYS * 2 + 2)
    series = {}
    for spec in INSTR:
        got = fetch_series(spec["symbol"], first.date(), now.date())
        if got:
            series[spec["key"]] = got
    for at in todo:
        r = react_at(at, series, now)
        if r:
            r["done"] = now.timestamp() >= _epoch(at) + AFTER[-1] * 60 + 600
            react[at] = r
    cut = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    hist["react"] = {k: v for k, v in react.items() if k >= cut}
    return hist


def group_tone(rows: list[dict]) -> str | None:
    """同じ時刻に出た指標の向きをまとめる: 景気（強い＋1・弱い−1）と物価（高い＋1・低い−1）の合計で多い方。重要度の高い指標は2倍。"""
    g = sum((2 if r.get("imp") == "高" else 1) * {"strong": 1, "weak": -1}.get(r.get("tone"), 0) for r in rows)
    p = sum((2 if r.get("imp") == "高" else 1) * {"hot": 1, "cool": -1}.get(r.get("tone"), 0) for r in rows)
    if abs(p) > abs(g):
        return "hot" if p > 0 else "cool"
    if g:
        return "strong" if g > 0 else "weak"
    return "mixed" if any(r.get("tone") for r in rows) else None


def stats(hist: dict, now: datetime, country: str = "米") -> dict:
    """記録の集計: 米国の指標が出た時刻ごとに向きをまとめ、向きごとの15分の反応の平均と、株・金利がどちらに動いた割合。"""
    cut = (now - timedelta(days=STATS_DAYS)).strftime("%Y-%m-%d")
    by_at: dict[str, list[dict]] = {}
    for r in (hist.get("rows") or {}).values():
        if r.get("country") == country and r.get("at") and r["at"] >= cut:
            by_at.setdefault(r["at"], []).append(r)
    groups: dict[str, list[dict]] = {}
    for at, rs in by_at.items():
        rx = (hist.get("react") or {}).get(at)
        tone = group_tone(rs)
        if not rx or tone not in TONE_LABEL:
            continue
        groups.setdefault(tone, []).append(rx["moves"])
    out = {"country": country, "days": STATS_DAYS, "rows": []}
    for tone in ("strong", "weak", "hot", "cool"):
        ms = groups.get(tone) or []
        row = {"tone": tone, "label": TONE_LABEL[tone], "n": len(ms), "small": len(ms) < SMALL}
        for spec in INSTR:
            xs = [m[spec["key"]]["d15"] for m in ms if spec["key"] in m]
            if xs:
                row[spec["key"]] = {"mean": round(mean(xs), spec["digits"] + 1), "up": round(sum(1 for x in xs if x > 0) / len(xs) * 100),
                                    "n": len(xs)}
        out["rows"].append(row)
    out["n"] = sum(r["n"] for r in out["rows"])
    return out


def _fmt_row(r: dict) -> str:
    parts = [f"{r['name']} {r.get('actual') or '—'}"]
    if r.get("cat") == "policy":
        parts = [f"{r['name']} {r.get('actual')}" + (f"で{r['decision']}" if r.get("decision") else "")]
        if r.get("forecast"):
            parts.append(r["forecast"])
        return "・".join(parts)
    if r.get("forecast"):
        parts.append(f"予想 {r['forecast']}")
    elif r.get("prior"):
        parts.append(f"前回 {r['prior']}")
    return "（".join(parts) + ("）" if len(parts) > 1 else "")


def view(hist: dict, slot: str, now: datetime, since: str, sched_until: str) -> dict:
    """画面と要約に出す分: since（JST の ISO）以降の結果と反応、今から sched_until までの予定、今週の予定。"""
    rows = [r for r in (hist.get("rows") or {}).values()
            if (r.get("at") or (r.get("date") or "") + "T23:59") >= since and (r.get("at") or r.get("date") or "") <= now.strftime("%Y-%m-%dT%H:%M")]
    rows.sort(key=lambda r: (r.get("at") or r.get("date") or "", 0 if r.get("country") in MAJOR else 1), reverse=True)
    react = {at: rx for at, rx in (hist.get("react") or {}).items() if at >= since}
    # 予定: 今から sched_until まで（発表済みは外す）。休場の行は今日の分だけ
    nowk = now.strftime("%Y-%m-%dT%H:%M")
    sched = []
    for day, items in sorted((hist.get("schedule") or {}).items()):
        for s in items:
            if s.get("note"):
                if day == now.date().isoformat():
                    sched.append(s)
                continue
            if s.get("at") and nowk <= s["at"] <= sched_until and not s.get("done"):
                sched.append(s)
    sched.sort(key=lambda s: s.get("at") or "")
    # 見出し: 主要国の結果の上振れ・下振れと向き、反応が大きかった時刻
    major = [r for r in rows if r.get("country") in MAJOR]
    head = None
    if major:
        by_c: dict[str, list[dict]] = {}
        for r in major:
            by_c.setdefault(r["country"], []).append(r)
        bits = []
        for c in MAJOR:
            rs = by_c.get(c)
            if not rs:
                continue
            up = sum(1 for r in rs if r.get("surprise") == "up")
            dn = sum(1 for r in rs if r.get("surprise") == "down")
            st = sum(1 for r in rs if r.get("tone") in ("strong",))
            wk = sum(1 for r in rs if r.get("tone") in ("weak",))
            hot = sum(1 for r in rs if r.get("tone") == "hot")
            cool = sum(1 for r in rs if r.get("tone") == "cool")
            if not (up or dn):
                bits.append(f"{c}{len(rs)}本（予想との比較なし）" if not any(r.get("forecast") for r in rs) else f"{c}{len(rs)}本（予想どおり）")
                continue
            t = f"{c}{len(rs)}本（上振れ{up}・下振れ{dn}"
            extra = []
            if st or wk:
                extra.append(f"景気 強{st}・弱{wk}")
            if hot or cool:
                extra.append(f"物価 高{hot}・低{cool}")
            bits.append(t + ("｜" + "・".join(extra) if extra else "") + "）")
        head = "、".join(bits[:3])
        # 反応がいちばん大きかった時刻: いちばん倍率の大きかった値段で言う（小さければ「反応は小さい」）
        best = None
        for rx in react.values():
            for k, mv in (rx.get("moves") or {}).items():
                if mv.get("ratio") is not None and (best is None or mv["ratio"] > best[2]["ratio"]):
                    best = (rx, k, mv)
        if best and best[2]["ratio"] >= MOVE_RATIO:
            rx, k, mv = best
            names = [r["name"] for r in major if r.get("at") == rx["at"]][:1]
            spec = next(s for s in INSTR if s["key"] == k)
            head += (f"。反応が大きかったのは {rx['at'][11:16]}" + (f"（{names[0]}）" if names else "") +
                     f"の発表後15分の{spec['label']} {mv['d15']:+.{spec['digits']}f}{spec['unit']}（ふだんの{mv['ratio']}倍）")
        elif react:
            head += "。発表の前後の値動きはどれもふだん並み"
    elif sched:
        nxt = next((s for s in sched if s.get("imp") == "高"), None) or next((s for s in sched if not s.get("note")), None)
        if nxt:
            head = f"結果はまだ無し。次は {nxt['at'][11:16]} {nxt['name']}" + (f"（予想 {nxt['forecast']}）" if nxt.get("forecast") else "")
    week = hist.get("week")
    if week and not (week.get("from", "") <= now.date().isoformat() <= (week.get("to") or "")):
        week = None
    return {"asof": now.isoformat(timespec="minutes"), "slot": slot, "since": since, "until": sched_until,
            "rows": rows[:60], "react": react, "schedule": sched[:30], "week": week, "headline": head,
            "stats": stats(hist, now), "labels": {"tone": TONE_LABEL, "surprise": SURPRISE_LABEL},
            "instr": [{k: s[k] for k in ("key", "label", "unit", "digits")} for s in INSTR],
            "rules": {"after": list(AFTER), "base_days": BASE_DAYS, "base_win": BASE_WIN, "big": BIG_RATIO, "move": MOVE_RATIO,
                      "major": list(MAJOR), "small": SMALL}}


def windows(slot: str, now: datetime, prev_jp: str | None) -> tuple[str, str]:
    """区分ごとの窓: 寄り前は前営業日の東京の引け（15:30）から、前場・大引は今朝 7:00 から。予定は翌朝 7:00 まで。"""
    if slot == "preopen":
        since = f"{prev_jp}T15:30" if prev_jp else (now - timedelta(hours=16)).strftime("%Y-%m-%dT%H:%M")
    else:
        since = now.strftime("%Y-%m-%dT07:00")
    until = (now + timedelta(days=1)).strftime("%Y-%m-%dT07:00")
    return since, until


def lines_for_brief(v: dict, ref=None) -> list[str]:
    """ai_brief の行（数字は付け直さない）。ref(title, url) は参照番号（「[W3]」）を返す関数。"""
    ref = ref or (lambda title, url: "")
    out = []
    rows = v.get("rows") or []
    major = [r for r in rows if r.get("country") in MAJOR]
    other = [r for r in rows if r.get("country") not in MAJOR]
    react = v.get("react") or {}
    for r in major[:30]:
        tag = []
        if r.get("surprise"):
            tag.append(SURPRISE_LABEL.get(r["surprise"], ""))
        if r.get("tone"):
            tag.append(TONE_LABEL[r["tone"]])
        if r.get("revised"):
            tag.append("前回は改定")
        line = (f"{(r.get('at') or r.get('date') or '')[5:16].replace('T', ' ')} {r['country']} {_fmt_row(r)}"
                + (f" 前回{r['prior']}" if r.get("prior") and r.get("forecast") else "")
                + (f" 重要度{r['imp']}" if r.get("imp") else "") + (f"［{'・'.join(t for t in tag if t)}］" if tag else ""))
        src = r.get("src") or {}
        out.append(line + ref(src.get("title") or r["name"], src.get("url")))
    if other:
        out.append("その他の国: " + "、".join(f"{r['country']} {_fmt_row(r)}" for r in other[:12]))
    for at in sorted(react, reverse=True)[:10]:
        rx = react[at]
        bits = []
        for spec in INSTR:
            m = rx["moves"].get(spec["key"])
            if not m:
                continue
            bits.append(f"{spec['label']} {m['d15']:+.{spec['digits']}f}{spec['unit']}"
                        + (f"（ふだんの{m['ratio']}倍）" if m.get("ratio") is not None else "")
                        + (f"→60分 {m['d60']:+.{spec['digits']}f}" if m.get("d60") is not None else ""))
        names = [r["name"] for r in major if r.get("at") == at]
        out.append(f"反応 {at[5:16].replace('T', ' ')}（{'・'.join(names[:3]) or '—'}）15分: " + "、".join(bits)
                   + {"big": "［大きく反応］", "move": "［反応］", "quiet": "［小さい］"}.get(rx.get("level"), ""))
    sch = [s for s in v.get("schedule") or [] if not s.get("note")]
    if sch:
        out.append("予定: " + "、".join(f"{s['at'][5:16].replace('T', ' ')}{('[' + s['imp'] + ']') if s.get('imp') else ''} {s['name']}"
                                     + (f"（予想 {s['forecast']}）" if s.get("forecast") else "") for s in sch[:14]))
    notes = [s["name"] for s in v.get("schedule") or [] if s.get("note")]
    if notes:
        out.append("休場など: " + "、".join(notes))
    st = v.get("stats") or {}
    if st.get("n"):
        bits = []
        for r in st.get("rows") or []:
            if not r["n"]:
                continue
            us = r.get("us10y") or {}
            es = r.get("es") or {}
            bits.append(f"{r['label']} {r['n']}回: 米10年 {us.get('mean', '—')}bp（上げ{us.get('up', '—')}%）・S&P先物 {es.get('mean', '—')}%（上げ{es.get('up', '—')}%）")
        out.append(f"記録（米国の指標・直近{st.get('days')}日、15分の反応の平均。{SMALL}回未満は少ない）: " + "／".join(bits))
    wk = v.get("week")
    if wk:
        days = []
        today = v.get("asof", "")[:10]
        for d in wk.get("days") or []:
            if d["date"] < today:
                continue
            stars = [f"{x['name']}" + (f"({x['time']})" if x.get("time") else "") for x in d["items"] if x.get("star")]
            if stars:
                days.append(f"{d['date'][5:]} " + "・".join(stars[:6]))
        if days:
            out.append("今週の重要（株探の★）: " + "／".join(days[:6]))
    return out
