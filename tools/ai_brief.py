"""Routine 用の材料の要約（トークンを減らす。DESIGN.md 23章）。

latest.json の1区分は 15〜20万字あり、そのまま読むとトークンの大半が使われない本文・URL・数値の桁に消える。
ここで「見立て・マクロ・決算・ニュース・買う候補の理由づけ」に要る分だけを、短い行に詰めて出す:

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

import re  # noqa: E402

from dashboard import attrib as attrib_mod, indicators as ind_mod, names as names_mod  # noqa: E402（行を作るだけ。取得はしない）
from dashboard.config import MACRO_TOPICS, OVERNIGHT_TOPICS  # noqa: E402
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
        m = io.get("model") or io
        b.add(f"想定オープン（{io.get('method_label')}）: {pct(io.get('gap_pct'), 2)}・{num(io.get('gap'), 0)}円（"
              + "、".join(f"{c.get('driver')} {c.get('display')}" for c in m.get("contributions") or []) + "）"
              + (f"。米国の終値だけなら {pct(m.get('close_only_pct'), 2)}" if m.get("close_only_pct") is not None else ""))
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


def why(b: Brief, d: dict):
    """値動きの分解（attrib.py）。日経平均の寄与度・時間の流れ・大きく動いた時間と同じ時間の動き・海外。数字は付け直さない。"""
    w = d.get("why") or {}
    if not w:
        return
    b.h("値動きの分解（なぜ動いたか。寄与度・時間の流れ・同じ時間の動き。機械の分解で、理由の読みはあなたの仕事。数字は付け直さない）")
    def ref(title, url):
        # 株探の大引け・前引けの記事（寄与度の出所）は K、同じ時間の見出し・開示は P
        return b.ref("K", title, url, "株探") if "finance.yahoo" in (url or "") else b.ref("P", title, url)
    for line in attrib_mod.lines_for_brief(w, ref):
        b.add(line)


def indicators(b: Brief, d: dict):
    """経済指標（indicators.py）。結果・予想・前回と向き、発表の前後15分・60分の値動き、予定、記録。数字は付け直さない。"""
    v = d.get("indicators") or {}
    if not v or not (v.get("rows") or v.get("schedule") or v.get("week")):
        return
    b.h(f"経済指標（{str(v.get('since') or '')[5:16].replace('T', ' ')}〜。結果・予想・前回と、発表の前後の値動き。"
        "向きは公開の表で機械が付けた。反応は同じ時刻のほかのニュースの分も入る。数字は付け直さない）")
    if v.get("headline"):
        b.add(f"要点（機械）: {v['headline']}")
    for line in ind_mod.lines_for_brief(v, lambda t, u: b.ref("W", t, u, "トレーダーズ・ウェブ")):
        b.add(line)


def world_preopen(b: Brief, d: dict):
    wl = [x for x in d.get("world") or [] if x.get("pct") is not None]
    if wl:
        b.add("欧州（前の夜の引け）: " + "、".join(f"{x['label']} {pct(x['pct'], 2)}" for x in wl))


LEVEL = {"big": "大きく動いた", "move": "動いた", "quiet": "大きな動きなし", "none": "値段が取れず"}


def overnight(b: Brief, d: dict):
    """夜間の値動き（overnight.py）。米国の引け後の先物・時間外、照合した見出し、日本株への響き（朝の窓の目安）、対処。数字は付け直さない。"""
    ov = d.get("overnight") or {}
    if not ov:
        return
    b.h(f"夜間の値動き（米国の引け後〜今朝。{str(ov.get('asof') or '')[11:16]} 時点。先物・時間外の値段と、過去2年の連動から出した目安。予測ではない）")
    b.add(f"判定: {LEVEL.get(ov.get('level'), ov.get('level'))}｜{ov.get('headline')}")
    fs = [f for f in ov.get("futures") or [] if f.get("fresh")]
    if fs:
        b.add("先物: " + "、".join(f"{f['label']} {num(f.get('last'))}（前日比 {pct(f.get('chg'), 2)}"
                                     + (f"・引け後 {pct(f.get('post'), 2)}" if f.get("post") is not None else "") + f"、{f.get('time')}）" for f in fs))
    nk = ov.get("nk")
    if nk:
        b.add(f"日経平均先物: {num(nk.get('last'))}（前日の日経平均の終値から {pct(nk.get('gap_pct'), 2)}、{nk.get('time')}）")
    ext = [n for n in ov.get("names") or [] if n.get("ext") is not None]
    if ext:
        ext.sort(key=lambda n: (n.get("role") is None, -abs(n["ext"])))
        b.add("時間外（通常取引の終値から）: " + "、".join(f"{n['label']} {pct(n['ext'])}（終値 {pct(n.get('chg'))}）" for n in ext[:12]))
    tot, cash = ov.get("total") or {}, ov.get("cash") or {}
    b.add("米国の夜（東京の前の引けのあと）: " + "、".join(
        f"{lab} 終値 {pct(cash.get(k))} → 引け後を足して {pct(tot.get(k))}" for k, lab in (("spx", "S&P500"), ("ndq", "NASDAQ"), ("sox", "SOX"))))
    hs = ov.get("headlines") or []
    lab = {n["key"]: n["label"] for n in ov.get("names") or []}
    if hs:
        b.add(f"見出し（{str(ov.get('since') or '')[5:16].replace('T', ' ')}〜。社名・話題に当たったものだけ。値動きの理由かどうかは確かめる）:")
        for x in hs[:10]:
            tag = "／".join(filter(None, ["社名: " + "・".join(lab.get(k, k) for k in x["names"]) if x.get("names") else "",
                                          "話題: " + "・".join(x["topics"]) if x.get("topics") else ""]))
            b.add(f"- {cut(x.get('title'), 90)}{b.ref('N', x.get('title'), x.get('url'), x.get('source'))}（{x.get('source')}・"
                  f"{str(x.get('published') or '')[5:16].replace('T', ' ')}。{tag}）")
    jp = ov.get("jp") or []
    if jp:
        b.add("日本株への響き（朝の窓の目安。米国の終値まで → 引け後を足した分。決定係数は過去の連動の強さ）: " + "、".join(
            f"{name(r.get('name'))}({r['code']}) {pct(r.get('est_close'))}→{pct(r.get('est'))}（{r.get('r2')}）" for r in jp[:10]))
    for p in ov.get("playbook") or []:
        b.add(f"- 対処・{p.get('who')}: {p.get('text')}")
    gm = ((ov.get("research") or {}).get("gap_model")) or {}
    if gm:
        b.add(f"窓の目安の確かめ（前半の係数で {gm.get('from')} 以降の大きく動いた夜）: 相関 {gm.get('corr')}・平均の誤差 {gm.get('mae')}%"
              f"（目安なし {gm.get('mae0')}%）・向きの一致 {gm.get('hit')}%")


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
    supply(b, th.get("supply") or {})
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
        b.add("材料の監視（上昇トレンド・売買代金の条件を満たし、決算・進捗・ニュース・同業の急騰のどれかがある。注文ではない）: "
              + "、".join(f"{x['name']}({x['code']}) 注文対象まで{pct(x.get('to'))}［" + "／".join(cut(y.get('t'), 34) for y in x.get("why") or []) + "］"
                          for x in w))


def _shares(v):
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v / 1e4:+,.0f}万株" if v < 0 else f"{v / 1e4:,.0f}万株"


def supply_line(x: dict) -> str:
    parts = []
    if x.get("above") is not None:
        parts.append(f"上値の出来高{x['above']:.0f}%・平均の値段{num(x.get('vwap'))}円（今{pct(x.get('gap'))}）")
        w = x.get("wall")
        if w:
            parts.append(f"壁{num(w.get('lo'))}〜{num(w.get('hi'))}円に{w.get('pct')}%")
    m = x.get("mg")
    if m:
        parts.append(f"信用 {m.get('date')} 買残{_shares(m.get('buy'))}（前週比{_shares(m.get('buy_chg'))}）売残{_shares(m.get('sell'))}"
                     f" 倍率{m.get('ratio')} 買残は出来高の{m.get('days')}日分"
                     + (f" {m.get('w')}週で買残{pct(m.get('buy_w'), 0)}・株価{pct(m.get('px_w'))}" if m.get("buy_w") is not None else ""))
        for t in m.get("notes") or []:
            parts.append(t)
    return f"{name(x.get('name'))}({x.get('code')}) " + "／".join(parts)


def supply(b: Brief, su: dict):
    if not su or not (su.get("watch") or su.get("orders")):
        return
    b.h("需給（上値のしこり・信用残。付け直さない。注文・判定には使わない）")
    up = su.get("up") or {}
    if up.get("heavy") and up.get("light"):
        b.add(f"しこり: {su.get('rule')}。次の{su.get('h')}営業日に市場に勝った割合（前半→後半） 30%未満 {up['light']} ／50〜70% {up['heavy']}"
              f" ／70%以上 {up.get('heavier')} ／全部 {up.get('all')}。差は小さい。信用残は研究の値を一言に添えている（警告は1週で買残+30%以上だけ）")
    for x in su.get("watch") or []:
        b.add("ウォッチ " + supply_line(x))
    for x in su.get("orders") or []:
        b.add("注文 " + supply_line(x))


def earnings(b: Brief, d: dict):
    e = d.get("earnings") or {}
    items = e.get("items") or []
    if not items:
        return
    b.h(f"決算（{e.get('scope')}・{e.get('asof')}。{e.get('n_total')}社中 {e.get('n_read')}社を読んだ）")
    for it in items:
        fl = it.get("flash") or {}
        mv = it.get("move") or {}
        # 引け後（15:30〜）の開示は、この値動きより後に出ている。反応は翌営業日に出るので、決算の評価に使わない
        mvl = f"開示前の値動き{pct(mv.get('pct'))}（引け後の開示。反応は翌営業日）" if it.get("after_close") else f"値動き{pct(mv.get('pct'))}"
        b.add(f"■ {it.get('name')}({it.get('code')}) {it.get('time') or ''}開示 {'・'.join(it.get('kinds') or [])} 向き:{it.get('dir') or '不明'} {it.get('industry') or ''}"
              f" テーマ:{'・'.join(it.get('themes') or []) or '—'} {mvl}")
        if fl.get("headline"):
            b.add(f"  速報: {cut(fl['headline'], 80)}｜{cut(fl.get('body'), 260)}{b.ref('E', fl['headline'], fl.get('url'), '株探')}")
        nts = it.get("notes") or []
        if nts:
            b.add("  印（機械）: " + "／".join(x.get("t", "") for x in nts))
        for key, label, n in (("reason", "修正の理由", 300), ("overview", "概況", 300), ("outlook", "予想の説明", 200)):
            if it.get(key):
                b.add(f"  {label}: {cut(it[key], n)}")
        for p in (it.get("press") or [])[:3]:
            b.add(f"  報道: {cut(p.get('title'), 70)}{b.ref('E', p.get('title'), p.get('url'), p.get('source'))}")
        ps = it.get("peers") or []
        if ps:
            b.add("  類似" + ("（値動きは開示前。決算への反応ではない）" if it.get("after_close") else "") + ": " + "、".join(
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
    earn_reactions(b, e)


def earn_reactions(b: Brief, e: dict):
    rx = e.get("reactions") or []
    if rx:
        b.h("決算への反応（今日。市場＝225採用の前日比の中央値との差。類似は類似銘柄の平均）")
        for r in rx[:12]:
            b.add(f"{name(r.get('name'))}({r['code']}) {r.get('date')} {r.get('time') or ''} 向き:{r.get('dir') or '不明'}"
                  f" 前日比{pct(r.get('r'))} 市場差{pct(r.get('ex'))}" + (f" 類似{pct(r.get('p'))}({r.get('pn')}銘柄)" if r.get("p") is not None else "")
                  + f"「{cut(r.get('head'), 40)}」")
    st = e.get("react") or {}
    if st:
        b.add(f"反応の記録（{st.get('from')}〜{st.get('to')}・{st.get('days')}日）: " + "、".join(
            f"{'上向き' if k == 'up' else '下向き'}{v['n']}件 自社{pct((v.get('self') or {}).get('avg'))}"
            + (f"・類似{pct((v.get('peers') or {}).get('avg'))}" if v.get("peers") else "")
            for k, v in st.items() if k in ("up", "down")) + "（件数が少ないうちは傾向と言えない）")


def newsflow(b: Brief, d: dict):
    nf = d.get("newsflow") or {}
    if not nf:
        return
    mv = nf.get("movers") or {}
    if mv.get("rows"):
        b.h(f"動いた銘柄の材料（機械の照合。市場＝225採用の中央値 {pct(mv.get('mkt'))}。開示{mv.get('disc')}・報道{mv.get('news')}"
            f"・業種ぐるみ{mv.get('group')}・見当たらない{mv.get('none')}）")
        grouped, none = [], []
        for r in mv["rows"]:
            if r["kind"] == "group":
                g = r.get("grp") or {}
                grouped.append(f"{name(r.get('name'))}({r['code']}){pct(r.get('pct'))}［{g.get('g')} 平均{pct(g.get('avg'))}］")
                continue
            if r["kind"] == "none":
                none.append(f"{name(r.get('name'))}({r['code']}){pct(r.get('pct'))}")
                continue
            if r["kind"] == "disc":
                m = "開示: " + "／".join(f"{x.get('when')} {cut(x.get('title'), 50)}" for x in r.get("disc")[:2])
            elif r["kind"] == "news":
                said = next((x for x in r.get("said") or [] if not x.get("list")), None)
                if said:
                    m = f"記事: {cut(said['text'], 70)}" + b.ref("A", said.get("title"), said.get("url"), said.get("media"))
                else:
                    x = r["news"][0]
                    m = f"見出し: {cut(x['title'], 60)}" + b.ref("P", x.get("title"), x.get("url"), x.get("media"))
            else:
                continue
            b.add(f"{name(r.get('name'))}({r['code']}) {pct(r.get('pct'))}" + (f"（市場差{pct(r.get('ex'))}）" if r.get("ex") is not None else "")
                  + f" {r.get('src')} {'/'.join(r.get('th') or [])}｜{m}")
        if grouped:
            b.add("業種ぐるみ（材料は見当たらないが、同じテーマ・業種の仲間も同じ向き。仲間の平均は市場差）: " + "、".join(grouped))
        if none:
            b.add("材料が見当たらない（需給・連想・地合いの可能性。こじつけない）: " + "、".join(none))
    if nf.get("buzz"):
        b.add("報道が多かった銘柄: " + "、".join(
            f"{name(x.get('name'))}({x['code']}) {x['n']}本・{len(x['media'])}媒体{('・' + pct(x['pct'])) if x.get('pct') is not None else ''}"
            for x in nf["buzz"][:8]))
    if nf.get("themes"):
        b.add(f"テーマの話題（見出しの本数。過去{nf.get('hist_days')}営業日の平均）: " + "、".join(
            f"{x['theme']}{x['n']}本" + (f"(平均{x['avg']})" if x.get("avg") is not None else "") + (f"・値動き{pct(x['move'])}" if x.get("move") is not None else "")
            for x in nf["themes"][:8]))
    sp = (d.get("thermo") or {}).get("spill") or {}
    if sp.get("today"):
        b.add("同業の急騰・急落（動かなかった仲間）: " + "／".join(
            f"{'急騰' if r['kind'] == 'up' else '急落'} {r['g']}: " + "・".join(f"{name(x['name'])}{pct(x['d1'])}" for x in r["ev"])
            + " → 動かず " + ("・".join(f"{name(x['name'])}{pct(x['d1'])}" for x in r["mates"][:4]) or "なし") for r in sp["today"][:6]))
        v = sp.get("verify") or {}
        try:
            j = v["hs"].index(5)
            b.add(f"  検証（{v['from']}〜{v['to']}、前半／後半）: 動かなかった仲間の5日後の市場差 {v['lag'][0][j].get('avg')}／{v['lag'][1][j].get('avg')}%、"
                  f"同じ日の全銘柄 {v['base'][0][j].get('avg')}／{v['base'][1][j].get('avg')}%（追いつくとは言えない）")
        except (KeyError, ValueError, IndexError, TypeError):
            pass


# 一覧・ランキング・テクニカルの見出し（読む価値が無く、トークンだけ使う）
_SKIP_HEAD = ("本日の【", "本日のランキング", "ETF売買動向", "日経会社情報DIGITAL")
# 報道の見出しの並べ方: 相場・企業・マクロに関わる見出しを先に（Google ニュースの検索語は本文にも当たるので、文化・生活の記事が混ざる）
_MKT_WORDS = re.compile(r"株|円|金利|物価|景気|決算|業績|売上|利益|増益|減益|赤字|黒字|最高益|上方修正|下方修正|買収|提携|出資|増資|上場|"
                        r"TOB|M&A|IPO|社長|CEO|受注|工場|投資|輸出|輸入|関税|貿易|半導体|AI|原油|市場|相場|指数|債|日銀|FRB|政策|予算|"
                        r"税|賃金|雇用|GDP|消費|値上げ|価格|需要|供給|経済|企業|事業|銀行|保険|証券|電力|制裁|中国|米国|米|欧州|中東|イラン|ロシア")


def _head_score(title: str, names: list[str]) -> int:
    t = str(title or "")
    score = 0
    if any(re.search(tp["pattern"], t) for tp in MACRO_TOPICS + OVERNIGHT_TOPICS):
        score += 2
    if any(n in t for n in names):
        score += 2
    if _MKT_WORDS.search(t):
        score += 1
    return score


def rank_headlines(rows: list[dict]) -> list[dict]:
    """関わりの強い見出しを先に（同じ点なら元の順＝新しい順）。どれにも当たらない見出しは最後。"""
    try:
        names = [n for n in {cleaned for cleaned in (str(x).replace("（株）", "").strip()
                                                     for x in names_mod.known().values())} if len(n) >= 3]
    except Exception:
        names = []
    return [r for _, _, r in sorted(((-_head_score(r.get("title"), names), i, r) for i, r in enumerate(rows)),
                                    key=lambda x: (x[0], x[1]))]


def articles(b: Brief, d: dict, slot: str):
    kb = d.get("kabutan") or {}
    b.h("株探（本文。相場の記事は長め）")
    # 決算の節に載せた決算速報は重ねない（同じ本文を2回読ませない）
    flashes = {(it.get("flash") or {}).get("headline") for it in (d.get("earnings") or {}).get("items") or []}
    for i, a in enumerate(kb.get("articles") or []):
        if (a.get("headline") or "").replace("【決算速報】", "") in flashes:
            continue
        long = any(k in (a.get("headline") or "") for k in ("大引け", "前引け", "マーケット日報", "明日の株式", "注目すべき", "業種"))
        b.add(f"- {a.get('timestamp')} {cut(a.get('headline'), 70)}{b.ref('K', a.get('headline'), a.get('url'), '株探')}"
              f"\n  {cut(a.get('body'), 900 if long else 300)}")
    hs = [x for x in kb.get("headlines") or [] if not any(k in (x.get("title") or "") for k in _SKIP_HEAD)]
    if hs:
        b.add("株探の見出し: " + "／".join(cut(x.get("title"), 60) + b.ref("H", x.get("title"), x.get("url"), "株探") for x in hs[:20]))
    p = d.get("press") or {}
    b.h("一次情報・報道")
    for x in (p.get("official") or [])[:12]:
        b.add(f"公式 {x.get('source')}: {cut(x.get('title'), 70)}{b.ref('O', x.get('title'), x.get('url'), x.get('source'))}")
    # 指標の結果・予定の短信は「経済指標」の節に構造にして出したので、ここは要人発言だけ（同じ材料を2回読ませない）
    has_ind = bool((d.get("indicators") or {}).get("rows") or (d.get("indicators") or {}).get("schedule"))
    for x in [x for x in p.get("wire") or [] if not has_ind or x.get("kind") == "remarks"][:20]:
        b.add(f"短信: {cut(x.get('title'), 80)}{b.ref('W', x.get('title'), x.get('url'), 'トレーダーズ・ウェブ')}")
    heads = [x for x in p.get("headlines") or [] if not any(k in (x.get("title") or "") for k in _SKIP_HEAD)]
    for x in rank_headlines(heads)[:32]:
        b.add(f"見出し {x.get('source')}: {cut(x.get('title'), 70)}{b.ref('P', x.get('title'), x.get('url'), x.get('source'))}")
    for x in (p.get("articles") or [])[:12]:
        b.add(f"- {x.get('provider')}: {cut(x.get('headline'), 70)}{b.ref('A', x.get('headline'), x.get('url'), x.get('provider'))}"
              + ("（有料部分の手前まで）" if x.get("partial") else "") + f"\n  {cut(x.get('body'), 420)}")
    for x in (p.get("overseas") or [])[:8]:
        b.add(f"海外 {x.get('source')}: {cut(x.get('title'), 80)}｜{cut(x.get('summary'), 140)}{b.ref('C', x.get('title'), x.get('url'), x.get('source'))}")
    for x in p.get("macro_articles") or []:
        hl = x.get("headline") or ""
        if has_ind and any(k in hl for k in ("イベントスケジュール", "主な経済指標")):
            continue        # 「経済指標」の節に予定・結果として読んだ
        n = 1400 if any(k in hl for k in ("スケジュール", "織り込み")) else 500
        b.add(f"- マクロ {cut(hl, 60)}{b.ref('X', hl, x.get('url'), x.get('provider') or 'トレーダーズ・ウェブ')}\n  {cut(x.get('body'), n)}")
    ng = [s.get("source") or s.get("key") for s in p.get("status") or [] if s.get("ok") is False]
    if ng:
        b.add("取れなかった情報源（「無かった」ではない）: " + "、".join(str(x) for x in ng))
    nw = d.get("news") or []
    if nw:
        b.add("Yahoo AIトピックス（AI生成。裏付けに使わない）: " + "／".join(cut(x.get("headline"), 50) for x in nw[:6]))


def phase(b: Brief, d: dict, slot: str):
    """局面と対処（phase.py）。振り返り（ai_review）と見立ての土台。数字は付け直さない。"""
    ph = d.get("phase") or {}
    if not ph:
        return
    st = ph.get("state") or {}
    b.h(f"局面と対処（36年の日経平均で同じ形を数えた。{ph.get('asof')} の引けまで。予測ではなく過去の分布）")
    b.add(f"局面: {ph.get('label')}｜{ph.get('headline')}" + ("（今日の日足なし＝前の営業日の形）" if ph.get("stale") else ""))
    cr = st.get("crash") or {}
    b.add(f"日経 {num(st.get('close'))}（{pct(st.get('chg'), 2)}）・5日 {pct(st.get('r5'))}・25日線から {pct(st.get('dev25'))}・"
          f"1年の高値 {num(st.get('high'))}（{st.get('high_date')}）から {pct(st.get('from_high'))}"
          + (f"・引けのあとの米国 {pct(st.get('us'), 2)}" if st.get("us") is not None else "・今夜の米国はまだ")
          + (f"｜直近の急落 {cr.get('date')} {pct(cr.get('r'))}（安値 {num(cr.get('low'))}、{cr.get('days')}営業日前、"
             f"{'安値を割った' if cr.get('broken') else '安値はまだ割っていない'}）" if cr else ""))
    for p in ph.get("playbook") or []:
        b.add(f"- {p.get('who')}: {p.get('text')}")
    sc = ph.get("scenarios") or {}
    for r in sc.get("rows") or []:
        b.add(f"  米国別: {r.get('def')}{'（今朝はこれ）' if r.get('now') else ''} {r.get('n')}回 翌朝の窓 中央値 {pct(r.get('gap_med'))}・"
              f"安値割れ {r.get('low_break')}%・5日の最安値 中央値 {pct(r.get('min5_med'))}・20日後 {pct(r.get('r20'))}"
              f"（プラス {r.get('p20')}%。前半 {pct(r.get('r20_first'))}／後半 {pct(r.get('r20_second'))}）")
    for x in ph.get("ladder") or []:
        b.add(f"  はしご: {num(x.get('price'))} {x.get('text')}")
    for k, v in (ph.get("focus") or {}).items():
        cs = v.get("cases") or []
        if cs:
            b.add(f"  過去の例（{k}、新しい順）: " + "／".join(
                f"{c['date']} 当日{pct(c['r'])} 翌朝{pct(c['gap'])} 5日の最安{pct(c['min5'])} 20日後{pct(c['r20'])}" for c in cs[:6]))
    mg = ph.get("margin") or {}
    if mg:
        b.add(f"信用買残（市場全体・JPX、{mg.get('date')}の週）: {mg.get('buy')}兆円（1週 {pct(mg.get('w1'))}・4週 {pct(mg.get('w4'))}・"
              f"26週 {pct(mg.get('w26'))}）・売残 {mg.get('sell')}兆円・倍率 {mg.get('ratio')}倍")
    iv = ph.get("investors") or {}
    if iv:
        rows = iv.get("rows") or []
        b.add("投資部門別（東証プライムの差引・億円、新しい週から）: " + "／".join(
            f"{r['end'][5:]}週 海外{r.get('foreign', 0):+,.0f} 個人{r.get('indiv', 0):+,.0f} 信託{r.get('trust', 0):+,.0f} 事業法人{r.get('corp', 0):+,.0f}"
            for r in rows[:4]) + f"｜海外は{iv.get('foreign_streak')}週続けて同じ向き、4週で {iv.get('foreign_sum4'):+,.0f}億円")
    lg = [x for x in ph.get("log") or [] if x.get("actual")]
    if lg:
        b.h("答え合わせ（局面の日の見込みと、その後の実際）")
        for x in lg[:6]:
            e, a = x.get("expect") or {}, x.get("actual") or {}
            b.add(f"{x['date']} {x.get('label')}: 見込み（過去{e.get('n')}回）翌朝 {pct(e.get('gap_med'))}・安値割れ {e.get('low_break')}%・20日後 {pct(e.get('r20'))}"
                  f" → 実際 米国 {pct(a.get('us'))}・翌朝 {pct(a.get('gap'))}" + (f"・安値割れ {'あり' if a.get('low_broken') else 'なし'}（{a.get('low_days')}日）" if 'low_broken' in a else "")
                  + (f"・5日後 {pct(a.get('r5'))}" if 'r5' in a else "") + (f"・20日後 {pct(a.get('r20'))}" if 'r20' in a else ""))


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
        world_preopen(b, d)
        overnight(b, d)
        indicators(b, d)
    else:
        market_session(b, d, slot)
        why(b, d)
        indicators(b, d)
    phase(b, d, slot)
    macro(b, d)
    thermo(b, d, d.get("picks") or {})
    earnings(b, d)
    newsflow(b, d)
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
