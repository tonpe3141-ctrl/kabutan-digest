"""Routine 用の材料の要約（トークンを減らす。DESIGN.md 23章）。

latest.json の1区分は 15〜20万字あり、そのまま読むとトークンの大半が使われない本文・URL・数値の桁に消える。
ここで「見立て・マクロ・決算・買う候補の理由づけ」に要る分だけを、短い行に詰めて出す:

  - 記事の本文は先頭だけ（相場の記事は長め、ほかは短め）。見出しは全部ではなく新しい順に上限まで
  - URL は出さず、[K3] のような参照番号を付ける。番号と URL の対応は /tmp/ai_refs_{SLOT}.json に書き、
    tools/ai_write.py が sources の番号を {title, url} に戻す（Routine は番号だけを書けばよい）
  - 数値は表示に要る桁に丸める

使い方:  python tools/ai_brief.py taibike            # 標準出力に要約
         python tools/ai_brief.py taibike --max 60000 # 字数の上限（超えたら本文から削る）
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LATEST = os.path.join(ROOT, "docs", "data", "latest.json")
REFS = "/tmp/ai_refs_{slot}.json"


class Brief:
    def __init__(self):
        self.lines: list[str] = []
        self.refs: dict[str, dict] = {}
        self.count: dict[str, int] = {}

    def ref(self, prefix: str, title: str, url: str | None, source: str | None = None) -> str:
        if not url:
            return ""
        n = self.count.get(prefix, 0) + 1
        self.count[prefix] = n
        key = f"{prefix}{n}"
        self.refs[key] = {"title": f"{source}: {title}" if source else title, "url": url}
        return f"[{key}]"

    def h(self, title: str):
        self.lines.append(f"\n## {title}")

    def add(self, s: str):
        if s:
            self.lines.append(s)


def pct(v, d=1):
    return "—" if not isinstance(v, (int, float)) else f"{v:+.{d}f}%"


def num(v, d=0):
    return "—" if not isinstance(v, (int, float)) else f"{v:,.{d}f}"


def cut(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def name(s):
    return str(s or "").replace("(株)", "").replace("（株）", "").strip()


def market_session(b: Brief, d: dict, slot: str):
    b.h("指数と広がり")
    for k, x in (d.get("indices") or {}).items():
        b.add(f"{x.get('label')}: {num(x.get('close'), 2)}（{pct(x.get('change_pct'), 2)}、{num(x.get('change'), 0)}）"
              f" 高{num(x.get('high'), 0)} 安{num(x.get('low'), 0)}" + ("・取れず/休場" if x.get("stale") else ""))
    dv = d.get("divergence") or {}
    if dv:
        b.add(f"日経とTOPIXの乖離: {dv.get('label')}（{dv.get('comment')}）")
    br = d.get("breadth") or {}
    if br:
        b.add(f"225の騰落: 上{br.get('up')}・下{br.get('down')}（{br.get('comment')}）")
    ss = d.get("session_shift") or {}
    if ss.get("verdict"):
        b.add(f"前場からの変化: {ss['verdict']}")
    s33 = d.get("sectors33") or {}
    rows = s33.get("rows") or []
    if rows:
        pr = s33.get("prime") or {}
        b.add(f"東証33業種: 上{s33.get('up')}・下{s33.get('down')}／プライム 上{pr.get('up')}・下{pr.get('down')}")
        b.add("  上位: " + "、".join(f"{r['sector']}{pct(r['change_pct'], 2)}" for r in rows[:5]))
        b.add("  下位: " + "、".join(f"{r['sector']}{pct(r['change_pct'], 2)}" for r in rows[-5:]))
    st = (d.get("sector_trend") or {}).get("rows") or []
    if st:
        b.add("業種の5日・20日（225の近似）: " + "、".join(f"{r['sector']} 5日{pct(r.get('d5'))}・20日{pct(r.get('d20'))}" for r in st[:5]))
    t = d.get("tables") or {}
    for key, label, n in (("value", "売買代金", 12), ("gainer", "上昇率", 8), ("loser", "下落率", 8),
                          ("ytd_high", "年初来高値", 8), ("vol_surge", "出来高急増", 6)):
        rows = (t.get(key) or {}).get("rows") or []
        if rows:
            b.add(f"{label}（{len(rows)}）: " + "、".join(f"{name(r.get('name'))}({r.get('code')}){pct(r.get('change_pct'))}" for r in rows[:n]))
    rd = d.get("ranking_delta") or {}
    if rd.get("new") or rd.get("rank_up"):
        b.add("売買代金の新顔: " + "、".join(f"{name(r.get('name'))}{pct(r.get('change_pct'))}" for r in rd.get("new") or [])
              + "／順位を上げた: " + "、".join(f"{name(r.get('name'))} {r.get('prev_rank')}→{r.get('rank')}位" for r in rd.get("rank_up") or []))
    sk = d.get("streaks") or []
    if sk:
        b.add("売買代金上位の連続: " + "、".join(f"{name(r.get('name'))}{r.get('days')}日" for r in sk[:6]))
    tf = d.get("theme_flow") or {}
    if tf.get("top"):
        b.add("テーマの資金（上）: " + "、".join(f"{x['theme']}{pct(x.get('avg_pct'))}({x.get('count')}銘柄)" for x in tf["top"][:6]))
    if tf.get("bottom"):
        b.add("テーマの資金（下）: " + "、".join(f"{x['theme']}{pct(x.get('avg_pct'))}" for x in tf["bottom"][:4]))
    ds = d.get("disclosure_summary") or {}
    if ds:
        b.add(f"開示: {ds.get('headline')}（" + "、".join(f"{x['label']}{x['count']}" for x in ds.get("items") or []) + "）")
    for key in ("kessan_intraday", "kessan_after"):
        tb = t.get(key) or {}
        rows = tb.get("rows") or []
        if rows:
            b.add(f"{tb.get('label')}（{len(rows)}件）: " + "、".join(
                f"{name(r.get('name'))}({r.get('code')}) {r.get('category')}" for r in rows[:15]))


def preopen_session(b: Brief, d: dict):
    b.h("夜間の米国と為替・寄り前の想定")
    us = d.get("us") or {}
    b.add("米国: " + "、".join(f"{x.get('label')} {num(x.get('last'), 0)}（{pct(x.get('change_pct'), 2)}）" for x in us.values()))
    su = d.get("sectors_us") or {}
    if su:
        rows = sorted(su.values(), key=lambda x: x.get("change_pct") or 0, reverse=True)
        b.add("米セクター: " + "、".join(f"{x.get('label')}{pct(x.get('change_pct'))}" for x in rows))
    io = d.get("implied_open") or {}
    if io:
        b.add(f"想定オープン（{io.get('method_label')}）: {pct(io.get('gap_pct'), 2)}・{num(io.get('gap'), 0)}円（"
              + "、".join(f"{c.get('driver')} {c.get('display')}" for c in io.get("contributions") or []) + "）")
    rk = d.get("risk") or {}
    if rk:
        b.add(f"リスク環境: {rk.get('label')}（" + "／".join(rk.get("reasons") or []) + "）")
    so = d.get("sector_outlook") or {}
    if so:
        b.add("米国からの連想 追い風: " + "、".join(x["sector"] for x in (so.get("tailwind") or [])[:4])
              + "／向かい風: " + "、".join(x["sector"] for x in (so.get("headwind") or [])[:4]))
    co = d.get("carryover") or {}
    ps = co.get("prev_session") or {}
    if ps:
        ix = ps.get("indices") or {}
        b.add(f"前営業日 {ps.get('date')}: " + "、".join(f"{x.get('label')} {num(x.get('close'), 0)}（{pct(x.get('change_pct'), 2)}）" for x in ix.values())
              + (f"。{(ps.get('session_shift') or {}).get('verdict')}" if (ps.get("session_shift") or {}).get("verdict") else ""))
    ah = co.get("after_hours_kessan") or []
    if ah:
        b.add(f"前営業日の引け後の開示（{len(ah)}件）: " + "、".join(f"{name(r.get('name'))}({r.get('code')}) {r.get('category')}" for r in ah[:15]))


def macro(b: Brief, d: dict):
    mv = d.get("macro_view") or {}
    if not mv:
        return
    b.h("マクロ（macro_view。金利は bp、ほかは %。pos=1年の位置0〜100）")
    for r in mv.get("rows") or []:
        rs = {"high": " 約3年の最高", "low": " 約3年の最低"}.get(r.get("record") or "", "")
        b.add(f"{r.get('label')} {r.get('last')}{r.get('unit') or ''}: 前日{r.get('d1')} 5日{r.get('d5')} 20日{r.get('d20')} 60日{r.get('d60')}"
              f" pos{r.get('pos')}{rs}" + (f" 向き:{r['trend']}" if r.get("trend") else ""))
    for s in mv.get("spreads") or []:
        b.add(f"{s.get('label')}: {s.get('last')}（20日 {s.get('d20')}bp）")
    for tp in mv.get("topics") or []:
        it = (tp.get("items") or [{}])[0]
        b.add(f"話題 {tp.get('label')}: {tp.get('n')}本・{len(tp.get('media') or [])}媒体（{'・'.join(tp.get('media') or [])}）"
              f" 例: {cut(it.get('title'), 60)}{b.ref('M', it.get('title'), it.get('url'), it.get('source'))}")
    if mv.get("commentary"):
        c = mv["commentary"]
        txt = c if isinstance(c, str) else " ".join(s.get("body", "") for s in (c.get("sections") or []))
        b.add(f"ルールの文（参考）: {cut(txt, 500)}")


def thermo(b: Brief, d: dict, picks: dict):
    th = d.get("thermo") or {}
    if not th:
        return
    b.h("温度計（付け直さない）")
    b.add(f"温度{th.get('temp')}・{th.get('zone')}（前回{th.get('temp_before')}）" + (f"・{th['consensus']}" if th.get("consensus") else "")
          + (f"・{th['turning']}" if th.get("turning") else ""))
    for f in th.get("factors") or []:
        b.add(f"  {f.get('label')} {f.get('score'):+d} {f.get('change') or ''}: {f.get('text')}")
    if th.get("sector_picks"):
        b.add("押し目・下げ止まりの業種: " + "、".join(f"{x['sector']}({x.get('class')})" for x in th["sector_picks"]))
    for key, label in (("hot", "高値掴み注意"), ("good_out", "好材料出尽くし"), ("bad_out", "悪材料出尽くし")):
        xs = th.get(key) or []
        if xs:
            b.add(f"{label}: " + "、".join(name(x.get("name")) for x in xs[:6]))
    if th.get("watch_guard"):
        b.add("ウォッチの注意書き: " + "、".join(name(x.get("name")) for x in th["watch_guard"][:5]))
    st = th.get("strength") or {}
    if st.get("top"):
        b.add("業種の強さ 上位: " + "、".join(f"{x['g']}(市場差60日{pct(x.get('rs60'))}・{x.get('quad')})" for x in st["top"]))
        b.add("業種の強さ 下位: " + "、".join(f"{x['g']}({pct(x.get('rs60'))})" for x in st.get("bottom") or []))
    cw = th.get("crowd") or {}
    if cw:
        b.h("混み合い・資金の移り先（付け直さない。予測ではなく事実と振れ幅）")
        v = cw.get("verify") or {}
        hot, allv = v.get("hot") or [None, None], v.get("all") or [None, None]
        if all(hot) and all(allv):
            b.add(f"印の条件: {cw.get('rule')}。翌日に市場より{cw.get('big')}%以上 弱い割合 {hot[0]['weak']}→{hot[1]['weak']}%（全銘柄 {allv[0]['weak']}→{allv[1]['weak']}%）"
                  f"・強い割合 {hot[0]['strong']}→{hot[1]['strong']}%（{allv[0]['strong']}→{allv[1]['strong']}%）・上回った割合 {hot[0]['up']}→{hot[1]['up']}%（前半→後半）")
        for x in cw.get("watch") or []:
            m = x.get("cw") or {}
            b.add(f"ウォッチ {x['name']}({x['code']})" + (f" 印: 3日{pct(m.get('r3'))}・出来高{m.get('vr')}倍" if m else "")
                  + (f" {'置き去り' if x.get('rot') == 'out' else '買われた'}: 対市場{pct(x.get('prev'))}→{pct(x.get('now'))}" if x.get("rot") else ""))
        rot = cw.get("rotation") or {}
        if rot:
            b.add(f"対市場の基準（全銘柄の中央値）: {rot.get('prev')} {pct((rot.get('mkt') or [None, None])[0])} → {rot.get('asof')} {pct((rot.get('mkt') or [None, None])[1])}")
            for key, label in (("out", "置き去り（前の営業日の主役）"), ("into", "買われた（前の営業日は出遅れ）")):
                xs = rot.get(key) or []
                if xs:
                    b.add(f"{label}{rot.get('n_' + key)}銘柄: " + "、".join(
                        f"{name(x['name'])}({'/'.join((x.get('th') or [])[:1])}){pct(x.get('prev'))}→{pct(x.get('now'))}" for x in xs[:6]))
            if rot.get("themes"):
                b.add("テーマ別: " + "、".join(f"{t['theme']}(置き去り{t['out']}・買われ{t['into']})" for t in rot["themes"]))
    sw = th.get("swing") or {}
    earn = (picks or {}).get("earn") or {}
    b.h(f"買う候補（{sw.get('asof')} の引けで出た注文。次の営業日だけ有効。価格は付け直さない）")

    def ectx(code):
        c = earn.get(code)
        if not c:
            return ""
        own = c.get("own")
        parts = []
        if own:
            parts.append(f"自社 {own.get('date')} {own.get('dir') or '向き不明'}「{cut(own.get('head'), 40)}」")
        if c.get("n"):
            parts.append(f"連想 上{c.get('up')}下{c.get('down')}（" + "、".join(f"{x['name']}{x.get('dir') or ''}" for x in c.get("by") or []) + "）")
        if c.get("next"):
            parts.append(f"次の決算 {c['next']}")
        return " ／決算: " + "・".join(parts)
    for o in sw.get("orders") or []:
        pe = o.get("peer") or {}
        b.add(f"短期 {o['name']}({o['code']}) 終値{num(o.get('close'), 1)} 指値{o.get('limit')} 損切り{o.get('stop')}({pct(o.get('stop_pct'))})"
              f" 売り目安{o.get('sell')}" + (f" 5日{pct(o['r5'])}" if o.get("r5") is not None else "") + f" 25日線{pct(o.get('dev25'))} {pe.get('label') or ''}"
              + (f"（{pe.get('group')} 20日{pct(pe.get('g20'))}・業種差{pe.get('rel20')}pt）" if pe.get("group") else "") + ectx(o["code"]))
    for o in (sw.get("mid") or {}).get("orders") or []:
        b.add(f"中期 {o['name']}({o['code']}) 指値{o.get('limit')} 損切り{o.get('stop')}({pct(o.get('stop_pct'))}) 高値から{o.get('age')}日・{pct(o.get('dd'))}"
              f" 1年の強さ上位{max(1, 100 - (o.get('rank') or 0))}% 60営業日持つ" + ectx(o["code"]))
    if not (sw.get("orders") or (sw.get("mid") or {}).get("orders")):
        b.add("注文なし")
    v = sw.get("verify") or {}
    a, acc = v.get("all") or {}, v.get("account") or {}
    if a:
        b.add(f"検証: 短期 {a.get('n')}回 勝率{a.get('win')}%（毎日買って5日後 {(v.get('base') or {}).get('win')}%）平均{pct(a.get('avg'), 2)}"
              f" 大負け{a.get('big_loss')}%／口座 年率{pct(acc.get('cagr'))}・最大の目減り{pct(acc.get('dd'))}")
    w = (picks or {}).get("watch") or []
    if w:
        b.add("決算の連想の監視（上向き・上昇トレンド・売買代金の条件を満たす。注文ではない）: "
              + "、".join(f"{x['name']}({x['code']}) 注文対象まで{pct(x.get('to'))}" for x in w))


def earnings(b: Brief, d: dict):
    e = d.get("earnings") or {}
    items = e.get("items") or []
    if not items:
        return
    b.h(f"決算（{e.get('scope')}・{e.get('asof')}。{e.get('n_total')}社中 {e.get('n_read')}社を読んだ）")
    for it in items:
        fl = it.get("flash") or {}
        mv = it.get("move") or {}
        b.add(f"■ {it.get('name')}({it.get('code')}) {'・'.join(it.get('kinds') or [])} 向き:{it.get('dir') or '不明'} {it.get('industry') or ''}"
              f" テーマ:{'・'.join(it.get('themes') or []) or '—'} 値動き{pct(mv.get('pct'))}")
        if fl.get("headline"):
            b.add(f"  速報: {cut(fl['headline'], 80)}｜{cut(fl.get('body'), 260)}{b.ref('E', fl['headline'], fl.get('url'), '株探')}")
        for key, label, n in (("reason", "修正の理由", 300), ("overview", "概況", 300), ("outlook", "予想の説明", 200)):
            if it.get(key):
                b.add(f"  {label}: {cut(it[key], n)}")
        for p in (it.get("press") or [])[:3]:
            b.add(f"  報道: {cut(p.get('title'), 70)}{b.ref('E', p.get('title'), p.get('url'), p.get('source'))}")
        ps = it.get("peers") or []
        if ps:
            b.add("  類似: " + "、".join(
                f"{p.get('name')}({p['code']}){pct(p.get('pct'))}・20日{pct(p.get('r20'))}"
                + (f"・決算{p['next']}" if p.get("next") else "")
                + (f"・同日{p['today']}" if p.get("today") else f"・前回{(p.get('last') or {}).get('dir')}" if p.get("last") else "")
                for p in ps))
    wd = e.get("wind") or {}
    for key, label in (("industries", "業種"), ("themes", "テーマ")):
        rows = wd.get(key) or []
        if rows:
            b.add(f"風向き（{label}・直近{wd.get('days')}営業日に読んだ開示）: " + "、".join(
                f"{r['key']} 上{r['up']}下{r['down']}まち{r['mixed']}" for r in rows[:8]))
    if e.get("more"):
        b.add("読んでいない会社: " + "、".join(name(x.get("name")) for x in e["more"][:15]))


def articles(b: Brief, d: dict, slot: str):
    kb = d.get("kabutan") or {}
    b.h("株探（本文。相場の記事は長め）")
    for i, a in enumerate(kb.get("articles") or []):
        long = any(k in (a.get("headline") or "") for k in ("大引け", "前引け", "マーケット日報", "明日の株式", "注目すべき", "業種"))
        b.add(f"- {a.get('timestamp')} {cut(a.get('headline'), 70)}{b.ref('K', a.get('headline'), a.get('url'), '株探')}"
              f"\n  {cut(a.get('body'), 900 if long else 300)}")
    hs = kb.get("headlines") or []
    if hs:
        b.add("株探の見出し: " + "／".join(cut(x.get("title"), 60) + b.ref("H", x.get("title"), x.get("url"), "株探") for x in hs[:25]))
    p = d.get("press") or {}
    b.h("一次情報・報道")
    for x in (p.get("official") or [])[:12]:
        b.add(f"公式 {x.get('source')}: {cut(x.get('title'), 70)}{b.ref('O', x.get('title'), x.get('url'), x.get('source'))}")
    for x in (p.get("wire") or [])[:20]:
        b.add(f"短信: {cut(x.get('title'), 80)}{b.ref('W', x.get('title'), x.get('url'), 'トレーダーズ・ウェブ')}")
    for x in (p.get("headlines") or [])[:40]:
        b.add(f"見出し {x.get('source')}: {cut(x.get('title'), 70)}{b.ref('P', x.get('title'), x.get('url'), x.get('source'))}")
    for x in (p.get("articles") or [])[:12]:
        b.add(f"- {x.get('provider')}: {cut(x.get('headline'), 70)}{b.ref('A', x.get('headline'), x.get('url'), x.get('provider'))}"
              + ("（有料部分の手前まで）" if x.get("partial") else "") + f"\n  {cut(x.get('body'), 420)}")
    for x in (p.get("overseas") or [])[:8]:
        b.add(f"海外 {x.get('source')}: {cut(x.get('title'), 80)}｜{cut(x.get('summary'), 140)}{b.ref('C', x.get('title'), x.get('url'), x.get('source'))}")
    for x in p.get("macro_articles") or []:
        hl = x.get("headline") or ""
        n = 1400 if any(k in hl for k in ("スケジュール", "織り込み")) else 500
        b.add(f"- マクロ {cut(hl, 60)}{b.ref('X', hl, x.get('url'), x.get('provider') or 'トレーダーズ・ウェブ')}\n  {cut(x.get('body'), n)}")
    ng = [s.get("source") or s.get("key") for s in p.get("status") or [] if s.get("ok") is False]
    if ng:
        b.add("取れなかった情報源（「無かった」ではない）: " + "、".join(str(x) for x in ng))
    nw = d.get("news") or []
    if nw:
        b.add("Yahoo AIトピックス（AI生成。裏付けに使わない）: " + "／".join(cut(x.get("headline"), 50) for x in nw[:6]))


def extras(b: Brief, d: dict, slot: str, date: str):
    if slot == "taibike":
        try:
            led = json.load(open(os.path.join(ROOT, "docs", "data", "ledger.json"), encoding="utf-8"))
            todo = [e for e in led.get("entries") or [] if e.get("first_seen") == date and not e.get("notes")]
        except (OSError, ValueError):
            todo = []
        if todo:
            b.h("台帳: 理由づけが要る今日の新規（ledger_notes に書く）")
            for e in todo:
                b.add(f"{name(e.get('name'))}({e['code']}) 引っかかった数字: {'・'.join(e.get('signals') or [])}")
    unk = ((d.get("theme_flow") or {}).get("unknown_codes")) or []
    if unk and slot != "preopen":
        b.h("テーマ辞書に無い銘柄（分かるものだけ themes_add に書く）")
        b.add("、".join(f"{name(x.get('name'))}({x['code']})" for x in unk))


def build(slot: str, limit: int) -> tuple[str, dict]:
    with open(LATEST, encoding="utf-8") as f:
        latest = json.load(f)
    s = (latest.get("slots") or {}).get(slot) or {}
    d = s.get("data") or {}
    b = Brief()
    b.add(f"# {slot} {latest.get('date')} 収集 {s.get('updated_at')}（数字はここと同じものだけを書く）")
    if slot == "preopen":
        preopen_session(b, d)
    else:
        market_session(b, d, slot)
    macro(b, d)
    thermo(b, d, d.get("picks") or {})
    earnings(b, d)
    articles(b, d, slot)
    extras(b, d, slot, latest.get("date") or "")
    text = "\n".join(b.lines)
    if len(text) > limit:
        text = text[:limit] + "\n…（上限で切った）"
    return text, b.refs


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    slot = args[0]
    limit = int(args[args.index("--max") + 1]) if "--max" in args else 60000
    text, refs = build(slot, limit)
    with open(REFS.format(slot=slot), "w", encoding="utf-8") as f:
        json.dump(refs, f, ensure_ascii=False)
    print(text)
    print(f"\n（{len(text):,}字・参照 {len(refs)}件。番号は tools/ai_write.py が URL に戻す）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
