/* マーケット — 描画ロジック
   外部ライブラリなし。データは docs/data/ 配下の JSON から読む。
   本文はスクレイピング由来のため、DOM 生成は textContent 経由で行う
   （h() の html: は自前で組み立てた SVG 専用）。

   画面は5つ。1つの情報は1か所だけに置き、ほかの画面には1行の要約とそこへ跳ぶボタンだけを出す（DESIGN.md 19章）:
     売買     … 今日やること。市況の一行 → 短期の押し目買い → 中期の押し目 → 保有銘柄 → 監視（ウォッチ・発掘・もうすぐ・業種で見る・追わない）
     市況     … 寄り前 / 前場 / 大引 / 推移 を切り替えて、相場で何が起きたかを読む
     業種     … 業種の強弱・4象限・全業種・日経の業種（8日の推移と温度）・テーマ
     ニュース … 保有・監視の銘柄の材料 → 開示 → 報道・公的機関・株探・市況記事・海外
     検証     … ルールの成績・口座・注文の実績・各判定の根拠・自分の記録
   銘柄はどの画面でも押すと銘柄シート（チャート・ルールでの位置・保有の判定・材料・買う前／売る前チェック）が開く。
   右上の虫眼鏡は、日足のある銘柄と今日のランキング・開示・直近の履歴を横断して引く。 */
(() => {
'use strict';

const SLOTS = ['preopen', 'zenba', 'taibike'];
const SLOT_LABEL = { preopen: '寄り前', zenba: '前場', taibike: '大引', trend: '推移' };
const MARKET_TABS = ['preopen', 'zenba', 'taibike', 'trend'];     // 市況の切り替え（推移は旧「履歴」）
const VIEWS = ['trade', 'market', 'sectors', 'news', 'verify'];
const VIEW_TITLE = { trade: '売買', market: '市況', sectors: '業種', news: 'ニュース', verify: '検証' };
const DEFAULT_REPO = 'tonpe3141-ctrl/kabutan-digest';
const LS = { codes: 'md.watchlist.codes', token: 'md.gh.token', repo: 'md.gh.repo', tab: 'md.tab', view: 'md.view',
  recent: 'md.recent' };
const HISTORY_DAYS = 15;            // 市況の推移・業種の8日・銘柄の検索が読み込む営業日数

let DATA = null;
let LEDGER = null;                  // docs/data/ledger.json（発掘台帳。売買タブの監視に並べる）
let WEEKLY = null;                  // docs/data/weekly.json（金曜の週報）
let THERMO = null;                  // docs/data/thermo.json（短期の押し目買いの注文・相場温度計）
let HIST = { dates: [], byDate: new Map(), loaded: false };
let activeSlot = null;
let activeView = 'trade';
const dirty = new Set(VIEWS);       // 描き直しが要る画面（開いている画面だけを描く）

/* ==================== 小道具 ==================== */
const jstNow = () => new Date(Date.now() + new Date().getTimezoneOffset() * 60000 + 9 * 3600000);
const $ = (id) => document.getElementById(id);

function h(tag, props = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'html') el.innerHTML = v;           // 自前の SVG のみに使用
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return el;
}

const isNum = (v) => typeof v === 'number' && isFinite(v);
const cls = (v) => (!isNum(v) || v === 0 ? 'flat' : v > 0 ? 'up' : 'down');
const cleanName = (s) => String(s || '').replace(/\(株\)|（株）|株式会社/g, '').trim();
const stockUrl = (code) => `https://kabutan.jp/stock/?code=${encodeURIComponent(code)}`;

function fmtNum(v, digits = 2) {
  if (!isNum(v)) return '—';
  return v.toLocaleString('ja-JP', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtPct(v, digits = 2) {
  if (!isNum(v)) return '—';
  const r = Number(v.toFixed(digits)) || 0;           // -0.001 を「-0.00%」にしない
  return (r > 0 ? '+' : '') + r.toFixed(digits) + '%';
}
function fmtSigned(v, digits = 2) {
  if (!isNum(v)) return '—';
  const r = Number(v.toFixed(digits)) || 0;
  return (r > 0 ? '+' : '') + fmtNum(r, digits);
}
function fmtPrice(v) {
  if (!isNum(v)) return '—';
  return fmtNum(v, v >= 1000 ? 0 : 1);
}
function fmtDate(iso, opts) {
  if (!iso) return '—';
  const d = new Date(iso.slice(0, 10) + 'T00:00:00+09:00');
  return d.toLocaleDateString('ja-JP', opts || { month: 'numeric', day: 'numeric', weekday: 'short', timeZone: 'Asia/Tokyo' });
}

function sparkline(series, height = 20) {
  if (!Array.isArray(series) || series.length < 3) return null;
  const w = 100, hgt = height, pad = 2;
  const min = Math.min(...series), max = Math.max(...series);
  const span = max - min || 1;
  const pts = series.map((v, i) => {
    const x = (i / (series.length - 1)) * w;
    const y = hgt - pad - ((v - min) / span) * (hgt - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const rising = series[series.length - 1] >= series[0];
  const color = rising ? 'var(--up)' : 'var(--down)';
  return h('div', { class: 'tile__spark' }, [
    h('div', {
      html: `<svg viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none" aria-hidden="true">` +
            `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" ` +
            `stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`,
    }),
  ]);
}

/* ==================== 部品 ==================== */
function card(title, sub, body, note, flush, id) {
  const kids = [];
  if (title) {
    kids.push(h('div', { class: 'card__head' }, [
      h('h2', { class: 'card__title', text: title }),
      sub ? (typeof sub === 'string' ? h('span', { class: 'card__sub', text: sub }) : sub) : null,
    ]));
  }
  [].concat(body).forEach((b) => b && kids.push(b));
  if (note) kids.push(h('div', { class: 'card__note', text: note }));
  return h('section', { class: 'card' + (flush ? ' card--flush' : ''), id: id || null }, kids);
}

/* 長い一覧は最初の n 件だけ出し、「すべて見る」で残りを開く */
function foldable(renderFn, total, shown, label) {
  if (total <= shown) return renderFn(total);
  const wrap = h('div', {});
  const draw = (n) => {
    wrap.textContent = '';
    wrap.appendChild(renderFn(n));
    if (n < total) {
      wrap.appendChild(h('button', { class: 'more', type: 'button',
        text: `${label || 'すべて'}を見る（残り ${total - n}）`, onclick: () => draw(total) }));
    }
  };
  draw(shown);
  return wrap;
}

function tile(label, value, delta, deltaPct, series) {
  return h('div', { class: 'tile' }, [
    h('div', { class: 'tile__label', text: label }),
    h('div', { class: 'tile__value num', text: value }),
    h('div', { class: 'tile__delta num ' + cls(deltaPct), text: delta }),
    sparkline(series),
  ]);
}

function quoteTiles(map, order, three) {
  const keys = order || Object.keys(map);
  const items = keys.filter((k) => map[k]).map((k) => {
    const q = map[k];
    const digits = q.digits !== undefined ? q.digits : (Math.abs(q.last) >= 1000 ? 0 : 2);
    return tile(q.label || k, fmtNum(q.last, digits),
      fmtSigned(q.change, digits) + '  ' + fmtPct(q.change_pct), q.change_pct, q.series);
  });
  if (!items.length) return h('div', { class: 'empty', text: 'データを取得できませんでした' });
  return h('div', { class: 'tiles' + (three ? ' tiles--3' : '') }, items);
}

function barList(items, opts = {}) {
  const max = Math.max(0.5, ...items.map((i) => Math.abs(i.value)));
  return h('div', { class: 'bars' }, items.map((it) => {
    if (it.gap) return h('div', { class: 'bars__gap' });
    const pctw = (Math.abs(it.value) / max) * 50;
    const positive = it.value >= 0;
    const fill = h('div', { class: 'bar__fill' });
    fill.style.width = pctw + '%';
    fill.style.background = positive ? 'var(--up)' : 'var(--down)';
    if (positive) fill.style.left = '50%'; else fill.style.right = '50%';
    return h('div', { class: 'bar' }, [
      h('div', { class: 'bar__label', text: it.label }),
      h('div', { class: 'bar__val num ' + cls(it.value), text: opts.raw ? fmtNum(it.value, 2) : fmtPct(it.value) }),
      h('div', { class: 'bar__track' }, [h('div', { class: 'bar__mid' }), fill]),
      it.sub ? h('div', { class: 'bar__drivers', text: it.sub }) : null,
    ]);
  }));
}

function stockRow(r, i, opts = {}) {
  const meta = [];
  if (r.code) meta.push(h('span', { text: r.code }));
  if (opts.meta) [].concat(opts.meta(r)).filter(Boolean).forEach((m) =>
    meta.push(typeof m === 'string' ? h('span', { class: 'tag', text: m }) : m));
  const inner = [
    h('div', { class: 'row__rank num', text: opts.rank === false ? '' : String(i + 1) }),
    h('div', { class: 'row__main' }, [
      h('div', { class: 'row__name', text: cleanName(r.name) || r.code || '—' }),
      meta.length ? h('div', { class: 'row__meta' }, meta) : null,
    ]),
    h('div', { class: 'row__right' }, [
      isNum(r.price) ? h('div', { class: 'row__price num', text: fmtPrice(r.price) }) : null,
      opts.right ? opts.right(r) : h('div', { class: 'row__delta num ' + cls(r.change_pct), text: fmtPct(r.change_pct) }),
    ]),
  ];
  return r.code
    ? h('button', { class: 'row row--btn', type: 'button', onclick: () => openStock(r.code) }, inner)
    : h('div', { class: 'row' }, inner);
}

function stockRows(rows, opts = {}) {
  const list = (rows || []).slice(0, opts.limit || rows.length);
  if (!list.length) return h('div', { class: 'empty', text: 'データなし' });
  return h('div', { class: 'rows' }, list.map((r, i) => stockRow(r, i, opts)));
}

/* 株価が動きやすい開示。自己株式取得や月次は件数が多く埋もれるので後回しにする */
const MATERIAL = ['業績予想の修正', '決算短信', '配当予想の修正'];
const UPGRADE_RE = /上方|増配|増額/;
const DOWNGRADE_RE = /下方|減配|減額|無配/;

function disclosureTone(r) {
  const t = (r.title || '') + (r.category || '');
  if (r.category !== '業績予想の修正' && r.category !== '配当予想の修正') return null;
  if (UPGRADE_RE.test(t)) return 'up';
  if (DOWNGRADE_RE.test(t)) return 'down';
  return null;
}

function disclosureRows(rows, limit) {
  const list = (rows || []).slice(0, limit || rows.length);
  if (!list.length) return h('div', { class: 'empty', text: '開示なし' });
  return h('div', { class: 'rows' }, list.map((r) => {
    const tone = disclosureTone(r);
    return h('button', { class: 'row row--btn row--disc', type: 'button', onclick: () => openStock(r.code) }, [
      h('div', { class: 'row__rank num', text: (r.time || '').slice(0, 5) }),
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name', text: cleanName(r.name) || r.code }),
        h('div', { class: 'row__meta' }, [h('span', { text: r.code }), h('span', { class: 'row__title', text: r.title || '' })]),
      ]),
      h('div', { class: 'row__right' }, [
        r.category ? h('span', {
          class: 'badge' + (tone === 'up' ? ' badge--up' : tone === 'down' ? ' badge--down'
                 : r.category === '業績予想の修正' ? ' badge--warn' : ''),
          text: tone === 'up' ? '上方・増配' : tone === 'down' ? '下方・減配' : r.category,
        }) : null,
        isNum(r.change_pct) ? h('div', { class: 'row__delta num ' + cls(r.change_pct), text: fmtPct(r.change_pct) }) : null,
      ]),
    ]);
  }));
}

function accordion(title, sub, bodyText, url, linkLabel) {
  return h('details', { class: 'acc' }, [
    h('summary', {}, [
      h('span', { class: 'acc__title' }, [
        document.createTextNode(title),
        sub ? h('small', { text: sub }) : null,
      ]),
    ]),
    h('div', { class: 'acc__body' }, [
      document.createTextNode(bodyText || '本文を取得できませんでした'),
      url ? h('div', { style: 'margin-top:10px' }, [
        h('a', { href: url, target: '_blank', rel: 'noopener', text: (linkLabel || '元記事を開く') + ' →' }),
      ]) : null,
    ]),
  ]);
}

function segmented(options, onSelect, initial) {
  const seg = h('div', { class: 'seg' });
  const set = (k) => [...seg.children].forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.k === k)));
  options.forEach(([k, label]) => {
    const b = h('button', { class: 'seg__btn', type: 'button', 'aria-pressed': 'false', text: label });
    b.dataset.k = k;
    b.addEventListener('click', () => { set(k); onSelect(k); });
    seg.appendChild(b);
  });
  set(initial);
  return seg;
}

/* ==================== 日経225ヒートマップ ==================== */
function heatCell(r) {
  const v = r.change_pct;
  const cap = 4;                                     // ±4% で色を振り切らせる
  const a = isNum(v) ? Math.min(1, Math.abs(v) / cap) * 0.82 + 0.10 : 0.06;
  const cell = h('button', {
    class: 'heat__cell', type: 'button', title: `${r.name || ''} ${r.code} ${fmtPct(v)}`,
    onclick: () => openStock(r.code),
  }, [
    h('div', { class: 'heat__name', text: r.name || r.code }),
    h('div', { class: 'heat__val num', text: isNum(v) ? fmtPct(v, 1) : '—' }),
  ]);
  const token = !isNum(v) || v === 0 ? 'var(--flat)' : (v > 0 ? 'var(--up)' : 'var(--down)');
  cell.style.background = `color-mix(in srgb, ${token} ${(a * 100).toFixed(0)}%, transparent)`;
  cell.style.color = a > 0.5 ? '#fff' : 'var(--text)';
  return cell;
}

function heatmapCard(rows, breadth) {
  const list = (rows || []).filter((r) => isNum(r.change_pct));
  if (list.length < 20) return null;

  const body = h('div', {});
  let mode = 'rank', expanded = false;
  const draw = () => {
    body.textContent = '';
    const grid = h('div', { class: 'heat' });
    if (mode === 'sector') {
      const groups = new Map();
      list.forEach((r) => {
        const k = r.sector || 'その他';
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(r);
      });
      const ordered = [...groups.entries()]
        .map(([k, v]) => [k, v, v.reduce((a, r) => a + r.change_pct, 0) / v.length])
        .sort((a, b) => b[2] - a[2]);
      (expanded ? ordered : ordered.slice(0, 6)).forEach(([name, items, avg]) => {
        grid.appendChild(h('div', { class: 'heat__group', text: `${name}　${fmtPct(avg, 1)}` }));
        items.sort((a, b) => b.change_pct - a.change_pct).forEach((r) => grid.appendChild(heatCell(r)));
      });
    } else {
      const sorted = [...list].sort((a, b) => b.change_pct - a.change_pct);
      if (expanded) sorted.forEach((r) => grid.appendChild(heatCell(r)));
      else {
        grid.appendChild(h('div', { class: 'heat__group', text: '上昇上位' }));
        sorted.slice(0, 12).forEach((r) => grid.appendChild(heatCell(r)));
        grid.appendChild(h('div', { class: 'heat__group', text: '下落上位' }));
        sorted.slice(-12).reverse().forEach((r) => grid.appendChild(heatCell(r)));
      }
    }
    body.appendChild(grid);
    if (!expanded) {
      body.appendChild(h('button', { class: 'more', type: 'button', text: `${list.length}銘柄すべてを見る`,
        onclick: () => { expanded = true; draw(); } }));
    }
  };
  const seg = segmented([['rank', '騰落順'], ['sector', '業種順']], (k) => { mode = k; draw(); }, 'rank');
  draw();

  return card('日経225 ヒートマップ', seg, [
    body,
    h('div', { class: 'legend' }, [
      h('span', { text: '下落' }), h('div', { class: 'legend__bar' }), h('span', { text: '上昇' }),
    ]),
  ], breadth ? `上昇 ${breadth.up} / 下落 ${breadth.down}（${breadth.up_ratio}% が上昇）。${breadth.comment}` : null, false, 'sec-heat');
}

/* ==================== 見立て ==================== */
function pickCommentary(d) {
  const ai = d.ai_commentary, rule = d.commentary;
  const c = ai && ai.sections && ai.sections.length ? ai : rule;
  if (!c || !c.sections || !c.sections.length) return null;
  return { c, isAi: c === ai };
}

/* 見立ての本文。長いので要点カードの中に畳んで出す（見出しは要点カードの先頭に出ている） */
function analysisDetails(d) {
  const picked = pickCommentary(d);
  if (!picked) return null;
  const { c, isAi } = picked;
  const note = isAi
    ? '生成AIによる分析です。数値データと相場振り返り記事をもとに作成していますが、誤りを含む可能性があります。売買を推奨するものではありません。'
    : '各カードの数値を組み合わせて機械的に文章化したもの。同じ数字からは同じ文章が出ます。売買を推奨するものではありません。';
  const kids = c.sections.map((s) => h('div', { class: 'analysis__sec' }, [
    h('div', { class: 'analysis__t', text: s.title }),
    h('div', { class: 'analysis__b', text: s.body }),
  ]));
  if (isAi && Array.isArray(c.sources) && c.sources.length) {
    kids.push(h('div', { class: 'analysis__srcs' }, c.sources.map((src) =>
      h('a', { href: src.url, target: '_blank', rel: 'noopener', text: '📰 ' + src.title }))));
  }
  kids.push(h('div', { class: 'card__note', text: note }));
  return h('details', { class: 'fold' }, [
    h('summary', { text: `見立てを読む（${isAi ? (c.method || 'AI分析') : 'ルールベース'}・${c.sections.length}項目）` }),
    h('div', { class: 'fold__body' }, kids),
  ]);
}

/* ==================== 3行サマリー ==================== */
function stat(label, value, sub, tone) {
  return h('div', { class: 'stat' }, [
    h('div', { class: 'stat__label', text: label }),
    h('div', { class: 'stat__value num ' + (tone || ''), text: value }),
    sub ? h('div', { class: 'stat__sub num ' + (tone || ''), text: sub }) : null,
  ]);
}

function summaryCard(d, slot) {
  const picked = pickCommentary(d);
  const headline = picked ? picked.c.headline : null;
  const stats = [], chips = [];

  if (slot === 'preopen') {
    const io = d.implied_open || {};
    const spx = (d.us || {}).spx, jpy = (d.macro || {}).usdjpy, sox = (d.us || {}).sox;
    stats.push(stat('想定オープン', fmtPct(io.gap_pct), isNum(io.gap) ? `${fmtSigned(io.gap, 0)}円` : null, cls(io.gap_pct)));
    if (spx) stats.push(stat('S&P500', fmtPct(spx.change_pct), fmtNum(spx.last, 0), cls(spx.change_pct)));
    if (jpy) stats.push(stat('ドル円', fmtNum(jpy.last, 2), fmtPct(jpy.change_pct), cls(jpy.change_pct)));
    if (d.risk) chips.push(h('span', { class: 'badge badge--' + (d.risk.tone === 'positive' ? 'up' : d.risk.tone === 'negative' ? 'down' : 'accent'), text: 'リスク環境: ' + d.risk.label }));
    if (sox) chips.push(h('span', { class: 'badge', text: `SOX ${fmtPct(sox.change_pct)}` }));
    const tw = ((d.sector_outlook || {}).tailwind || []).slice(0, 2).map((s) => s.sector.replace(/（.*）/, ''));
    if (tw.length) chips.push(h('span', { class: 'badge badge--up', text: '追い風: ' + tw.join('・') }));
    const ps = ((d.carryover || {}).prev_session || {});
    if (ps.session_shift) chips.push(h('span', { class: 'badge', text: `前営業日（${md(ps.date)}）: ${ps.session_shift.verdict}` }));
  } else {
    const nk = (d.indices || {}).nikkei, tp = (d.indices || {}).topix, br = d.breadth;
    if (nk) stats.push(stat('日経平均', fmtPct(nk.change_pct), fmtNum(nk.close, 0), cls(nk.change_pct)));
    if (tp) stats.push(stat('TOPIX', fmtPct(tp.change_pct), fmtNum(tp.close, 2), cls(tp.change_pct)));
    if (br) stats.push(stat('225中 上昇', `${br.up}`, `下落 ${br.down}`, br.up > br.down ? 'up' : br.up < br.down ? 'down' : 'flat'));
    if (d.divergence) chips.push(h('span', { class: 'badge badge--' + (d.divergence.tone === 'warn' ? 'warn' : 'accent'), text: d.divergence.label }));
    const s33 = ((d.sectors33 || {}).rows || []);
    const sj = s33.length >= 20 ? s33.map((r) => ({ sector: r.sector })) : (d.sectors_jp || []);
    if (sj.length) {
      chips.push(h('span', { class: 'badge badge--up', text: '強い: ' + sj.slice(0, 2).map((s) => s.sector).join('・') }));
      chips.push(h('span', { class: 'badge badge--down', text: '弱い: ' + sj.slice(-2).reverse().map((s) => s.sector).join('・') }));
    }
    const tf = ((d.theme_flow || {}).top || []).slice(0, 2).map((t) => '#' + t.theme);
    if (tf.length) chips.push(h('span', { class: 'badge badge--accent', text: 'テーマ: ' + tf.join(' ') }));
    const lt = d.ledger_today;
    if (lt && lt.added && lt.added.length) {
      chips.push(h('button', { class: 'badge badge--warn badge--btn', type: 'button', onclick: () => selectView('trade', 'sw-watch'),
        text: '発掘に追加: ' + lt.added.slice(0, 3).map((a) => a.name).join('・') + ' ›' }));
    }
    if (slot === 'zenba' && d.verify_open) {
      chips.push(h('span', { class: 'badge', text: `寄り前想定 ${fmtPct(d.verify_open.expected_pct)} → 実際 ${fmtPct(d.verify_open.actual_pct)}` }));
    }
    if (slot === 'taibike' && d.session_shift) {
      chips.push(h('span', { class: 'badge badge--' + (d.session_shift.tone === 'positive' ? 'up' : d.session_shift.tone === 'negative' ? 'down' : 'accent'), text: '後場: ' + d.session_shift.verdict }));
    }
    const ds = d.disclosure_summary;
    if (ds) {
      const rev = (ds.items || []).find((i) => i.label === '業績予想の修正');
      chips.push(h('button', { class: 'badge badge--btn' + (rev && rev.count >= 5 ? ' badge--warn' : ''), type: 'button', onclick: () => selectView('news', 'nw-disc'),
        text: `開示 ${ds.total}件` + (rev ? `・修正 ${rev.count}` : '') + ' ›' }));
    }
  }

  const ea = d.earnings;
  if (ea && (ea.items || []).length) {
    const top = ((ea.wind || {}).industries || []).find((r) => r.lean);
    const et = (d.ai_earnings && d.ai_earnings.headline)
      || (top ? `${top.key}の決算が${EARN_DIR[top.lean][0]}（上${top.up}・下${top.down}）` : `${ea.n_read}社を読む`);
    chips.push(h('button', { class: 'badge badge--btn', type: 'button', text: '決算: ' + et + ' ›', onclick: () => selectView('news', 'nw-earn') }));
  }
  const mh = (d.ai_macro && d.ai_macro.headline) || (d.macro_view && d.macro_view.headline);
  if (mh) {
    chips.push(h('button', { class: 'badge badge--btn', type: 'button', text: 'マクロ: ' + mh.split('。')[0] + ' ›',
      onclick: () => { const t = document.getElementById('sec-macro'); if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' }); } }));
  }
  return h('section', { class: 'card summary', id: 'sec-summary' }, [
    headline ? h('p', { class: 'summary__head', text: headline }) : null,
    stats.length ? h('div', { class: 'summary__stats' }, stats) : null,
    chips.length ? h('div', { class: 'summary__line' }, chips) : null,
    analysisDetails(d),
  ]);
}

function hero(label, value, deltaText, deltaVal, aside, verdict, tone, id) {
  return h('section', { class: 'card hero', id: id || null }, [
    h('div', { class: 'hero__label', text: label }),
    h('div', { class: 'hero__row' }, [
      h('div', {}, [
        h('div', { class: 'hero__value num ' + cls(deltaVal), text: value }),
        h('div', { class: 'hero__delta num ' + cls(deltaVal), text: deltaText }),
      ]),
      aside ? h('div', { class: 'hero__aside' }, aside) : null,
    ]),
    verdict ? h('div', { class: 'hero__verdict is-' + (tone || 'neutral'), text: verdict }) : null,
  ]);
}

function noData(slot) {
  const others = SLOTS.filter((s) => s !== slot && (DATA.slots || {})[s]);
  return h('section', { class: 'card' }, [
    h('h2', { class: 'card__title', text: SLOT_LABEL[slot] + 'のデータはまだありません' }),
    h('p', { class: 'hint', text: '自動更新が走ると表示されます。上の時間帯ボタンに実際の更新時刻を出しています。' +
      (others.length ? `　いまは「${others.map((s) => SLOT_LABEL[s]).join('」「')}」が読めます。` : '') }),
  ]);
}


/* ==================== 今日の業種・テーマ ==================== */
/* 今日の業種の騰落。東証33業種（株探の記事）と日経225採用銘柄の業種平均を1枚で切り替える。
   何日も続く強さは業種タブ（強弱・日経の業種の8日）に置き、ここはその日の騰落だけ */
function sectorsTodayCard(d) {
  const sec = d.sectors33 || {};
  const s33 = (sec.rows || []).length >= 20 ? sec.rows : null;
  const s225 = (d.sectors_jp || []).length >= 4 ? d.sectors_jp : null;
  if (!s33 && !s225) return null;
  const body = h('div', {});
  const note = h('div', { class: 'card__note' });
  const draw = (mode) => {
    body.textContent = '';
    let all, label;
    if (mode === 's33') {
      all = s33.map((r) => ({ label: r.sector, value: r.change_pct, sub: (r.leaders || []).map((l) => l.name).join('・') || null }));
      label = '全33業種';
      const pr = sec.prime;
      note.textContent = (isNum(sec.up) ? `上昇 ${sec.up} / 下落 ${sec.down} 業種。` : '') +
        (pr ? `プライム ${pr.total}銘柄中 上昇 ${pr.up} / 下落 ${pr.down}。` : '') +
        '出典: 株探「本日の【業種】騰落ランキング」（Yahoo!ファイナンス配信）。業種名の下は上昇率上位の銘柄。';
    } else {
      all = s225.map((x) => ({
        label: `${x.sector}（${x.count}）`, value: x.avg_pct,
        sub: x.best && x.worst
          ? `高 ${x.best.name || x.best.code} ${fmtPct(x.best.change_pct, 1)} ／ 安 ${x.worst.name || x.worst.code} ${fmtPct(x.worst.change_pct, 1)}` : null,
      }));
      label = `全${all.length}業種`;
      note.textContent = '日経の業種区分で採用銘柄をまとめた単純平均（括弧は銘柄数）。時価総額加重の東証33業種指数とは一致しません。';
    }
    const short = all.slice(0, 6).concat([{ gap: true }], all.slice(-6));
    body.appendChild(foldable((n) => barList(n >= all.length ? all : short), all.length, short.length, label));
  };
  const opts = [s33 ? ['s33', '東証33業種'] : null, s225 ? ['s225', '225採用の平均'] : null].filter(Boolean);
  const sub = opts.length > 1 ? segmented(opts, draw, opts[0][0]) : (opts[0][0] === 's33' ? '東証33業種' : '225採用銘柄の平均');
  draw(opts[0][0]);
  return h('section', { class: 'card', id: 'sec-sector' }, [
    h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: '業種の騰落' }), typeof sub === 'string' ? h('span', { class: 'card__sub', text: sub }) : sub]),
    body, note,
    h('button', { class: 'more', type: 'button', text: '業種の強弱（何日も続く強さ）を業種タブで見る', onclick: () => selectView('sectors') }),
  ]);
}

function themeCard(flow) {
  const top = (flow && flow.top) || [];
  if (!top.length) return null;
  const streak = new Map(((flow && flow.streaks) || []).map((s) => [s.theme, s.days]));
  const rows = h('div', { class: 'rows' }, top.map((t, i) => {
    const meta = [h('span', { text: `${t.count}銘柄` })];
    if (streak.get(t.theme) > 1) meta.push(h('span', { class: 'tag', text: `${streak.get(t.theme)}日連続` }));
    if (!t.was_top) meta.push(h('span', { class: 'tag tag--warn', text: '初動' }));
    meta.push(h('span', { text: (t.names || []).join('・') }));
    return h('div', { class: 'row' }, [
      h('div', { class: 'row__rank num', text: String(i + 1) }),
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name', text: '#' + t.theme }),
        h('div', { class: 'row__meta' }, meta),
      ]),
      h('div', { class: 'row__right' }, [
        h('div', { class: 'row__delta num ' + cls(t.avg_pct), text: isNum(t.avg_pct) ? fmtPct(t.avg_pct) : '—' }),
      ]),
    ]);
  }));
  const unknown = (flow.unknown_codes || []).length;
  return card('テーマ別の資金の向き', `辞書 ${flow.dictionary_size || 0}銘柄`, rows,
    '売買代金上位・上昇率上位・年初来高値更新の銘柄を、銘柄→テーマ辞書で束ねた集計。' +
    `平均は束ねた銘柄の騰落率。辞書で束ねられた割合 ${Math.round((flow.coverage || 0) * 100)}%` +
    (unknown ? `、未登録 ${unknown}銘柄（AIが記事を読んで辞書に追記します）` : '') + '。', true, 'sec-theme');
}

/* ==================== ニュース ====================
   株探・報道・公的機関・市況記事・海外と、TDnet の開示を1つの画面にまとめる。
   今日の区分（大引・前場・寄り前）を新しい順に重ね、同じ見出しは1本にして、時刻の新しい順に並べる。
   先頭は「保有・監視の銘柄の材料」（保有銘柄・注文・ウォッチリストの銘柄に出た開示と見出し）。
   報道は株探以外も信頼できる配信元だけ（dashboard/sources/press.py。Google ニュース経由は配信元ドメインで照合済み） */
const NEWS_KINDS = [
  ['all', 'すべて'], ['official', '公的機関'], ['press', '報道'], ['kabutan', '株探'], ['market', '市況記事'], ['overseas', '海外'],
];
const NEWS_NOTE = {
  all: '報道・公的機関・株探・市況記事・海外を時刻の新しい順に。本文のある記事は押すと開きます。',
  official: '日本銀行・財務省・日本取引所グループ・FRB の公式 RSS（一次情報）。相場に関係する表題だけに絞っている。',
  press: 'ロイター・ブルームバーグ・日本経済新聞・時事通信（Google ニュース経由。配信元ドメインで照合）と NHK の見出し。本文は各社サイトで読む。',
  kabutan: '株探の記事見出し（Google ニュース経由）と、Yahoo!ファイナンスに配信された株探ニュースの本文（大引け・マーケット日報・業種の騰落など）。',
  market: '時事通信・トレーダーズ・ウェブ（DZH）・ウエルスアドバイザーの Yahoo!ファイナンス配信（「冒頭のみ」は有料部分の手前まで）と、Yahoo!ファイナンスのマーケットAIトピックス。',
  overseas: 'CNBC の Markets・Economy・Earnings（英語）。',
};

const normText = (s) => String(s || '').normalize('NFKC').toUpperCase().replace(/\s+/g, '');

/* 並べ替え用の時刻（YYYY-MM-DDTHH:MM）。「16:38」だけの記事は、その区分の更新時刻より後なら前の営業日の記事 */
function newsTime(x, slotUpd) {
  const date = (DATA && DATA.date) || isoToday();
  if (x.published) return String(x.published).slice(0, 16);
  const t = String(x.timestamp || '');
  let m = t.match(/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})/);
  if (m) return `${date.slice(0, 4)}-${pad2(m[1])}-${pad2(m[2])}T${pad2(m[3])}:${m[4]}`;
  m = t.match(/^(\d{1,2}):(\d{2})/);
  if (!m) return '';
  const hm = `${pad2(m[1])}:${m[2]}`;
  const upd = String(slotUpd || '').slice(11, 16);
  return `${upd && hm > upd ? prevWeekday(date) : date}T${hm}`;
}

function gatherNews() {
  const slots = (DATA && DATA.slots) || {};
  const seen = new Set();
  const items = [];
  const add = (x, kind, source, upd) => {
    const title = x.title || x.headline || '';
    const k = normText(title);
    if (!title || seen.has(k)) return;
    seen.add(k);
    items.push({ kind, title, url: x.url || null, body: x.body || null, summary: x.summary || null, partial: !!x.partial,
      source: source || x.source || '', t: newsTime(x, upd) });
  };
  let failed = null;
  // 新しい区分から重ねる（同じ見出しは新しい区分のほうを残す）
  ['taibike', 'zenba', 'preopen'].forEach((s) => {
    const e = slots[s];
    const d = e && e.data;
    if (!d) return;
    const upd = e.updated_at;
    const pr = d.press || {}, kb = d.kabutan || {};
    if (failed === null && pr.status) failed = pr.status.filter((x) => !x.ok).map((x) => x.label);
    (pr.official || []).forEach((x) => add(x, 'official', x.source, upd));
    (pr.headlines || []).forEach((x) => add(x, 'press', x.source, upd));
    (kb.articles || []).forEach((x) => add(x, 'kabutan', '株探', upd));
    (kb.headlines || []).forEach((x) => add(x, 'kabutan', '株探', upd));
    (pr.articles || []).forEach((x) => add(x, 'market', x.provider || x.category, upd));
    (d.news || []).forEach((x) => add(x, 'market', 'AIトピックス' + (x.category ? `・${x.category}` : ''), upd));
    (pr.overseas || []).forEach((x) => add(x, 'overseas', x.source, upd));
  });
  items.sort((a, b) => (b.t || '').localeCompare(a.t || ''));
  const sess = latestSession() || {};
  const t = sess.tables || {};
  const pre = (slots.preopen || {}).data || {};
  return {
    items, failed: failed || [],
    disc: {
      after: (t.kessan_after || {}).rows || [],
      intraday: (t.kessan_intraday || {}).rows || [],
      carry: (pre.carryover || {}).after_hours_kessan || [],
      summary: sess.disclosure_summary || null,
    },
  };
}

function newsWhen(x) {
  return x.t ? `${Number(x.t.slice(5, 7))}/${Number(x.t.slice(8, 10))} ${x.t.slice(11, 16)}` : '';
}

function newsItem(x, tag) {
  const when = newsWhen(x);
  if (x.body) {
    return h('details', { class: 'acc news' }, [
      h('summary', {}, [h('span', { class: 'acc__title' }, [
        document.createTextNode(x.title),
        h('small', {}, [tag ? h('b', { class: 'news__tag', text: tag + '　' }) : null,
          document.createTextNode([when, x.source, x.partial ? '冒頭のみ' : null].filter(Boolean).join('　'))]),
      ])]),
      h('div', { class: 'acc__body' }, [
        document.createTextNode(x.body),
        x.url ? h('div', { style: 'margin-top:10px' }, [h('a', { href: x.url, target: '_blank', rel: 'noopener', text: '元記事を開く →' })]) : null,
      ]),
    ]);
  }
  const inner = [
    h('div', { class: 'row__rank num press__time' }, x.t ? [
      h('div', { text: `${Number(x.t.slice(5, 7))}/${Number(x.t.slice(8, 10))}` }), h('div', { text: x.t.slice(11, 16) })] : []),
    h('div', { class: 'row__main' }, [
      h('div', { class: 'row__name press__title', text: x.title }),
      h('div', { class: 'row__meta' }, [tag ? h('span', { class: 'tag tag--warn', text: tag }) : null, h('span', { class: 'tag', text: x.source })]),
      x.summary ? h('div', { class: 'press__summary', text: x.summary }) : null,
    ]),
  ];
  return x.url ? h('a', { class: 'row row--press', href: x.url, target: '_blank', rel: 'noopener' }, inner)
    : h('div', { class: 'row row--press' }, inner);
}

/* 保有銘柄・注文・ウォッチリストの銘柄（コード → 社名と理由） */
function myStocks() {
  const m = new Map();
  const put = (code, name, why) => {
    if (!code) return;
    const c = String(code);
    const x = m.get(c) || { code: c, name: '', why: [] };
    if (!x.why.includes(why)) x.why.push(why);
    if (!x.name && name) x.name = cleanName(name);
    m.set(c, x);
  };
  readPos().forEach((p) => put(p.code, p.name, '保有'));
  readHold().forEach((p) => put(p.code, p.name, '保有'));
  ((SW() || {}).orders || []).forEach((o) => put(o.code, o.name, '注文'));
  effectiveWatchlist(sessData()).forEach((w) => put(w.code, jaName(w.code, w.name), 'ウォッチ'));
  m.forEach((x) => { const st = stockOf(x.code); if (st && st.n && JA_RE.test(st.n)) x.name = cleanName(st.n); if (!x.name) x.name = x.code; });
  return m;
}

/* 見出しに社名（3文字以上）かコードが語として入っているか。カタカナ・英数字の社名は、前後が同じ字種で続くときは当てない
   （「フリー」が「ジェフリーズ」に、「ＩＨＩ」が別の英字列に当たらないように） */
const KANA_RE = /[\u30a0-\u30ff]/, ALNUM_RE = /[A-Z0-9]/;
function hasWord(t, key) {
  if (!key) return false;
  const same = (a, b) => a && b && ((KANA_RE.test(a) && KANA_RE.test(b)) || (ALNUM_RE.test(a) && ALNUM_RE.test(b)));
  for (let i = t.indexOf(key); i >= 0; i = t.indexOf(key, i + 1)) {
    if (!same(t[i - 1], key[0]) && !same(t[i + key.length], key[key.length - 1])) return true;
  }
  return false;
}
function titleHas(title, name, code) {
  const t = normText(title), key = normText(name);
  return (key.length >= 3 && hasWord(t, key)) || hasWord(t, code);
}
function mineIn(title, mine) {
  for (const x of mine.values()) if (titleHas(title, x.name, x.code)) return x;
  return null;
}

function myNewsCard(feed, mine) {
  if (!mine.size) return null;
  const disc = [];
  [['after', '引け後'], ['intraday', '場中'], ['carry', '前営業日の引け後']].forEach(([k, when]) =>
    (feed.disc[k] || []).forEach((r) => { if (mine.has(String(r.code))) disc.push({ ...r, time: when }); }));
  const news = [];
  feed.items.forEach((x) => { const hit = mineIn(x.title, mine); if (hit) news.push([x, hit]); });
  const body = [];
  if (disc.length) body.push(disclosureRows(disc, 20));
  if (news.length) body.push(h('div', { class: 'feed' }, news.slice(0, 20).map(([x, hit]) => newsItem(x, `${hit.name}（${hit.why.join('・')}）`))));
  if (!body.length) {
    return card('保有・監視の銘柄の材料', `${mine.size}銘柄`, h('p', { class: 'hint', style: 'margin:0',
      text: '保有銘柄・注文・ウォッチリストの銘柄に、今日の開示も見出しもありません。' }), null, false, 'nw-mine');
  }
  return card('保有・監視の銘柄の材料', `開示 ${disc.length}・見出し ${news.length}`, body,
    `保有銘柄・注文・ウォッチリストの ${mine.size}銘柄について、TDnet の開示（コードで照合）と、社名かコードが入った見出し。` +
    '決算・修正は翌営業日の寄りで値が飛びやすい。', true, 'nw-mine');
}

function disclosureCard(feed) {
  const D = feed.disc;
  const defs = [['after', '引け後', '今日の引け後の開示。翌営業日の寄りで値が飛びやすい。'], ['intraday', '場中', '今日の場中の開示。'],
    ['carry', '前営業日の引け後', '前営業日の引け後の開示。今日の寄りで動きやすい。']].filter(([k]) => (D[k] || []).length);
  if (!defs.length) return card('開示（決算・業績修正）', null, h('div', { class: 'empty', text: '今日の開示はまだありません' }), null, false, 'nw-disc');
  const body = h('div', {});
  const note = h('div', { class: 'card__note' });
  const draw = (k) => {
    body.textContent = '';
    const rows = D[k];
    const material = rows.filter((r) => MATERIAL.includes(r.category));
    const rest = rows.filter((r) => !MATERIAL.includes(r.category));
    const main = material.length ? material : rest;
    body.appendChild(foldable((n) => disclosureRows(main, n), main.length, 10, '全件'));
    if (material.length && rest.length) {
      body.appendChild(h('details', { class: 'acc' }, [
        h('summary', {}, [h('span', { class: 'acc__title', text: `その他の開示 ${rest.length}件（自己株式取得・月次など）` })]),
        disclosureRows(rest, 60),
      ]));
    }
    note.textContent = defs.find((x) => x[0] === k)[2] + '「上方・増配」「下方・減配」は表題から読んだもの。押すと銘柄の詳しい画面が開きます。';
  };
  const seg = segmented(defs.map(([k, l]) => [k, `${l} ${D[k].length}`]), draw, defs[0][0]);
  seg.classList.add('seg--scroll');
  draw(defs[0][0]);
  const sum = D.summary;
  return h('section', { class: 'card card--flush', id: 'nw-disc' }, [
    h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: '開示（決算・業績修正）' }),
      sum ? h('div', { class: 'chips' }, sum.items.slice(0, 2).map((i) =>
        h('span', { class: 'badge' + (i.label === '業績予想の修正' ? ' badge--warn' : ''), text: `${i.label} ${i.count}` }))) : null]),
    h('div', { class: 'card__seg' }, seg), body, note,
  ]);
}

/* ==================== 決算から読む（DESIGN.md 22章） ====================
   決算・修正の開示ごとに、会社の説明（TDnet の PDF）・株探の決算速報・類似銘柄の値動きと決算予定を機械が集め（data.earnings）、
   何が分かったか・業界の風向き・類似銘柄への連想・次に確かめること を Claude が書く（data.ai_earnings）。
   類似銘柄は連想の材料で、買う候補ではない（買う候補は売買タブの2つのルールだけ）。 */
const EARN_DIR = { up: ['上向き', 'up'], down: ['下向き', 'down'], mixed: ['強弱まちまち', 'warn'] };

/* 決算の材料がある区分。大引があれば前場は出さない（大引は前場までの開示も読んでいる） */
function earnSources() {
  const slots = (DATA && DATA.slots) || {};
  const out = [];
  ['taibike', 'zenba', 'preopen'].forEach((s) => {
    const d = (slots[s] || {}).data || {};
    if (d.earnings && (d.earnings.items || []).length) out.push({ slot: s, e: d.earnings, ai: d.ai_earnings || null });
  });
  return out.filter((x, i, a) => !(x.slot === 'zenba' && a.some((y) => y.slot === 'taibike')));
}

/* 銘柄シート用: その銘柄の決算と、その銘柄を類似銘柄に挙げた決算 */
function earnFor(code) {
  const self = [], peerOf = [];
  earnSources().forEach((src) => (src.e.items || []).forEach((it) => {
    const ai = ((src.ai && src.ai.items) || []).find((x) => String(x.code) === String(it.code)) || null;
    if (String(it.code) === code) self.push({ src, it, ai });
    const p = (it.peers || []).find((x) => String(x.code) === code);
    if (p) peerOf.push({ src, it, ai, why: ((ai && ai.peers) || []).find((x) => String(x.code) === code) });
  }));
  return { self, peerOf };
}

/* 銘柄シートから「決算から読む」へ。シートは履歴を戻して閉じる（非同期）ので、閉じ終わってから跳ぶ
   （先に跳ぶと、戻ったときのスクロールの復元で位置が上書きされる） */
function earnJump() {
  const go = () => selectView('news', 'nw-earn');
  if ($('stockSheet').open && history.state && history.state.sheet) {
    window.addEventListener('popstate', () => setTimeout(go, 60), { once: true });
    closeSheet();
  } else go();
}

function earnDirBadge(dir) {
  const x = EARN_DIR[dir];
  return x ? h('span', { class: 'badge badge--' + x[1], text: x[0] }) : null;
}

function earnPeerChip(p) {
  const tail = [];
  if (p.today) tail.push('同じ日に発表' + (EARN_DIR[p.today] ? `（${EARN_DIR[p.today][0]}）` : ''));
  else if (p.last) tail.push(`${md(p.last.date)}の決算` + (EARN_DIR[p.last.dir] ? `（${EARN_DIR[p.last.dir][0]}）` : ''));
  if (p.next) tail.push(`${md(p.next)} 決算予定`);
  if (isNum(p.r20)) tail.push(`20日 ${fmtPct(p.r20, 1)}`);
  return h('button', { class: 'peer', type: 'button', title: p.src || '', onclick: () => openStock(p.code) }, [
    h('span', { class: 'peer__top' }, [
      h('span', { class: 'peer__name', text: cleanName(p.name) || p.code }),
      isNum(p.pct) ? h('span', { class: 'peer__pct num ' + cls(p.pct), text: fmtPct(p.pct, 1) }) : null,
    ]),
    tail.length ? h('span', { class: 'peer__meta', text: tail.join('・') }) : null,
  ]);
}

function earnText(label, text, url, linkLabel) {
  return h('div', { class: 'earn__src' }, [
    h('div', { class: 'earn__lh', text: label }),
    h('div', { class: 'earn__body', text }),
    url ? h('a', { href: url, target: '_blank', rel: 'noopener', text: (linkLabel || '元を開く') + ' →' }) : null,
  ]);
}

function earnItem(it, ai, moveLabel) {
  const flash = it.flash || {};
  const pdfs = it.pdfs || {};
  const mv = (it.move || {}).pct;
  const kids = [
    h('button', { class: 'earn__head', type: 'button', onclick: () => openStock(it.code) }, [
      h('span', { class: 'earn__time num', text: (it.time || '').slice(0, 5) }),
      h('span', { class: 'earn__name', text: cleanName(it.name) || it.code }),
      h('span', { class: 'earn__code', text: it.code }),
      it.industry ? h('span', { class: 'kind', text: it.industry }) : null,
      earnDirBadge(it.dir),
      isNum(mv) ? h('span', { class: 'earn__move num ' + cls(mv), title: moveLabel, text: fmtPct(mv, 1) }) : null,
    ]),
    h('div', { class: 'earn__flash', text: flash.headline || (it.titles || [])[0] || '' }),
  ];
  if (ai && ai.read) kids.push(h('div', { class: 'earn__read', text: ai.read }));
  if (ai && ai.wind) kids.push(h('div', { class: 'earn__line' }, [h('b', { text: '風向き ' }), document.createTextNode(ai.wind)]));
  const peers = it.peers || [];
  if (peers.length) {
    const why = new Map(((ai && ai.peers) || []).map((p) => [String(p.code), p.why]));
    kids.push(h('div', { class: 'earn__lh', text: '類似銘柄（押すと銘柄シート）' }));
    kids.push(h('div', { class: 'peers' }, peers.map(earnPeerChip)));
    const whys = peers.filter((p) => why.get(String(p.code))).map((p) =>
      h('li', {}, [h('b', { text: (cleanName(p.name) || p.code) + ' ' }), document.createTextNode(why.get(String(p.code)))]));
    if (whys.length) kids.push(h('ul', { class: 'earn__whys' }, whys));
  }
  if (ai && ai.next) kids.push(h('div', { class: 'earn__line earn__next' }, [h('b', { text: '次に確かめる ' }), document.createTextNode(ai.next)]));
  const mat = [];
  if (flash.body) mat.push(earnText('株探の決算速報（数字の要約）', flash.body, flash.url, '記事を開く'));
  if (it.reason) mat.push(earnText('会社の説明: 修正の理由', it.reason, it.reason_pdf, 'PDF（TDnet）'));
  if (it.overview) mat.push(earnText('会社の説明: 経営成績の概況', it.overview, pdfs['決算短信'], 'PDF（TDnet）'));
  if (it.outlook) mat.push(earnText('会社の説明: 業績予想', it.outlook, null));
  if ((it.press || []).length) {
    mat.push(h('div', { class: 'earn__src' }, [h('div', { class: 'earn__lh', text: 'ほかの報道' })].concat(it.press.map((x) =>
      h('a', { class: 'earn__press', href: x.url, target: '_blank', rel: 'noopener', text: `📰 ${x.title}（${x.provider}）` })))));
  }
  const rest = Object.entries(pdfs).filter(([k, u]) => u !== it.reason_pdf && !(k === '決算短信' && it.overview));
  if (rest.length) {
    mat.push(h('div', { class: 'earn__src' }, rest.map(([k, u]) => h('a', { href: u, target: '_blank', rel: 'noopener', text: `${k}の PDF（TDnet） →` }))));
  }
  if (mat.length) {
    kids.push(h('details', { class: 'fold earn__mat' }, [
      h('summary', { text: '材料を読む（会社の説明・決算速報' + ((it.press || []).length ? '・報道' : '') + '）' }),
      h('div', { class: 'fold__body' }, mat),
    ]));
  }
  return h('div', { class: 'earn', id: 'earn-' + it.code }, kids);
}

/* 業種の風向き: 直近の営業日に読んだ開示の向きを業種ごとに数えたもの（機械）。全開示ではない */
function earnWind(w) {
  const rows = (w.industries || []).slice(0, 6);
  if (!rows.length) return null;
  return h('div', { class: 'earn__windbox' }, [
    h('div', { class: 'earn__lh', text: `業種の風向き（${w.days > 1 ? `直近${w.days}営業日` : 'この日'}に読んだ開示 ${w.n}件の向き。${w.min_n}件以上の業種）` }),
    h('div', { class: 'rows' }, rows.map((r) => h('div', { class: 'row wind' }, [
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name' }, [document.createTextNode(r.key + ' '), earnDirBadge(r.lean)]),
        h('div', { class: 'row__meta wind__names' }, (r.names || []).slice(0, 5).map((x) => h('button', {
          class: 'wind__name ' + (x.dir === 'up' ? 'up' : x.dir === 'down' ? 'down' : ''), type: 'button',
          text: (cleanName(x.name) || x.code) + (x.dir === 'up' ? '↑' : x.dir === 'down' ? '↓' : ''), onclick: () => openStock(x.code) }))),
      ]),
      h('div', { class: 'row__right wind__n' }, [
        h('span', { class: 'num up', text: `上 ${r.up}` }), h('span', { class: 'num down', text: `下 ${r.down}` }),
      ]),
    ]))),
  ]);
}

function earnCard() {
  const srcs = earnSources();
  if (!srcs.length) return null;               // 決算・修正の開示が無い日は出さない
  const body = h('div', { class: 'card__pad' });
  const note = h('div', { class: 'card__note' });
  const badge = h('span', { class: 'badge badge--accent' });
  const draw = (slot) => {
    body.textContent = '';
    const { e, ai } = srcs.find((x) => x.slot === slot);
    const aiItems = new Map(((ai && ai.items) || []).map((x, i) => [String(x.code), { ...x, i }]));
    badge.textContent = ai ? (ai.method || 'AI分析') : '材料のみ';
    if (ai && ai.headline) body.appendChild(h('p', { class: 'analysis__headline', text: ai.headline }));
    if (ai && (ai.winds || []).length) {
      body.appendChild(h('div', { class: 'macro__analysis' }, ai.winds.map((w) => h('div', { class: 'analysis__sec' }, [
        h('div', { class: 'analysis__t', text: w.title }),
        h('div', { class: 'analysis__b', text: w.body }),
        (w.codes || []).length ? h('div', { class: 'chips' }, w.codes.map((c) => {
          const it = (e.items || []).find((x) => String(x.code) === String(c));
          const nm = it ? it.name : (jaName(String(c), '') || String(c));
          return h('button', { class: 'badge badge--btn', type: 'button', text: cleanName(nm) + ' ›', onclick: () => openStock(String(c)) });
        })) : null,
      ]))));
    }
    const wt = earnWind(e.wind || {});
    if (wt) body.appendChild(wt);
    const items = (e.items || []).map((it, i) => ({ it, i, a: aiItems.get(String(it.code)) }))
      .sort((x, y) => (x.a ? x.a.i : 999 + x.i) - (y.a ? y.a.i : 999 + y.i));
    const moveLabel = slot === 'preopen' ? '開示の日の値動き（引け後の開示は、まだ反応していない）' : 'きょうの値動き';
    body.appendChild(h('div', { class: 'earn__lh earn__lh--list', text: `${e.n_read}社の決算（${slot === 'preopen' ? '右の％は開示の日の値動き。引け後の開示への反応はこれから' : '右の％はきょうの値動き'}）` }));
    body.appendChild(foldable((n) => h('div', { class: 'earns' }, items.slice(0, n).map((x) => earnItem(x.it, x.a, moveLabel))), items.length, 6, '全社'));
    const more = e.more || [];
    if (more.length) {
      body.appendChild(h('p', { class: 'hint', text: `読んでいない開示 ${e.n_total - e.n_read}社（1回に読むのは修正を先に最大${e.n_read}社）: ` +
        more.slice(0, 15).map((x) => cleanName(x.name) || x.code).join('、') + (more.length > 15 ? ' ほか' : '') + '。開示の一覧は下のカード。' }));
    }
    if (ai && Array.isArray(ai.sources) && ai.sources.length) {
      body.appendChild(h('div', { class: 'analysis__srcs' }, ai.sources.map((s) =>
        h('a', { href: s.url, target: '_blank', rel: 'noopener', text: '📰 ' + s.title }))));
    }
    note.textContent = (ai ? '読み（何が分かったか・風向き・類似銘柄の理由・次に確かめる）は生成AIによる分析で、誤りを含む可能性があります。'
      : 'AI の読みはまだありません（Routine の分析が終わると出ます）。下は機械が集めた材料です。') +
      '会社の説明は TDnet の PDF（一次情報）、数字の要約と「よく比較される銘柄」は株探の決算速報、業種は Yahoo!ファイナンスから集めたもの。' +
      '類似銘柄は 株探の比較銘柄 → テーマ辞書の同じテーマ → 日経225の同じ業種 の順。風向きは読んだ開示の向きの数で、全開示の集計ではない。' +
      '類似銘柄は連想の材料で、買う候補ではありません（買う候補は売買タブの2つのルールだけ）。予測ではなく、売買を推奨するものでもありません。';
  };
  const label = (x) => (x.slot === 'preopen' ? `${md(x.e.asof)} 引け後` : `${md(x.e.asof)} ${x.slot === 'zenba' ? '前場まで' : '今日'}`) + ` ${x.e.n_read}社`;
  const kids = [h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: '決算から読む' }), badge])];
  if (srcs.length > 1) {
    const seg = segmented(srcs.map((x) => [x.slot, label(x)]), draw, srcs[0].slot);
    seg.classList.add('seg--scroll');
    kids.push(h('div', { class: 'card__seg' }, seg));
  } else {
    kids.push(h('div', { class: 'card__pad hint', text: label(srcs[0]) + `（${srcs[0].e.scope}）` }));
  }
  draw(srcs[0].slot);
  kids.push(body, note);
  return h('section', { class: 'card card--flush earncard', id: 'nw-earn' }, kids);
}

function newsFeedCard(feed) {
  const items = feed.items;
  const count = (k) => (k === 'all' ? items.length : items.filter((x) => x.kind === k).length);
  const kinds = NEWS_KINDS.filter(([k]) => count(k));
  const failNote = feed.failed.length ? `　取得できなかった情報源: ${feed.failed.join('、')}。` : '';
  if (!kinds.length) return card('ニュース', 'データなし', null, failNote || null, false, 'nw-feed');
  const body = h('div', {});
  const note = h('div', { class: 'card__note' });
  const draw = (k) => {
    body.textContent = '';
    const rows = k === 'all' ? items : items.filter((x) => x.kind === k);
    body.appendChild(foldable((n) => h('div', { class: 'feed' }, rows.slice(0, n).map((x) => newsItem(x))), rows.length, 15, '全件'));
    note.textContent = NEWS_NOTE[k] + failNote;
  };
  const seg = segmented(kinds.map(([k, l]) => [k, `${l} ${count(k)}`]), draw, 'all');
  seg.classList.add('seg--scroll');
  draw('all');
  return h('section', { class: 'card card--flush', id: 'nw-feed' }, [
    h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: 'ニュース' }), h('span', { class: 'card__sub', text: '新しい順' })]),
    h('div', { class: 'card__seg' }, seg), body, note,
  ]);
}

function renderNews() {
  if (!DATA || !Object.keys(DATA.slots || {}).length) return [card('ニュース', null, h('div', { class: 'empty', text: '今日のデータはまだありません' }))];
  const feed = gatherNews();
  return [myNewsCard(feed, myStocks()), earnCard(), disclosureCard(feed), newsFeedCard(feed)];
}

/* ==================== 市況: 寄り前 ==================== */
function renderPreopen(d, upd) {
  const out = [summaryCard(d, 'preopen')];
  const io = d.implied_open;
  if (io) {
    const gapTxt = isNum(io.gap) ? `${fmtSigned(io.gap, 0)}円` : '';
    const aside = [h('span', { class: 'badge badge--accent', text: io.method_label || '' })];
    if (isNum(io.prev_close)) aside.push(h('div', { class: 'num', text: `前日終値 ${fmtNum(io.prev_close, 0)}` }));
    let note = null;
    if (io.method === 'futures') {
      if (isNum(io.futures_last)) aside.push(h('div', { class: 'num', text: `先物 ${fmtNum(io.futures_last, 0)}` }));
      const m = io.model;
      note = `${io.source_label || '日経平均先物'}の清算値（${io.asof || '日付不明'}）と前日終値の差。` +
        (m && isNum(m.gap_pct) ? `参考: ${m.method_label || '簡易推計'}では ${fmtPct(m.gap_pct)}` + (isNum(m.gap) ? `（${fmtSigned(m.gap, 0)}円）` : '') : '');
    } else if (io.method === 'model' && io.contributions) {
      note = (io.futures_note ? io.futures_note + '。' : '') +
        '内訳: ' + io.contributions.map((c) => `${c.driver} ${c.display || ''} → ${fmtSigned(c.value)}pt`).join('　/　') +
        (io.formula ? `　（係数: ${io.formula}）` : '');
    }
    out.push(hero('日経平均 想定オープン', fmtPct(io.gap_pct), gapTxt, io.gap_pct, aside, note, 'neutral', 'sec-open'));
  }
  if (d.us && Object.keys(d.us).length) {
    out.push(card('米国市場', d.freshness && d.freshness.us_asof ? d.freshness.us_asof + ' 終値' : null,
      quoteTiles(d.us, ['spx', 'ndq', 'dji', 'sox', 'rut', 'vix']), null, false, 'sec-us'));
  }
  out.push(macroCard(d, upd));
  if (d.risk) {
    out.push(card('リスク環境',
      h('span', { class: 'badge badge--' + (d.risk.tone === 'positive' ? 'up' : d.risk.tone === 'negative' ? 'down' : 'accent'), text: d.risk.label }),
      h('ul', { class: 'reasons' }, (d.risk.reasons || []).map((r) => h('li', { text: r }))), null, false, 'sec-risk'));
  }
  out.push(usLinkCard(d));
  out.push(thermoCard());
  return out;
}

/* 米国からの連想（日本の業種）と米国セクターを1枚で切り替える */
function usLinkCard(d) {
  const so = d.sector_outlook;
  const hasSo = so && so.tailwind && so.tailwind.length;
  const us = d.sectors_us && Object.values(d.sectors_us).filter((s) => isNum(s.change_pct));
  if (!hasSo && !(us && us.length)) return null;
  const body = h('div', {});
  const note = h('div', { class: 'card__note' });
  const draw = (k) => {
    body.textContent = '';
    if (k === 'jp') {
      const mk = (arr) => arr.map((s) => ({
        label: s.sector, value: s.score,
        sub: (s.drivers || []).map((x) => `${x.driver} ${x.display || fmtPct(x.raw)}`).join(' · '),
      }));
      const tw = mk(so.tailwind), hw = mk(so.headwind || []);
      const all = tw.slice(0, 4).concat(hw.length ? [{ gap: true }] : [], hw.slice(0, 4));
      body.appendChild(foldable((n) => barList(n >= all.length ? tw.concat([{ gap: true }], hw) : all, { raw: true }),
        tw.length + hw.length + 1, all.length, '全業種'));
      note.textContent = '各業種を説明する米国側の指標（半導体・金利・為替など）の前日変化を重み付けした相対スコア。株価の予測ではなく、どの物色テーマに追い風が吹いているかの並び順。';
    } else {
      const items = us.slice().sort((a, b) => b.change_pct - a.change_pct).map((s) => ({ label: s.label, value: s.change_pct }));
      body.appendChild(foldable((n) => barList(items.slice(0, n)), items.length, 6, '12セクター'));
      note.textContent = '米国のセクター別の前日比。';
    }
  };
  const opts = [hasSo ? ['jp', '日本の業種'] : null, us && us.length ? ['us', '米国セクター'] : null].filter(Boolean);
  draw(opts[0][0]);
  return h('section', { class: 'card', id: 'sec-outlook' }, [
    h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: '米国からの連想' }),
      opts.length > 1 ? segmented(opts, draw, opts[0][0]) : h('span', { class: 'card__sub', text: opts[0][1] })]),
    body, note,
  ]);
}

/* ==================== マクロ環境（金利・為替・商品の流れと、報道で多かった話題。DESIGN.md 20章） ====================
   上から: 見立ての見出し → 系列の表（水準・前日・5日・20日・1年のレンジの位置）→ 見立ての本文（AI が書いていれば AI、
   無ければ数字だけで組み立てた文章）→ ニュースの論点・指標と要人発言・材料の本文（畳む）。
   ニュースタブの一覧には、ここの短信・材料の本文は入れない（1つの情報は1か所だけ）。 */
function macroMove(r, k) {
  const v = r[k];
  if (!isNum(v)) return '—';
  if (r.group === 'rate') return v ? `${v > 0 ? '+' : ''}${v}bp` : '0bp';
  return fmtPct(v, 1);
}
function macroLevel(r) {
  if (r.group === 'rate') return fmtNum(r.last, 3) + '%';
  return fmtNum(r.last, Math.min(r.digits ?? 2, 2));
}
/* 1年のレンジの中の位置（左端＝1年の最低、右端＝最高） */
function rangeBar(pos) {
  if (!isNum(pos)) return h('span', { text: '—' });
  const mark = h('i', { class: 'range__mark' });
  mark.style.left = Math.max(0, Math.min(100, pos)) + '%';
  return h('span', { class: 'range', title: `1年のレンジの ${pos}% の位置`, 'aria-label': `1年のレンジの ${pos}% の位置` }, [mark]);
}
function macroRowNote(r) {
  if (r.record === 'high') return `約${r.years}年で最高`;
  if (r.record === 'low') return `約${r.years}年で最低`;
  if (r.trend) return `20日で${r.trend}`;
  return null;
}
function macroTable(rows) {
  return h('div', { class: 'tablewrap' }, h('table', { class: 'bt bt--macro' }, [
    h('thead', {}, h('tr', {}, ['', '水準', '前日', '5日', '20日', '1年の位置'].map((t) => h('th', { text: t })))),
    h('tbody', {}, rows.map((r) => {
      const note = macroRowNote(r);
      return h('tr', {}, [
        h('th', {}, [document.createTextNode(r.label),
          note ? h('small', { class: 'macro__note ' + (r.record === 'high' || (r.trend && isNum(r.norm) && r.norm > 0) ? 'up' : 'down'), text: note }) : null]),
        h('td', { class: 'num', text: macroLevel(r) }),
        h('td', { class: 'num ' + cls(r.d1), text: macroMove(r, 'd1') }),
        h('td', { class: 'num ' + cls(r.d5), text: macroMove(r, 'd5') }),
        h('td', { class: 'num ' + cls(r.d20), text: macroMove(r, 'd20') }),
        h('td', {}, rangeBar(r.pos)),
      ]);
    })),
  ]));
}
/* 報道・短信の1行（ニュースタブと同じ部品） */
function macroNews(x, tag, upd) {
  return newsItem({ title: x.title || x.headline, url: x.url, body: x.body || null, partial: !!x.partial,
    source: x.source || x.provider || '', t: newsTime(x, upd) }, tag);
}
function macroCard(d, upd) {
  const mv = d.macro_view;
  const ai = d.ai_macro && (d.ai_macro.sections || []).length ? d.ai_macro : null;
  const c = ai || (mv && mv.commentary);
  const pr = d.press || {};
  if (!mv && !c) {
    return card('マクロ環境', null, h('div', { class: 'empty', text: 'マクロ環境のデータはまだありません。次の自動更新（寄り前・前場・大引）で作られます。' }), null, false, 'sec-macro');
  }
  const kids = [];
  const head = (c && c.headline) || (mv && mv.headline);
  if (head) kids.push(h('p', { class: 'analysis__headline', text: head }));
  const rows = (mv && mv.rows) || [];
  if (rows.length) {
    const groups = [['rate', '金利'], ['fx', '為替'], ['commo', '商品']];
    kids.push(macroTable(rows.slice().sort((a, b) => groups.findIndex((g) => g[0] === a.group) - groups.findIndex((g) => g[0] === b.group))));
    const sp = (mv.spreads || []).map((s) => `${s.label} ${fmtNum(s.last, 2)}%（20日 ${s.d20 > 0 ? '+' : ''}${s.d20}bp）`);
    if (sp.length) kids.push(h('div', { class: 'hint' }, sp.map((t) => h('div', { text: t }))));
  }
  if (c && c.sections && c.sections.length) {
    const secs = c.sections.map((s) => h('div', { class: 'analysis__sec' }, [
      h('div', { class: 'analysis__t', text: s.title }), h('div', { class: 'analysis__b', text: s.body })]));
    if (ai && Array.isArray(ai.sources) && ai.sources.length) {
      secs.push(h('div', { class: 'analysis__srcs' }, ai.sources.map((src) =>
        h('a', { href: src.url, target: '_blank', rel: 'noopener', text: '📰 ' + src.title }))));
    }
    kids.push(h('div', { class: 'macro__analysis' }, secs));
  }
  const tps = (mv && mv.topics) || [];
  if (tps.length) {
    kids.push(h('details', { class: 'fold' }, [
      h('summary', { text: `ニュースの論点（${tps.length}つの話題・何媒体が報じたか）` }),
      h('div', { class: 'fold__body' }, tps.map((t) => h('div', { class: 'topic' }, [
        h('div', { class: 'topic__head' }, [h('b', { text: t.label }),
          h('span', { class: 'topic__n', text: `${t.media.length}媒体・${t.n}本` + (t.official ? `（公的機関 ${t.official}）` : '') })]),
        h('div', { class: 'topic__media', text: t.media.join('・') }),
        h('div', { class: 'feed' }, (t.items || []).map((x) => macroNews(x, x.official ? '公式' : null, upd))),
      ]))),
    ]));
  }
  const wire = pr.wire || [];
  if (wire.length) {
    const tag = { result: '結果', schedule: '予定', remarks: '発言' };
    kids.push(h('details', { class: 'fold' }, [
      h('summary', { text: `経済指標と要人発言（${wire.length}本）` }),
      h('div', { class: 'fold__body' }, foldable((n) => h('div', { class: 'feed' }, wire.slice(0, n).map((x) => macroNews(x, tag[x.kind], upd))),
        wire.length, 12, '全件')),
    ]));
  }
  const arts = pr.macro_articles || [];
  if (arts.length) {
    kids.push(h('details', { class: 'fold' }, [
      h('summary', { text: `材料を読む（予定・FF金利の織り込み・指標の一覧など ${arts.length}本）` }),
      h('div', { class: 'fold__body' }, h('div', { class: 'feed' }, arts.map((x) => macroNews(x, null, upd)))),
    ]));
  }
  const th = (mv && mv.rules && mv.rules.trend) || {};
  const note = (ai
    ? '見立ては生成AIによる分析です。表の数字と報道・公的機関・株探の記事をもとに書いていますが、誤りを含む可能性があります。'
    : '見立ては表の数字と見出しの本数だけで組み立てた文章です（同じ数字からは同じ文章）。') +
    `「20日で上昇」などは20営業日の変化が目安（米金利 ${th.us10y ?? 20}bp・日本10年 ${th.jp10y ?? 10}bp・ドル円 ${th.usdjpy ?? 2}%・原油 ${th.wti ?? 8}%・金 ${th.gold ?? 5}%）を超えたもの。` +
    '「最高・最低」は取れている約3年の日足（CNBC）で比べたもの。1年の位置は左端が1年の最低、右端が最高。予測ではありません。売買を推奨するものではありません。';
  const el = card('マクロ環境', h('span', { class: 'badge badge--accent', text: ai ? (ai.method || 'AI分析') : 'ルールベース' }), kids, note, false, 'sec-macro');
  el.classList.add('macro');
  return el;
}

/* ==================== 市況: 前場・大引 ==================== */
function indexTiles(indices) {
  const items = Object.values(indices || {}).map((v) =>
    tile(v.label + (v.stale ? '（前日）' : ''), fmtNum(v.close, 2),
         fmtPct(v.change_pct) + '  ' + fmtSigned(v.change, Math.abs(v.close) >= 10000 ? 0 : 2), v.change_pct));
  if (!items.length) return h('div', { class: 'empty', text: '指数を取得できませんでした' });
  return h('div', { class: 'tiles tiles--3' }, items);
}

/* 保有・注文・ウォッチの銘柄に付ける印（ランキング・ヒートマップで自分の銘柄を見つけやすく） */
function mineTag(code, mine) {
  const x = mine && mine.get(String(code));
  return x ? h('span', { class: 'tag tag--mine', text: '★' + x.why[0] }) : null;
}

/* ランキング: 売買代金・値上がり・値下がり・高値更新・出来高急増を1枚で切り替える */
function rankingCard(d) {
  const t = d.tables || {};
  const dl = d.ranking_delta || {};
  const newSet = new Set((dl.new || []).map((n) => n.code));
  const upMap = new Map((dl.rank_up || []).map((n) => [n.code, n]));
  const streakMap = new Map((d.streaks || []).map((s) => [s.code, s]));
  const mine = myStocks();
  const valueLabel = (t.value || {}).label || '売買代金';
  const span = (x) => (x ? h('span', { text: x }) : null);
  const defs = [
    ['value', valueLabel, {
      meta: (r) => [mineTag(r.code, mine), newSet.has(r.code) ? '🆕 新規' : null, upMap.has(r.code) ? `▲${upMap.get(r.code).jump}位` : null,
        streakMap.has(r.code) ? `${streakMap.get(r.code).days}日連続` : null],
      note: (valueLabel === '売買代金' ? '売買代金の上位＝資金が向かった先。' : '売買代金が取れず出来高の上位を出しています。') +
        ((dl.new && dl.new.length) ? `前営業日から新たに上位入り: ${dl.new.slice(0, 6).map((n) => cleanName(n.name) || n.code).join('、')}。` : ''),
    }],
    ['gainer', '値上がり', { meta: (r) => [mineTag(r.code, mine), span(r.market)], note: '値上がり率の上位。' }],
    ['loser', '値下がり', { meta: (r) => [mineTag(r.code, mine), span(r.market)], note: '値下がり率の上位。' }],
    ['ytd_high', '高値更新', {
      meta: (r) => [mineTag(r.code, mine), span(r.market), span(isNum(r.metric) ? `前高値 ${fmtPrice(r.metric)}` : null)],
      note: '年初来高値を更新した銘柄。右の％は前営業日までの年初来高値をどれだけ上抜けたか（前日比ではない）。ETF・REIT は除外。',
    }],
    ['vol_surge', '出来高急増', {
      meta: (r) => [mineTag(r.code, mine), span(r.market), span(isNum(r.volume) ? `出来高 ${fmtNum(r.volume, 0)}` : null)],
      right: (r) => h('div', { class: 'row__delta num', text: isNum(r.metric) ? `×${fmtNum(r.metric, 1)}` : '—' }),
      note: '右は前日出来高に対する倍率。ETF・REIT は除外。低位株や小型株が多いので、売買代金上位との重なりを見る。',
    }],
  ].filter(([k]) => t[k] && (t[k].rows || []).length);
  if (!defs.length) return null;
  const body = h('div', {});
  const note = h('div', { class: 'card__note' });
  const draw = (k) => {
    const [, , o] = defs.find((x) => x[0] === k);
    const rows = t[k].rows;
    body.textContent = '';
    body.appendChild(foldable((n) => stockRows(rows, { limit: n, meta: o.meta, right: o.right }), rows.length, 10, `${rows.length}位まで`));
    note.textContent = o.note + '★は保有・注文・ウォッチの銘柄。押すと銘柄の詳しい画面が開きます。';
  };
  const seg = segmented(defs.map(([k, l]) => [k, l]), draw, defs[0][0]);
  seg.classList.add('seg--scroll');
  draw(defs[0][0]);
  return h('section', { class: 'card card--flush', id: 'sec-rank' }, [
    h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: 'ランキング' })]),
    h('div', { class: 'card__seg' }, seg), body, note,
  ]);
}

function renderSession(d, slot, upd) {
  const out = [summaryCard(d, slot)];
  const nk = (d.indices || {}).nikkei;
  let verdict = null, tone = 'neutral';
  if (slot === 'taibike' && d.session_shift) {
    verdict = '後場: ' + d.session_shift.verdict +
      (isNum(d.session_shift.indices?.[0]?.diff) ? `（前場終値比 ${fmtSigned(d.session_shift.indices[0].diff, 0)}円）` : '');
    tone = d.session_shift.tone;
  } else if (slot === 'zenba' && d.verify_open) {
    verdict = `寄り前の想定 ${fmtPct(d.verify_open.expected_pct)} に対し実際 ${fmtPct(d.verify_open.actual_pct)}。` + d.verify_open.verdict;
    tone = d.verify_open.tone;
  }
  out.push(card('指数', nk && nk.asof ? nk.asof + (nk.stale ? '（前営業日の値）' : '') : null, [
    indexTiles(d.indices),
    d.divergence ? h('div', { class: 'card__note', text: d.divergence.comment }) : null,
    verdict ? h('div', { class: 'hero__verdict is-' + tone, text: verdict }) : null,
  ], null, false, 'sec-index'));
  out.push(macroCard(d, upd));
  out.push(sectorsTodayCard(d));
  out.push(themeCard(d.theme_flow));
  out.push(rotationCard());
  out.push(rankingCard(d));
  out.push(heatmapCard(d.constituents, d.breadth));
  out.push(thermoCard());
  return out;
}

/* ==================== ウォッチリスト ==================== */
function localCodes() {
  try {
    const v = JSON.parse(localStorage.getItem(LS.codes) || 'null');
    return Array.isArray(v) ? v : null;
  } catch (e) { return null; }
}

function effectiveWatchlist(d) {
  const server = (d && d.watchlist) || [];
  const byCode = new Map(server.filter((s) => s && s.code).map((s) => [String(s.code), s]));
  const serverCodes = [...byCode.keys()];
  let local = localCodes();
  // サーバ側が端末の編集内容に追いついたら、端末の上書きは役目を終える。
  if (local && local.length === serverCodes.length && local.every((c) => byCode.has(String(c)))) {
    try { localStorage.removeItem(LS.codes); } catch (e) { /* 非対応環境は無視 */ }
    local = null;
  }
  const codes = local || serverCodes;
  const missing = codes.filter((c) => !byCode.has(String(c)));
  const fallback = missing.length ? localQuotes(missing.map(String)) : new Map();
  return codes.map((c) => {
    const s = byCode.get(String(c));
    if (s) return { ...s, name: jaName(s.code, s.name) };
    return Object.assign({ code: String(c) }, fallback.get(String(c)) || {}, { pending: true });
  });
}

/* 日本語の社名を優先する。以前の収集は CNBC の英語の社名（"ASICS Corporation"）をそのまま入れていたので、
   英語しか無いときは温度計の日足（thermo.json）に載っている日本語の社名に置き換える。 */
const JA_RE = /[\u3040-\u30ff\u3400-\u9fff]/;
function jaName(code, name) {
  if (name && JA_RE.test(name)) return name;
  const n = ((((THERMO || {}).stocks || {})[code]) || {}).n;
  return n && JA_RE.test(n) ? n : name;
}

/* サーバの watchlist.json に無いコード（端末だけで登録した分）は、手元にあるデータで埋める。
   温度計の日足（thermo.json: 225採用・台帳・テーマ辞書の銘柄）→ 今日の一覧・直近の履歴の順。 */
function localQuotes(codes) {
  const out = new Map();
  const stocks = (THERMO || {}).stocks || {};
  codes.forEach((c) => {
    const st = stocks[c];
    if (st && isNum(st.price)) {
      out.set(c, { name: st.n, price: st.price, change_pct: st.d1, sector: st.s, asof: THERMO.asof });
    }
  });
  const rest = codes.filter((c) => !out.has(c));
  if (rest.length) {
    searchIndex().forEach((x) => {
      if (!rest.includes(x.code) || out.has(x.code) || !isNum(x.price)) return;
      out.set(x.code, { name: x.name, price: x.price, change_pct: x.change_pct });
    });
  }
  return out;
}

function hasGitHubToken() {
  try { return !!localStorage.getItem(LS.token); } catch (e) { return false; }
}

/* 保存したウォッチリストの登録・解除（銘柄シートの☆から）。トークンがあればリポジトリにも書く */
async function toggleWatch(code) {
  const cur = effectiveWatchlist(sessData()).map((s) => String(s.code));
  const on = !cur.includes(code);
  if (on && cur.length >= 40) return { ok: false, msg: '登録は40銘柄までです' };
  const next = on ? cur.concat([code]) : cur.filter((c) => c !== code);
  try { localStorage.setItem(LS.codes, JSON.stringify(next)); } catch (e) { /* 保存できない環境は無視 */ }
  try {
    const r = await pushToGitHub(next);
    return { ok: true, on, msg: (on ? 'ウォッチリストに入れました' : 'ウォッチリストから外しました') +
      (r.synced ? '（リポジトリに保存。次の収集から開示・ランキングと突き合わせます）' : '（この端末だけ。リポジトリに書くには監視の「編集」→「連携設定」）') };
  } catch (e) {
    return { ok: true, on, msg: '端末には保存しましたが、リポジトリ側は失敗しました: ' + e.message };
  }
}

/* ==================== 売買（短期の押し目買いと保有銘柄） ====================
   売買タブは「今日やること」だけを上から並べる:
     0. 市況の一行   … いまの時間帯にやること・見立ての見出し・日経／想定オープン・温度（押すと市況タブ）
     1. 短期の押し目買い … 前の営業日の引けで出た買いの指値（次の営業日だけ有効）と株数（dashboard/swing.py、DESIGN.md 13章）
     1b. 中期の押し目 … 1年で強い銘柄の1〜3か月の調整で押した日の指値・調整中の銘柄（DESIGN.md 21章）
     2. 保有銘柄     … 押し目買い・中期の押し目で買った銘柄の計画と、自分の判断で持つ銘柄の判定（hold.py、18章）を1つに
     3. 監視         … もうすぐ注文対象・ウォッチリスト・発掘（台帳）・業種で見る・追いかけない。あと何％下げたら注文対象か
   成績・ルール・判定の根拠は検証タブ、買う前／売る前チェックは銘柄シートに置く。
   買う候補を出すのは短期と中期の2つのルールだけで、1銘柄に1つの計画（中期の形の押しは中期だけ。同じ銘柄に別々の計画を出さない）。 */
function latestSession() {
  const slots = (DATA && DATA.slots) || {};
  return (slots.taibike || slots.zenba || {}).data || null;
}
/* 今日の区分のうち、場中か大引のデータ（無ければ寄り前） */
function sessData() {
  return latestSession() || (((DATA || {}).slots || {}).preopen || {}).data || {};
}
/* いちばん新しく届いた区分（更新時刻の順） */
function latestSlot() {
  const slots = (DATA && DATA.slots) || {};
  return SLOTS.filter((s) => slots[s]).sort((a, b) => String(slots[b].updated_at || '').localeCompare(String(slots[a].updated_at || '')))[0] || null;
}
function stockOf(code) { return (((THERMO || {}).stocks || {})[code]) || null; }

function histSessions() {
  return HIST.dates.slice().reverse().map((d) => HIST.byDate.get(d)).filter(Boolean);
}

const LS_RISK = { capital: 'md.risk.capital', slot: 'md.risk.slot' };
const LS_POS = 'md.swing.pos';        // 保有中（この端末だけ）
const LS_DONE = 'md.swing.done';      // 手仕舞った記録（この端末だけ）
const LS_GUARD = 'md.guard.log';
const ZONE_TONE = { hot: 'up', warm: 'warn', neutral: 'accent', cool: 'accent', cold: 'down' };
const CLASS_TONE = {
  '押し目': 'ok', '売られすぎ・下げ止まり': 'ok', '上昇トレンド': 'accent', '中立': '',
  '下落トレンド': 'down', '過熱': 'warn',
};
const RULE_DEFAULT = { rsi_n: 2, rsi_max: 10, ma_long: 200, ma_mid: 50, liq_min: 10, min_price: 300, entry_atr: 0.5,
  stop_atr: 3, exit_n: 4, floor: 0.2, max_hold: 10, max_orders: 5, sector_cap: 2, slot_pct: 10, cost: 0.1,
  peer_n: 20, peer_dip: -5, peer_up: 3, peer_lag: -5,
  // 中期の押し目（swing.py の MID_*。DESIGN.md 21章）
  mid_mom_n: 250, mid_mom_skip: 20, mid_top: 0.6667, mid_slope_n: 20, mid_hi_n: 60, mid_age: [20, 59], mid_depth: -10,
  mid_stop_atr: 1, mid_hold: 60, mid_slots: 6 };

/* 業種の中での位置（押しの形）。swing.py の PEER_CLASSES と同じキー。
   4年の検証（DESIGN.md 14章で決め、15章の売り指値の下限つきで数え直した。前半 2023-24・後半 2025-26）の勝率を並べて出す */
const PEER_INFO = {
  dip_lag: { label: '業種ぐるみの押し・下げ大', short: '業種ぐるみ・下げ大', tone: 'ok', stat: '前半 89%・後半 93%' },
  dip: { label: '業種ぐるみの押し', short: '業種ぐるみ', tone: 'ok', stat: '前半 93%・後半 89%' },
  lag: { label: '出遅れの押し', short: '出遅れ', tone: 'accent', stat: '前半 84%・後半 91%' },
  plain: { label: 'ふつうの押し', short: 'ふつう', tone: '', stat: '' },
  none: { label: '比べる業種なし', short: '業種なし', tone: '', stat: '' },
  hot: { label: '業種の上げに沿った押し', short: '見送り', tone: 'warn', stat: '前半 75%・後半 85%' },
};
const HOT_AVG = '−0.6%／+0.05%';     // 見送りの1回の平均（前半／後半）。ほかの押しは +0.4〜+6.4%
const fmtPt = (v) => (isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(1) + 'pt' : '—');
/* 押しの形の一言（「半導体製造装置 −12.3%（20日）・業種より −6.1pt」） */
function peerLine(g, g20, rel) {
  if (!g) return '';
  return `業種「${g}」${fmtPct(g20, 1)}（${rules().peer_n}日・自分を除いた中央値）、この銘柄は業種より ${fmtPt(rel)}`;
}
function peerBadge(key) {
  const p = PEER_INFO[key];
  if (!p || key === 'none' || key === 'plain') return null;
  return h('span', { class: 'badge' + (p.tone ? ' badge--' + p.tone : ''), text: p.label });
}

function SW() { return (THERMO || {}).swing || null; }
function MID() { return (SW() || {}).mid || null; }
function rules() { return { ...RULE_DEFAULT, ...((SW() || {}).rules || {}) }; }
function swOf(code) { return (((THERMO || {}).stocks || {})[code] || {}).sw || null; }
/* 中期の押し目の注文（今日の引けで出たもの）と、保有中の中期の銘柄 */
function midOrderOf(code) { return ((MID() || {}).orders || []).find((o) => o.code === code) || null; }
const isMidPos = (p) => p && p.kind === 'mid';

function lsNum(key, def) {
  try { const v = parseFloat(localStorage.getItem(key)); return isFinite(v) && v > 0 ? v : def; } catch (e) { return def; }
}
function lsJson(key) {
  try { const v = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(v) ? v : []; } catch (e) { return []; }
}
function lsSave(key, v) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* 保存できない環境は無視 */ }
}
function fmtYen(v) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1) + '億円';
  if (a >= 1e4) return (v / 1e4).toLocaleString('ja-JP', { maximumFractionDigits: a >= 1e6 ? 0 : 1 }) + '万円';
  return Math.round(v).toLocaleString('ja-JP') + '円';
}
const pad2 = (n) => String(n).padStart(2, '0');
function isoToday() { const n = jstNow(); return `${n.getFullYear()}-${pad2(n.getMonth() + 1)}-${pad2(n.getDate())}`; }
function nextWeekday(iso) {
  const d = new Date(iso + 'T00:00:00Z');
  do { d.setUTCDate(d.getUTCDate() + 1); } while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  return d.toISOString().slice(0, 10);
}
function prevWeekday(iso) {
  const d = new Date(iso + 'T00:00:00Z');
  do { d.setUTCDate(d.getUTCDate() - 1); } while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  return d.toISOString().slice(0, 10);
}

/* 注文が有効な日。asof の引けで出た注文は「次の営業日」だけ有効（祝日は分からないので平日で数える）。
   有効な日の大引（15:30）を過ぎたら期限切れ。次の注文は、その日の日足がそろった大引の更新（夕方〜夜）か、
   翌朝の寄り前の更新（7時台）で出る（大引の時点で CNBC の日足に当日の分が無い日がある） */
function orderDay(asof) {
  if (!asof) return null;
  const valid = nextWeekday(asof), today = isoToday();
  const label = `${md(valid)}（${wd(valid)}）`;
  const n = jstNow();
  const closed = today === valid && n.getHours() * 60 + n.getMinutes() >= 15 * 60 + 30;
  if (today > valid || closed) {
    return { valid, label, state: 'expired', text: `${label}の注文は期限切れ。次の注文は、その日の日足がそろった大引の更新（夕方〜夜）か、翌朝の寄り前の更新（7時台）で出ます` };
  }
  if (today === valid) return { valid, label, state: 'today', text: `今日 ${label} だけ有効` };
  return { valid, label, state: 'next', text: `次の営業日 ${label} だけ有効（祝日なら翌営業日）` };
}

/* ---- 株数: 1件の注文の金額 = 資金 × slot%（100株単位で切り下げ）。金額をそろえる（DESIGN.md 16章）。
   損切り幅から株数を決める（1回の損 = 資金の1%）と値動きの小さい銘柄に資金が偏り、4年の検証では同じ張り具合で
   口座の伸びが遅かった。損切りまで行ったときの損は、金額と資金の%で並べて出す ---- */
function riskSettings() {
  return { capital: lsNum(LS_RISK.capital, null), slot: lsNum(LS_RISK.slot, rules().slot_pct) };
}
function sharesFor(entry, stop) {
  const st = riskSettings();
  if (!st.capital || !isNum(entry) || !(entry > 0)) return null;
  const budget = st.capital * st.slot / 100;
  const shares = Math.floor(budget / entry / 100) * 100;
  const per = isNum(stop) && entry > stop ? entry - stop : null;
  return { shares, cost: shares * entry, loss: per ? shares * per : null, budget, per, st, lot: entry * 100 };
}
function sizeText(entry, stop) {
  const z = sharesFor(entry, stop);
  if (!z) return null;
  if (z.shares <= 0) {
    return { none: true, text: `1件の金額 ${fmtYen(z.budget)}（資金の${z.st.slot}%）では100株も買えない` +
      `（100株で ${fmtYen(z.lot)}・資金の${Math.round(z.lot / z.st.capital * 100)}%）` };
  }
  const lossPct = z.loss ? z.loss / z.st.capital * 100 : null;
  return { text: `${z.shares.toLocaleString('ja-JP')}株（約${fmtYen(z.cost)}・資金の${Math.round(z.cost / z.st.capital * 100)}%）` +
    (z.loss ? `　損切りで −${fmtYen(z.loss)}（資金の${lossPct.toFixed(1)}%）` : ''), shares: z.shares, loss: z.loss, lossPct };
}
/* 保有中（この端末の記録）に使っている金額と、新しい注文に使える空き資金 */
function freeCash() {
  const st = riskSettings();
  if (!st.capital) return null;
  const used = readPos().reduce((a, p) => a + (isNum(p.entry) && p.shares ? p.entry * p.shares : 0), 0);
  const free = Math.max(0, st.capital - used);
  const budget = st.capital * st.slot / 100;
  return { used, free, budget, fit: Math.floor(free / budget + 1e-9), st };
}

function riskSettingsBox(onChange) {
  const st = riskSettings();
  const field = (label, key, value, ph, unit) => {
    const input = h('input', { type: 'number', inputmode: 'decimal', min: '0', step: 'any', placeholder: ph, value: value ?? '' });
    input.addEventListener('change', () => {
      try {
        const v = parseFloat(input.value);
        if (isFinite(v) && v > 0) localStorage.setItem(key, String(v)); else localStorage.removeItem(key);
      } catch (e) { /* 保存できない環境は無視 */ }
      onChange();
    });
    return h('label', { class: 'risk__field' }, [h('span', { text: label }), input, h('small', { text: unit })]);
  };
  const r = rules();
  return h('details', { class: 'risk' + (st.capital ? '' : ' risk--empty'), open: st.capital ? null : 'open' }, [
    h('summary', { text: st.capital ? `株数の計算: 資金 ${fmtYen(st.capital)}・1件 資金の${st.slot}%（${fmtYen(st.capital * st.slot / 100)}）・1日${r.max_orders}件まで`
      : '株数を出すには資金を入れてください' }),
    h('div', { class: 'risk__grid' }, [
      field('運用資金', LS_RISK.capital, st.capital, '例: 3000000', '円'),
      field('1件の金額', LS_RISK.slot, st.slot, String(r.slot_pct), '% of 資金'),
    ]),
    h('p', { class: 'hint', text: `1件の注文は資金の${r.slot_pct}%ずつ（金額をそろえる）。1日${r.max_orders}件までなので、1日に新しく入るのは資金の半分まで。` +
      '保有中に使っていない資金の範囲で、上から順に置きます。4年の検証では、損切り幅から株数を決める（1回の損＝資金の1%）より、' +
      '同じ張り具合で口座が速く増えました（値動きの大きい銘柄ほど1件あたりの期待値が高いのに、損切り幅で決めると値動きの小さい銘柄に資金が偏るため。DESIGN.md 16章）。' +
      '1件を大きくすると速く増えるぶん、目減りも深くなります（このルールについて）。数字はこの端末にだけ保存されます。' }),
  ]);
}

/* 呼値の単位（swing.py の TICKS と同じ）。売りの指値は切り上げて置く */
const TICKS = [[3000, 1], [5000, 5], [30000, 10], [50000, 50], [300000, 100], [500000, 500], [3000000, 1000]];
function tickUp(p) {
  if (!isNum(p)) return null;
  const t = (TICKS.find(([lim]) => p <= lim) || [0, 5000])[1];
  return Math.ceil(p / t - 1e-9) * t;
}
/* 保有中の銘柄の、次の営業日の売り指値 = max(直近4日の終値の平均, 買値 +0.2%)。
   直近4日の平均は約定日の引けのあとに決まる（それまでは null）。floor: 下限のほうを置いているか */
function posSell(p, sw) {
  const r = rules();
  const ready = sw && sw.asof && sw.asof >= p.date && isNum(sw.sell);
  if (!ready) return { ready: false, v: null, floor: false };
  const ma = tickUp(sw.sell);
  const fl = tickUp(p.entry * (1 + r.floor / 100));
  return { ready: true, v: Math.max(ma, fl), floor: fl > ma, ma, fl };
}

/* 注文の価格（呼値の刻み）。整数ならそのまま、そうでなければ小数1桁 */
function fmtTick(v) {
  if (!isNum(v)) return '—';
  return Number.isInteger(v) ? fmtNum(v, 0) : fmtPrice(v);
}
const ym = (iso) => (iso ? `${iso.slice(0, 4)}/${Number(iso.slice(5, 7))}` : '—');

function cell(label, value, sub, tone) {
  return h('div', { class: 'plan__cell' }, [
    h('div', { class: 'plan__k', text: label }),
    h('div', { class: 'plan__v num ' + (tone || ''), text: value }),
    sub ? h('div', { class: 'plan__s', text: sub }) : null,
  ]);
}

/* ---- 1. 注文 ---- */
function readPos() { return lsJson(LS_POS); }
function writePos(v) { lsSave(LS_POS, v); }
function readDone() { return lsJson(LS_DONE); }
function writeDone(v) { lsSave(LS_DONE, v.slice(-200)); }
/* 端末の記録（買えた・売った・保有・ウォッチ）を変えたあと。どの画面にも出うるので全部を描き直し印にして、開いている画面とシートを描く */
function refreshPlan() {
  VIEWS.forEach((v) => dirty.add(v));
  const y = window.scrollY;
  render();
  window.scrollTo(0, y);
  if ($('stockSheet').open) drawSheetBody(false);
}

function boughtForm(o, day, mid) {
  const z = sizeText(o.limit, o.stop);
  const price = h('input', { type: 'number', inputmode: 'decimal', step: 'any', value: String(o.limit) });
  const qty = h('input', { type: 'number', inputmode: 'numeric', step: '100', min: '0', value: z && z.shares ? String(z.shares) : '' });
  const date = h('input', { type: 'date', value: day && day.state !== 'expired' ? day.valid : isoToday() });
  const save = h('button', { class: 'btn btn--primary', type: 'button', text: '保有中に入れる', onclick: () => {
    const e = parseFloat(price.value);
    if (!isFinite(e) || e <= 0) { price.focus(); return; }
    const r = rules();
    const pos = readPos().filter((p) => p.code !== o.code);
    const rec = { code: o.code, name: o.name, entry: e, shares: parseInt(qty.value, 10) || null, date: date.value || isoToday(),
      atr: o.atr, stop: Math.floor((e - r.stop_atr * o.atr) * 10) / 10, asof: o.asof };
    // 中期の押し目: 損切りは min(調整の安値, 約定値) − 1ATR、売りの指値は置かず60営業日目の引けで売る
    if (mid) Object.assign(rec, { kind: 'mid', low: o.low, hi: o.hi, stop: tickDown(Math.min(o.low, e) - r.mid_stop_atr * o.atr) });
    pos.push(rec);
    writePos(pos);
    refreshPlan();
  } });
  return h('div', { class: 'order__form' }, [
    h('label', {}, [h('span', { text: '約定値' }), price]),
    h('label', {}, [h('span', { text: '株数' }), qty]),
    h('label', {}, [h('span', { text: '約定日' }), date]),
    save,
    h('p', { class: 'hint', text: mid
      ? '寄りで指値より安く買えたときは、その値段を入れてください（約定値が調整の安値より下なら、損切りは約定値 − 1ATR で置き直します）。'
      : '寄りで指値より安く買えたときは、その値段を入れてください（損切りは約定値 − 3ATR で置き直します）。' }),
  ]);
}

function orderCard(o, rank, day, extra, inSheet) {
  const held = readPos().some((p) => p.code === o.code);
  const z = sizeText(o.limit, o.stop);
  const formBox = h('div', {});
  const acts = h('div', { class: 'order__acts' }, [
    held ? h('span', { class: 'badge badge--ok', text: '保有銘柄に記録済み' })
      : h('button', { class: 'btn', type: 'button', text: '買えた', onclick: (ev) => {
        ev.currentTarget.disabled = true;
        formBox.appendChild(boughtForm(o, day));
      } }),
    inSheet ? null : h('button', { class: 'btn btn--ghost', type: 'button', text: 'チャートと根拠', onclick: () => openStock(o.code) }),
  ]);
  const meta = [h('span', { text: o.code }), o.sector ? h('span', { text: o.sector }) : null,
    h('span', { text: `終値 ${fmtPrice(o.close)}` }),
    isNum(o.rsi2) ? h('span', { text: `2日RSI ${Math.round(o.rsi2)}` }) : null,
    isNum(o.r5) ? h('span', { text: `5日 ${fmtPct(o.r5, 1)}` }) : null,
    isNum(o.dev25) ? h('span', { text: `25日線 ${fmtPct(o.dev25, 1)}` }) : null];
  const pc = o.peer || null;
  const title = [h('div', { class: 'row__name', text: cleanName(o.name) || o.code }), h('div', { class: 'row__meta' }, meta)];
  return h('div', { class: 'order' + (extra ? ' order--more' : '') }, [
    // 銘柄シートの中では社名と数字はシートの上に出ているので、見出しは付けない
    inSheet ? null : h('div', { class: 'order__head' }, [
      rank ? h('span', { class: 'order__rank num', text: String(rank) }) : null,
      h('button', { class: 'order__title order__title--btn', type: 'button', onclick: () => openStock(o.code) }, title),
    ]),
    pc ? h('div', { class: 'order__peer' }, [peerBadge(pc.cls),
      h('span', { class: 'order__peer-t', text: peerLine(pc.g, pc.g20, pc.rel20) })]) : null,
    pc && strengthOf(pc.g) ? h('div', { class: 'order__peer' }, [quadBadge(strengthOf(pc.g).quad),
      h('span', { class: 'order__peer-t', text: strengthLine(pc.g) })]) : null,
    h('div', { class: 'plan__grid' }, [
      cell('買いの指値', fmtTick(o.limit), `終値${fmtPct(o.to_limit, 1)}・この日だけ`),
      cell('損切り', fmtTick(o.stop), `${fmtPct(o.stop_pct, 1)}・逆指値`, 'down'),
      cell('売りの目安', fmtTick(o.sell), `${fmtPct(o.sell_pct, 1)}・毎朝更新`, 'up'),
      cell('株数', z && z.shares ? `${z.shares.toLocaleString('ja-JP')}株` : '—', z ? (z.none ? '1件の金額では買えない'
        : isNum(z.loss) ? `損切りで −${fmtYen(z.loss)}（資金の${z.lossPct.toFixed(1)}%）` : '') : '資金を入れると出ます'),
    ]),
    z && z.none ? h('div', { class: 'plan__size plan__size--none', text: z.text }) : null,
    extra ? h('div', { class: 'hint', style: 'margin:4px 0 0', text: extra }) : acts,
    formBox,
  ]);
}

/* ---- 1b. 中期の押し目（大きなトレンドの中の1〜3か月の調整で押した日。dashboard/swing.py の MID_*、DESIGN.md 21章） ---- */
function midHeld() { return readPos().filter(isMidPos); }

function midOrderCard(o, rank, day, extra, inSheet) {
  const r = rules();
  const held = readPos().some((p) => p.code === o.code);
  const z = sizeText(o.limit, o.stop);
  const formBox = h('div', {});
  const acts = h('div', { class: 'order__acts' }, [
    held ? h('span', { class: 'badge badge--ok', text: '保有銘柄に記録済み' })
      : h('button', { class: 'btn', type: 'button', text: '買えた', onclick: (ev) => {
        ev.currentTarget.disabled = true;
        formBox.appendChild(boughtForm(o, day, true));
      } }),
    inSheet ? null : h('button', { class: 'btn btn--ghost', type: 'button', text: 'チャートと根拠', onclick: () => openStock(o.code) }),
  ]);
  const meta = [h('span', { text: o.code }), o.sector ? h('span', { text: o.sector }) : null,
    h('span', { text: `終値 ${fmtPrice(o.close)}` }),
    isNum(o.rank) ? h('span', { text: `1年の強さ 上位${Math.max(1, 100 - o.rank)}%` }) : null,
    isNum(o.age) ? h('span', { text: `高値から${o.age}営業日・${fmtPct(o.dd, 1)}` }) : null,
    isNum(o.rsi2) ? h('span', { text: `2日RSI ${Math.round(o.rsi2)}` }) : null];
  const title = [h('div', { class: 'row__name', text: cleanName(o.name) || o.code }), h('div', { class: 'row__meta' }, meta)];
  return h('div', { class: 'order' + (extra ? ' order--more' : '') }, [
    inSheet ? null : h('div', { class: 'order__head' }, [
      rank ? h('span', { class: 'order__rank num', text: String(rank) }) : null,
      h('button', { class: 'order__title order__title--btn', type: 'button', onclick: () => openStock(o.code) }, title),
    ]),
    h('div', { class: 'plan__grid' }, [
      cell('買いの指値', fmtTick(o.limit), `終値${fmtPct(o.to_limit, 1)}・この日だけ`),
      cell('損切り', fmtTick(o.stop), `${fmtPct(o.stop_pct, 1)}・調整の安値−${r.mid_stop_atr}ATR`, 'down'),
      cell('売り', `${o.hold || r.mid_hold}日目`, `営業日の引けで。高値 ${fmtTick(o.hi)}（${fmtPct(o.to_hi, 1)}）`, 'up'),
      cell('株数', z && z.shares ? `${z.shares.toLocaleString('ja-JP')}株` : '—', z ? (z.none ? '1件の金額では買えない'
        : isNum(z.loss) ? `損切りで −${fmtYen(z.loss)}（資金の${z.lossPct.toFixed(1)}%）` : '') : '資金を入れると出ます'),
    ]),
    z && z.none ? h('div', { class: 'plan__size plan__size--none', text: z.text }) : null,
    extra ? h('div', { class: 'hint', style: 'margin:4px 0 0', text: extra }) : acts,
    formBox,
  ]);
}

function midCard() {
  const mid = MID();
  if (!mid) return null;
  const r = rules();
  const sw = SW() || {};
  const day = orderDay(sw.asof);
  const orders = mid.orders || [];
  const held = midHeld();
  const free = Math.max(0, r.mid_slots - held.length);
  const body = [];
  body.push(h('p', { class: 'hint', style: 'margin:0 14px 8px', text:
    `1年で強く上げた銘柄（12か月の強さが上位1/3・200日線が上向き）が、60日高値から${r.mid_age[0]}〜${r.mid_age[1]}営業日・${r.mid_depth}% 以上の調整をしている中で、` +
    `短く押した日（2日RSI ${r.rsi_max}未満）に指値を置く。${r.mid_hold}営業日持ち、調整の安値を割ったら手仕舞う（売りの指値は置かない）。` +
    '短期の押し目買いとは別の計画で、同じ銘柄に両方は出しません。' }));
  if (orders.length) {
    body.push(h('div', { class: 'orders__when' + (day && day.state === 'expired' ? ' is-expired' : !free ? ' is-short' : '') }, [
      h('div', { class: 'orders__k', text: `${md(sw.asof)}（${wd(sw.asof)}）の引けで出た中期の注文・中期の保有 ${held.length}／${r.mid_slots}銘柄` +
        (free ? `（あと${free}件置ける。上から順に）` : '') }),
      h('div', { class: 'orders__v', text: !free ? `中期の枠（${r.mid_slots}銘柄）が埋まっているので、新しい中期の注文は置かない（検証の口座も同じ置き方です）` : day ? day.text : '' }),
    ]));
    body.push(h('div', { class: 'orders' }, orders.map((o, i) => midOrderCard(o, i + 1, day,
      i >= free ? `中期の枠（${r.mid_slots}銘柄）を超える分。保有が減れば同じ条件で使えます` : null))));
  } else {
    body.push(h('div', { class: 'callout callout--accent', style: 'margin:0 14px 8px', text:
      `今日は中期の注文なし。調整中の銘柄が短く押した日に出ます（下の「調整中の銘柄」）。中期の保有 ${held.length}／${r.mid_slots}銘柄。` }));
  }
  const near = (mid.near || []).filter((x) => isNum(x.trig));
  if (near.length) {
    body.push(h('details', { class: 'acc acc--inline', open: orders.length ? null : 'open' }, [
      h('summary', {}, [h('span', { class: 'acc__title', text: `調整中の銘柄 ${mid.n_shape || near.length}（押し待ち。近い順）` })]),
      foldable((n) => h('div', {}, near.slice(0, n).map((x) => h('button', { class: 'signal signal--btn', type: 'button', onclick: () => openStock(x.code) }, [
        h('div', { class: 'signal__name', text: cleanName(x.name) || x.code }),
        h('div', { class: 'signal__right num' }, [
          h('b', { class: 'down', text: fmtPct(x.to, 1) }),
          h('small', { text: `${x.code}　${fmtPrice(x.price)}円` }),
        ]),
        h('div', { class: 'signal__tags' }, [
          h('span', { class: 'badge badge--accent', text: `高値から${x.age}日・${fmtPct(x.dd, 1)}` }),
          isNum(x.rank) ? h('span', { class: 'badge', text: `1年の強さ 上位${Math.max(1, 100 - x.rank)}%` }) : null,
        ]),
        h('div', { class: 'signal__why', text: `終値が ${fmtPrice(x.trig)}円 未満で引けたら、翌営業日に中期の注文が出る` +
          (x.ok === false ? '（ただしそこまで下げると200日線の条件も割れる）' : '') + `。調整の安値 ${fmtPrice(x.low)}円` }),
      ]))), near.length, 6, '全銘柄'),
    ]));
  }
  body.push(midEvidenceLine());
  return card('中期の押し目', orders.length ? `${orders.length}銘柄` : '大きなトレンドの中の調整', body, null, true, 'sw-mid');
}

/* 中期の注文のそばに置く検証の一行（比べる相手と偏りつき） */
function midEvidenceLine() {
  const v = ((SW() || {}).verify || {});
  const m = v.mid || {};
  const a = m.all || {}, b = (m.base || {}).all || {};
  const acc = v.account || {}, prev = v.account_prev || {};
  const text = a.n
    ? `検証（${a.n}回）: 1回の平均 ${fmtPct(a.avg, 2)}・勝率 ${a.win}%（調整の条件なしで強い銘柄の押しを同じ出口で買うと ${fmtPct(b.avg, 2)}・${b.win ?? '—'}%）。` +
      (isNum(acc.cagr) && isNum(prev.cagr) ? `短期と合わせた口座は年率 ${fmtPct(acc.cagr, 1)}・最大の目減り ${fmtPct(acc.dd, 1)}（短期だけなら ${fmtPct(prev.cagr, 1)}・${fmtPct(prev.dd, 1)}）。` : '') +
      '勝率は低く、少数の大きく伸びた銘柄が平均を作る。今の採用銘柄だけで測っている（生存者の偏り）。'
    : '検証に足りる日足がまだありません（12か月の強さに250営業日が要ります）。予測ではなく、決めた規則どおりに注文を置くための目安です。';
  return h('button', { class: 'evidence', type: 'button', onclick: () => selectView('verify', 'v-mid') }, [
    h('span', { text }), h('b', { text: '検証を見る ›' })]);
}

function ordersCard() {
  const sw = SW();
  if (!sw) {
    return card('注文', null, h('div', { class: 'empty', text: '四本値の日足がまだありません。次の大引の更新で注文が出ます。' }), null, false, 'sw-orders');
  }
  const r = rules();
  const day = orderDay(sw.asof);
  const orders = sw.orders || [];
  const body = [];
  // いつ有効か・資金で何件置けるかを1つの枠に（注文の前に読む量を減らす）
  const fc = orders.length && !(day && day.state === 'expired') ? freeCash() : null;
  const fit = fc ? Math.min(fc.fit, orders.length) : null;
  body.push(h('div', { class: 'orders__when' + (day && day.state === 'expired' ? ' is-expired' : fc && fit < orders.length ? ' is-short' : '') }, [
    h('div', { class: 'orders__k', text: `${md(sw.asof)}（${wd(sw.asof)}）の引けで出た買い注文` +
      (fc ? `・空き資金 ${fmtYen(fc.free)}（1件 ${fmtYen(fc.budget)}）で上から${fit}件` : '') }),
    h('div', { class: 'orders__v', text: day ? day.text : '' }),
    fc && fit < orders.length ? h('div', { class: 'orders__k', text: `保有に ${fmtYen(fc.used)}。置けない分は見送り、空いた資金は次の注文に回します（検証の口座も同じ置き方です）。` }) : null,
  ]));
  body.push(riskSettingsBox(refreshPlan));
  if (!orders.length) {
    body.push(h('div', { class: 'callout callout--accent', text: ((MID() || {}).orders || []).length
      ? '短期の注文なし。今日押した銘柄は、大きなトレンドの中の調整なので下の「中期の押し目」に出しています（同じ銘柄に両方は出しません）。'
      : '注文なし。上昇トレンドの銘柄で、短く押したもの（2日RSI 10未満）がありません。待つのも作戦です。下の「監視」に、もうすぐ注文対象になる銘柄を出しています。' }));
  } else {
    body.push(h('div', { class: 'orders' }, orders.map((o, i) => orderCard(o, i + 1, day))));
  }
  const more = sw.more || [];
  if (more.length) {
    body.push(h('details', { class: 'acc acc--inline' }, [
      h('summary', {}, [h('span', { class: 'acc__title', text: `次点 ${more.length}銘柄（1日${r.max_orders}銘柄・同じ業種${r.sector_cap}銘柄までの上限で外れたもの）` })]),
      h('div', { class: 'orders' }, more.map((o) => orderCard(o, null, day, '上限で外れた次点。枠が空いていれば同じ条件で使えます'))),
    ]));
  }
  const skip = sw.skip || [];
  if (skip.length) {
    body.push(h('details', { class: 'acc acc--inline' }, [
      h('summary', {}, [h('span', { class: 'acc__title', text: `見送り ${skip.length}銘柄（業種の上げに沿った押し）` })]),
      h('div', {}, skip.map((o) => h('button', { class: 'signal signal--btn', type: 'button', onclick: () => openStock(o.code) }, [
        h('div', { class: 'signal__name', text: cleanName(o.name) || o.code }),
        h('div', { class: 'signal__right num' }, [h('small', { text: `${o.code}　${fmtPrice(o.close)}円` })]),
        h('div', { class: 'signal__why', text: o.peer ? peerLine(o.peer.g, o.peer.g20, o.peer.rel20) : '' }),
      ]))),
      h('p', { class: 'hint', style: 'margin:6px 14px 10px', text:
        `業種が${r.peer_n}日で +${r.peer_up}% 以上上げている中で、自分も業種並み（業種との差が ${r.peer_lag}pt より上）の押し。` +
        `4年の検証で勝率 ${PEER_INFO.hot.stat}・1回の平均 ${HOT_AVG}（前半／後半）と、ほかの押しより明らかに弱い。業種の上げが一服し始めた押しになりやすい。同じ押しでも、業種より遅れている銘柄（出遅れの押し）は勝率 ${PEER_INFO.lag.stat} でした。` }),
    ]));
  }
  body.push(h('details', { class: 'acc acc--inline' }, [
    h('summary', {}, [h('span', { class: 'acc__title', text: '置き方（IFD・OCO）と並べ方' })]),
    h('div', { class: 'acc__body acc__body--plain', text:
      `買いは指値（終値 − ${r.entry_atr}ATR、この日だけ）を IFD（約定したら損切りの逆指値が自動で入る注文）で置くと、検証と同じく約定した日から損切り（約定値 − ${r.stop_atr}ATR）が効きます。` +
      `翌日からは毎朝、「売りの指値」（直近${r.exit_n}日の終値の平均。ただし約定値 +${r.floor}% より下には置かない）と損切りを OCO（片方が約定したらもう片方は取り消し）で置き直す。` +
      `${r.max_hold}営業日で売れなければ引けで売る。\n並べ方は、25日線からの下離れ＋業種より遅れている分が大きい順（業種ぐるみの押し・出遅れの押しが先に来る）。1日${r.max_orders}銘柄・同じ業種${r.sector_cap}銘柄まで。` }),
  ]));
  body.push(evidenceLine());
  return card('短期の押し目買い', orders.length ? `${orders.length}銘柄` : null, body, null, true, 'sw-orders');
}

/* 注文のそばに置く検証の一行。勝率は比べる相手と、偏り（生存者・急落）と一緒に出す（CLAUDE.md の約束） */
function evidenceLine() {
  const sw = SW() || {};
  const v = sw.verify || {};
  const a = v.all || {}, base = v.base || {}, acc = v.account || {};
  const text = a.n
    ? `検証（直近${v.days || ''}営業日・${a.n}回）: 勝率 ${a.win}%（同じ銘柄を毎日買って5日後に売ると ${base.win ?? '—'}%）・1回の平均 ${fmtPct(a.avg, 2)}` +
      (isNum(acc.cagr) ? `、中期の押し目と合わせて本番どおりに置いた口座は年率 ${fmtPct(acc.cagr, 1)}・最大の目減り ${fmtPct(acc.dd, 1)}` : '') +
      '。今の採用銘柄だけで測っている（生存者の偏り）・急落に弱い。予測ではなく、決めた規則どおりに注文を置くための目安です。'
    : '検証に足りる日足がまだありません。予測ではなく、決めた規則どおりに注文を置くための目安です。';
  return h('button', { class: 'evidence', type: 'button', onclick: () => selectView('verify', 'v-swing') }, [
    h('span', { text }), h('b', { text: '検証を見る ›' })]);
}

/* ---- 2. 保有中（この端末に記録した銘柄） ---- */
function heldDays(p, sw) {
  const cal = (SW() || {}).cal || [];
  const asof = (sw && sw.asof) || (SW() || {}).asof;
  return cal.filter((d) => d > p.date && (!asof || d <= asof)).length;
}

function positionRow(p, inSheet) {
  const r = rules();
  const st = ((THERMO || {}).stocks || {})[p.code] || {};
  const sw = st.sw || {};
  const now = st.price;
  const pl = isNum(now) ? (now / p.entry - 1) * 100 : null;
  const held = heldDays(p, sw);
  const mid = isMidPos(p);                              // 中期の押し目: 売りの指値は置かず、損切りか60営業日目の引け
  const ps = mid ? { ready: false, v: null, floor: false } : posSell(p, sw);   // 約定日の引けまでの日足が入っていれば売り指値が出せる
  const hold = mid ? r.mid_hold : r.max_hold;
  const due = held + 1 >= hold;
  const lines = [];
  if (isNum(now) && now <= p.stop) lines.push(h('div', { class: 'guard__note guard__note--warn', text: mid
    ? '損切りの価格（調整の安値 − 1ATR）を割っています。調整明けの見立てが外れたので、逆指値が約定していなければ計画どおりに手仕舞う'
    : '損切りの価格を割っています。逆指値が約定していなければ、計画どおりに手仕舞う' }));
  if (due) lines.push(h('div', { class: 'guard__note guard__note--info', text: mid
    ? `期限（${hold}営業日）。次の営業日の引けで売る`
    : `期限（${r.max_hold}営業日）。次の営業日に売り指値に届かなければ引けで売る` }));
  if (mid && !due) lines.push(h('div', { class: 'guard__note guard__note--info', text:
    '売りの指値は置かない（伸びる銘柄を早く売らない）。損切りの逆指値だけを置いておき、上げても下げても決めた日まで持つ' }));
  const crowdTxt = crowdNote(st);
  if (crowdTxt) lines.push(h('div', { class: 'guard__note guard__note--warn', text: crowdTxt }));
  const doneBox = h('div', {});
  const exitBtn = h('button', { class: 'btn', type: 'button', text: '売った', onclick: (ev) => {
    ev.currentTarget.disabled = true;
    const price = h('input', { type: 'number', inputmode: 'decimal', step: 'any', value: String(ps.ready ? ps.v : (now || p.entry)) });
    const date = h('input', { type: 'date', value: isoToday() });
    doneBox.appendChild(h('div', { class: 'order__form' }, [
      h('label', {}, [h('span', { text: '売値' }), price]),
      h('label', {}, [h('span', { text: '売った日' }), date]),
      h('button', { class: 'btn btn--primary', type: 'button', text: '記録して保有中から外す', onclick: () => {
        const x = parseFloat(price.value);
        if (!isFinite(x) || x <= 0) { price.focus(); return; }
        const done = readDone();
        done.push({ ...p, exit: x, out: date.value || isoToday(), ret: Math.round(((x / p.entry - 1) * 100 - r.cost) * 100) / 100 });
        writeDone(done);
        writePos(readPos().filter((q) => q.code !== p.code));
        refreshPlan();
      } }),
    ]));
  } });
  const drop = h('button', { class: 'btn btn--ghost', type: 'button', text: '取り消し', onclick: () => {
    if (confirm(`${cleanName(p.name)} を保有銘柄から外しますか？（記録は残しません）`)) { writePos(readPos().filter((q) => q.code !== p.code)); refreshPlan(); }
  } });
  const title = [
    h('div', { class: 'row__name' }, [document.createTextNode(cleanName(p.name) || p.code), h('span', { class: 'kind kind--rule', text: mid ? '中期の押し目' : '押し目買い' })]),
    h('div', { class: 'row__meta' }, [h('span', { text: p.code }), h('span', { text: `${md(p.date)} ${fmtPrice(p.entry)}円で買い` }),
      p.shares ? h('span', { text: `${p.shares.toLocaleString('ja-JP')}株` }) : null,
      h('span', { text: `${held}営業日目` })]),
  ];
  return h('div', { class: 'order' }, [
    h('div', { class: 'order__head' }, [
      inSheet ? h('div', { class: 'order__title' }, title)
        : h('button', { class: 'order__title order__title--btn', type: 'button', onclick: () => openStock(p.code, 'sell') }, title),
      h('div', { class: 'order__pl num ' + cls(pl), text: isNum(pl) ? fmtPct(pl, 1) : '—' }),
    ]),
    h('div', { class: 'plan__grid plan__grid--3' }, mid ? [
      cell('損切り（逆指値）', fmtPrice(p.stop), `${fmtPct((p.stop / p.entry - 1) * 100, 1)}・調整の安値−${r.mid_stop_atr}ATR`, 'down'),
      cell('期限', due ? '次の営業日' : `あと${hold - held}日`, due ? '引けで売る' : `${hold}営業日目の引け`),
      cell('60日高値', isNum(p.hi) ? fmtTick(p.hi) : '—', isNum(p.hi) && isNum(now) ? `いまから ${fmtPct((p.hi / now - 1) * 100, 1)}` : '調整の起点', 'up'),
    ] : [
      cell('次の売り指値', ps.ready ? fmtTick(ps.v) : '—',
        ps.ready ? `${ps.floor ? `下限（買値+${r.floor}%）` : `直近${r.exit_n}日の平均`}・${fmtPct((ps.v / p.entry - 1) * 100, 1)}` : '約定日の引けのあとに出ます', 'up'),
      cell('損切り（逆指値）', fmtPrice(p.stop), fmtPct((p.stop / p.entry - 1) * 100, 1), 'down'),
      cell('期限', due ? '次の営業日' : `あと${r.max_hold - held}日`, due ? '引けで売る' : `${r.max_hold}営業日で引け`),
    ]),
    lines.length ? h('div', { style: 'margin-top:6px' }, lines) : null,
    h('div', { class: 'order__acts' }, [exitBtn, inSheet ? null : h('button', { class: 'btn btn--ghost', type: 'button', text: 'チャートと根拠', onclick: () => openStock(p.code, 'sell') }), drop]),
    doneBox,
  ]);
}

/* ---- 保有株の判定（自分の判断で持っている銘柄。dashboard/hold.py、DESIGN.md 18章） ----
   押し目買いのルールで買った銘柄は「保有中」の計画（売り指値・損切り・期限）で手仕舞う。ここはそれ以外の銘柄。
   判定は前の営業日（大引の更新のあとは今日）の引けの形だけで決まる: 20日の騰落（勝ち・中立・負け・急落）と2日RSI。
   買値は判定に使わない（撤退ラインと含み損益の表示だけ）。場中は判定を変えず、撤退ライン・売り指値に届いたかだけを見る
   （後場の騰落は前場の形からは読めなかった）。記録はこの端末にだけ保存する（公開リポジトリに保有を書かない）。 */
const LS_HOLD = 'md.hold';              // 保有株（この端末だけ）
const LS_HOLD_DONE = 'md.hold.done';    // 売った記録（この端末だけ）
function readHold() { return lsJson(LS_HOLD); }
function writeHold(v) { lsSave(LS_HOLD, v); }
function HOLDX() { return (THERMO || {}).hold || null; }
function hdOf(code) { return (((THERMO || {}).stocks || {})[code] || {}).hd || null; }
const HOLD_TONE = { ok: 'ok', accent: 'accent', warn: 'warn', '': '' };
const tickDown = (p) => {
  if (!isNum(p)) return null;
  const t = (TICKS.find(([lim]) => p <= lim) || [0, 5000])[1];
  return Math.floor(p / t + 1e-9) * t;
};

/* 今の時間帯（東証の立会）。祝日は分からないので平日で数える */
function marketPhase() {
  const n = jstNow();
  if (n.getDay() === 0 || n.getDay() === 6) return 'closed';
  const m = n.getHours() * 60 + n.getMinutes();
  if (m < 9 * 60) return 'pre';
  if (m < 11 * 60 + 30) return 'am';
  if (m < 12 * 60 + 30) return 'lunch';
  if (m < 15 * 60 + 30) return 'pm';
  return 'after';
}

/* 今日の前場の値（前場の更新のウォッチリストにある銘柄だけ。始値・高値・安値つき） */
function amQuote(code) {
  const z = ((DATA || {}).slots || {}).zenba;
  // 大引の更新が来たら前場の値は使わない（引けの値と、明日の判定に切り替わる）
  if (!z || !z.data || DATA.date !== isoToday() || (DATA.slots || {}).taibike) return null;
  const w = (z.data.watchlist || []).find((x) => x.code === code && isNum(x.price));
  if (!w) return null;
  const nk = (z.data.indices || {}).nikkei;
  return { ...w, updated: (z.updated_at || '').slice(11, 16), nk: nk && !nk.stale ? nk.change_pct : null };
}

/* 撤退ライン（自分で決めた値。無ければ目安 = 買値 − 3ATR） */
function holdStop(p, hd) {
  if (isNum(p.stop) && p.stop > 0) return { v: p.stop, own: true };
  const r = (HOLDX() || {}).rules || {};
  if (hd && isNum(hd.atr) && isNum(p.entry)) return { v: tickDown(p.entry - (r.stop_atr || 3) * hd.atr), own: false };
  return { v: null, own: false };
}

/* 判定の根拠の一行: この形が次の20日に日経とどれだけ差をつけたか（日足キャッシュに毎日当てた検証） */
function holdEvidence(hd) {
  const v = (HOLDX() || {}).verify;
  if (!v || !hd) return null;
  const key = ['trim_limit', 'trim_now', 'trim'].includes(hd.v) ? hd.v : hd.cls;
  const s = (v.cls || {})[key], all = (v.cls || {}).all;
  if (!s || !all || !s[0].n) return null;
  const pt = (x) => { if (!isNum(x)) return '—'; const r = Number(x.toFixed(1)) || 0; return (r > 0 ? '+' : '') + r.toFixed(1) + 'pt'; };
  return `この形のあと20営業日の日経との差: 前半 ${pt(s[0].avg)}／後半 ${pt(s[1].avg)}（全銘柄 ${pt(all[0].avg)}／${pt(all[1].avg)}）。` +
    `日経に勝った割合 ${s[0].win ?? '—'}%／${s[1].win ?? '—'}%。${md(v.from)}〜${md(v.to)} の日足、前半は ${md(v.split)} まで`;
}

/* 判定ごとの「次にやること」。day は売り指値が有効な日のラベル */
function holdTodo(hd, stop, day) {
  const r = (HOLDX() || {}).rules || {};
  const sell = isNum(hd.sell) ? `${fmtTick(hd.sell)}円` : '—';
  const st = isNum(stop.v) ? `撤退ライン ${fmtTick(stop.v)}円 を割ったら売る（逆指値を置いておく）` : '撤退ラインを決めておく';
  switch (hd.v) {
    case 'hold': return `売らない。${st}`;
    case 'wait': return `投げない。減らすなら、戻って売り指値 ${sell}（直近${r.exit_n || 4}日の終値の平均）に届いてから`;
    case 'trim_limit': return `減らすなら${day}の売り指値 ${sell}（直近${r.exit_n || 4}日の終値の平均）。寄りの成行では売らない。` +
      `${r.exec_n || 5}営業日で届かなければ${r.exec_n || 5}日目の引けで`;
    case 'trim_now': return `減らすなら${day}の寄り〜場中に（戻った日は、寄りで売っても売り指値 ${sell} でも差がなかった）`;
    case 'trim': return `減らすなら売り指値 ${sell}（直近${r.exit_n || 4}日の終値の平均）で。成行で投げない`;
    default: return `値動きの形からは差がない。${st}。あとは業績の前提（決算・修正）で決める`;
  }
}

function holdRow(p, inSheet) {
  const st = ((THERMO || {}).stocks || {})[p.code] || {};
  const hd = st.hd;
  const X = HOLDX() || {};
  const vd = hd ? (X.verdicts || {})[hd.v] : null;
  const am = amQuote(p.code);
  const now = am ? am.price : st.price;
  const pl = isNum(now) && isNum(p.entry) ? (now / p.entry - 1) * 100 : null;
  const plYen = isNum(now) && isNum(p.entry) && p.shares ? (now - p.entry) * p.shares : null;
  const stop = holdStop(p, hd);
  const day = hd ? orderDay(hd.asof) : null;
  const swingPos = readPos().some((q) => q.code === p.code);
  const lines = [];
  const note = (tone, text) => lines.push(h('div', { class: 'guard__note guard__note--' + tone, text }));

  // 撤退ライン・売り指値に届いたか（前場の高安・今の値）
  const lo = am && isNum(am.low) ? Math.min(am.low, am.price) : now;
  const hi = am && isNum(am.high) ? Math.max(am.high, am.price) : now;
  if (isNum(stop.v) && isNum(lo) && lo <= stop.v) {
    note('warn', stop.own
      ? `${am ? `前場（${am.updated} 更新）の安値 ${fmtPrice(lo)}円` : `${fmtPrice(now)}円`}で撤退ライン ${fmtTick(stop.v)}円 を割った。決めたとおり売る（逆指値を置いていれば約定している）`
      : `目安の撤退ライン（買値 − 3ATR = ${fmtTick(stop.v)}円）をもう割っている。撤退ラインを決めていなかった分、負けが目安より深い。` +
        'ここから投げるかどうかは上の判定（売るなら売り指値で）。次に買う銘柄は、買う前に撤退ラインを決めて「編集」で入れておく');
  }
  if (hd && hd.v && hd.v.startsWith('trim') && isNum(hd.sell) && isNum(hi) && hi >= hd.sell && day && day.state === 'today') {
    note('chance', `今日 ${fmtPrice(hi)}円 まで上げて売り指値 ${fmtTick(hd.sell)}円 に届いた（置いていれば約定している）`);
  }
  if (am) {
    const ph = marketPhase();
    note('info', `前場 ${fmtPct(am.change_pct, 1)}（日経 ${fmtPct(am.nk, 1)}・${am.updated} 更新）。` +
      (ph === 'lunch' || ph === 'pm' || ph === 'am'
        ? '後場は判定を変えない。前場の形から後場の騰落は読めなかった（検証タブの「保有株の判定の根拠」）。決めた価格（売り指値・撤退ライン）だけで動く'
        : '前場の形では判定を変えない'));
  } else if (['am', 'lunch', 'pm'].includes(marketPhase()) && ((DATA || {}).slots || {}).zenba && !((DATA.slots.zenba.data || {}).watchlist || []).some((w) => w.code === p.code)) {
    note('info', 'ウォッチリスト（リポジトリに反映したもの）に入れておくと、前場の更新で前場の安値・高値も撤退ライン・売り指値と突き合わせます');
  }
  const lagTxt = lagNote(am);
  if (lagTxt) note('warn', lagTxt);
  const crowdTxt = crowdNote(st);
  if (crowdTxt) note('warn', crowdTxt);
  const ev = st.ev;
  if (ev && ev.dir === 'down' && !String(ev.label || '').startsWith('悪材料出尽くし')) {
    note('warn', `下方修正・減配の開示（${md(ev.date)}）。判定は値動きだけで出している。業績の前提が変わったなら、持つ理由を見直す`);
  } else if (ev && ev.label) {
    note('info', `${ev.label}（${md(ev.date)}・その後 ${fmtPct(ev.since, 1)}）`);
  }

  const acts = h('div', { class: 'order__acts' });
  const formBox = h('div', {});
  acts.appendChild(h('button', { class: 'btn', type: 'button', text: '売った', onclick: (e) => {
    e.currentTarget.disabled = true;
    const price = h('input', { type: 'number', inputmode: 'decimal', step: 'any', value: isNum(now) ? String(now) : '' });
    const qty = h('input', { type: 'number', inputmode: 'numeric', step: '100', min: '0', value: p.shares ? String(p.shares) : '' });
    formBox.appendChild(h('div', { class: 'order__form' }, [
      h('label', {}, [h('span', { text: '売値' }), price]),
      h('label', {}, [h('span', { text: '売った株数' }), qty]),
      h('button', { class: 'btn btn--primary', type: 'button', text: '記録する', onclick: () => {
        const x = parseFloat(price.value);
        if (!isFinite(x) || x <= 0) { price.focus(); return; }
        const n = parseInt(qty.value, 10) || null;
        const done = lsJson(LS_HOLD_DONE);
        done.push({ ...p, exit: x, sold: n, out: isoToday(), v: hd && hd.v, ret: isNum(p.entry) ? Math.round((x / p.entry - 1) * 10000) / 100 : null });
        lsSave(LS_HOLD_DONE, done.slice(-200));
        // 判断メモにも残す（売ったあとにどうなったかを振り返る）
        const log = readLog();
        log.push({ at: isoToday(), code: p.code, name: p.name || st.n, intent: 'sell', verdict: `売った（判定: ${vd ? vd.label : '—'}）`,
          level: 'ok', price: x });
        writeLog(log);
        const rest = readHold().map((q) => (q.code !== p.code ? q : n && q.shares && n < q.shares ? { ...q, shares: q.shares - n } : null)).filter(Boolean);
        writeHold(rest);
        refreshPlan();
      } }),
      h('p', { class: 'hint', text: '一部だけ売ったときは株数を減らして残します。判断メモに「売った」として残り、その後の値動きと比べられます。' }),
    ]));
  } }));
  acts.appendChild(h('button', { class: 'btn btn--ghost', type: 'button', text: '編集', onclick: () => {
    formBox.textContent = '';
    formBox.appendChild(holdForm(p));
  } }));
  if (!inSheet) acts.appendChild(h('button', { class: 'btn btn--ghost', type: 'button', text: 'チャートと根拠', onclick: () => openStock(p.code, 'sell') }));

  const title = [
    h('div', { class: 'row__name' }, [document.createTextNode(cleanName(p.name || st.n) || p.code), h('span', { class: 'kind', text: '自分の判断' })]),
    h('div', { class: 'row__meta' }, [
      h('span', { text: p.code }),
      isNum(p.entry) ? h('span', { text: `買値 ${fmtPrice(p.entry)}円` }) : null,
      p.shares ? h('span', { text: `${p.shares.toLocaleString('ja-JP')}株` }) : null,
      isNum(now) ? h('span', { text: `いま ${fmtPrice(now)}円` }) : null,
    ]),
  ];
  return h('div', { class: 'order hold' }, [
    h('div', { class: 'order__head' }, [
      inSheet ? h('div', { class: 'order__title' }, title)
        : h('button', { class: 'order__title order__title--btn', type: 'button', onclick: () => openStock(p.code, 'sell') }, title),
      h('div', { class: 'hold__pl' }, [
        h('div', { class: 'order__pl num ' + cls(pl), text: isNum(pl) ? fmtPct(pl, 1) : '—' }),
        isNum(plYen) ? h('div', { class: 'hold__yen num ' + cls(plYen), text: (plYen > 0 ? '+' : plYen < 0 ? '−' : '') + fmtYen(Math.abs(plYen)) }) : null,
      ]),
    ]),
    swingPos
      ? h('div', { class: 'callout callout--accent', text: '押し目買いのルールで買った銘柄。売りはルールの計画（売り指値・損切り・期限）どおりに。' })
      : !hd || !vd
        ? h('div', { class: 'callout callout--' + (X.verdicts ? 'warn' : 'accent'), text: X.verdicts
          ? 'この銘柄の四本値の日足がまだありません（ウォッチリストに入れると次の更新で入ります）。'
          : '判定は次の収集（寄り前・前場・大引の更新）から出ます。いま出ているデータは、この機能を入れる前に作られたものです。' })
        : h('div', { class: 'hold__verdict' }, [
          h('div', { class: 'hold__vhead' }, [
            h('span', { class: 'badge badge--' + (HOLD_TONE[vd.tone] || ''), text: vd.label }),
            h('span', { class: 'hold__asof', text: `${md(hd.asof)}の引けの形・${(X.classes || {})[hd.cls] || ''}` }),
          ]),
          h('div', { class: 'hold__todo', text: holdTodo(hd, stop, day ? day.label : '次の営業日') }),
          h('div', { class: 'hold__why', text: `${vd.text}。20日 ${fmtPct(hd.r20, 1)}・2日RSI ${isNum(hd.rsi2) ? Math.round(hd.rsi2) : '—'}・60日高値から ${fmtPct(hd.dd60, 1)}` }),
          (() => { const t = holdEvidence(hd); return t ? h('div', { class: 'hold__ev', text: t }) : null; })(),
        ]),
    swingPos || !hd ? null : h('div', { class: 'plan__grid plan__grid--3' }, [
      cell(hd.v && hd.v.startsWith('trim') || hd.v === 'wait' ? '売り指値' : '売るなら', isNum(hd.sell) ? fmtTick(hd.sell) : '—',
        day ? `${day.label}・直近4日の平均` : '直近4日の平均', 'up'),
      cell(stop.own ? '撤退ライン' : '撤退ライン（目安）', isNum(stop.v) ? fmtTick(stop.v) : '—',
        isNum(stop.v) && isNum(now) ? `いまから ${fmtPct((stop.v / now - 1) * 100, 1)}${stop.own ? '' : '・買値−3ATR'}` : '', 'down'),
      cell('前の引け', isNum(hd.c) ? fmtPrice(hd.c) : fmtPrice(st.price), `${md(hd.asof)}・5日 ${fmtPct(hd.r5, 1)}`),
    ]),
    lines.length ? h('div', { style: 'margin-top:6px' }, lines) : null,
    acts,
    formBox,
  ]);
}

/* 保有株を足す・直すフォーム。p が無ければ新規（ウォッチから選べる）、p.fresh は銘柄シートからコードを入れた新規 */
function holdForm(p) {
  const edit = !!(p && !p.fresh);
  const code = h('input', { type: 'text', inputmode: 'numeric', maxlength: '5', autocomplete: 'off', placeholder: '7203', value: p ? p.code : '' });
  const entry = h('input', { type: 'number', inputmode: 'decimal', step: 'any', min: '0', placeholder: '買値', value: p && isNum(p.entry) ? String(p.entry) : '' });
  const qty = h('input', { type: 'number', inputmode: 'numeric', step: '100', min: '0', placeholder: '100', value: p && p.shares ? String(p.shares) : '' });
  const stop = h('input', { type: 'number', inputmode: 'decimal', step: 'any', min: '0', placeholder: '任意', value: p && isNum(p.stop) ? String(p.stop) : '' });
  const msg = h('p', { class: 'hint', text: '撤退ラインは空なら目安（買値 − 3ATR）を出します。数字はこの端末にだけ保存されます。' });
  const watch = effectiveWatchlist(sessData());
  const held = new Set(readHold().map((q) => q.code));
  const chips = p ? null : h('div', { class: 'hold__chips' }, watch.filter((w) => !held.has(w.code)).slice(0, 12).map((w) =>
    h('button', { class: 'chip', type: 'button', text: cleanName(w.name) || w.code, onclick: () => {
      code.value = w.code;
      if (!entry.value && isNum(w.price)) entry.placeholder = `いま ${fmtPrice(w.price)}`;
      entry.focus();
    } })));
  if (p && p.fresh) { const now = nowPrice(p.code); if (isNum(now)) entry.placeholder = `いま ${fmtPrice(now)}`; }
  const save = h('button', { class: 'btn btn--primary', type: 'button', text: edit ? '直す' : '保有銘柄に入れる', onclick: () => {
    const c = code.value.trim().toUpperCase();
    if (!/^[0-9][0-9A-Z]{3}$/.test(c)) { code.focus(); msg.textContent = '証券コード（4桁）を入れてください'; return; }
    const e = parseFloat(entry.value);
    if (!isFinite(e) || e <= 0) { entry.focus(); msg.textContent = '買値を入れてください（平均取得単価）'; return; }
    const s = parseFloat(stop.value);
    const st = stockOf(c) || {};
    const w = watch.find((x) => x.code === c);
    const rec = { code: c, name: (edit && p.name) || st.n || (w && w.name) || (p && p.name) || c, entry: e, shares: parseInt(qty.value, 10) || null,
      stop: isFinite(s) && s > 0 ? s : null, date: (edit && p.date) || isoToday() };
    writeHold(readHold().filter((q) => q.code !== c && (!edit || q.code !== p.code)).concat([rec]));
    refreshPlan();
  } });
  return h('div', {}, [
    chips && chips.childNodes.length ? h('div', { class: 'hint', style: 'margin:0 0 4px', text: 'ウォッチリストから選ぶ' }) : null,
    chips && chips.childNodes.length ? chips : null,
    h('div', { class: 'order__form' }, [
      h('label', {}, [h('span', { text: '証券コード' }), code]),
      h('label', {}, [h('span', { text: '買値（平均）' }), entry]),
      h('label', {}, [h('span', { text: '株数' }), qty]),
      h('label', {}, [h('span', { text: '撤退ライン' }), stop]),
      save,
      edit ? h('button', { class: 'btn btn--ghost', type: 'button', text: '保有銘柄から外す（記録しない）', onclick: () => {
        if (confirm(`${cleanName(p.name)} を保有銘柄から外しますか？`)) { writeHold(readHold().filter((q) => q.code !== p.code)); refreshPlan(); }
      } }) : null,
      msg,
    ]),
  ]);
}

/* 判定の根拠（検証の表）。畳んで出す */
function holdProofCard() {
  const X = HOLDX();
  if (!X) return card('保有株の判定の根拠', null, h('div', { class: 'empty', text: '判定のデータは次の収集から出ます' }), null, false, 'v-hold');
  const v = X.verify;
  const pt = (x) => { if (!isNum(x)) return '—'; const r = Number(x.toFixed(1)) || 0; return (r > 0 ? '+' : '') + r.toFixed(1); };
  const pc = (x) => fmtPct(x, 2);
  const kids = [];
  if (v) {
    const rows = [['win', '勝ち→持つ'], ['crash', '急落→売らない'], ['lose', '負け→減らす'], ['flat', '中立'], ['all', '全銘柄']];
    const nAll = v.cls.all ? v.cls.all[0].n + v.cls.all[1].n : 0;
    kids.push(h('div', { class: 'check__lh', text: `形ごとの、次の20営業日の日経との差（pt）と日経に勝った割合。勝ち＝20日 +${X.rules.win}%以上、` +
      `負け＝${X.rules.lose}〜${X.rules.crash}%、急落＝${X.rules.crash}%超。${md(v.from)}〜${md(v.to)}（${nAll.toLocaleString('ja-JP')}件）、前半は ${md(v.split)} まで` }));
    kids.push(h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['形', '前半', '後半', '勝った割合'].map((t, i) => h('th', { text: t, style: i ? null : 'text-align:left' })))),
      h('tbody', {}, rows.filter(([k]) => (v.cls || {})[k]).map(([k, label]) => {
        const s = v.cls[k];
        return h('tr', {}, [h('th', { text: label }), h('td', { class: 'num ' + cls(s[0].avg), text: pt(s[0].avg) }),
          h('td', { class: 'num ' + cls(s[1].avg), text: pt(s[1].avg) }), h('td', { class: 'num', text: `${s[0].win ?? '—'}／${s[1].win ?? '—'}%` })]);
      })),
    ])));
    const R = X.research;
    if (R) {
      const q = (k) => `${pt(R.cls[k][0])}／${pt(R.cls[k][1])}pt`;
      kids.push(h('p', { class: 'hint', text: `判定の線を決めた4年の研究（${R.stocks}銘柄・${R.period[0]}で決め、${R.period[1]}で確かめた）では: ` +
        `勝ち ${q('win')}、負け ${q('lose')}、急落 ${q('crash')}、中立 ${q('flat')}、全銘柄 ${q('all')}。上の表はそれを毎日、手元の約2年の日足で数え直したもの。` }));
    }
    const ex = (v.exec || {}).dip;
    if (ex && ex[0].n) {
      kids.push(h('p', { class: 'hint', text: `売り方: 押した日（2日RSI < ${X.rules.dip}）の翌日から売り指値（直近${X.rules.exit_n}日の平均・${X.rules.exec_n}日目の引けまで）で売ると、` +
        `翌日の寄りで成行で売るより 前半 ${pc(ex[0].avg)}／後半 ${pc(ex[1].avg)}（高く売れた割合 ${ex[0].win}%／${ex[1].win}%）。` +
        '戻った日（2日RSI > 70）はどちらでも同じ。' }));
    }
    const g = v.gap || {};
    if (g.dn && g.dn[0].n) {
      kids.push(h('p', { class: 'hint', text: `寄りの窓（参考・判定には使わない）: 前日比 −${X.rules.gap}% 以下で寄った日の寄り→大引は 前半 ${pc(g.dn[0].avg)}／後半 ${pc(g.dn[1].avg)}、` +
        `+${X.rules.gap}% 以上は ${pc(g.up[0].avg)}／${pc(g.up[1].avg)}。4年で見ると年によって向きが逆になる（窓を開けて下げた日: 2024年 −1.7%、2025年 +0.7%）ので、寄りの窓で売り方を変える規則は置いていない。` }));
    }
  }
  const E = X.exits;
  if (E) {
    kids.push(h('div', { class: 'check__lh', style: 'margin-top:10px', text: `値動きで売る規則の比較（研究・前半 ${E.period[0]}／後半 ${E.period[1]}。全銘柄を5日おきに買ったことにして最長${E.days}営業日、コスト込み）` }));
    kids.push(h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['出口', '平均%', '下から5%'].map((t, i) => h('th', { text: t, style: i ? null : 'text-align:left' })))),
      h('tbody', {}, E.rows.map((r) => h('tr', {}, [h('th', { text: r.label }),
        h('td', { class: 'num', text: `${pt(r.avg[0])}／${pt(r.avg[1])}` }), h('td', { class: 'num', text: `${pt(r.p5[0])}／${pt(r.p5[1])}` })]))),
    ])));
    kids.push(h('p', { class: 'hint', text: '損切りや追いかけ売りは、深い負けを浅くする代わりに平均を下げた（保険料）。撤退ラインは「ここまでなら負けてよい」額で決める保険で、勝つための道具ではない。' }));
  }
  const P = X.pm;
  if (P) {
    kids.push(h('div', { class: 'check__lh', style: 'margin-top:10px', text: `前場の騰落（前日比）ごとの、後場寄り→大引（研究・1時間足 ${P.stocks}銘柄・前半 ${P.period[0]}／後半 ${P.period[1]}）` }));
    kids.push(h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['前場', '後場の平均%', '上げた割合'].map((t, i) => h('th', { text: t, style: i ? null : 'text-align:left' })))),
      h('tbody', {}, P.rows.map((r) => h('tr', {}, [h('th', { text: r.k }),
        h('td', { class: 'num', text: `${fmtSigned(r.pm[0], 2)}／${fmtSigned(r.pm[1], 2)}` }), h('td', { class: 'num', text: `${r.win[0]}／${r.win[1]}%` })]))),
    ])));
    kids.push(h('p', { class: 'hint', text: `後場の値動きの幅（1銘柄1日の標準偏差）は ±${P.sd[0]}〜${P.sd[1]}% で、前場の形による平均の差（±0.3%以内）はその中に埋もれる。前場を見て後場に売り買いを変えても、平均では何も変わらなかった。` }));
  }
  kids.push(h('p', { class: 'hint', text: '差は平均の話で、1銘柄ずつでは日経に勝つ割合は4〜5割。今の採用銘柄だけで測っている（生存者の偏り）。予測ではなく、同じ形の銘柄が過去にどうなったかの並びです。' }));
  return card('保有株の判定の根拠', '自分の判断で持つ銘柄', kids,
    '判定は引けの形（20日の騰落と2日RSI）だけで決め、買値は使いません（「買値まで戻ったら売る」は負けを長く持つ癖）。売買タブの保有銘柄に出る判定は、この表の形から出ています。', false, 'v-hold');
}

/* ---- 2. 保有銘柄: 押し目買いで買った銘柄（計画で手仕舞う）と、自分の判断で持つ銘柄（引けの形で判定）を1つに ---- */
function nowPrice(code) {
  const am = amQuote(code);
  return am ? am.price : (stockOf(code) || {}).price;
}

function portfolioCard() {
  const pos = readPos();
  const own = readHold().filter((p) => !pos.some((q) => q.code === p.code));
  const X = HOLDX();
  const addBox = h('div', {});
  const addBtn = h('button', { class: 'btn btn--ghost', type: 'button', text: '＋ 追加', onclick: () => {
    addBox.textContent = '';
    addBox.appendChild(holdForm(null));
    addBtn.hidden = true;
  } });
  const note = '押し目買いの銘柄は計画（毎朝の売り指値・損切り・期限）どおりに、中期の押し目の銘柄は損切り（調整の安値−1ATR）か60営業日目の引けで手仕舞い、' +
    '自分の判断で持つ銘柄は引けの形（20日の騰落と2日RSI）で「持つ／今は売らない／減らす候補」を出します。' +
    '買値・株数はこの端末にだけ保存されます。予測でも売買の推奨でもありません。';
  if (!pos.length && !own.length) {
    return card('保有銘柄', addBtn, [
      h('p', { class: 'hint', style: 'margin:0 0 8px', text: '注文が約定したら注文の「買えた」を押すと、ここに毎朝の売り指値・損切り・期限が出ます。' +
        'ほかに持っている銘柄は「＋ 追加」でコード・買値・株数を入れると、毎日の引けの形から判定と次の営業日の売り指値・撤退ラインを出します。' }),
      addBox,
    ], note, false, 'sw-pos');
  }
  // 合計（今の値が分かる銘柄だけ）と、判定の内訳
  let val = 0, cost = 0;
  const cnt = {};
  const nMid = pos.filter(isMidPos).length;
  if (pos.length - nMid) cnt['押し目買い'] = pos.length - nMid;
  if (nMid) cnt['中期の押し目'] = nMid;
  pos.concat(own).forEach((p) => {
    const now = nowPrice(p.code);
    if (p.shares && isNum(now) && isNum(p.entry)) { val += now * p.shares; cost += p.entry * p.shares; }
  });
  own.forEach((p) => {
    const hd = hdOf(p.code);
    const vd = hd && X ? (X.verdicts || {})[hd.v] : null;
    if (vd) cnt[vd.label] = (cnt[vd.label] || 0) + 1;
  });
  const due = pos.filter((p) => { const sw = swOf(p.code); return heldDays(p, sw) + 1 >= (isMidPos(p) ? rules().mid_hold : rules().max_hold); }).length;
  const hit = pos.filter((p) => { const n = nowPrice(p.code); return isNum(n) && n <= p.stop; }).length;
  const head = h('div', { class: 'hold__sum' }, [
    h('div', {}, [
      h('div', { class: 'decision__k', text: `${pos.length + own.length}銘柄` + (due ? `・期限 ${due}` : '') + (hit ? `・損切りの価格割れ ${hit}` : '') }),
      h('div', { class: 'hold__counts' }, Object.entries(cnt).map(([k, n]) => h('span', { class: 'badge', text: `${k} ${n}` }))),
    ]),
    cost ? h('div', { class: 'hold__total num ' + cls(val - cost) }, [
      h('div', { text: `${val - cost > 0 ? '+' : val - cost < 0 ? '−' : ''}${fmtYen(Math.abs(val - cost))}` }),
      h('small', { text: `${fmtPct((val / cost - 1) * 100, 1)}・評価 ${fmtYen(val)}` }),
    ]) : null,
  ]);
  return card('保有銘柄', addBtn, [
    head,
    h('div', { class: 'orders' }, pos.map((p) => positionRow(p)).concat(own.map((p) => holdRow(p)))),
    h('div', { class: 'hold__pad' }, [addBox]),
    own.length ? h('button', { class: 'evidence', type: 'button', onclick: () => selectView('verify', 'v-hold') }, [
      h('span', { text: '判定は4年の日足で、形ごとに次の20営業日の日経との差を測ったもの（1銘柄ずつでは日経に勝つ割合は4〜5割・生存者の偏りあり）。' +
        '値動きで売る規則（損切り・追いかけ売り・線割れ）は平均を下げ、深い負けを浅くする保険でした。' }),
      h('b', { text: '判定の根拠を見る ›' })]) : null,
  ], note, true, 'sw-pos');
}

/* ---- 3. 監視（発掘・ウォッチリスト・もうすぐ注文対象を1つに） ---- */
const WATCH_STATE = {
  signal: ['注文対象', 'ok'], near: ['もうすぐ', 'accent'], wait: ['待つ', ''], skip: ['見送り', 'warn'], edge: ['トレンドの境目', 'warn'],
  out: ['トレンド外', 'down'], thin: ['商い不足', ''], short: ['日足不足', ''], none: ['日足なし', ''],
};
const WATCH_ORDER = ['signal', 'near', 'wait', 'skip', 'edge', 'out', 'thin', 'short', 'none'];

function watchState(code) {
  const r = rules();
  const sw = swOf(code);
  if (!sw) return { key: 'none', text: '次の大引の更新で日足が入ります（日経225・テーマ辞書・台帳・ウォッチリストが対象）' };
  // 中期の形（大きなトレンドの中の1〜3か月の調整）の銘柄は、中期の押し目の計画だけを出す（1銘柄に1つの計画）
  const mdx = sw.md;
  if (mdx && mdx.st === 'signal') {
    return { key: 'signal', text: midOrderOf(code) ? '今日の中期の押し目の注文に入っています' : '中期の押し目の注文対象' };
  }
  if (mdx) {
    const shape = `1年で強く、60日高値から${mdx.age}営業日・${fmtPct(mdx.dd, 1)}の調整中`;
    if (isNum(mdx.trig)) {
      return { key: mdx.to >= -3 && mdx.ok !== false ? 'near' : 'wait', to: mdx.to,
        text: `${shape}。終値が ${fmtPrice(mdx.trig)}円 未満（${fmtPct(mdx.to, 1)}）で引けたら、翌営業日に中期の押し目の注文が出る` +
          (mdx.ok === false ? '（ただしそこまで下げると200日線の条件も割れる）' : '') };
    }
    return { key: 'wait', text: `${shape}。押した日に中期の押し目の注文が出る` };
  }
  if (sw.st === 'signal' && sw.pc === 'hot') {
    return { key: 'skip', text: `押した形だが、業種の上げに沿った押しなので見送り（${peerLine(sw.g, sw.g20, sw.rel)}）` };
  }
  if (sw.st === 'signal') {
    const inOrders = ((SW() || {}).orders || []).some((o) => o.code === code);
    return { key: 'signal', text: inOrders ? '今日の注文に入っています' : '注文対象（上限・業種の偏りで次点）' };
  }
  if (sw.st === 'out') return { key: 'out', text: `上昇トレンドではない（200日線 ${fmtPrice(sw.ma200)}円の下、または50日線が200日線の下）。このルールでは買わない` };
  if (sw.st === 'thin') return { key: 'thin', text: `20日平均の売買代金 ${isNum(sw.tv) ? sw.tv.toFixed(1) : '—'}億円（${r.liq_min}億円未満）か、株価${r.min_price}円未満` };
  if (sw.st === 'short') return { key: 'short', text: '200日線を引くだけの日足がまだありません' };
  if (isNum(sw.trig)) {
    if (!sw.ok) return { key: 'edge', text: `終値が ${fmtPrice(sw.trig)}円（${fmtPct(sw.to, 1)}）まで押すと、上昇トレンドの条件も割れる。注文対象にはなりにくい`, to: sw.to };
    if (sw.pc === 'hot') {
      return { key: 'wait', to: sw.to, text: `終値が ${fmtPrice(sw.trig)}円 未満（${fmtPct(sw.to, 1)}）で入口の形になるが、今は業種の上げに沿っているので見送りの見込み（業種より ${r.peer_lag}pt 以上遅れれば出る）` };
    }
    const tail = sw.pc && PEER_INFO[sw.pc] && !['plain', 'none'].includes(sw.pc) ? `。今のままなら「${PEER_INFO[sw.pc].label}」` : '';
    return { key: sw.to >= -3 ? 'near' : 'wait', to: sw.to,
      text: `終値が ${fmtPrice(sw.trig)}円 未満（${fmtPct(sw.to, 1)}）で引けたら、翌営業日に指値の注文が出る${tail}` };
  }
  return { key: 'wait', text: '押し待ち' };
}

function watchItems() {
  const map = new Map();
  const add = (code, name, src, extra) => {
    if (!code) return;
    const it = map.get(code) || { code, name: cleanName(name) || code, src: [], signals: [], notes: null };
    if (!it.src.includes(src)) it.src.push(src);
    if (extra) Object.assign(it, { signals: extra.signals || it.signals, notes: extra.notes || it.notes, first: extra.first || it.first, wl: extra.wl || it.wl });
    map.set(code, it);
  };
  ((LEDGER && LEDGER.entries) || []).filter((e) => e.status !== 'closed').forEach((e) =>
    add(e.code, e.name, '発掘', { signals: e.signals || [], notes: e.notes, first: e.first_seen }));
  effectiveWatchlist(sessData()).forEach((w) => add(String(w.code), w.name, 'ウォッチ', { wl: w }));
  ((SW() || {}).near || []).forEach((n) => add(n.code, n.name, 'もうすぐ'));
  const items = [...map.values()].map((it) => {
    const st = stockOf(it.code);
    return { ...it, name: cleanName((st || {}).n) || it.name || it.code, st, ws: watchState(it.code) };
  });
  items.sort((a, b) => WATCH_ORDER.indexOf(a.ws.key) - WATCH_ORDER.indexOf(b.ws.key) || (b.ws.to ?? -99) - (a.ws.to ?? -99));
  return items;
}

function watchRow(it) {
  const [label, tone] = WATCH_STATE[it.ws.key];
  const st = it.st || {};
  const wl = it.wl || {};
  const price = isNum(st.price) ? st.price : wl.price;
  const d1 = isNum(st.d1) ? st.d1 : wl.change_pct;
  return h('button', { class: 'signal signal--btn', type: 'button', onclick: () => openStock(it.code) }, [
    h('div', { class: 'signal__name', text: it.name }),
    h('div', { class: 'signal__right num' }, [
      h('b', { class: cls(d1), text: isNum(d1) ? fmtPct(d1) : '—' }),
      h('small', { text: `${it.code}　${isNum(price) ? fmtPrice(price) + '円' : ''}` }),
    ]),
    h('div', { class: 'signal__tags' }, [h('span', { class: 'badge' + (tone ? ' badge--' + tone : ''), text: label })]
      .concat((swOf(it.code) || {}).md ? [h('span', { class: 'badge badge--accent', text: '中期' })] : [])
      .concat(['signal', 'near', 'wait'].includes(it.ws.key) && !(swOf(it.code) || {}).md && (swOf(it.code) || {}).pc !== 'hot' ? [peerBadge((swOf(it.code) || {}).pc)] : [])
      .concat(it.src.filter((s) => s !== 'もうすぐ').map((s) => h('span', { class: 'badge badge--src', text: s })))
      .concat((wl.disclosures || []).map((m) => h('span', { class: 'badge badge--warn', text: '📄 ' + m })))
      .concat((wl.tags || []).slice(0, 2).map((t) => h('span', { class: 'badge badge--accent', text: t })))
      .concat(wl.pending ? [h('span', { class: 'badge badge--warn', text: hasGitHubToken() ? 'リポジトリ反映待ち' : 'この端末だけ' })] : [])
      .concat((it.signals || []).slice(0, 2).map((s) => h('span', { class: 'badge', text: s })))),
    h('div', { class: 'signal__why', text: it.ws.text }),
    it.notes ? h('div', { class: 'signal__why watch__note', text: it.notes }) : null,
  ]);
}

function watchCard() {
  const all = watchItems();
  const L = (THERMO || {}).lists || {};
  const avoid = [].concat((L.hot || []).map((x) => [x, '高値掴み注意', `RSI ${isNum(x.rsi) ? Math.round(x.rsi) : '—'}・5日 ${fmtPct(x.r5, 1)}`]),
    (L.good_out || []).map((x) => [x, '好材料出尽くし', `${md(x.date)} 上方修正から ${fmtPct(x.since, 1)}`]));
  const groups = ((SW() || {}).peers || []);
  const editBtn = h('button', { class: 'btn btn--ghost', type: 'button', text: 'ウォッチを編集', onclick: () => openSheet(sessData()) });
  const list = h('div', {});
  const note = h('div', { class: 'card__note' });
  const count = (s) => all.filter((it) => it.src.includes(s)).length;
  const wlItems = all.filter((it) => it.src.includes('ウォッチ'));
  const pending = wlItems.filter((it) => (it.wl || {}).pending).length;
  const NOTES = {
    all: '見つけた日に買わず、このルールの形（上昇トレンド中の短い押し）になるのを待つための一覧。上から、注文対象・もうすぐ（あと3%以内の下げで注文対象になり、その価格でも上昇トレンドを保つ）・待つ、の順。押すと銘柄の詳しい画面が開きます。',
    'もうすぐ': 'あと3%以内の下げで注文対象になり、その価格でも上昇トレンドを保つ銘柄。',
    'ウォッチ': '自分で登録した銘柄。今日のランキング・開示に出たものは印が付きます。' + (pending ? (hasGitHubToken()
      ? `　${pending}銘柄はまだリポジトリに届いていません。「ウォッチを編集」→「保存して反映」をもう一度押してください。`
      : `　${pending}銘柄はこの端末にだけ保存されていて、サーバ側の収集は対象にしていません（開示・ランキングの突き合わせ、日足の取得は行われません）。「ウォッチを編集」→「連携設定」で GitHub トークンを保存すると、リポジトリに書き込まれます。`) : ''),
    '発掘': '売買代金・開示・テーマから機械が見つけた銘柄（理由づけは AI）。見つけた日の終値で買った場合の成績は検証タブ。',
    peer: '', avoid: '短期で上がりすぎた銘柄（RSI 75以上・25日線から+15%以上・5日で+15%以上）と、好材料でも上がらない銘柄。上がっているから買う、はいつもの負けパターン。' +
      'このルールの注文は上昇トレンド中の「下げた日」だけに出るので、ここに並ぶ銘柄には出ません。',
  };
  let filter = 'all';
  const draw = () => {
    list.textContent = '';
    if (filter === 'peer') {
      const g = peerGroups();
      list.appendChild(g.body);
      note.textContent = g.note;
      return;
    }
    if (filter === 'avoid') {
      list.appendChild(h('div', {}, avoid.map(([x, why, sub]) => h('button', { class: 'signal signal--btn', type: 'button', onclick: () => openStock(x.code) }, [
        h('div', { class: 'signal__name', text: cleanName(x.name) || x.code }),
        h('div', { class: 'signal__right num' }, [h('small', { text: x.code })]),
        h('div', { class: 'signal__tags' }, [h('span', { class: 'badge badge--warn', text: why })]),
        h('div', { class: 'signal__why', text: sub }),
      ]))));
      note.textContent = NOTES.avoid;
      return;
    }
    const rows = all.filter((it) => filter === 'all' || it.src.includes(filter));
    note.textContent = NOTES[filter];
    if (!rows.length) {
      list.appendChild(h('div', { class: 'empty', style: 'padding:6px 14px', text: filter === 'ウォッチ'
        ? '登録がありません。「ウォッチを編集」か、銘柄の詳しい画面の☆で登録します。' : 'なし' }));
      return;
    }
    list.appendChild(foldable((n) => h('div', {}, rows.slice(0, n).map(watchRow)), rows.length, 10, '全件'));
  };
  const opts = [['all', `すべて ${all.length}`], ['もうすぐ', `もうすぐ ${count('もうすぐ')}`], ['ウォッチ', `ウォッチ ${count('ウォッチ')}`],
    ['発掘', `発掘 ${count('発掘')}`]];
  if (SW()) opts.push(['peer', `業種で見る ${groups.length}`]);
  if (avoid.length) opts.push(['avoid', `追わない ${avoid.length}`]);
  const seg = segmented(opts, (k) => { filter = k; draw(); }, 'all');
  seg.classList.add('seg--scroll');
  draw();
  return h('section', { class: 'card card--flush', id: 'sw-watch' }, [
    h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: '監視' }), editBtn]),
    h('div', { class: 'card__seg' }, seg), list, note,
  ]);
}

/* ---- 3b. 業種の中の位置（業種ぐるみの下げ・業種の上げと、その中で下げが大きい／遅れている銘柄） ---- */
const PEER_STATE = {
  signal: ['注文対象', 'ok'], wait: ['押し待ち', ''], out: ['トレンド外', 'down'], thin: ['商い不足', ''], short: ['日足不足', ''],
};

function peerMember(m, kind) {
  const r = rules();
  let st = PEER_STATE[m.st] || ['—', ''];
  let why;
  if (m.st === 'signal' && midOrderOf(m.code)) {
    why = '中期の押し目の注文に入っています（大きなトレンドの中の調整で押した日）';
  } else if (m.st === 'signal' && m.pc === 'hot') {
    st = ['見送り', 'warn'];
    why = '押したが業種並み（業種の上げに沿った押し）';
  } else if (m.st === 'signal') {
    const inOrders = ((SW() || {}).orders || []).some((o) => o.code === m.code);
    why = inOrders ? '今日の注文に入っています' : '注文対象（上限・業種の偏りで次点）';
  } else if (m.st === 'wait' && isNum(m.to) && m.ok) {
    if (m.to >= -3) st = ['もうすぐ', 'accent'];
    why = `終値が ${fmtPrice(m.trig)}円 未満（あと ${Math.abs(m.to).toFixed(1)}% 下げて 2日RSI ${r.rsi_max}未満）で引けたら注文対象` +
      (m.pc === 'hot' ? '。ただし業種並みのままなら見送り' : '');
  } else if (m.st === 'wait' && isNum(m.to)) {
    why = `あと ${Math.abs(m.to).toFixed(1)}% 下げると入口の形だが、そこまで押すと上昇トレンドの条件も割れる`;
  } else if (m.st === 'wait') {
    why = '押し待ち（上昇トレンドを保ったまま押す形を待つ）';
  } else if (m.st === 'out') {
    why = kind === 'dip' ? '上昇トレンドが崩れた。下げが大きくても、このルールでは買わない（検証で、業種だけの急落では戻りが弱かった）'
      : '上昇トレンドではない。このルールでは買わない';
  } else if (m.st === 'thin') {
    why = `売買代金 ${r.liq_min}億円未満か株価 ${r.min_price}円未満`;
  }
  return h('button', { class: 'signal signal--btn', type: 'button', onclick: () => openStock(m.code) }, [
    h('div', { class: 'signal__name', text: cleanName(m.name) || m.code }),
    h('div', { class: 'signal__right num' }, [
      h('b', { class: cls(m.rel20), text: `業種より ${fmtPt(m.rel20)}` }),
      h('small', { text: `${m.code}　${r.peer_n}日 ${fmtPct(m.r20, 1)}` }),
    ]),
    h('div', { class: 'signal__tags' }, [h('span', { class: 'badge' + (st[1] ? ' badge--' + st[1] : ''), text: st[0] })]),
    why ? h('div', { class: 'signal__why', text: why }) : null,
  ]);
}

function peerGroups() {
  const sw = SW();
  const groups = (sw && sw.peers) || [];
  const r = rules();
  const note = `同じ業種（テーマ辞書の主テーマ、無ければ日経の業種）の${r.peer_n}日の騰落を、自分を除いた中央値と比べます。` +
    `4年の検証（前半 2023-24 で決め、後半 2025-26 で確かめた。売り指値の下限つき）で、上昇トレンド中の押し（2日RSI ${r.rsi_max}未満）の勝率は、` +
    `業種ぐるみの押し（業種 ${r.peer_dip}% 以下）${PEER_INFO.dip.stat}、そのうち業種より ${r.peer_lag}pt 以上下げた銘柄 ${PEER_INFO.dip_lag.stat}、` +
    `出遅れの押し（業種は +${r.peer_up}% 以上、自分は ${r.peer_lag}pt 以上遅れ）${PEER_INFO.lag.stat}、業種の上げに沿った押し ${PEER_INFO.hot.stat}（1回の平均 ${HOT_AVG}。見送り）。` +
    'ただし「下げが大きい銘柄ほど戻る」の多くは、相場全体が下げた日の効き目でした（業種をでたらめに入れ替えても勝率は8〜9割）。' +
    '出遅れ銘柄を押していない日に買う・上昇トレンドが崩れた銘柄を業種の急落で買う、は検証で効かなかったので注文は出しません。';
  if (!groups.length) {
    return { note, body: h('p', { class: 'hint', style: 'margin:0 14px', text:
      `今日は、${r.peer_n}日で ${r.peer_dip}% 以下に下げた業種も、+${r.peer_up}% 以上に上げた業種もありません。` }) };
  }
  const kids = groups.map((g, i) => {
    const dip = g.kind === 'dip';
    const members = (g.members || []).filter((m) => m.st !== 'short');
    return h('details', { class: 'acc peer', open: dip && i < 2 ? 'open' : null }, [
      h('summary', {}, [
        h('span', { class: 'acc__title' }, [
          h('span', { text: g.g + '　' }), h('span', { class: 'num ' + cls(g.g20), text: fmtPct(g.g20, 1) }),
          h('small', { text: `${dip ? '業種ぐるみの下げ' : '業種の上げ'}・${g.n}銘柄の${r.peer_n}日騰落の中央値` }),
        ]),
      ]),
      h('p', { class: 'hint', style: 'margin:2px 14px 6px', text: dip
        ? '上から、業種の中でも下げが大きい順。押した日（注文対象）に指値で拾うのがこのルールの形'
        : '上から、業種より遅れている順（出遅れ）。遅れている銘柄が押した日に注文が出る。業種並みの押しは見送り' }),
      foldable((n) => h('div', {}, members.slice(0, n).map((m) => peerMember(m, g.kind))), members.length, 6, '全銘柄'),
    ]);
  });
  return { note, body: h('div', {}, kids) };
}

/* ---- 5. 成績 ---- */
/* 口座の資産の推移（初日=1）。1 の高さに点線。自前の SVG なので html: を使う */
function equityChart(curve) {
  if (!Array.isArray(curve) || curve.length < 5) return null;
  const W = 320, H = 90, n = curve.length;
  const vs = curve.map((p) => p[1]);
  let lo = Math.min(1, ...vs), hi = Math.max(1, ...vs);
  const pad = (hi - lo) * 0.08 || 0.01;
  lo -= pad; hi += pad;
  const x = (i) => (i / (n - 1)) * W;
  const y = (v) => H - ((v - lo) / (hi - lo)) * H;
  const pts = vs.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return h('div', { class: 'tc' }, [
    h('div', { html: `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="tc__svg" role="img" aria-label="資産の推移">` +
      `<line x1="0" x2="${W}" y1="${y(1).toFixed(1)}" y2="${y(1).toFixed(1)}" class="tc__grid"/>` +
      `<polyline points="${pts}" class="tc__line" vector-effect="non-scaling-stroke"/></svg>` }),
    h('div', { class: 'tc__axis' }, [h('span', { text: ym(curve[0][0]) }), h('span', { text: '資産の推移（初日＝1、点線）' }),
      h('span', { text: ym(curve[n - 1][0]) })]),
  ]);
}

/* 口座の再現（swing.account）: 資産の倍率・年率・最大の目減り・月でプラスの割合と、資産の推移 */
function accountBlock(acc, title, note) {
  return h('div', { class: 'decision__block' }, [
    h('div', { class: 'decision__bh', text: title }),
    h('div', { class: 'summary__stats' }, [
      stat('資産', `×${acc.final.toFixed(2)}`, isNum(acc.cagr) ? `年率 ${fmtPct(acc.cagr, 1)}` : `${acc.days}営業日`, cls(acc.final - 1)),
      stat('最大の目減り', fmtPct(acc.dd, 1), isNum(acc.under) ? `戻るまで最長${acc.under}日` : null, acc.dd < 0 ? 'down' : ''),
      stat('月でプラス', isNum(acc.m_up) ? `${acc.m_up}%` : '—', isNum(acc.q_up) ? `3か月 ${acc.q_up}%` : `${acc.months || 0}か月`),
    ]),
    equityChart(acc.curve),
    h('p', { class: 'hint', text: note }),
  ]);
}

function statRow(label, s, from, total) {
  const pc = (v) => h('td', { class: 'num ' + cls(v), text: isNum(v) ? fmtPct(v, 1) : '—' });
  return h('tr', { class: total ? 'bt__all' : '' }, [
    h('th', {}, [document.createTextNode(label), from ? h('small', { class: 'bt__sub', text: `${ym(from)}〜` }) : null]),
    h('td', { class: 'num', text: isNum(s.n) ? String(s.n) : '—' }),
    h('td', { class: 'num', text: isNum(s.win) ? s.win + '%' : '—' }),
    pc(s.avg),
    h('td', { class: 'num', text: isNum(s.pf) ? s.pf.toFixed(2) : '—' }),
    pc(s.worst),
  ]);
}

function statsCard() {
  const sw = SW();
  const v = (sw && sw.verify) || null;
  const body = [];
  if (v && v.all && v.all.n) {
    const a = v.all, base = v.base || {};
    body.push(h('div', { class: 'summary__stats' }, [
      stat('勝率', `${a.win}%`, `${a.n}回（比べる相手 ${base.win ?? '—'}%）`, 'up'),
      stat('1回の平均', fmtPct(a.avg, 2), `勝ち ${fmtPct(a.avg_win, 1)}／負け ${fmtPct(a.avg_loss, 1)}`, cls(a.avg)),
      stat('PF', isNum(a.pf) ? a.pf.toFixed(2) : '—', `利益÷損失・平均 ${a.days}日で手仕舞い`),
    ]));
    body.push(h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['期間', '回数', '勝率', '平均', 'PF', '最悪'].map((t) => h('th', { text: t })))),
      h('tbody', {}, [
        statRow('前半', v.early, v.early.from),
        statRow('後半', v.late, v.late.from),
        statRow('直近', v.recent, v.recent.from),
        statRow('全期間', v.all, null, true),
      ]),
    ])));
    body.push(h('p', { class: 'hint', text: `比べる相手: 同じ銘柄を毎日、翌日の寄りで買って5営業日後の引けで売った場合（${(base.n || 0).toLocaleString('ja-JP')}回）の勝率 ${base.win ?? '—'}%・平均 ${fmtPct(base.avg, 2)}。` +
      `損切りで終わったのは ${a.stops}%、売り指値で終わったのは ${a.sells}%、期限で終わったのは ${a.times}%。` +
      (isNum(a.d2) ? `買ってから平均 ${a.days}営業日で手仕舞い（約定した日の翌日までに ${a.d2}%）、1回の平均を保有日数で割ると 1日あたり ${fmtPct(a.per_day, 2)}。` : '') +
      (isNum(a.small) ? `勝ちのうち +0.5% 以下の小さな勝ち（多くは売り指値の下限＝買値+${rules().floor}%で終わった売り）が全体の ${a.small}%、` +
        `−5% 以下の大きな負けが ${a.big_loss}%。勝率が高い分、負けは1回が大きい（注文ごとに、損切りで失う額と資金の%を出しています）。` : '') }));
    const acc = v.account;
    if (acc && acc.days) {
      const r = rules();
      const prev = v.account_prev || {};
      body.push(accountBlock(acc, `本番どおりに置いた口座（短期と中期を合わせて・資金の${acc.slot}%ずつ）`,
        `${ym(acc.from)}〜${ym(acc.to)} に、毎日の引けで中期の押し目の注文を先に（中期の保有が${acc.mid_slots || r.mid_slots}銘柄になるまで）、` +
        `次に短期の押し目買いの注文を上から${r.max_orders}件まで、空いている資金の${acc.slot}%ずつ置いた場合` +
        `（翌日だけ有効・約定しなければ資金はその日遊ぶ・保有中の銘柄には重ねない・短期は同じ業種${r.sector_cap}銘柄まで）。` +
        `稼働率 ${acc.util}%（資金のうち株に入っていた割合の平均）・短期 ${acc.n}回（勝率 ${acc.win ?? '—'}%）・中期 ${acc.mid_n || 0}回（勝率 ${acc.mid_win ?? '—'}%・平均 ${fmtPct(acc.mid_avg, 1)}）。` +
        (isNum(prev.cagr) ? `以前の置き方（短期だけ）なら年率 ${fmtPct(prev.cagr, 1)}・最大の目減り ${fmtPct(prev.dd, 1)}・資産 ×${(prev.final || 0).toFixed(2)}。` : '') +
        '上の1回ごとの成績は資金の制約を置かない数え方で、約定しなかった注文の資金が遊ぶ分が入っていません。どれくらいの期間で増えるかは、こちらの口座で見てください。' +
        '4年（2023-08〜2026-09）では、短期だけ 年率 +18%／+41%（前半／後半）・最大の目減り −15%／−13%、中期と合わせて +30%／+61%・−17%／−17%（DESIGN.md 21章）。'));
    }
    const pv = v.peer || null;
    if (pv) {
      const rowsP = ['dip_lag', 'dip', 'lag', 'plain', 'none'].filter((k) => (pv[k] || {}).n)
        .map((k) => statRow(PEER_INFO[k].short, pv[k]));
      if ((v.skipped || {}).n) rowsP.push(statRow(PEER_INFO.hot.short, v.skipped));
      body.push(h('div', { class: 'decision__block' }, [
        h('div', { class: 'decision__bh', text: '押しの形ごと（業種の中での位置・同じ期間）' }),
        h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
          h('thead', {}, h('tr', {}, ['形', '回数', '勝率', '平均', 'PF', '最悪'].map((t) => h('th', { text: t })))),
          h('tbody', {}, rowsP),
        ])),
        h('p', { class: 'hint', text: '業種ぐるみ＝業種の20日が −5% 以下（下げ大＝その中で業種より −5pt 以上）、出遅れ＝業種は +3% 以上で自分は −5pt 以上遅れ、' +
          '見送り＝業種は +3% 以上で自分も業種並み。見送りは注文に数えず、同じ規則で約定・手仕舞いを測った成績です。回数の少ない形は数字が振れます。' +
          '4年（2022-10〜）の数字は「このルールについて」と DESIGN.md 14章。' }),
      ]));
    }
  } else {
    body.push(h('div', { class: 'empty', text: '検証に足りる日足がまだありません（200日線が引けてから数えます）' }));
  }
  const p = (sw && sw.paper) || {};
  if (p.issued) {
    const recent = (p.recent || []).slice().reverse().slice(0, 12);
    body.push(h('div', { class: 'decision__block' }, [
      h('div', { class: 'decision__bh', text: `このアプリが出した注文の実績（${md(p.since)}〜・${p.issued}件）` }),
      h('p', { class: 'stats__line', text: p.n
        ? `結果が出た ${p.n}回: 勝率 ${p.win}%・平均 ${fmtPct(p.avg, 2)}` + (isNum(p.pf) ? `・PF ${p.pf.toFixed(2)}` : '') +
          `。約定率 ${isNum(p.fill) ? p.fill + '%' : '—'}、保有中 ${p.open || 0}件。`
        : `まだ結果が出た注文はありません（${isNum(p.fill) ? `約定率 ${p.fill}%、` : ''}保有中 ${p.open || 0}件）。検証と同じ規則で、四本値から約定・手仕舞いを測ります。` }),
      h('div', {}, recent.map((e) => h('div', { class: 'paper__row' }, [
        h('span', { class: 'paper__d num', text: md(e.asof) }),
        h('span', { class: 'paper__n', text: cleanName(e.name) || e.code }),
        h('span', { class: 'paper__r num ' + (isNum(e.ret) ? cls(e.ret) : ''), text:
          e.done && !e.filled ? '約定せず' : isNum(e.ret) ? `${fmtPct(e.ret, 1)}（${{ sell: '売り指値', stop: '損切り', time: '期限' }[e.why] || ''}）`
            : e.filled ? `保有中 ${isNum(e.last) && isNum(e.entry) ? fmtPct((e.last / e.entry - 1) * 100, 1) : ''}` : '待ち' }),
      ]))),
    ]));
    const pa = p.account;
    if (pa && pa.days >= 2) {
      body.push(accountBlock(pa, `アプリの注文どおりに置いた口座（${md(pa.from)}〜・資金の${pa.slot}%ずつ）`,
        `出した注文を、出した日から上の口座と同じ置き方（空いている資金の${pa.slot}%ずつ・約定しなければ資金はその日遊ぶ）で並べた実績。` +
        `約定・手仕舞いは検証と同じ規則で四本値から測ります。${pa.days}営業日ぶんなので、数字はまだ振れます。`));
    }
  }
  return card('押し目買いの成績', v ? `${ym(v.from)}〜${ym(v.to)}・${v.universe}銘柄` : null, body,
    (v ? v.note + ' ' : '') + '銘柄は今の日経225採用・テーマ辞書・台帳・ウォッチリストで、途中で上場廃止になった銘柄は入っていない（その分だけ良く見える）。' +
    '押し目買いは急落に弱い（2024年8月の急落のような日は、損切りが寄りの窓で滑る）。1件を資金の10%前後に抑え、1日5件までで使ってください。', true, 'v-swing');
}

/* 中期の押し目の成績とルール（検証タブ）。1回ごとの成績を、調整の条件なしの強い銘柄の押しと並べる */
function midStatsCard() {
  const sw = SW();
  const v = (sw && sw.verify) || {};
  const m = v.mid || null;
  const r = rules();
  const body = [];
  if (m && m.all && m.all.n) {
    const a = m.all, b = (m.base || {}).all || {};
    body.push(h('div', { class: 'summary__stats' }, [
      stat('1回の平均', fmtPct(a.avg, 2), `比べる相手 ${fmtPct(b.avg, 2)}`, cls(a.avg)),
      stat('勝率', `${a.win}%`, `${a.n}回（相手 ${b.win ?? '—'}%）`),
      stat('PF', isNum(a.pf) ? a.pf.toFixed(2) : '—', `相手 ${isNum(b.pf) ? b.pf.toFixed(2) : '—'}・平均 ${a.days}日`),
    ]));
    const bs = m.base || {};
    body.push(h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['期間', '回数', '勝率', '平均', 'PF', '最悪'].map((t) => h('th', { text: t })))),
      h('tbody', {}, [
        statRow('中期 前半', m.early, m.early.from), statRow('相手 前半', bs.early || {}),
        statRow('中期 後半', m.late, m.late.from), statRow('相手 後半', bs.late || {}),
        statRow('中期 全期間', m.all, null, true), statRow('相手 全期間', b),
      ]),
    ])));
    body.push(h('p', { class: 'hint', text: `比べる相手: 12か月の強さが上位1/3・上昇トレンドの銘柄が押した日（2日RSI ${r.rsi_max}未満）に、1〜3か月の調整の条件なしで同じ注文を置いた場合。` +
      `損切りで終わったのは ${a.stops}%、${r.mid_hold}営業日の期限で終わったのは ${a.times}%。勝ちの平均 ${fmtPct(a.avg_win, 1)}・負けの平均 ${fmtPct(a.avg_loss, 1)}。` +
      '勝率は低く、損切りで終わる回が多い。少数の大きく伸びた銘柄が平均を作る形です。' }));
  } else {
    body.push(h('div', { class: 'empty', text: '検証に足りる日足がまだありません（12か月の強さに250営業日が要ります）' }));
  }
  const p = ((MID() || {}).paper) || {};
  if (p.issued) {
    const recent = (p.recent || []).slice().reverse().slice(0, 12);
    body.push(h('div', { class: 'decision__block' }, [
      h('div', { class: 'decision__bh', text: `このアプリが出した中期の注文の実績（${md(p.since)}〜・${p.issued}件）` }),
      h('p', { class: 'stats__line', text: p.n
        ? `結果が出た ${p.n}回: 勝率 ${p.win}%・平均 ${fmtPct(p.avg, 2)}。約定率 ${isNum(p.fill) ? p.fill + '%' : '—'}、保有中 ${p.open || 0}件。`
        : `まだ結果が出た注文はありません（${isNum(p.fill) ? `約定率 ${p.fill}%、` : ''}保有中 ${p.open || 0}件）。${r.mid_hold}営業日持つので、結果が出るまで3か月ほどかかります。` }),
      h('div', {}, recent.map((e) => h('div', { class: 'paper__row' }, [
        h('span', { class: 'paper__d num', text: md(e.asof) }),
        h('span', { class: 'paper__n', text: cleanName(e.name) || e.code }),
        h('span', { class: 'paper__r num ' + (isNum(e.ret) ? cls(e.ret) : ''), text:
          e.done && !e.filled ? '約定せず' : isNum(e.ret) ? `${fmtPct(e.ret, 1)}（${{ stop: '損切り', time: '期限' }[e.why] || ''}）`
            : e.filled ? `保有中 ${isNum(e.last) && isNum(e.entry) ? fmtPct((e.last / e.entry - 1) * 100, 1) : ''}` : '待ち' }),
      ]))),
    ]));
  }
  const lines = [
    ['形', `12か月の強さ（${r.mid_mom_skip}営業日前の終値 ÷ ${r.mid_mom_n}営業日前の終値。直近1か月は除く）が、その日の銘柄の上位1/3。終値 > ${r.ma_long}日線で、${r.ma_long}日線が${r.mid_slope_n}営業日前より上。` +
      `${r.mid_hi_n}営業日の最高値（終値）から${r.mid_age[0]}〜${r.mid_age[1]}営業日たち、そこから ${r.mid_depth}% 以下（1〜3か月の調整）`],
    ['入口', `その形の銘柄が2日RSI ${r.rsi_max}未満まで短く押した日。売買代金（20日平均）${r.liq_min}億円以上・株価${r.min_price}円以上`],
    ['買い', `翌営業日だけ有効の指値 = 終値 − ${r.entry_atr}×ATR(14)（短期と同じ）`],
    ['損切り', `min(調整の安値, 約定値) − ${r.mid_stop_atr}×ATR(14) に逆指値。調整の安値（高値の日からの終値の最安）を割ったら「調整明け」の見立て違い`],
    ['売り', `売りの指値は置かない。${r.mid_hold}営業日目の引けで売る（伸びる銘柄を早く売らない）`],
    ['枠と並べ方', `中期の保有は${r.mid_slots}銘柄まで・1件は資金の${r.slot_pct}%。短期より先に置く。同じ日に複数あれば25日線からの下離れの大きい順`],
    ['短期との関係', '中期の形の押しは中期の計画だけで出し、短期の押し目買いの注文には出さない（1銘柄に1つの計画）'],
  ];
  body.push(h('div', { class: 'decision__block' }, [
    h('div', { class: 'decision__bh', text: '中期の押し目のルール' }),
    h('dl', { class: 'rule' }, lines.flatMap(([k, t]) => [h('dt', { text: k }), h('dd', { text: t })])),
  ]));
  body.push(h('details', { class: 'acc acc--inline' }, [
    h('summary', {}, [h('span', { class: 'acc__title', text: '4年の研究と、採らなかった形（DESIGN.md 21章）' })]),
    h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['前半 2023-24／後半 2025-26', '中期', '比べる相手'].map((t) => h('th', { text: t })))),
      h('tbody', {}, [
        ['1回の平均（本番の出口）', '+5.38／+6.20%', '+2.95／+3.55%'],
        ['勝率', '34／27%', '27／23%'],
        ['PF', '2.83／2.38', '1.98／1.84'],
        ['60営業日持つだけ（損切りなし）', '+11.2／+14.2%', '+6.5／+9.2%'],
        ['同・大勝ちの上位10銘柄を除く', '+7.9／+7.9%', '+4.7／+6.7%'],
      ].map((row) => h('tr', {}, [h('th', { text: row[0] }), h('td', { class: 'num', text: row[1] }), h('td', { class: 'num', text: row[2] })]))),
    ])),
    h('p', { class: 'hint', text:
      '1年で強い銘柄が1〜3か月調整した押しは、調整の条件なしで強い銘柄の押しを買うより、前半・後半とも、日経225の銘柄だけでも、試した7つの出口のどれでも成績が良かった' +
      '（年ごとでは、60営業日持つだけなら2023〜2026年のどの年も上。本番の出口では2023年だけ同じくらい: +10.6% 対 +11.0%）。' +
      '口座では、短期の押し目買いに中期の枠（6銘柄）を足すと、並び順をでたらめにした30通りで年率は前半・後半とも30通りすべてで上、シャープは28通り・24通りで上、最大の目減りは30通りすべてで深い（中央値 −15% → −20%）。' +
      'ただし口座の改善の多くは、大きく伸びた少数の銘柄（メタプラネット・古河電工・さくらインターネットなど）から来ていて、上位10銘柄を除くと口座の改善はほぼ消える。' +
      'テーマ辞書は今注目されている銘柄で作られているので、「強い銘柄は強いまま」を実際より良く見せている可能性がある（生存者の偏り）。向きは確か、大きさは不確かと読んでください。' +
      '枠は多いほど（8・10銘柄）この期間では良かったが、この偏りを考えて6銘柄に抑えた。' }),
    h('p', { class: 'hint', text:
      '試して採らなかったもの: 保ち合いからの高値更新で買う（前半は平均より悪い）、調整の途中で25日線・50日線を回復した日に買う（大きなトレンドの銘柄をどの日に買っても変わらないか、それより悪い。' +
      '強い銘柄が20日線を回復した日は、次の20日でむしろ負けた）、' +
      '押していない日に寄りで買う（押した日の指値のほうが前半で良い）、出口を40・80営業日・5ATR・−20%・200日線割れにする（前半の口座で安値割れの出口が最も良く、後半も最も良かった）、' +
      '並べ方を強さ・調整の深さの順にする（でたらめな並びと差がない）。' }),
  ]));
  return card('中期の押し目の成績', m && m.from ? `${ym(m.from)}〜${ym(v.to)}` : null, body,
    (m ? m.note + ' ' : '') + '予測ではなく、決めた規則どおりに注文を置くための目安です。', true, 'v-mid');
}

function ruleCard() {
  const r = rules();
  const lines = [
    ['入口', `上昇トレンド（終値 > ${r.ma_long}日線、${r.ma_mid}日線 > ${r.ma_long}日線）の銘柄が、2日RSI ${r.rsi_max}未満まで短く押した日。売買代金（20日平均）${r.liq_min}億円以上・株価${r.min_price}円以上`],
    ['買い', `翌営業日だけ有効の指値 = 終値 − ${r.entry_atr}×ATR(14)。押した日の、さらに下で拾う。IFD で置けば約定と同時に損切りが入る`],
    ['売り', `翌日から毎朝、指値 = 直近${r.exit_n}日の終値の平均（「終値が5日線を上回ったら売る」を前もって置ける形にしたもの）。ただし買値 +${r.floor}% より下には置かない（含み損のうちは戻りを待つ）。損切りと OCO で置き直す`],
    ['損切り', `約定値 − ${r.stop_atr}×ATR(14) に逆指値。ふだんの揺れでは掛からない距離`],
    ['期限', `${r.max_hold}営業日で売れなければ引けで売る`],
    ['業種', `同じ業種（テーマ辞書の主テーマ、無ければ日経の業種）の${r.peer_n}日騰落の中央値（自分を除く）と比べる。業種が +${r.peer_up}% 以上上げていて、自分も業種並み（差が ${r.peer_lag}pt より上）の押しは見送り`],
    ['並べ方', `25日線からの下離れ＋業種より遅れている分が大きい順（業種ぐるみの押し・出遅れの押しが先）。1日${r.max_orders}銘柄まで・同じ業種は${r.sector_cap}銘柄まで`],
    ['株数', `1件 = 資金の${r.slot_pct}%（金額をそろえる）。保有中に使っていない資金の範囲で、上から順に置く`],
  ];
  const tbl = (head, rows) => h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
    h('thead', {}, h('tr', {}, head.map((t) => h('th', { text: t })))),
    h('tbody', {}, rows.map((row) => h('tr', {}, [h('th', { text: row[0] })].concat(row.slice(1).map((c) => h('td', { class: 'num', text: c })))))),
  ]));
  return card('押し目買いのルール', null, [
    h('dl', { class: 'rule' }, lines.flatMap(([k, v]) => [h('dt', { text: k }), h('dd', { text: v })])),
    h('p', { class: 'hint', text:
      '2022年10月〜2026年9月の四本値（約380銘柄）で、場中の安値での損切り・窓開け・売買コストまで再現して選んだルールです。' +
      '以前の作戦ボード（押し目・深押し・上向き転換・売られすぎ・相対力リーダー）は同じ条件で勝率 39〜54%、発掘の入口（上がった・商いが膨らんだ銘柄を見つけた日に買う）は日経平均に負けていました。' +
      'このルールは前半（2023〜24年）で決め、後半（2025〜26年）でも勝率69%を保ち、売り指値に下限を付けて 86〜87% になりました。' +
      '株数は、同じ張り具合で口座が速く増える「金額をそろえる」決め方にしています。詳しくは DESIGN.md 13〜16章。' }),
    h('details', { class: 'acc acc--inline' }, [
      h('summary', {}, [h('span', { class: 'acc__title', text: '株数の決め方と口座の伸び（DESIGN.md 16章）' })]),
      tbl(['2023-08〜', '損1%で', `${r.slot_pct}%ずつ`], [
        ['年率 前半／後半', '+12.6／+28.3%', '+19.4／+43.2%'],
        ['最大の目減り', '−11.9／−11.4%', '−14.3／−12.5%'],
        ['シャープ', '0.98／2.15', '1.25／2.19'],
        ['月でプラス', '68%', '79%'],
        ['3か月でプラス', '89%', '89%'],
        ['3年の資産', '1.78倍', '2.35倍'],
      ]),
      h('p', { class: 'hint', text: `「損1%で」は今までの決め方（損切りまでの幅から、1回の損が資金の1%になる株数。1銘柄20%まで）、「${r.slot_pct}%ずつ」はいまの決め方（金額をそろえる）。` +
        'どちらも本番どおりの置き方（引けで注文を上から1日5件まで置き、約定しなければ資金はその日遊ぶ・同じ業種2銘柄まで）。前半 2023-24・後半 2025-26。' +
        '置いた注文が約定するのは38%で、資金の半分以上は遊んでいます。それでも同じ張り具合なら、金額をそろえるほうが速く増えました' +
        '（並び順をでたらめにした30通りで、資金の10〜15%と1回の損1〜1.5%を比べると、年率は23〜30通り・シャープは21〜26通りで金額均等が上）。' +
        '値動きの大きい銘柄ほど1件あたりの期待値が高いのに、損切り幅で株数を決めると値動きの小さい銘柄に資金が偏るためです。目減りは1〜2pt深くなります。' +
        '1件を資金の20%にすると年率 +36%・最大の目減り −23% で、シャープは下がります（候補が重なる日に置けない）。' }),
      h('p', { class: 'hint', text: '試したが採らなかったもの（前半と後半で向きがそろわない、または偶然の範囲）: 期限を7日・5日に、約定した日にも売りの指値を置く' +
        '（後半では30通り中3通りしか上回らない）、並べ方を「売りの目安までの距離」に（前半は良く後半は悪い）、指値を浅く（約定は増えるが1回の質が落ち、口座は悪化）、' +
        '枠を増やす（1件が小さくなるだけで稼働率が下がる）、1回の損の歯止め（年率が下がり、目減りは同じ）。1回ごとの保有は平均4営業日で、これより速くする出口は見つかりませんでした。' }),
    ]),
    h('details', { class: 'acc acc--inline' }, [
      h('summary', {}, [h('span', { class: 'acc__title', text: '売り指値の下限（DESIGN.md 15章）' })]),
      h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
        h('thead', {}, h('tr', {}, ['前半／後半', '以前', 'いま'].map((t) => h('th', { text: t })))),
        h('tbody', {}, [
          ['勝率', '70／71%', '86／87%'],
          ['平均（%）', '+0.47／+1.09', '+0.60／+1.26'],
          ['PF', '1.42／1.94', '1.57／2.19'],
          ['−2〜0%の負け', '14／14%', '0／0%'],
          ['0〜0.5%の勝ち', '8／6%', '22／20%'],
          ['−5%以下の負け', '9／9%', '11／10%'],
          ['口座の年率＊', '+31／+75%', '+40／+83%'],
          ['口座の最大DD＊', '−21／−18%', '−22／−19%'],
        ].map((row) => h('tr', {}, [h('th', { text: row[0] }), h('td', { class: 'num', text: row[1] }), h('td', { class: 'num', text: row[2] })]))),
      ])),
      h('p', { class: 'hint', text: '前半 2023-24・後半 2025-26。＊口座は資金5等分・同じ業種2銘柄まで。この口座の数字は「その日に約定した注文の中から上位を選ぶ」楽観的な数え方で、' +
        '置いた注文が約定しない分（約定は38%）を見ていませんでした。本番どおりに数え直すと、同じ資金5等分で前半 +9%・後半 +65%（上の「株数の決め方と口座の伸び」）。' +
        '以前は、直近4日の平均が買値より下にあるうちに平均まで戻ると、そこで小さな損を確定させていました。' +
        'その売りの多くは10営業日のうちに買値の上まで戻っていたので、売り指値は買値 +0.2%（往復のコスト0.1%を引いてもプラス）より下に置かないことにしました。' +
        '勝率の上がり分の多くは「小さな負けが小さな勝ちに変わった」もので、戻らずに損切り・期限まで行く負けは少し増えます。それでも4年のどの年も平均と PF は良くなりました。' +
        '下限 0.2〜0.5%・平均の日数 3〜5日・期限 7〜15日の18通りすべてで勝率 81〜90% で、たまたま良い一点ではありません。' }),
    ]),
    h('details', { class: 'acc acc--inline' }, [
      h('summary', {}, [h('span', { class: 'acc__title', text: '業種の中での位置（DESIGN.md 14章）' })]),
      h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
        h('thead', {}, h('tr', {}, ['押しの形', '前半 2023-24', '後半 2025-26'].map((t) => h('th', { text: t })))),
        h('tbody', {}, [
          ['業種ぐるみの押し・下げ大', '89%・+6.1%（18回）', '93%・+6.4%（46回）'],
          ['業種ぐるみの押し（上を除く）', '93%・+1.4%', '89%・+2.7%'],
          ['出遅れの押し', '84%・+0.5%', '91%・+1.5%'],
          ['ふつうの押し', '85%・+0.4%', '86%・+0.7%'],
          ['比べる業種なし', '84%・+0.4%', '84%・+0.6%'],
          ['業種の上げに沿った押し（見送り）', '75%・−0.6%', '85%・+0.05%'],
          ['見送りを除いた全体', '86%', '87%'],
        ].map((row) => h('tr', {}, [h('th', { text: row[0] }), h('td', { class: 'num', text: row[1] }), h('td', { class: 'num', text: row[2] })]))),
      ])),
      h('p', { class: 'hint', text: '勝率・1回の平均（売買コスト込み・売り指値の下限つきで数え直した数字）。見送りを入れて並べ方を変えると、資金5等分の口座で年率 +43.6% → +48.7%、最大ドローダウン −23.8% → −22.5%（2023-08〜2026-09。15章までの楽観的な口座の数え方）。' +
        '業種をでたらめに入れ替えた場合（8通り）の年率は +37〜50% で、効き目の向きは確かでも大きさには幅があります。' +
        '「業種が上げているのに遅れている銘柄を、押していない日に買う」と「上昇トレンドが崩れた銘柄を業種の急落で買う」は、検証で効かなかった（後半で負け）ので注文は出しません。' }),
    ]),
  ], null, false, 'v-rule');
}

function ledgerCard() {
  const led = (LEDGER || {}).stats || {};
  if (!(led.by_signal || []).length) return null;
  return card('発掘の入口の成績', '見つけた日の終値で買った場合', ledgerStatsTable(led),
    '売買代金・開示・テーマから機械が見つけた銘柄を、見つけた日の終値で買って5営業日持った場合（中央値）。日経平均に負けていたので、' +
    '発掘は「買う候補」ではなく監視に並べ、押し目買いの形になるのを待ちます。件数の少ない入口は数字が振れます。', false, 'v-ledger');
}

/* 自分の記録: 押し目買いの手仕舞い・保有株を売った記録・判断メモ（すべてこの端末だけ） */
function myRecordCard() {
  const done = readDone();
  const sold = lsJson(LS_HOLD_DONE);
  const kids = [];
  if (done.length) {
    const rs = done.map((d) => d.ret).filter(isNum);
    const win = rs.filter((x) => x > 0).length;
    kids.push(h('div', { class: 'decision__block decision__block--first' }, [
      h('div', { class: 'decision__bh', text: `押し目買いの手仕舞い（${done.length}回）` }),
      h('p', { class: 'stats__line', text: rs.length ? `勝率 ${Math.round(win / rs.length * 100)}%・平均 ${fmtPct(rs.reduce((a, b) => a + b, 0) / rs.length, 2)}（売買コスト ${rules().cost}% を引いた値）。` +
        '検証の数字より悪いときは、計画にない売り（損切り前の狼狽売り・売り指値より前の利確）が混ざっていないかを見直す。' : '' }),
      h('div', {}, done.slice().reverse().slice(0, 10).map((e) => h('div', { class: 'paper__row' }, [
        h('span', { class: 'paper__d num', text: md(e.out) }),
        h('span', { class: 'paper__n', text: cleanName(e.name) || e.code }),
        h('span', { class: 'paper__r num ' + cls(e.ret), text: fmtPct(e.ret, 1) }),
      ]))),
    ]));
  }
  if (sold.length) {
    kids.push(h('div', { class: 'decision__block' + (kids.length ? '' : ' decision__block--first') }, [
      h('div', { class: 'decision__bh', text: `保有株を売った記録（${sold.length}回）` }),
      h('div', {}, sold.slice().reverse().slice(0, 10).map((e) => {
        const now = (stockOf(e.code) || {}).price;
        const after = isNum(now) && isNum(e.exit) ? (now / e.exit - 1) * 100 : null;
        return h('div', { class: 'paper__row' }, [
          h('span', { class: 'paper__d num', text: md(e.out) }),
          h('span', { class: 'paper__n', text: `${cleanName(e.name) || e.code}（買値から ${fmtPct(e.ret, 1)}）` }),
          h('span', { class: 'paper__r num ' + cls(after), text: isNum(after) ? `その後 ${fmtPct(after, 1)}` : '—' }),
        ]);
      })),
    ]));
  }
  const log = logCard();
  if (!kids.length && !log) {
    return card('自分の記録', null, h('p', { class: 'hint', style: 'margin:0', text:
      '注文の「買えた」→保有銘柄の「売った」、銘柄の詳しい画面の「この判断をメモする」で、ここに自分の売買と判断が溜まります。この端末にだけ保存されます。' }), null, false, 'v-mine');
  }
  return card('自分の記録', 'この端末だけ', kids.concat(log ? [log] : []), null, false, 'v-mine');
}

function ledgerStatsTable(st) {
  const rows = (st && st.by_signal) || [];
  const min = st.min_count || 10;
  const all = st.overall || {};
  const pc = (v) => h('td', { class: 'num ' + cls(v), text: isNum(v) ? fmtPct(v, 1) : '—' });
  const rate = (v) => h('td', { class: 'num', text: isNum(v) ? Math.round(v * 100) + '%' : '—' });
  const tr = (label, r, sub) => h('tr', { class: sub ? 'bt__all' : '' }, [
    h('th', {}, [document.createTextNode(label), isNum(r.count) && r.count < min ? h('small', { class: 'bt__sub', text: '件数不足' }) : null]),
    h('td', { class: 'num', text: String(r.count ?? '—') }), pc(r.d5_median), pc(r.x5_median), rate(r.beat5 ?? null),
  ]);
  return h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
    h('thead', {}, h('tr', {}, ['入口', '件数', '5日後', '日経比', '日経に勝った'].map((t) => h('th', { text: t })))),
    h('tbody', {}, rows.map((r) => tr(r.signal, r)).concat(all.count ? [tr('全体', all, true)] : [])),
  ]));
}

/* ---- 7. 相場の温度（逆張りの物差し） ---- */
function tempGauge(temp) {
  // 0〜100 を5つの帯に塗り分け、現在値に印を付ける（色だけに頼らず帯の名前も下に出す）
  const bands = [[0, 20, 'cold', '総悲観'], [20, 38, 'cool', '悲観'], [38, 62, 'neutral', '中立'],
    [62, 80, 'warm', '楽観'], [80, 100, 'hot', '過熱']];
  const bar = h('div', { class: 'gauge' }, bands.map(([a, b, tone]) => {
    const seg = h('div', { class: 'gauge__seg gauge__seg--' + tone });
    seg.style.width = (b - a) + '%';
    return seg;
  }));
  if (isNum(temp)) {
    const mark = h('div', { class: 'gauge__mark' });
    mark.style.left = Math.max(0, Math.min(100, temp)) + '%';
    bar.appendChild(mark);
  }
  const labels = h('div', { class: 'gauge__labels' }, bands.map(([a, b, , name]) => {
    const l = h('span', { text: name });
    l.style.width = (b - a) + '%';
    return l;
  }));
  return h('div', {}, [bar, labels]);
}

function scorePips(score) {
  // −2〜+2 を5マスで。0 を中央に、+ は赤（追い風／過熱の側）、− は青（向かい風／悲観の側）
  return h('span', { class: 'pips', 'aria-label': `スコア ${score}` }, [-2, -1, 0, 1, 2].map((v) => {
    let on = false;
    if (isNum(score)) on = v === 0 ? score === 0 : (v > 0 ? score >= v : score <= v);
    return h('i', { class: 'pip' + (on ? (v > 0 ? ' pip--up' : v < 0 ? ' pip--down' : ' pip--mid') : '') });
  }));
}

/* 市況タブの相場温度: 温度計と帯・10営業日前との比較・8つの軸（畳む）と、検証の結論の一行（表は検証タブ） */
function thermoCard() {
  const th = THERMO || {};
  const mk = th.market;
  if (!mk || !isNum(mk.temp)) {
    return card('相場温度', null, h('div', { class: 'empty', text:
      mk && mk.guide ? mk.guide : '温度計のデータがまだありません。次の自動更新（寄り前・前場・大引）で作られます。' }), null, false, 'sec-temp');
  }
  const tone = ZONE_TONE[mk.tone] || 'accent';
  const diff = isNum(mk.temp_before) ? mk.temp - mk.temp_before : null;
  const aside = [
    h('span', { class: 'badge badge--' + tone, text: mk.zone }),
    h('div', { text: `追い風 ${mk.tailwind}・向かい風 ${mk.headwind}・中立 ${mk.neutral ?? (mk.n - mk.tailwind - mk.headwind)}` }),
    isNum(diff) ? h('div', { class: 'num', text: `10営業日前 ${mk.temp_before}（${diff >= 0 ? '+' : ''}${diff}）` }) : null,
  ];
  const kids = [
    h('div', { class: 'hero__label', text: `相場温度（${mk.n}軸・${mk.asof ? fmtDate(mk.asof) : ''}時点）` }),
    h('div', { class: 'hero__row' }, [
      h('div', {}, [h('div', { class: 'hero__value num', text: String(mk.temp) }),
        h('div', { class: 'gauge__caption', text: '0＝全部向かい風　100＝全部追い風' })]),
      h('div', { class: 'hero__aside' }, aside),
    ]),
    tempGauge(mk.temp),
  ];
  if (mk.consensus) {
    kids.push(h('div', { class: 'callout callout--' + (mk.consensus === '全面追い風' ? 'warn' : 'chance'),
      text: mk.consensus === '全面追い風'
        ? 'ほぼすべての軸が追い風。良い材料が出そろった「天井圏に多い形」です。ここで買いたくなる気持ちこそ要注意。'
        : 'ほぼすべての軸が向かい風。悪い材料が出そろった「大底圏に多い形」です。ここで売りたくなる気持ちこそ要注意。' }));
  }
  if (mk.turning) kids.push(h('div', { class: 'callout callout--accent', text: mk.turning }));
  kids.push(h('div', { class: 'hero__verdict', text: mk.guide }));
  const moves = [];
  if ((mk.improving || []).length) moves.push('改善: ' + mk.improving.join('・'));
  if ((mk.worsening || []).length) moves.push('悪化: ' + mk.worsening.join('・'));
  if (moves.length) kids.push(h('div', { class: 'hint', text: '10営業日前との比較　' + moves.join('　') }));
  const fs = mk.factors || [];
  if (fs.length) {
    const arrow = { '改善': '↗ 追い風の側へ', '悪化': '↘ 向かい風の側へ', '横ばい': '→ 横ばい' };
    kids.push(h('details', { class: 'fold' }, [
      h('summary', { text: `8つの軸（+ 追い風／過熱　− 向かい風／悲観）` }),
      h('div', { class: 'factors' }, fs.map((f) => {
        const subs = (f.subs || []).map((x) => x.text).filter(Boolean).join('　');
        return h('details', { class: 'factor' + (f.available ? '' : ' factor--na') }, [
          h('summary', {}, [
            h('span', { class: 'factor__label', text: f.label }),
            f.available ? scorePips(f.score) : h('span', { class: 'factor__na', text: '材料不足' }),
            h('span', { class: 'factor__chg ' + (f.change === '改善' ? 'up' : f.change === '悪化' ? 'down' : 'flat'), text: f.change ? arrow[f.change] : '' }),
          ]),
          h('div', { class: 'factor__body' }, [subs ? h('div', { text: subs }) : null, h('div', { class: 'hint', text: f.note || '' })]),
        ]);
      })),
    ]));
  }
  const bt = th.backtest;
  kids.push(h('button', { class: 'evidence', type: 'button', onclick: () => selectView('verify', 'v-thermo') }, [
    h('span', { text: (bt && bt.zones ? `検証（${fmtDate(bt.from, { year: 'numeric', month: 'numeric', timeZone: 'Asia/Tokyo' })}〜）: ${btVerdict(bt)}` : '') +
      '温度は注文の条件には使っていません（押し目買いは、相場全体が弱い日のほうがむしろ成績が良かった）。上がると買いたくなる・下がると売りたくなる衝動の逆側に立つための物差しです。' }),
    h('b', { text: '温度の検証を見る ›' })]));
  return h('section', { class: 'card hero', id: 'sec-temp' }, kids);
}

/* ==================== 急騰して混み合った銘柄・主役の入れ替わり（DESIGN.md 24章） ==================== */
function CW() { return (THERMO || {}).crowd || null; }

/* 前の引けまでの3日で急騰し、出来高が膨らんだ銘柄の一行。方向の予測ではなく「市場と違う動きをしやすい」印（検証は検証タブ） */
function crowdNote(st) {
  const cw = st && st.cw;
  if (!cw) return null;
  const X = CW() || {};
  const v = (X.verify || {}).cls || {};
  const rg = (a, k) => (a && a[0] && a[1] ? `${Math.round(Math.min(a[0][k], a[1][k]))}〜${Math.round(Math.max(a[0][k], a[1][k]))}%` : null);
  const big = (X.verify || {}).big || 3;
  const ev = rg(v.hot, 'weak')
    ? `。同じ形の銘柄は翌日、市場より${big}%以上弱くなる割合が ${rg(v.hot, 'weak')}（通常 ${rg(v.all, 'weak')}）、強くなる割合も ${rg(v.hot, 'strong')}。` +
      '向きは五分で、売買の合図ではなく、市場とずれても耐えられる株数・撤退ラインかの確認用'
    : '';
  return `${md(cw.asof)}の引けまでに3日で ${fmtPct(cw.r3, 1)}・出来高 ${cw.vr} 倍の急騰${ev}`;
}

/* 市場が上げているのに自分の銘柄が逆行している日の一行（前場のウォッチ値と日経の差。事実の指摘で、判定は変えない） */
function lagNote(am) {
  if (!am || !isNum(am.change_pct) || !isNum(am.nk) || am.nk < 1) return null;
  const ex = am.change_pct - am.nk;
  if (ex > -2) return null;
  return `日経 ${fmtPct(am.nk, 1)} に対して ${fmtPct(am.change_pct, 1)}（${fmtSigned(ex, 1)}pt）。市場が買われる日に置いていかれている。` +
    '前の営業日に買われた銘柄から、出遅れていた銘柄へ資金が移る日に起きやすい（市況の「資金の移り先」）。判定は変えない';
}

/* 市況: 前の営業日の主役が今日は市場を下回った銘柄と、その逆（相場全体が動いた日に、どこへ資金が移ったか） */
function rotationCard() {
  const r = (CW() || {}).rotation;
  if (!r || (!(r.out || []).length && !(r.into || []).length)) return null;
  const ex = (x) => h('div', { class: 'row__delta num', text: `${fmtSigned(x.prev, 1)}→${fmtSigned(x.now, 1)}pt` });
  const meta = (x) => [(x.th || []).slice(0, 2).map((t) => '#' + t).join(' ') || null];
  const kids = [h('div', { class: 'check__lh', text: `対市場（全銘柄の中央値）の差。${md(r.prev)} ${fmtSigned(r.mkt[0], 1)}% → ${md(r.asof)} ${fmtSigned(r.mkt[1], 1)}%` })];
  const section = (title, rows, n) => {
    if (!rows.length) return;
    kids.push(h('div', { class: 'check__lh', style: 'margin-top:8px', text: `${title}（${n}銘柄）` }));
    kids.push(h('div', { class: 'rows rows--norank' }, rows.map((x, i) => stockRow({ ...x, price: null }, i, { rank: false, meta, right: ex }))));
  };
  section(`前の営業日に市場を +${r.lead}pt 以上上回り、今日は ${r.lag}pt 以下に置いていかれた`, r.out || [], r.n_out);
  section(`前の営業日は市場以下で、今日は +${r.up}pt 以上買われた`, r.into || [], r.n_into);
  const th = (r.themes || []).filter((t) => t.out + t.into > 1);
  if (th.length) kids.push(h('p', { class: 'hint', text: 'テーマ別: ' + th.map((t) => `#${t.theme}（置き去り ${t.out}・買われ ${t.into}）`).join('　') }));
  kids.push(h('p', { class: 'hint', text: `売買代金の20日平均が ${r.min_tv} 億円以上の銘柄だけ。今日の動きの事実の並べ替えで、明日の予測ではない。` +
    '持っている銘柄が「置き去り」に入っていたら、まず資金の移り先の問題か、個別の悪材料（開示・ニュース）があったかを分けて確認する。' +
    '置き去りになった銘柄の翌日・5日後の動きは全銘柄と変わらなかった（過去2年）ので、戻りを待つ・慌てて投げる根拠にはならない。' }));
  return card('資金の移り先', `${md(r.asof)}の引け`, kids, null, false, 'sec-rotation');
}

function crowdProofCard() {
  const X = CW();
  const v = X && X.verify;
  if (!v) return card('急騰した銘柄の翌日', null, h('div', { class: 'empty', text: '検証は次の収集から出ます' }), null, false, 'v-crowd');
  const cells = (a) => (a ? [`${a.n.toLocaleString('ja-JP')}件`, fmtSigned(a.avg, 2), `${a.up}%`, `${a.weak}%`, `${a.strong}%`] : ['—', '—', '—', '—', '—']);
  const rows = [['hot', '急騰して混み合った銘柄'], ['all', '全銘柄']];
  const kids = [
    h('div', { class: 'check__lh', text: `${X.rules.text}（引けの形）。翌日の、全銘柄の中央値との差。${md(v.from)}〜${md(v.to)}、前半は ${md(v.split)} まで` }),
    h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['', '期間', '件数', '平均%', '上回った', `${v.big}%以上弱い`, `${v.big}%以上強い`].map((t, i) => h('th', { text: t, style: i ? null : 'text-align:left' })))),
      h('tbody', {}, rows.flatMap(([k, label]) => [0, 1].map((half) => h('tr', {}, [
        h('th', { text: half ? '' : label }), h('td', { text: half ? '後半' : '前半' }),
        ...cells((v.cls[k] || [])[half]).map((t) => h('td', { class: 'num', text: t })),
      ])))),
    ])),
    h('p', { class: 'hint', text: '見方: 平均はプラス（急騰した銘柄は翌日も平均では市場を上回った）で、上回った割合も五分。つまり「過熱だから売る」は成り立たない。' +
      '変わるのは振れ幅で、市場より3%以上弱くなる日が全銘柄の3〜5倍、強くなる日も同じだけ増える。前半・後半で同じ。' }),
    h('p', { class: 'hint', text: '同じ検証で、次の形は天井のサインにならなかった: 上ヒゲ、高値から −25% の戻り、2日RSI 95 超、売買代金の上位が連続（フジクラは70営業日ずっと上位で情報が無い）。' +
      '今の採用銘柄だけで測るので、上げ続けた銘柄に偏る。予測ではなく、同じ形の銘柄が過去にどうなったかの並びです。' }),
  ];
  return card('急騰した銘柄の翌日', '混み合いの印の根拠', kids,
    '印は保有銘柄と銘柄の画面に出ます。注文の条件には使っていません。', false, 'v-crowd');
}

function tempChart(series) {
  // 温度の推移（過去250営業日）。帯の境目に点線。自前の SVG なので html: を使う
  if (!Array.isArray(series) || series.length < 10) return null;
  const W = 320, H = 90, n = series.length;
  const x = (i) => (i / (n - 1)) * W;
  const y = (v) => H - (v / 100) * H;
  const pts = series.map((p, i) => `${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ');
  const grid = [20, 38, 62, 80].map((v) => `<line x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" class="tc__grid"/>` +
    `<text x="${W - 2}" y="${y(v) - 2}" class="tc__tick" text-anchor="end">${v}</text>`).join('');
  return h('div', { class: 'tc' }, [
    h('div', { html: `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="tc__svg" role="img" aria-label="温度の推移">` +
      grid + `<polyline points="${pts}" class="tc__line" vector-effect="non-scaling-stroke"/></svg>` }),
    h('div', { class: 'tc__axis' }, [h('span', { text: md(series[0][0]) }), h('span', { text: md(series[n - 1][0]) })]),
  ]);
}

function backtestCard(bt) {
  if (!bt || !bt.zones) return null;
  const all = bt.all || {};
  const cellv = (v, pct) => h('td', { class: 'num ' + (pct ? cls(v) : ''), text: isNum(v) ? (pct ? fmtPct(v, 1) : String(v) + (pct === false ? '%' : '')) : '—' });
  const rows = bt.zones.filter((z) => z.days).map((z) => h('tr', {}, [
    h('th', {}, [h('span', { class: 'badge badge--' + (ZONE_TONE[z.tone] || 'accent'), text: z.zone })]),
    h('td', { class: 'num', text: `${z.days}日／${z.episodes}回` }),
    cellv(z.median20, true), cellv(z.up20, false), cellv(z.median60, true), cellv(z.up60, false),
  ]));
  (bt.consensus || []).filter((c) => c.days).forEach((c) => rows.push(h('tr', { class: 'bt__cons' }, [
    h('th', { text: c.consensus }), h('td', { class: 'num', text: `${c.days}日／${c.episodes}回` }),
    cellv(c.median20, true), cellv(c.up20, false), cellv(c.median60, true), cellv(c.up60, false),
  ])));
  rows.push(h('tr', { class: 'bt__all' }, [
    h('th', { text: '全期間' }), h('td', { class: 'num', text: `${all.days}日` }),
    cellv(all.median20, true), cellv(all.up20, false), cellv(all.median60, true), cellv(all.up60, false),
  ]));
  const verdict = btVerdict(bt);
  return card('相場温度の検証（この読みは当たるのか）', `${fmtDate(bt.from, { year: 'numeric', month: 'numeric', timeZone: 'Asia/Tokyo' })}〜の検証`, [
    h('p', { class: 'bt__verdict', text: verdict }),
    tempChart(bt.series),
    h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
      h('thead', {}, h('tr', {}, ['帯', '日数／局面', '20日後', '上昇', '60日後', '上昇'].map((t) => h('th', { text: t })))),
      h('tbody', {}, rows),
    ])),
  ], bt.note + ' 数字は日経平均の騰落の中央値と、上昇した日の割合。予測ではありません。', false, 'v-thermo');
}

/* 仮説（冷たいほど後が良い）が成り立っているかを、数字から1行で言う。都合の悪い向きもそのまま書く */
function btVerdict(bt) {
  const z = Object.fromEntries(bt.zones.map((r) => [r.zone, r]));
  const cold = [z['総悲観'], z['悲観']].filter((r) => r && isNum(r.median60) && r.n60 >= 10);
  const hot = [z['過熱'], z['楽観']].filter((r) => r && isNum(r.median60) && r.n60 >= 10);
  if (!cold.length || !hot.length) return 'まだ比べられるだけの日数がありません。';
  const c = Math.max(...cold.map((r) => r.median60)), w = Math.min(...hot.map((r) => r.median60));
  return c > w
    ? `この期間では、温度が低い日のほうが60日後の日経平均が良かった（悲観側 ${fmtPct(c, 1)} ＞ 楽観側 ${fmtPct(w, 1)}）。逆張りの見方と整合します。`
    : `この期間では、温度が高い日のほうが60日後も良かった（楽観側 ${fmtPct(w, 1)} ≧ 悲観側 ${fmtPct(c, 1)}）。強い上昇相場では「過熱＝天井」とは限らない点に注意。`;
}

/* 日経の業種の温度（表）。業種タブの「日経の業種」の切り替えの1つ */
function sectorTableBody(sec) {
  if (!Array.isArray(sec) || !sec.length) return null;
  const rows = [...sec].sort((a, b) => (b.r20 ?? -99) - (a.r20 ?? -99));
  return foldable((n) => h('div', { class: 'tablewrap' }, h('table', { class: 'bt bt--sec' }, [
    h('thead', {}, h('tr', {}, ['業種', '判定', '5日', '20日', 'RSI', 'マクロ'].map((t) => h('th', { text: t })))),
    h('tbody', {}, rows.slice(0, n).map((s) => h('tr', {}, [
      h('th', { text: s.sector }),
      h('td', {}, h('span', { class: 'badge badge--' + (CLASS_TONE[s.class] || ''), text: s.class })),
      h('td', { class: 'num ' + cls(s.r5), text: fmtPct(s.r5, 1) }),
      h('td', { class: 'num ' + cls(s.r20), text: fmtPct(s.r20, 1) }),
      h('td', { class: 'num', text: isNum(s.rsi) ? String(Math.round(s.rsi)) : '—' }),
      h('td', { text: s.macro ? s.macro.label : '—' }),
    ]))),
  ])), rows.length, 12, '全業種');
}

function themeToneCard(themes) {
  const rows = (themes || []).filter((t) => t.label);
  if (!rows.length) return null;
  return card('テーマの論調と値動き', '見出し3営業日', h('div', {}, rows.slice(0, 8).map((t) => h('div', { class: 'signal' }, [
    h('div', { class: 'signal__name', text: '#' + t.theme }),
    h('div', { class: 'signal__right num' }, [h('span', { class: 'track' }, [
      h('span', {}, ['5日 ', h('b', { class: cls(t.r5), text: fmtPct(t.r5, 1) })]),
      h('span', {}, ['20日 ', h('b', { class: cls(t.r20), text: fmtPct(t.r20, 1) })]),
    ])]),
    h('div', { class: 'signal__tags' }, [
      h('span', { class: 'badge badge--' + (t.tone === 'warn' ? 'warn' : t.tone === 'chance' ? 'ok' : 'accent'), text: t.label }),
      (t.news_pos || t.news_neg) ? h('span', { class: 'badge', text: `見出し 強気${t.news_pos}／弱気${t.news_neg}` }) : null,
    ]),
  ]))), '株探・市況記事の見出しの強気／弱気の語とテーマの値動きの組み合わせ。好材料一色で過熱 → 出尽くしに注意、悪材料が続くのに下げ止まり → 出尽くしの兆し。', true, 'sc-themes');
}

function trackCard(track) {
  const rows = (track || []).filter((r) => r.count && !['押し目候補', '売られすぎ反発', '相対力リーダー', '深押し', '上向き転換'].includes(r.kind));
  if (!rows.length) return null;
  const expect = { '高値掴み注意': '下がれば当たり', '好材料出尽くし': '下がれば当たり', '過熱業種': '下がれば当たり' };
  return card('警告と業種の判定の成績', '中央値', h('div', { class: 'tablewrap' }, h('table', { class: 'bt' }, [
    h('thead', {}, h('tr', {}, ['判定', '件数', '5日後', '日経比', '20日後'].map((t) => h('th', { text: t })))),
    h('tbody', {}, rows.map((r) => h('tr', {}, [
      h('th', {}, [document.createTextNode(r.kind), expect[r.kind] ? h('small', { class: 'bt__sub', text: expect[r.kind] }) : null]),
      h('td', { class: 'num', text: `${r.count}（${r.n5}）` }),
      h('td', { class: 'num ' + cls(r.d5_median), text: fmtPct(r.d5_median, 1) }),
      h('td', { class: 'num ' + cls(r.x5_median), text: fmtPct(r.x5_median, 1) }),
      h('td', { class: 'num ' + cls(r.d20_median), text: fmtPct(r.d20_median, 1) }),
    ]))),
  ])), '大引で出した判定を記録し、5日後・20日後の騰落を追っています（括弧内は5日後が出た件数）。同じ判定は20営業日のあいだ記録し直しません。', false, 'v-track');
}

/* ---- 日足チャート（銘柄シート用）----
   docs/data/cache/bars.json（終値。約130営業日）を初めて使うときだけ読む。
   終値・5日線・25日線に、注文の指値・損切り・売りの目安（注文対象の日）か、待つ価格と200日線（待つ日）を横線で重ねる。 */
let BARS = null;
let barsLoading = null;
function loadBars() {
  if (BARS) return Promise.resolve(BARS);
  if (!barsLoading) barsLoading = fetchJson('data/cache/bars.json').then((b) => { BARS = b; if (!b) barsLoading = null; return b; });
  return barsLoading;
}

function movingAvg(xs, n) {
  const out = [];
  let sum = 0;
  xs.forEach((v, i) => {
    sum += v;
    if (i >= n) sum -= xs[i - n];
    out.push(i >= n - 1 ? sum / n : null);
  });
  return out;
}

function priceChartSvg(closes, lines) {
  const n = closes.length;
  const ma5 = movingAvg(closes, 5), ma25 = movingAvg(closes, 25);
  const vals = closes.concat(lines.map((l) => l[1]));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const padv = (hi - lo) * 0.06 || hi * 0.02;
  lo -= padv; hi += padv;
  const W = 320, H = 150, R = 40;
  const x = (i) => (i / Math.max(1, n - 1)) * (W - R);
  const y = (v) => H - ((v - lo) / (hi - lo)) * H;
  const path = (arr) => {
    let d = '', pen = false;
    arr.forEach((v, i) => {
      if (!isNum(v)) { pen = false; return; }
      d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1);
      pen = true;
    });
    return d;
  };
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="pc__svg" role="img" aria-label="日足と注文の価格">`;
  // 横線のラベルが重ならないよう、上から順に最低 10px ずつ離す
  const labels = lines.map(([k, v, label]) => ({ k, label, ly: y(v) + 3 })).sort((a, b) => a.ly - b.ly);
  labels.forEach((l, i) => { if (i && l.ly - labels[i - 1].ly < 10) l.ly = labels[i - 1].ly + 10; });
  lines.forEach(([k, v]) => {
    svg += `<line x1="0" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="pc__lv pc__lv--${k}" vector-effect="non-scaling-stroke"/>`;
  });
  labels.forEach((l) => {
    svg += `<text x="${W - 2}" y="${l.ly.toFixed(1)}" class="pc__lt pc__lt--${l.k}" text-anchor="end">${l.label}</text>`;
  });
  svg += `<path d="${path(ma25)}" class="pc__ma75" vector-effect="non-scaling-stroke"/>` +
    `<path d="${path(ma5)}" class="pc__ma25" vector-effect="non-scaling-stroke"/>` +
    `<path d="${path(closes)}" class="pc__close" vector-effect="non-scaling-stroke"/></svg>`;
  return svg;
}

function priceChart(code, lines) {
  const box = h('div', { class: 'pc' }, [h('div', { class: 'hint', text: '日足を読み込み中…' })]);
  loadBars().then((b) => {
    box.textContent = '';
    const arr = b && b.stocks && b.stocks[code];
    const dates = (b && b.dates) || [];
    if (!arr || !dates.length) { box.appendChild(h('div', { class: 'hint', text: '日足がありません' })); return; }
    const pts = dates.map((d, i) => [d, arr[i]]).filter((p) => isNum(p[1]));
    if (pts.length < 10) { box.appendChild(h('div', { class: 'hint', text: '日足がまだ足りません' })); return; }
    // 終値の幅から大きく外れる横線（何年も前の買値など）を入れると線が潰れるので、外して凡例に値だけ書く
    const closes = pts.map((p) => p[1]);
    const lo = Math.min(...closes), hi = Math.max(...closes);
    const nums = lines.filter((l) => isNum(l[1]));
    const inRange = nums.filter((l) => l[1] >= lo * 0.75 && l[1] <= hi * 1.25);
    const outside = nums.filter((l) => !inRange.includes(l));
    box.appendChild(h('div', { html: priceChartSvg(closes, inRange) }));
    box.appendChild(h('div', { class: 'pc__legend' }, [
      h('span', { class: 'pc__k pc__k--close', text: '終値' }), h('span', { class: 'pc__k pc__k--ma25', text: '5日線' }),
      h('span', { class: 'pc__k pc__k--ma75', text: '25日線' }),
      outside.length ? h('span', { text: outside.map((l) => `${l[2]} ${fmtTick(l[1])}`).join('・') + '（図の外）' }) : null,
      h('span', { class: 'pc__axis', text: `${md(pts[0][0])}〜${md(pts[pts.length - 1][0])}（確定した終値）` }),
    ]));
  });
  return box;
}

/* ---- 買う前／売る前チェック（銘柄シートの中で使う） ---- */
function impulseCheck(code, intent) {
  const th = THERMO || {};
  const s = (th.stocks || {})[code];
  if (!s) return null;
  const r = rules();
  const sw = s.sw || {};
  const mk = th.market || {};
  const sec = (th.sectors || []).find((x) => x.sector === s.s);
  const nkD1 = (() => { const d = latestSession(); const nk = d && (d.indices || {}).nikkei; return nk && !nk.stale ? nk.change_pct : null; })();
  const order = [...((SW() || {}).orders || []), ...((SW() || {}).more || [])].find((o) => o.code === code) || null;
  const pos = readPos().find((p) => p.code === code) || null;
  const bad = [], good = [], plan = [];
  const ev = s.ev;
  const ps = pos ? posSell(pos, sw) : { ready: false };   // 約定日の引けのあとから売り指値が決まる
  let level, title;
  const mo = midOrderOf(code);
  const mdx = sw.md || null;
  if (intent === 'buy' && mdx) {
    // 中期の形（大きなトレンドの中の1〜3か月の調整）の銘柄は、中期の押し目の計画だけで判定する（1銘柄に1つの計画）
    const shape = `1年の強さ 上位${Math.max(1, 100 - mdx.rank)}%・60日高値 ${fmtPrice(mdx.hi)}円から${mdx.age}営業日・${fmtPct(mdx.dd, 1)} の調整中（調整の安値 ${fmtPrice(mdx.low)}円）`;
    if (mo) {
      good.push(`中期の押し目の注文対象。${shape}`);
      good.push(`指値 ${fmtPrice(mo.limit)}円（終値${fmtPct(mo.to_limit, 1)}）・損切り ${fmtPrice(mo.stop)}円（調整の安値−${r.mid_stop_atr}ATR）・${r.mid_hold}営業日持つ`);
    } else {
      bad.push(`${shape}。まだ押していない` + (isNum(mdx.trig) ? `（終値が ${fmtPrice(mdx.trig)}円 未満で引けた翌営業日に中期の注文が出る）` : ''));
    }
    if (isNum(s.d1) && s.d1 >= 3) bad.push(`直近 ${fmtPct(s.d1, 1)} の急騰。調整明けを先回りして飛び乗るより、押した日の指値で`);
    if (ev && ev.dir === 'down' && !String(ev.label || '').startsWith('悪材料出尽くし')) bad.push(`下方修正・減配の開示（${md(ev.date)}）。調整の理由が業績なら、見立ての前提が違う`);
    if (mo) {
      level = bad.length >= 2 ? 'wait' : 'ok';
      title = level === 'ok' ? `中期の注文どおりに（指値で・1件は資金の${r.slot_pct}%）` : '中期の注文対象。ただし気になる点あり（1件を小さく）';
      plan.push(`${orderDay((SW() || {}).asof)?.text || '次の営業日だけ有効'}。指値 ${fmtPrice(mo.limit)}円で、成行では買わない`);
      plan.push(`IFD で損切りの逆指値 ${fmtPrice(mo.stop)}円 を入れる。売りの指値は置かず、${r.mid_hold}営業日目の引けで売る（中期の保有は${r.mid_slots}銘柄まで）`);
      plan.push('勝率は3割前後で、損切りで終わる回が多い。少数の大きく伸びた銘柄が平均を作るので、損切りを動かさず、伸びた銘柄を早く売らない');
    } else {
      level = 'stop';
      title = '今日は買わない（調整中。押した日に中期の注文が出る）';
      if (isNum(mdx.trig)) plan.push(`待つ価格: 終値 ${fmtPrice(mdx.trig)}円 未満。そこで引けたら、翌営業日の中期の注文に出ます`);
      plan.push('調整明けを当てにして上げた日に成行で買うより、押した日の指値で。調整の安値を割ったら見立て違い');
    }
    return { s, level, title, bad, good, plan };
  }
  if (intent === 'buy') {
    // まず、このルールでの位置（買う側の判断はこのルールだけ）
    if (sw.st === 'signal' && order) {
      good.push(`短期の押し目買いの注文対象。指値 ${fmtPrice(order.limit)}円（終値${fmtPct(order.to_limit, 1)}）・損切り ${fmtPrice(order.stop)}円・売りの目安 ${fmtPrice(order.sell)}円`);
      const pi = PEER_INFO[sw.pc];
      if (pi && pi.stat) good.push(`${pi.label}（${peerLine(sw.g, sw.g20, sw.rel)}）。4年の検証で勝率 ${pi.stat}`);
    } else if (sw.st === 'signal' && sw.pc === 'hot') {
      bad.push(`押した形だが、業種の上げに沿った押し（${peerLine(sw.g, sw.g20, sw.rel)}）。4年の検証で勝率 ${PEER_INFO.hot.stat}・1回の平均 ${HOT_AVG} とほかの押しより弱いので、注文は見送り`);
    } else if (sw.st === 'wait' && isNum(sw.trig)) {
      bad.push(`まだ注文対象ではない。終値が ${fmtPrice(sw.trig)}円 未満（${fmtPct(sw.to, 1)}）で引けた翌営業日に、指値の注文が出る` + (sw.ok ? '' : '（ただしそこまで下げると上昇トレンドの条件も割れる）'));
    } else if (sw.st === 'out') {
      bad.push(`上昇トレンドではない（200日線 ${fmtPrice(sw.ma200)}円の下、または50日線が200日線の下）。このルールでは買わない`);
    } else if (sw.st === 'thin') {
      bad.push(`売買代金（20日平均 ${isNum(sw.tv) ? sw.tv.toFixed(1) : '—'}億円）が少ないか、${r.min_price}円未満の低位株。このルールでは買わない`);
    } else if (!sw.st) {
      bad.push('四本値の日足がまだ無いので、このルールでの位置を出せない');
    }
    if (isNum(s.r5) && s.r5 >= 10) bad.push(`5日で ${fmtPct(s.r5, 1)}。「上がると買いたくなる」いつものパターンに当てはまる`);
    if (isNum(s.d1) && s.d1 >= 3) bad.push(`直近 ${fmtPct(s.d1, 1)} の急騰。当日の急騰に飛び乗るのは高値掴みの典型`);
    if (isNum(s.rsi) && s.rsi >= 70) bad.push(`RSI(14) ${Math.round(s.rsi)}。買われすぎの水準`);
    if (isNum(s.dev25) && s.dev25 >= 10) bad.push(`25日線から ${fmtPct(s.dev25, 1)}。平均への戻り（押し）が起きやすい距離`);
    if (sec && sec.class === '過熱') bad.push(`業種（${sec.sector}）も短期で過熱`);
    if (ev && ev.label === '好材料出尽くし') bad.push(`上方修正のあと ${fmtPct(ev.since, 1)}。好材料でも上がらない＝出尽くし`);
    if (isNum(mk.temp) && mk.temp >= 80) bad.push(`相場全体が「${mk.zone}」（温度 ${mk.temp}）。良い材料が出そろった局面`);
    if (ev && ev.label.startsWith('悪材料出尽くし')) good.push(`下方修正のあとも ${fmtPct(ev.since, 1)}。悪材料で下げない＝アク抜け`);
    if (sw.st === 'signal' && order) {
      level = bad.length >= 2 ? 'wait' : 'ok';
      title = level === 'ok' ? `注文どおりに（指値で・1件は資金の${r.slot_pct}%）` : '注文対象。ただし気になる点あり（1件を小さく）';
      plan.push(`${orderDay((SW() || {}).asof)?.text || '次の営業日だけ有効'}。指値 ${fmtPrice(order.limit)}円で、成行では買わない`);
      plan.push(`IFD で置くと、約定と同時に損切りの逆指値 ${fmtPrice(order.stop)}円 が入る。翌日からは毎朝、売りの指値と損切りを OCO で置き直す（売買タブの保有銘柄）`);
    } else {
      level = 'stop';
      title = sw.st === 'wait' ? '今日は買わない（注文が出るまで待つ）'
        : sw.st === 'signal' && sw.pc === 'hot' ? '買わない（業種の上げに沿った押しは見送り）' : '買わない（このルールの形ではない）';
      if (sw.st === 'wait' && isNum(sw.trig)) plan.push(`待つ価格: 終値 ${fmtPrice(sw.trig)}円 未満。そこで引けたら、翌営業日の注文に出ます`);
      if (sw.pc === 'hot') plan.push(`業種の上げが一服して業種ぐるみで押すか、この銘柄が業種より ${Math.abs(r.peer_lag)}pt 以上遅れて押した日（出遅れの押し）なら注文が出ます`);
      plan.push('上がっている日に成行で買うのは、検証でいちばん負けやすかった形（高値に近い強い銘柄を追う・話題になった日に買う）');
    }
  } else {
    const downEv = ev && ev.dir === 'down' && !String(ev.label || '').startsWith('悪材料出尽くし');
    const hd = s.hd;
    const X = HOLDX() || {};
    const vd = hd ? (X.verdicts || {})[hd.v] : null;
    const own = readHold().find((q) => q.code === code) || null;
    if (pos && isMidPos(pos)) {
      if (isNum(s.price) && s.price > pos.stop) bad.push(`中期の押し目で買った銘柄。損切りの価格（${fmtPrice(pos.stop)}円＝調整の安値−${r.mid_stop_atr}ATR）はまだ割っていない。計画どおりなら、売るのは損切りか${r.mid_hold}営業日目の引けのどちらか（上げても売りの指値は置かない）`);
      else good.push(`損切りの価格（${fmtPrice(pos.stop)}円）を割っている。調整明けの見立てが外れたので、計画どおり手仕舞う`);
    } else if (pos) {
      const stop = pos.stop;
      if (isNum(s.price) && s.price > stop) bad.push(`損切りの価格（${fmtPrice(stop)}円）はまだ割っていない。計画どおりなら、売るのは売り指値（${ps.ready ? fmtTick(ps.v) + '円' : '約定日の引けのあとに決まる'}）か損切りか期限のどれか`);
      else good.push(`損切りの価格（${fmtPrice(stop)}円）を割っている。計画どおり手仕舞う`);
    } else if (vd) {
      // 保有株の判定（hold.py）。引けの形（20日の騰落と2日RSI）だけで決まる。買値は使わない
      const ev20 = holdEvidence(hd);
      const why = `${vd.text}（20日 ${fmtPct(hd.r20, 1)}・2日RSI ${isNum(hd.rsi2) ? Math.round(hd.rsi2) : '—'}）`;
      if (hd.v === 'hold' || hd.v === 'wait') bad.push(why + (ev20 ? `。${ev20}` : ''));
      else if (hd.v !== 'flat') good.push(why + (ev20 ? `。${ev20}` : ''));
      const stop = holdStop(own || { entry: null }, hd);
      if (own && isNum(stop.v) && isNum(s.price) && s.price <= stop.v) good.push(`${stop.own ? '決めた' : '目安の'}撤退ライン（${fmtTick(stop.v)}円）を割っている`);
    }
    if (isNum(s.d1) && s.d1 <= -3 && !downEv) bad.push(`直近 ${fmtPct(s.d1, 1)}。ただし下方修正などの個別の悪材料は見当たらない`);
    if (isNum(s.d1) && s.d1 < 0 && ((isNum(nkD1) && nkD1 < 0) || (sec && isNum(sec.d1) && sec.d1 < 0))) bad.push('相場・業種と一緒の下げ（地合いの下げ）。狼狽売りになりやすい');
    if (!vd) {
      if (isNum(s.rsi) && s.rsi <= 30) bad.push(`RSI(14) ${Math.round(s.rsi)}。売られすぎ。ここで売ると反発を取り逃がしやすい`);
      if (isNum(s.dev25) && s.dev25 <= -10) bad.push(`25日線から ${fmtPct(s.dev25, 1)}。平均への戻りが起きやすい距離`);
      if ((s.cls || []).includes('下落トレンド') && isNum(s.r60) && s.r60 <= -15) good.push(`60日で ${fmtPct(s.r60, 1)} の下落トレンド。戻りで減らすのは合理的`);
      if (isNum(mk.temp) && mk.temp <= 38) bad.push(`相場全体が「${mk.zone}」（温度 ${mk.temp}）。悪材料が出そろった局面は底に近いことが多い`);
      if ((s.cls || []).includes('高値掴み注意')) good.push('短期で上がりすぎ。上がったところで一部を売るのは逆張りの基本（良いニュースで売る）');
      if (isNum(mk.temp) && mk.temp >= 80) good.push(`相場全体が「${mk.zone}」。一部の利益確定は合理的`);
    }
    if (ev && ev.label.startsWith('悪材料出尽くし')) bad.push(`下方修正のあと ${fmtPct(ev.since, 1)}。悪材料で下げない＝アク抜け`);
    if (downEv) good.push(`下方修正・減配の開示（${md(ev.date)}）。業績の前提が変わったなら、保有理由を見直すのは合理的`);
    if (ev && ev.label === '好材料出尽くし') good.push(`上方修正でも ${fmtPct(ev.since, 1)}。材料出尽くしの売りが出ている`);
    if (!pos && vd) {
      // 判定を見出しにする（同じ銘柄に保有株カードと違うことを言わない）
      level = { hold: 'stop', wait: 'stop', trim_limit: 'wait', trim: 'wait', trim_now: 'ok', flat: 'wait' }[hd.v] || 'wait';
      title = { hold: '売らない（勝ちを早く売らない）', wait: '今は売らない（急落のあとは反発しやすい）',
        trim_limit: '減らすなら売り指値で（寄りの成行で投げない）', trim: '減らすなら売り指値で', trim_now: '減らすなら戻った今',
        flat: '形からは差がない（撤退ラインと業績の前提で）' }[hd.v] || vd.label;
      if (downEv && (hd.v === 'hold' || hd.v === 'wait' || hd.v === 'flat')) { level = 'wait'; title += '。ただし業績の前提を見直す'; }
      plan.push(holdTodo(hd, holdStop(own || { entry: null }, hd), (orderDay(hd.asof) || {}).label || '次の営業日'));
      if (!own) plan.push('上の「＋ 保有銘柄に入れる」で買値と株数を入れると、撤退ラインと含み損益も並べて出します');
    } else {
      if (bad.length >= 2 && !good.length) { level = 'stop'; title = '今日は売らない（一晩おく）'; }
      else if (bad.length && good.length) { level = 'wait'; title = '売るなら半分だけ'; }
      else if (good.length) { level = 'ok'; title = '売る理由がある（計画どおりに）'; }
      else { level = 'wait'; title = '急いで売る理由は見当たらない'; }
      if (pos && isMidPos(pos)) plan.push(`計画の売り: 損切り ${fmtPrice(pos.stop)}円／${r.mid_hold}営業日目の引け（売りの指値は置かない）`);
      else if (pos) plan.push(`計画の売り: 指値 ${ps.ready ? fmtTick(ps.v) + `円（毎朝置き直す・買値+${r.floor}%より下には置かない）` : '約定日の引けのあとに出る'}／損切り ${fmtPrice(pos.stop)}円／${r.max_hold}営業日で引け`);
      if (level === 'stop') plan.push('成行で売らず、翌日の寄り付き後まで待って同じチェックをもう一度');
      if (!pos) plan.push('売るなら「どこまで下がったら売るか」を先に決め、その価格で機械的に');
    }
  }
  return { s, level, title, bad, good, plan };
}

function readLog() {
  try { return JSON.parse(localStorage.getItem(LS_GUARD) || '[]') || []; } catch (e) { return []; }
}
function writeLog(log) {
  try { localStorage.setItem(LS_GUARD, JSON.stringify(log.slice(-60))); } catch (e) { /* 保存できない環境は無視 */ }
}

/* 判断メモ（銘柄シートの「この判断をメモする」で溜まる）。検証タブの「自分の記録」の中に出す */
function logCard() {
  const log = readLog();
  if (!log.length) return null;
  const rows = [...log].reverse().slice(0, 20).map((e) => {
    const now = (stockOf(e.code) || {}).price;
    const ch = isNum(now) && isNum(e.price) ? (now / e.price - 1) * 100 : null;
    let read = '';
    if (isNum(ch)) {
      if (e.intent === 'buy') read = e.level === 'stop' ? (ch < 0 ? '見送って正解' : '見送った後も上昇') : (ch >= 0 ? '買って正解' : '買った後に下落');
      else read = e.level === 'stop' ? (ch >= 0 ? '売らずに正解' : '売らなかった後も下落') : (ch <= 0 ? '売って正解' : '売った後に上昇');
    }
    return h('button', { class: 'signal signal--btn', type: 'button', onclick: () => openStock(e.code) }, [
      h('div', { class: 'signal__name', text: `${cleanName(e.name)}（${e.code}）` }),
      h('div', { class: 'signal__right num' }, [h('b', { class: cls(ch), text: isNum(ch) ? fmtPct(ch, 1) : '—' }),
        h('small', { text: `${md(e.at)} ${fmtPrice(e.price)}円 → ${isNum(now) ? fmtPrice(now) : '—'}円` })]),
      h('div', { class: 'signal__tags' }, [
        h('span', { class: 'badge ' + (e.intent === 'buy' ? 'badge--up' : 'badge--down'), text: e.intent === 'buy' ? '買いたい' : '売りたい' }),
        h('span', { class: 'badge badge--accent', text: e.verdict }),
        read ? h('span', { class: 'badge', text: read }) : null,
      ]),
    ]);
  });
  return h('div', { class: 'decision__block' }, [
    h('div', { class: 'decision__bh', text: `判断メモ（${log.length}件）` }),
    h('p', { class: 'hint', style: 'margin:0 0 4px', text: '「見送った判断」「売らなかった判断」がその後どうなったかを振り返るためのものです。' }),
    h('div', { class: 'memo' }, rows),
    h('button', { class: 'more', type: 'button', text: 'メモを消す', onclick: () => {
      if (confirm('判断メモをすべて消しますか？')) { writeLog([]); refreshPlan(); }
    } }),
  ]);
}

/* ---- 0. 市況の一行（売買タブの先頭）。いまの時間帯にやること・見立ての見出し・主な数字。押すと市況タブ ---- */
const PHASE_TODO = {
  pre: '寄りの前: 注文（指値）と、保有銘柄の売り指値・損切りを置く',
  am: '場中: 判定は変えない。決めた価格（指値・損切り・撤退ライン）に届いたかだけを見る',
  lunch: '昼休み: 後場も判定は変えない（前場の形から後場の騰落は読めなかった）',
  pm: '後場: 決めた価格（売り指値・損切り・撤退ライン）だけで動く',
  closed: '休場日: 次の営業日の注文と売り指値を確かめておく',
};
function pulseCard() {
  const slots = (DATA && DATA.slots) || {};
  const ls = latestSlot();
  const d = ls ? (slots[ls].data || {}) : {};
  const ph = marketPhase();
  const sw = SW();
  const fresh = sw && sw.asof === isoToday();
  const todo = ph === 'after'
    ? (fresh ? '引けのあと: 今日の引けで、次の営業日の注文と売り指値が出ています' : '引けのあと: 大引の更新（夕方〜夜）で、次の営業日の注文と売り指値が出ます')
    : PHASE_TODO[ph];
  const picked = pickCommentary(d);
  const stats = [];
  if (ls === 'preopen') {
    const io = d.implied_open || {}, spx = (d.us || {}).spx, jpy = (d.macro || {}).usdjpy;
    stats.push(stat('想定オープン', fmtPct(io.gap_pct), isNum(io.gap) ? `${fmtSigned(io.gap, 0)}円` : null, cls(io.gap_pct)));
    if (spx) stats.push(stat('S&P500', fmtPct(spx.change_pct), fmtNum(spx.last, 0), cls(spx.change_pct)));
    if (jpy) stats.push(stat('ドル円', fmtNum(jpy.last, 2), fmtPct(jpy.change_pct), cls(jpy.change_pct)));
  } else if (ls) {
    const nk = (d.indices || {}).nikkei, tp = (d.indices || {}).topix, br = d.breadth;
    if (nk) stats.push(stat('日経平均', fmtPct(nk.change_pct), fmtNum(nk.close, 0), cls(nk.change_pct)));
    if (tp) stats.push(stat('TOPIX', fmtPct(tp.change_pct), fmtNum(tp.close, 0), cls(tp.change_pct)));
    if (br) stats.push(stat('騰落', `${br.up}／${br.down}`, `上昇 ${br.up_ratio}%`, br.up > br.down ? 'up' : br.up < br.down ? 'down' : 'flat'));
  }
  const mk = (THERMO || {}).market;
  if (mk && isNum(mk.temp) && stats.length < 4) stats.push(stat('温度', String(mk.temp), mk.zone, ZONE_TONE[mk.tone] === 'up' ? 'up' : ZONE_TONE[mk.tone] === 'down' ? 'down' : ''));
  return h('button', { class: 'card pulse', type: 'button', id: 'sw-pulse', onclick: () => selectView('market') }, [
    h('div', { class: 'pulse__todo', text: todo }),
    picked && picked.c.headline ? h('p', { class: 'pulse__head', text: picked.c.headline }) : null,
    stats.length ? h('div', { class: 'summary__stats pulse__stats' + (stats.length > 3 ? ' pulse__stats--4' : '') }, stats) : null,
    h('div', { class: 'pulse__go', text: ls ? `${SLOT_LABEL[ls]}の市況を読む ›` : '市況を開く ›' }),
  ]);
}

function renderTrade() {
  const out = [pulseCard()];
  if (!THERMO) {
    out.push(card('注文', null, h('div', { class: 'empty', text: 'データがまだありません。次の自動更新で作られます。' }), null, false, 'sw-orders'));
    out.push(portfolioCard());
    return out;
  }
  out.push(ordersCard());
  const mc = midCard();
  if (mc) out.push(mc);
  out.push(portfolioCard());
  out.push(watchCard());
  return out;
}

/* ==================== 業種 ====================
   どの業種に追い風が吹いていて、どの業種が負けているか（thermo.json の strength。dashboard/sectors.py、DESIGN.md 17章）。
     1. 追い風・向かい風 … 強さの上位と下位、順位を上げている／下げている業種
     2. 4象限の図       … 横に強さ（中期）、縦に勢い（10日）。直近10営業日の軌跡
     3. 全業種          … 強さの順。開くと市場との差・200日線・50日線より上の割合・売買代金の増え方・材料・構成銘柄
     4. 読み方と検証    … 4象限ごとの次の20日と、押し目買いの注文の成績を業種の強さで分けたもの
   強さは値動きだけで決める（見立てや材料を混ぜない）。注文の条件にも並べ方にも使わない。 */
const QUAD_INFO = {
  lead: { label: '先行', tone: 'up', short: '強い・勢いあり', act: '追い風が続いている業種。この業種で押し目買いの注文が出れば、業種の後押しがある' },
  fade: { label: '一服', tone: 'warn', short: '強い・勢いが鈍った', act: '強い業種の一息。押し目買いの注文は、この局面の業種で最も成績が良かった' },
  turn: { label: '改善', tone: 'accent', short: '弱い・戻っている', act: '弱い業種の戻り。次の20日も市場に負けがちだった。戻りを追わない' },
  lag: { label: '出遅れ', tone: 'down', short: '弱い・勢いなし', act: '向かい風の業種。この業種の押しは、押し目買いの成績も落ちた' },
};
const QUAD_ORDER = ['lead', 'fade', 'turn', 'lag'];
const TIER_TONE = { strong: 'up', up: 'up', mid: '', down: 'down', weak: 'down' };
// 4年の検証（本番の sectors.py で数え直した。前半 2023-07〜2024-12／後半 2025-01〜2026-08、40業種、5営業日おき）。
// 次の20営業日の、業種と市場（全銘柄の等ウェイト）の差の平均
const STRENGTH_4Y = {
  top: ['+1.2%', '+2.2%'], topPos: ['53%', '55%'], bottom: ['−0.4%', '−1.1%'], bottomPos: ['40%', '41%'], beat: ['56%', '64%'],
  quad: { lead: ['+0.1%', '+1.6%'], fade: ['+0.1%', '+0.1%'], turn: ['−0.4%', '−0.7%'], lag: ['−0.5%', '−0.8%'] },
};
// 押し目買いの注文（今のルール。swing.verify と同じ数え方）を、注文の日の業種の強さで分けた成績（勝率・1回の平均・PF。前半／後半）
// 列は [勝率, 1回の平均, PF] の前半／後半
const SWING_BY_STRENGTH = [
  ['上位1/5', '強さ', ['87', '86'], ['+1.18', '+1.95'], ['2.12', '2.30']],
  ['中間', '', ['86', '89'], ['+0.53', '+1.50'], ['1.52', '2.87']],
  ['下位1/5', '', ['86', '82'], ['+0.42', '+0.35'], ['1.38', '1.26']],
  ['一服の業種', '強い・勢いが鈍った', ['87', '90'], ['+0.89', '+2.22'], ['1.88', '3.85']],
  ['業種ぐるみの押し', '強さ 上半分の業種', ['95', '95'], ['+2.68', '+5.64'], ['6.08', '11.26']],
  ['業種ぐるみの押し', '強さ 下半分の業種', ['89', '86'], ['+0.65', '+1.65'], ['1.51', '2.09']],
  ['注文の全体', '', ['86', '87'], ['+0.60', '+1.24'], ['1.57', '2.17']],
];

function ST() { return (THERMO || {}).strength || null; }
function strengthOf(g) { const st = ST(); return (g && st && (st.rows || []).find((r) => r.g === g)) || null; }
const rankMove = (r) => (isNum(r.rank20) ? r.rank20 - r.rank : null);     // プラス＝20営業日前より順位を上げた

function quadBadge(q) {
  const qi = QUAD_INFO[q];
  return qi ? h('span', { class: 'badge badge--' + qi.tone, text: qi.label }) : null;
}
function scoreBar(r) {
  return h('span', { class: 'sbar', title: `強さ ${r.score}` }, [
    h('span', { class: 'sbar__fill sbar__fill--' + (TIER_TONE[r.tier] || 'mid'), style: `width:${Math.max(3, Math.min(100, r.score))}%` }),
  ]);
}
function moveText(r) {
  const m = rankMove(r);
  if (!isNum(m)) return null;
  if (m === 0) return h('span', { class: 'smove flat', text: '→' });
  return h('span', { class: 'smove ' + (m > 0 ? 'up' : 'down'), text: (m > 0 ? '▲' : '▼') + Math.abs(m) });
}
/* 売買タブの注文・監視に添える一言（「業種「半導体」の強さ 3位/40・先行」） */
function strengthLine(g) {
  const r = strengthOf(g);
  if (!r) return null;
  const st = ST();
  return `業種「${g}」の強さ ${r.rank}位/${st.n}・${(QUAD_INFO[r.quad] || {}).label || '—'}`;
}

/* 一覧の行（押すと全業種の一覧でその業種を開く） */
function strengthRow(r) {
  return h('button', { class: 'srow', type: 'button', onclick: () => focusGroup(r.g) }, [
    h('span', { class: 'srow__rank num', text: String(r.rank) }),
    h('span', { class: 'srow__main' }, [
      h('span', { class: 'srow__name', text: r.g }),
      h('small', { class: 'num', text: `強さ ${r.score}・50日線より上 ${r.br50}%` }),
    ]),
    quadBadge(r.quad),
    h('span', { class: 'srow__v num' }, [
      h('b', { class: cls(r.rs60), text: fmtPct(r.rs60, 1) }),
      h('small', { class: cls(r.rs120), text: `120日 ${fmtPct(r.rs120, 0)}` }),
    ]),
  ]);
}

function strengthHero(st) {
  const rows = st.rows || [];
  const top = rows.slice(0, 5);
  const bot = rows.slice(-5).reverse();
  const moved = rows.filter((r) => isNum(rankMove(r)) && Math.abs(rankMove(r)) >= 5);
  const rising = moved.filter((r) => rankMove(r) > 0).sort((a, b) => rankMove(b) - rankMove(a)).slice(0, 4);
  const falling = moved.filter((r) => rankMove(r) < 0).sort((a, b) => rankMove(a) - rankMove(b)).slice(0, 4);
  const mk = st.market || {};
  const v = st.verify;
  const names = (xs) => xs.slice(0, 3).map((r) => r.g).join('・');
  const chips = (xs) => h('div', { class: 'chips', style: 'margin-top:4px' }, xs.map((r) =>
    h('button', { class: 'badge badge--btn', type: 'button', onclick: () => focusGroup(r.g) }, [
      document.createTextNode(`${r.g} `), moveText(r), document.createTextNode(` → ${r.rank}位`)])));
  return h('section', { class: 'card decision decision--accent', id: 'sc-hero' }, [
    h('div', { class: 'decision__head' }, [
      h('div', {}, [
        h('div', { class: 'decision__k', text: `業種の強弱（${md(st.asof)} の引けまで・${st.n}業種）` }),
        h('div', { class: 'decision__label decision__label--accent', text: `追い風 ${top[0].g}` }),
      ]),
      h('div', { class: 'decision__aside' }, [
        h('span', { class: 'badge', text: `市場 20日 ${fmtPct(mk.r20, 1)}` }),
        h('span', { class: 'badge', text: `60日 ${fmtPct(mk.r60, 1)}` }),
      ]),
    ]),
    h('p', { class: 'decision__text', text: `強い: ${names(top)}。弱い: ${names(bot)}。` }),
    h('div', { class: 'sgroup__h' }, [h('span', { class: 'up', text: '追い風（強い業種）' }), h('small', { text: '市場との差 60日／120日' })]),
    h('div', {}, top.map(strengthRow)),
    h('div', { class: 'sgroup__h' }, [h('span', { class: 'down', text: '向かい風（弱い業種）' }), h('small', { text: '市場との差 60日／120日' })]),
    h('div', {}, bot.map(strengthRow)),
    rising.length ? h('div', { class: 'sgroup__h' }, [h('span', { text: '順位を上げている（20営業日前から5位以上）' })]) : null,
    rising.length ? chips(rising) : null,
    falling.length ? h('div', { class: 'sgroup__h' }, [h('span', { text: '順位を下げている' })]) : null,
    falling.length ? chips(falling) : null,
    v && v.top ? h('button', { class: 'evidence evidence--in', type: 'button', onclick: () => selectView('verify', 'v-sector') }, [h('span', { text:
      `直近の検証（${ym(v.from)}〜${ym(v.to)}、${v.dates}回）: 強さの上位1/5 の業種は、次の20営業日に市場を平均 ${fmtSigned(v.top.avg, 1)}% 上回り、` +
      `下位1/5 は ${fmtSigned(v.bottom.avg, 1)}%。上位が下位を上回った回は ${v.beat}%。ただし業種1つ1つが市場に勝った割合は ${v.top.pos}% で、` +
      '平均は大きく勝つ業種に引っ張られています。注文の条件には使っていません。' }), h('b', { text: '業種の強さの検証を見る ›' })]) : null,
    h('div', { class: 'card__note', text:
      '強さ＝市場との差（60日・120日）、業種の200日線からの位置、50日線より上にある構成銘柄の割合、の4つの順位の平均（0〜100）。' +
      '業種は押し目買いと同じ束ね方（テーマ辞書の主テーマ、無ければ日経の業種。4銘柄以上）、市場は日足のある全銘柄の等ウェイト。予測ではありません。' }),
  ]);
}

/* ---- 4象限の図（自前の SVG を DOM で組む。業種名はテーマ辞書由来なので textContent で入れる） ---- */
const SVGNS = 'http://www.w3.org/2000/svg';
function sv(tag, attrs = {}, children = []) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  [].concat(children).forEach((c) => c && el.appendChild(c));
  return el;
}

function rrgCard(st) {
  const rows = (st.rows || []).filter((r) => isNum(r.score) && isNum(r.mom));
  if (rows.length < 4) return null;
  const W = 340, H = 318, L = 24, R = 8, T = 20, B = 36;
  const X = (v) => L + (W - L - R) * v / 100;
  const Y = (v) => T + (H - T - B) * (1 - v / 100);
  const svg = sv('svg', { viewBox: `0 0 ${W} ${H}`, class: 'rrg', role: 'img', 'aria-label': '業種の4象限の図（横が強さ、縦が勢い）' });
  [['turn', 0, 50, 50, 100], ['lead', 50, 50, 100, 100], ['lag', 0, 0, 50, 50], ['fade', 50, 0, 100, 50]].forEach(([q, x0, y0, x1, y1]) => {
    svg.appendChild(sv('rect', { x: X(x0), y: Y(y1), width: X(x1) - X(x0), height: Y(y0) - Y(y1), class: 'rrg__q rrg__q--' + q }));
  });
  // 象限の名前は図の外（上と下）に置き、点や業種名と重ならないようにする
  const corner = [['turn', X(0), T - 7, 'start'], ['lead', X(100), T - 7, 'end'],
    ['lag', X(0), Y(0) + 12, 'start'], ['fade', X(100), Y(0) + 12, 'end']];
  corner.forEach(([q, x, y, anchor]) => svg.appendChild(sv('text', { x, y, 'text-anchor': anchor, class: 'rrg__ql rrg__c--' + q,
    text: `${QUAD_INFO[q].label}（${QUAD_INFO[q].short}）` })));
  svg.appendChild(sv('text', { x: X(50), y: H - 4, 'text-anchor': 'middle', class: 'rrg__ax', text: '弱い ← 強さ（60日・120日の市場比ほか）→ 強い' }));
  svg.appendChild(sv('text', { x: 10, y: Y(50), 'text-anchor': 'middle', class: 'rrg__ax', transform: `rotate(-90 10 ${Y(50)})`, text: '勢い（10日の市場比）→' }));
  // 名前を出す業種: 強い5・弱い3・順位を大きく動かした3。ほかは点だけ（押すと一覧で開く）
  const moved = rows.filter((r) => isNum(rankMove(r))).sort((a, b) => Math.abs(rankMove(b)) - Math.abs(rankMove(a))).slice(0, 3);
  const named = new Set([...rows.slice(0, 5), ...rows.slice(-3), ...moved].map((r) => r.g));
  const boxes = [];
  const fits = (b) => b.x0 >= L && b.x1 <= W && b.y0 >= T && b.y1 <= Y(0) && !boxes.some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0);
  const dots = sv('g');
  const labels = sv('g');
  // 弱い順に描き、強い業種の点と名前を上に重ねる
  [...rows].reverse().forEach((r) => {
    const x = X(r.score), y = Y(r.mom);
    const on = named.has(r.g);
    if (on && Array.isArray(r.trail)) {
      // 軌跡は10営業日前・5営業日前・今日の3点（毎日の点を結ぶと勢いの順位の日々の揺れで読めなくなる）
      const tr = r.trail;
      const pts = [tr[0], tr[Math.floor((tr.length - 1) / 2)], tr[tr.length - 1]].filter((p) => Array.isArray(p))
        .map(([a, b]) => `${X(a).toFixed(1)},${Y(b).toFixed(1)}`);
      if (pts.length > 1) dots.appendChild(sv('polyline', { points: pts.join(' '), class: 'rrg__trail rrg__c--' + r.quad }));
    }
    dots.appendChild(sv('circle', { cx: x, cy: y, r: on ? 4 : 3, class: 'rrg__dot rrg__c--' + r.quad + (on ? '' : ' rrg__dot--dim') }));
    dots.appendChild(sv('circle', { cx: x, cy: y, r: 11, class: 'rrg__hit', onclick: () => focusGroup(r.g) }, [sv('title', { text: `${r.g}（${r.rank}位・強さ ${r.score}・勢い ${r.mom}）` })]));
    if (!on) return;
    const w = r.g.length * 10 + 4, hgt = 12;
    const cands = [[x + 6, y + 4, 'start', x + 5, y - 7], [x - 6, y + 4, 'end', x - 5 - w, y - 7],
      [x, y - 8, 'middle', x - w / 2, y - 18], [x, y + 15, 'middle', x - w / 2, y + 5]];
    for (const [tx, ty, anchor, bx, by] of cands) {
      const b = { x0: bx, x1: bx + w, y0: by, y1: by + hgt };
      if (!fits(b)) continue;
      boxes.push(b);
      labels.appendChild(sv('text', { x: tx, y: ty, 'text-anchor': anchor, class: 'rrg__lab', text: r.g }));
      break;
    }
  });
  svg.appendChild(dots);
  svg.appendChild(labels);
  return card('4象限', '点を押すと一覧で開く', h('div', { class: 'rrg__wrap' }, svg),
    '横は強さ（中期）、縦は勢い（市場との差・10日）。どちらも全業種の中の順位（0〜100）で、真ん中の線は全業種の真ん中。' +
    '名前のある業種は10営業日前→5営業日前→今日の軌跡つき（点が今日）。強い業種は右上（先行）と右下（一服）を行き来し、' +
    '左上（改善）に上がってきた弱い業種は、4年の検証では次の20日も市場に負けがちでした。', false, 'sc-map');
}

/* ---- 全業種の一覧 ---- */
let sectorFocus = null;                 // 一覧を描き直してから開く関数（図・上位の行から呼ぶ）
function focusGroup(g) { if (sectorFocus) sectorFocus(g); }

function sectorExtras(r) {
  const out = [];
  const ts = ((THERMO || {}).sectors || []).find((s) => s.sector === r.sector);
  if (ts && ts.macro && ts.macro.label) {
    out.push(['マクロ（20日）', `${ts.macro.label}（日経の業種「${r.sector}」の金利・為替・原油などへの感応度から）`]);
  }
  const so = ((((DATA || {}).slots || {}).preopen || {}).data || {}).sector_outlook;
  const us = so && r.link ? (so.all || []).find((x) => x.sector === r.link) : null;
  if (us && isNum(us.score)) {
    out.push(['米国の連想（今朝）', `${us.score > 0 ? '追い風' : us.score < 0 ? '向かい風' : '中立'} ${fmtSigned(us.score, 2)}` +
      ((us.drivers || []).length ? `（${us.drivers.slice(0, 2).map((d) => `${d.driver} ${d.display}`).join('・')}）` : '')]);
  }
  if (Array.isArray(r.rev) && (r.rev[0] || r.rev[1])) out.push(['業績修正（直近10営業日）', `上方 ${r.rev[0]}件・下方 ${r.rev[1]}件`]);
  const tt = ((THERMO || {}).themes || []).find((t) => t.theme === r.g);
  if (tt && tt.label) out.push(['見出しの論調', tt.label]);
  return out;
}

function sectorMember(m) {
  const ws = watchState(m.code);
  const [label, tone] = WATCH_STATE[ws.key] || ['—', ''];
  const st = ((THERMO || {}).stocks || {})[m.code] || {};
  return h('button', { class: 'signal signal--btn', type: 'button', onclick: () => openStock(m.code) }, [
    h('div', { class: 'signal__name', text: cleanName(st.n || m.name) || m.code }),
    h('div', { class: 'signal__right num' }, [
      h('b', { class: cls(m.r20), text: `20日 ${fmtPct(m.r20, 1)}` }),
      h('small', { text: `${m.code}　60日 ${fmtPct(m.r60, 1)}` }),
    ]),
    h('div', { class: 'signal__tags' }, [h('span', { class: 'badge' + (tone ? ' badge--' + tone : ''), text: label }),
      ['signal', 'near', 'wait'].includes(ws.key) && (swOf(m.code) || {}).pc !== 'hot' ? peerBadge((swOf(m.code) || {}).pc) : null]),
    ['signal', 'near', 'skip'].includes(ws.key) ? h('div', { class: 'signal__why', text: ws.text }) : null,
  ]);
}

function groupAcc(r, st) {
  const qi = QUAD_INFO[r.quad] || {};
  const extras = sectorExtras(r);
  const members = r.members || [];
  const acc = h('details', { class: 'acc sacc' }, [
    h('summary', {}, [
      h('span', { class: 'acc__title' }, [
        h('span', { class: 'sacc__name' }, [h('span', { class: 'num', text: `${r.rank}. ` }), document.createTextNode(r.g), h('span', { text: ' ' }), moveText(r)]),
        h('small', { class: 'num', text: `${r.n}銘柄・強さ ${r.score}・60日 市場比 ${fmtPct(r.rs60, 1)}` }),
      ]),
      h('span', { class: 'sacc__right' }, [quadBadge(r.quad), scoreBar(r)]),
    ]),
    h('div', { class: 'sacc__body' }, [
      h('p', { class: 'sacc__act', text: `${qi.label || ''}（${qi.short || ''}）: ${qi.act || ''}` }),
      h('div', { class: 'plan__grid' }, [
        cell('市場比 5日', fmtPct(r.rs5, 1), null, cls(r.rs5)),
        cell('20日', fmtPct(r.rs20, 1), null, cls(r.rs20)),
        cell('60日', fmtPct(r.rs60, 1), null, cls(r.rs60)),
        cell('120日', fmtPct(r.rs120, 1), null, cls(r.rs120)),
      ]),
      h('div', { class: 'plan__grid', style: 'margin-top:4px' }, [
        cell('200日線から', fmtPct(r.ma200, 1), '業種の値動き', cls(r.ma200)),
        cell('50日線の上', `${r.br50}%`, '構成銘柄の割合'),
        cell('売買代金', isNum(r.flow) ? `${r.flow.toFixed(2)}倍` : '—', '5日÷60日・市場比', isNum(r.flow) ? (r.flow >= 1.2 ? 'up' : r.flow <= 0.8 ? 'down' : '') : ''),
        cell('今日', fmtPct(r.d1, 1), `20日 ${fmtPct(r.r20, 1)}`, cls(r.d1)),
      ]),
      h('div', { class: 'sacc__rank num', text: `順位: 今日 ${r.rank}位／5営業日前 ${r.rank5 ?? '—'}位／20営業日前 ${r.rank20 ?? '—'}位（${st.n}業種）` }),
      extras.length ? h('div', { class: 'sacc__ext' }, [h('div', { class: 'sacc__k', text: '材料（値動きの外。強さには入れていない・検証していない）' })]
        .concat(extras.map(([k, v]) => h('div', { class: 'sacc__line' }, [h('b', { text: k + ' ' }), document.createTextNode(v)])))) : null,
      h('div', { class: 'sacc__k', style: 'margin-top:8px', text: '構成銘柄（20日の騰落の順）と、押し目買いのルールでの位置' }),
    ]),
    foldable((n) => h('div', {}, members.slice(0, n).map(sectorMember)), members.length, 8, '全銘柄'),
  ]);
  acc.dataset.g = r.g;
  return acc;
}

function strengthListCard(st) {
  const rows = st.rows || [];
  const list = h('div', {});
  let filter = 'all';
  const draw = () => {
    list.textContent = '';
    const rs = rows.filter((r) => filter === 'all' || r.quad === filter);
    if (!rs.length) { list.appendChild(h('div', { class: 'empty', style: 'padding:6px 14px', text: 'なし' })); return; }
    rs.forEach((r) => list.appendChild(groupAcc(r, st)));
  };
  const count = (q) => rows.filter((r) => r.quad === q).length;
  const seg = segmented([['all', `全部 ${rows.length}`]].concat(QUAD_ORDER.map((q) => [q, `${QUAD_INFO[q].label} ${count(q)}`])),
    (k) => { filter = k; draw(); }, 'all');
  seg.classList.add('seg--scroll');
  sectorFocus = (g) => {
    if (filter !== 'all') { filter = 'all'; [...seg.children].forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.k === 'all'))); draw(); }
    const el = [...list.children].find((d) => d.dataset && d.dataset.g === g);
    if (!el) return;
    el.open = true;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  draw();
  return card('全業種', '強さの順', [h('div', { style: 'padding:0 14px 8px' }, seg), list],
    '▲▼は20営業日前からの順位の動き。開くと、市場との差・業種の200日線からの位置・50日線より上の銘柄の割合・売買代金の増え方と、' +
    '値動きの外の材料（マクロ・米国の連想・業績修正・見出し）、構成銘柄の押し目買いのルールでの位置（押すと銘柄の詳しい画面）。', true, 'sc-list');
}

/* ---- 読み方と検証 ---- */
function strengthGuideCard(st) {
  const v = st.verify || {};
  const q = v.quad || {};
  const cellV = (x) => (x && isNum(x.avg) ? `${fmtSigned(x.avg, 1)}%（${x.pos}%）` : '—');
  const tbl = (head, body) => h('div', { class: 'tablewrap' }, h('table', { class: 'bt bt--wrap' }, [
    h('thead', {}, h('tr', {}, head.map((t) => h('th', { text: t })))), h('tbody', {}, body)]));
  const quadRows = QUAD_ORDER.map((k) => h('tr', {}, [
    h('th', {}, [quadBadge(k), h('small', { class: 'bt__sub', text: QUAD_INFO[k].short })]),
    h('td', { class: 'num', text: cellV(q[k]) }),
    h('td', { class: 'num', text: `${STRENGTH_4Y.quad[k][0]}／${STRENGTH_4Y.quad[k][1]}` }),
  ]));
  const tierRows = [['強さの上位1/5', v.top, `${STRENGTH_4Y.top[0]}／${STRENGTH_4Y.top[1]}`],
    ['強さの下位1/5', v.bottom, `${STRENGTH_4Y.bottom[0]}／${STRENGTH_4Y.bottom[1]}`],
    ['全業種', v.all, '—']].map(([k, x, y]) => h('tr', {}, [h('th', { text: k }), h('td', { class: 'num', text: cellV(x) }), h('td', { class: 'num', text: y })]));
  const swRows = SWING_BY_STRENGTH.map(([k, sub, w, a, pf], i) => h('tr', { class: i === SWING_BY_STRENGTH.length - 1 ? 'bt__all' : null }, [
    h('th', {}, [document.createTextNode(k), sub ? h('small', { class: 'bt__sub', text: sub }) : null]),
    h('td', { class: 'num', text: `${w[0]}／${w[1]}%` }), h('td', { class: 'num', text: `${a[0]}／${a[1]}%` }),
    h('td', { class: 'num', text: `${pf[0]}／${pf[1]}` })]));
  return card('業種の強さの検証', '次の20営業日の市場との差', [
    h('p', { class: 'bt__verdict', text: '強い業種は強いまま、弱い業種は弱いままのことが多かった。弱い業種の戻り（改善）は追わない。' }),
    tbl(['象限', `直近（${ym(v.from)}〜）`, '4年 前半／後半'], quadRows),
    tbl(['強さ', '直近', '4年 前半／後半'], tierRows),
    h('p', { class: 'hint', text: `括弧は業種が市場に勝った割合。4年の「上位が下位を上回った回」は ${STRENGTH_4Y.beat[0]}／${STRENGTH_4Y.beat[1]}` +
      (isNum(v.beat) ? `、直近は ${v.beat}%。` : '。') + '平均の差ははっきりしているが、1回ごとの当たり外れは大きい。' }),
    h('div', { class: 'sgroup__h', style: 'margin-top:12px' }, [h('span', { text: '押し目買いの注文を、注文の日の業種の強さで分けると' })]),
    tbl(['業種', '勝率', '1回の平均', 'PF'], swRows),
    h('p', { class: 'hint', text:
      '4年の検証（同じ銘柄は手仕舞うまで次を数えない）。各欄は前半 2023-24／後半 2025-26。強い業種の押しは成績が良く、弱い業種（下位1/5）の押しは両方の期間で落ちた。特に「強い業種が業種ぐるみで下げた押し」が良い。' +
      'ただし、この強さを注文の並べ方や見送りに入れた口座の再現は、前半（決める期間）では良くなったが後半（確かめる期間）では良くならなかったので、' +
      '注文の条件にも並べ方にも使っていません。注文を見るときの追い風・向かい風の目安として使ってください。' }),
  ], '業種は今のテーマ辞書で束ねているので、今注目されている銘柄で過去を測る偏り（生存者の偏り）を含みます。' +
    '日経の業種（225銘柄を業種で束ねたもの）で同じ物差しを測ると、効き目ははっきりしませんでした（業種タブの「日経の業種」は別の物差し）。' +
    '短い期間（5日・10日）の強さだけでは先は読めません。直近の列は日足キャッシュ（約2年）で毎回数え直しています。', false, 'v-sector');
}

/* 日経の業種（225採用銘柄の業種平均）: 8日の推移（ヒートマップ）と温度（判定・5日・20日・RSI・マクロ）を1枚で切り替える。
   テーマ辞書で束ねた強弱（上）とは別の物差し */
function nikkeiSectorCard() {
  const days = HIST.loaded ? tradingDays() : [];
  const heat = HIST.loaded ? sectorHeatBody(days) : null;
  const table = sectorTableBody((THERMO || {}).sectors);
  if (!heat && !table && HIST.loaded) return null;
  const body = h('div', {});
  const note = h('div', { class: 'card__note' });
  const NOTES = {
    heat: '日経225採用銘柄を業種ごとに単純平均した大引の騰落率。直近8営業日の合計が大きい順に、上位5と下位5を出しています。' +
      `業種名の横の小さな数字は採用銘柄数で、${SECTOR_MIN_COUNT}銘柄未満の業種は1銘柄の動きで大きく振れるため上位・下位からは外し、「すべて」にだけ薄く出します。` +
      '合計は日々の騰落率を足したもの（複利の累積とは少し違う）。その下の「6/8日↑」は期間中に上げた日数。',
    table: '業種は日経225採用銘柄の等ウェイト。「押し目」＝60日で上昇・足元で調整、「過熱」＝短期で上がりすぎ。マクロは直近20日の金利・為替・原油・米株の動きと業種の感応度から（値動きの外の材料で、検証していない）。',
  };
  const draw = (k) => {
    body.textContent = '';
    if (k === 'heat') body.appendChild(heat || h('div', { class: 'empty', text: HIST.loaded ? '履歴がまだありません' : '履歴を読み込み中…' }));
    else body.appendChild(table || h('div', { class: 'empty', text: '温度計のデータがまだありません' }));
    note.textContent = NOTES[k];
  };
  const opts = [['heat', '8日の推移'], ['table', '温度（5日・20日）']];
  draw('heat');
  return h('section', { class: 'card', id: 'sc-nikkei' }, [
    h('div', { class: 'card__head' }, [h('h2', { class: 'card__title', text: '日経の業種' }), segmented(opts, draw, 'heat')]),
    body, note,
  ]);
}

function renderSectors() {
  const out = [];
  const st = ST();
  if (!st || !(st.rows || []).length) {
    out.push(card('業種の強弱', null, h('div', { class: 'empty', text: '業種の強弱のデータは次の自動更新から出ます。' }), null, false, 'sc-hero'));
  } else {
    out.push(strengthHero(st));
    out.push(rrgCard(st));
    out.push(strengthListCard(st));
  }
  out.push(nikkeiSectorCard());
  out.push(themeToneCard((THERMO || {}).themes));
  return out;
}

/* ==================== 市況: 推移（旧「履歴」） ====================
   最近の指数の向きと、日々の相場を並べて比べるための切り替え。
     1. 指数の推移 … 日経とTOPIXを期間の初日=0% にそろえた線（1本の軸）。ねじれが線の開きで見える
     2. 日々の記録 … 列をそろえた表。同じ列は同じ尺度のバーなので、上下に目を動かすだけで比べられる
     3. 週報
   業種×日のヒートマップ（sectorHeatBody）は業種タブの「日経の業種」に置く。
   休場・未取得の日は数字を出さず線1本（前営業日の値が並ぶと変化が読めなくなるため）。 */
const HIST_CHART_DAYS = 20;          // 指数の線に載せる営業日数
const HEAT_SCALE = 3;                // ヒートマップの色が飽和する騰落率（%）
const SECTOR_MIN_COUNT = 3;          // これ未満の銘柄数の業種は、上位・下位の代表に選ばない（1銘柄で振れるため）
const bigSector = (count) => !isNum(count) || count >= SECTOR_MIN_COUNT;

function weeklyCard() {
  const w = WEEKLY;
  if (!w || !Array.isArray(w.sections) || !w.sections.length) return null;
  // 長文なので見出しだけ出し、本文は畳む（最初の画面をグラフに譲る）
  return card('週報', w.week_end ? fmtDate(w.week_end) + ' まで' : null, [
    h('p', { class: 'analysis__headline', text: w.headline || '' }),
    h('details', { class: 'weekly' }, [
      h('summary', { text: '本文を読む' }),
      h('div', {}, w.sections.map((s) => h('div', { class: 'analysis__sec' }, [
        h('div', { class: 'analysis__t', text: s.title }),
        h('div', { class: 'analysis__b', text: s.body }),
      ]))),
    ]),
  ], (w.method || 'Claude による週次総括') + '。売買を推奨するものではありません。', false, 'tr-weekly');
}

/* ---- 履歴1日分から値を取り出す ---- */
function histIndex(s, key) {
  const idx = ((s.taibike || {}).indices || (s.zenba || {}).indices || {});
  const ix = idx[key];
  if (!ix) return null;
  // 始値・高値・安値がすべて 0 の気配は取得の穴埋め（9/4 など）。前日比 0 は実値ではないので「不明」にする
  if (!ix.open && !ix.high && !ix.low && !ix.change) return { ...ix, change: null, change_pct: null };
  return ix;
}
const histPartial = (s) => !(s.taibike && s.taibike.indices);     // 大引がまだ（前場の値）

/* 為替・原油。大引時点の値があればそれを、無い日は寄り前（前夜の米国時間）の値に落とす。
   寄り前の値には前日比が無いので、前営業日のスナップショットと比べて自前で出す。 */
function histMacro(s, prev, key) {
  const m = ((s.taibike || {}).macro || (s.zenba || {}).macro || {})[key];
  if (m && isNum(m.last)) {
    let pct = m.change_pct;
    if (!isNum(pct) && prev) {
      const p = histMacro(prev, null, key);
      if (p && p.last) pct = (m.last / p.last - 1) * 100;
    }
    return { last: m.last, change_pct: pct, morning: false };
  }
  const q = ((s.preopen || {}).quotes || {})[key];
  if (!isNum(q)) return null;
  const base = prev ? (histMacro(prev, null, key) || {}).last : null;
  return { last: q, change_pct: isNum(base) && base ? (q / base - 1) * 100 : null, morning: true };
}

/* 業種は日経225採用銘柄の業種平均（単純平均）に一本化する。毎営業日そろって取れる唯一の業種データで、
   東証33業種（株探の記事）は取れる日と取れない日があり、時点も寄付／大引が混ざるため比較に使えない。 */
function histSectorMap(s) {
  const t = (s.taibike || {}).sectors, z = (s.zenba || {}).sectors;
  const rows = (t && t.length) ? t : ((z && z.length) ? z : null);
  if (!rows) return null;
  const map = new Map();
  rows.forEach((r) => { if (r && isNum(r.avg_pct)) map.set(r.sector, { pct: r.avg_pct, count: r.count }); });
  return map.size ? map : null;
}

/* 終値の無い日の呼び名。過去日、または当日でも前場／大引の収集が走ったのに指数が前営業日の値なら休場。
   当日で寄り前しか無ければまだ分からないので「未取得」 */
function closedLabel(s) {
  if (s.date < jstNow().toISOString().slice(0, 10)) return '休場';
  return (s.zenba && s.zenba.indices) || (s.taibike && s.taibike.indices) ? '休場' : '未取得';
}

/* 終値のある営業日（古い順）。休場・未取得の日は除く */
function tradingDays() {
  return histSessions().slice().reverse().filter((s) => {
    const nk = histIndex(s, 'nikkei');
    return nk && isNum(nk.close) && !nk.stale;
  });
}

const md = (iso) => fmtDate(iso, { month: 'numeric', day: 'numeric', timeZone: 'Asia/Tokyo' });
const wd = (iso) => fmtDate(iso, { weekday: 'short', timeZone: 'Asia/Tokyo' });

function niceStep(span) {
  const raw = span / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}

/* ---- 1. 指数の推移 ---- */
function indexCard(days) {
  const pts = days.slice(-HIST_CHART_DAYS);
  if (pts.length < 2) return null;
  const nk0 = histIndex(pts[0], 'nikkei').close;
  const tp0 = (histIndex(pts[0], 'topix') || {}).close;
  const rows = pts.map((s) => {
    const nk = histIndex(s, 'nikkei'), tp = histIndex(s, 'topix');
    return {
      s, nk, tp,
      nkc: (nk.close / nk0 - 1) * 100,
      tpc: tp && isNum(tp.close) && tp0 ? (tp.close / tp0 - 1) * 100 : null,
    };
  });
  const n = rows.length;
  const vals = [0].concat(rows.map((r) => r.nkc), rows.filter((r) => isNum(r.tpc)).map((r) => r.tpc));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const step = niceStep(Math.max(hi - lo, 1));
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;

  const W = 340, H = 168, ML = 38, MR = 10, MT = 8, MB = 20;
  const x = (i) => ML + (n === 1 ? 0 : i * (W - ML - MR) / (n - 1));
  const y = (v) => MT + (hi - v) / (hi - lo) * (H - MT - MB);
  const pathOf = (key) => rows.map((r, i) => isNum(r[key]) ? `${x(i).toFixed(1)},${y(r[key]).toFixed(1)}` : null)
    .filter(Boolean).join(' ');

  let svg = `<svg class="ix__svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="日経平均とTOPIXの推移（期間初日=0%）">`;
  for (let v = lo; v <= hi + 1e-9; v += step) {
    const yy = y(v).toFixed(1);
    const zero = Math.abs(v) < 1e-9;
    svg += `<line x1="${ML}" x2="${W - MR}" y1="${yy}" y2="${yy}" class="${zero ? 'ix__zero' : 'ix__grid'}"/>`;
    svg += `<text x="${ML - 5}" y="${yy}" class="ix__tick" text-anchor="end" dominant-baseline="middle">${(v > 0 ? '+' : '') + (+v.toFixed(2))}%</text>`;
  }
  const every = Math.max(1, Math.ceil(n / 5));
  rows.forEach((r, i) => {
    if (i % every !== 0 && i !== n - 1) return;
    if (i !== n - 1 && n - 1 - i < every * 0.6) return;     // 最後のラベルと重なるものは出さない
    const anchor = i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle';
    svg += `<text x="${x(i).toFixed(1)}" y="${H - 5}" class="ix__tick" text-anchor="${anchor}">${md(r.s.date)}</text>`;
  });
  svg += `<line class="ix__cross" x1="0" x2="0" y1="${MT}" y2="${H - MB}" visibility="hidden"/>`;
  svg += `<polyline points="${pathOf('tpc')}" class="ix__line ix__line--tp"/>`;
  svg += `<polyline points="${pathOf('nkc')}" class="ix__line ix__line--nk"/>`;
  svg += `<circle class="ix__dot ix__dot--tp" r="4"/><circle class="ix__dot ix__dot--nk" r="4"/>`;
  svg += '</svg>';

  // 読み取り欄: 既定は最新日。指でなぞるとその日に合わせて書き換える（ツールチップの代わり。指で隠れない）
  const roDate = h('b', { class: 'ix__ro-date' });
  const roNk = h('span', { class: 'num' }), roTp = h('span', { class: 'num' });
  const readout = h('div', { class: 'ix__ro' }, [roDate,
    h('span', { class: 'ix__ro-item' }, [h('i', { class: 'key key--nk' }), '日経 ', roNk]),
    h('span', { class: 'ix__ro-item' }, [h('i', { class: 'key key--tp' }), 'TOPIX ', roTp])]);
  const plot = h('div', { class: 'ix__plot', html: svg });
  const q = (sel) => plot.querySelector(sel);
  const setIdx = (i, hover) => {
    const r = rows[i];
    roDate.textContent = `${md(r.s.date)}(${wd(r.s.date)})` + (histPartial(r.s) ? ' 前場' : '');
    const put = (el, ix) => {
      el.textContent = '';
      if (!ix) { el.textContent = '—'; return; }
      el.appendChild(document.createTextNode(fmtNum(ix.close, ix.close >= 10000 ? 0 : 2) + ' '));
      el.appendChild(h('span', { class: cls(ix.change_pct), text: fmtPct(ix.change_pct) }));
    };
    put(roNk, r.nk); put(roTp, r.tp);
    const cross = q('.ix__cross');
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i));
    cross.setAttribute('visibility', hover ? 'visible' : 'hidden');
    const dot = (sel, v) => {
      const c = q(sel);
      if (!isNum(v)) { c.setAttribute('visibility', 'hidden'); return; }
      c.setAttribute('visibility', 'visible'); c.setAttribute('cx', x(i)); c.setAttribute('cy', y(v));
    };
    dot('.ix__dot--nk', r.nkc); dot('.ix__dot--tp', r.tpc);
  };
  const pick = (ev) => {
    const box = plot.firstElementChild.getBoundingClientRect();
    const px = (ev.clientX - box.left) / box.width * W;
    const i = Math.max(0, Math.min(n - 1, Math.round((px - ML) / ((W - ML - MR) / Math.max(1, n - 1)))));
    setIdx(i, true);
  };
  plot.addEventListener('pointerdown', pick);
  plot.addEventListener('pointermove', pick);
  plot.addEventListener('pointerleave', () => setIdx(n - 1, false));
  setIdx(n - 1, false);

  // 表: 凡例を兼ねる。線の色と同じキーを行頭に置く
  const last = rows[n - 1];
  const back = (key, k) => {
    if (n <= k) return null;
    const a = histIndex(rows[n - 1 - k].s, key), b = histIndex(last.s, key);
    return a && b && a.close ? (b.close / a.close - 1) * 100 : null;
  };
  const periodLabel = `${n}日`;
  const cell = (v) => h('td', { class: 'num ' + cls(v), text: isNum(v) ? fmtPct(v) : '—' });
  const table = h('table', { class: 'ix__table' }, [
    h('thead', {}, h('tr', {}, ['', '前日比', '5日', periodLabel].map((t) => h('th', { text: t })))),
    h('tbody', {}, [
      h('tr', {}, [h('th', {}, [h('i', { class: 'key key--nk' }), '日経平均']),
        cell(last.nk.change_pct), cell(back('nikkei', 5)), cell(last.nkc)]),
      h('tr', {}, [h('th', {}, [h('i', { class: 'key key--tp' }), 'TOPIX']),
        cell(last.tp && last.tp.change_pct), cell(back('topix', 5)), cell(last.tpc)]),
    ]),
  ]);

  // NT倍率: 日経÷TOPIX。上がる＝日経（値がさ株）がTOPIXより強い
  let nt = null;
  if (last.tp && last.tp.close) {
    const now = last.nk.close / last.tp.close;
    const r5 = n > 5 ? rows[n - 6] : rows[0];
    const then = r5.tp && r5.tp.close ? r5.nk.close / r5.tp.close : null;
    nt = h('div', { class: 'ix__nt' }, [
      h('span', { text: 'NT倍率 ' }), h('b', { class: 'num', text: now.toFixed(2) }),
      then ? h('span', { class: 'num', text: `（${md(r5.s.date)} ${then.toFixed(2)} から ${fmtSigned(now - then, 2)}）` }) : null,
      h('div', { class: 'ix__nt-hint', text: '上がるほど日経平均がTOPIXより強い（値がさ株が相場を引っ張っている）' }),
    ]);
  }

  return card('指数の推移', `${md(rows[0].s.date)} を 0% とした騰落`, [readout, plot, table, nt],
    '線は期間初日の終値を 0% とした騰落率（日経とTOPIXを同じ目盛りで比べるため）。' +
    '指でなぞるとその日の終値に切り替わります。休場日は詰めて描いています。', false, 'tr-index');
}

/* ---- 業種の8日（ヒートマップ。業種タブの「日経の業種」で使う） ---- */
function sectorHeatCell(v) {
  if (!isNum(v)) return h('div', { class: 'hm__c hm__c--na', text: '' });
  const p = Math.min(Math.abs(v) / HEAT_SCALE, 1);
  const el = h('div', { class: 'hm__c num' + (p > 0.55 ? ' hm__c--strong' : ''), text: (v > 0 ? '+' : '') + v.toFixed(1) });
  el.style.background = `color-mix(in oklab, ${v >= 0 ? 'var(--up)' : 'var(--down)'} ${Math.round(p * 100)}%, var(--heat-0))`;
  return el;
}

function sectorHeatBody(days) {
  // 順位は常に直近8営業日の合計で決める（端末の幅で「強い業種」が変わらないように）。
  // 狭い画面では表示する日の列だけ減らし、合計の列は8日のまま
  const span = days.filter((s) => histSectorMap(s)).slice(-8);
  if (!span.length) return null;
  const shown = window.innerWidth < 370 ? Math.min(6, span.length) : span.length;
  const cols = span.slice(-shown);
  const maps = span.map(histSectorMap);
  const names = new Set();
  maps.forEach((m) => m.forEach((_, k) => names.add(k)));
  const rows = [...names].map((name) => {
    const vals = maps.map((m) => (m.get(name) || {}).pct);
    const got = vals.filter(isNum);
    const count = (maps[maps.length - 1].get(name) || {}).count;
    return { name, vals, count, sum: got.reduce((a, b) => a + b, 0), days: got.length,
             ups: got.filter((v) => v > 0).length };
  }).sort((a, b) => b.sum - a.sum);
  const major = rows.filter((r) => bigSector(r.count));

  const grid = h('div', { class: 'hm' });
  grid.style.gridTemplateColumns = `minmax(64px, auto) repeat(${cols.length}, minmax(0, 1fr)) 44px`;
  const draw = (all) => {
    grid.textContent = '';
    grid.appendChild(h('div', { class: 'hm__h hm__h--name', text: '業種' }));
    cols.forEach((s) => grid.appendChild(h('div', { class: 'hm__h' }, [
      document.createTextNode(md(s.date).replace(/^\d+\//, '')), h('small', { text: histPartial(s) ? '前場' : wd(s.date) })])));
    grid.appendChild(h('div', { class: 'hm__h hm__h--sum' }, [document.createTextNode('合計'), h('small', { text: `${span.length}日` })]));
    const K = 5;
    const list = (all || major.length <= K * 2 + 2) ? (all ? rows : major)
      : major.slice(0, K).concat([null], major.slice(-K));
    list.forEach((r) => {
      if (!r) {
        grid.appendChild(h('div', { class: 'hm__gap', text: `… ほか ${rows.length - K * 2} 業種 …` }));
        return;
      }
      const small = !bigSector(r.count);
      grid.appendChild(h('div', { class: 'hm__name' + (small ? ' hm__name--small' : ''), title: r.count ? `採用 ${r.count} 銘柄` : null }, [
        document.createTextNode(r.name), r.count ? h('small', { text: String(r.count) }) : null]));
      r.vals.slice(-shown).forEach((v, i) => {
        const c = sectorHeatCell(v);
        if (isNum(v)) c.title = `${r.name} ${md(cols[i].date)} ${fmtPct(v)}`;
        grid.appendChild(c);
      });
      // 合計の下に「何日上げたか」。合計が同じでも、毎日上げたのか1日の急騰かが分かれる
      grid.appendChild(h('div', { class: 'hm__sum' }, [
        h('div', { class: 'num ' + cls(r.sum), text: fmtSigned(r.sum, 1) }),
        h('small', { class: 'num', text: `${r.ups}/${r.days}日↑` }),
      ]));
    });
  };
  draw(false);
  const btn = rows.length > major.length || major.length > 12
    ? h('button', { class: 'more', type: 'button', text: `${rows.length}業種すべてを見る` }) : null;
  if (btn) {
    let all = false;
    btn.addEventListener('click', () => { all = !all; draw(all); btn.textContent = all ? '上位と下位だけに戻す' : `${rows.length}業種すべてを見る`; });
  }
  const legend = h('div', { class: 'hm__legend' }, [
    h('span', { class: 'num', text: `−${HEAT_SCALE}%` }), h('i', { class: 'hm__ramp' }),
    h('span', { class: 'num', text: `+${HEAT_SCALE}%` }),
    h('span', { class: 'hm__legend-t', text: '各日の業種平均の騰落率（％）' }),
  ]);
  return h('div', {}, [h('div', { class: 'hm__span', text: `${md(span[0].date)}〜${md(span[span.length - 1].date)}の合計順` }), legend, grid, btn]);
}

/* ---- 2. 日々の記録（列をそろえた表） ---- */
function dayCell(v, level, max, mark) {
  const bar = h('i', { class: 'dt__bar' });
  if (isNum(v) && max > 0) {
    const w = Math.min(Math.abs(v) / max, 1) * 50;
    bar.style.width = w + '%';
    bar.style[v >= 0 ? 'left' : 'right'] = '50%';
    bar.style.background = v >= 0 ? 'var(--up)' : 'var(--down)';
  }
  return h('div', { class: 'dt__c' }, [
    h('div', { class: 'dt__pct num ' + cls(v), text: isNum(v) ? fmtPct(v) : '—' }),
    h('div', { class: 'dt__lv num', text: level + (mark ? '*' : '') }),
    h('div', { class: 'dt__track' }, [bar]),
  ]);
}

function dailyCard(sessions) {
  // sessions は新しい順。列ごとに同じ尺度のバーにするため、先に各列の最大幅を決める
  const tradingRows = sessions.map((s, i) => {
    const nk = histIndex(s, 'nikkei');
    if (!nk || nk.stale) return { s, closed: true };
    const prevTrading = sessions.slice(i + 1).find((p) => { const x = histIndex(p, 'nikkei'); return x && !x.stale; }) || null;
    return {
      s, nk, tp: histIndex(s, 'topix'),
      fx: histMacro(s, prevTrading, 'usdjpy'), oil: histMacro(s, prevTrading, 'wti'),
      sec: histSectorMap(s),
    };
  });
  const live = tradingRows.filter((r) => !r.closed);
  const maxOf = (f) => Math.max(0.5, ...live.map(f).filter(isNum).map(Math.abs));
  const mx = {
    nk: maxOf((r) => r.nk.change_pct), tp: maxOf((r) => r.tp && r.tp.change_pct),
    fx: maxOf((r) => r.fx && r.fx.change_pct), oil: maxOf((r) => r.oil && r.oil.change_pct),
  };
  const anyMorning = live.some((r) => (r.fx && r.fx.morning) || (r.oil && r.oil.morning));

  const head = h('div', { class: 'dt__row dt__head' }, ['日付', '日経平均', 'TOPIX', 'ドル円', 'WTI原油']
    .map((t) => h('div', { text: t })));
  const body = tradingRows.map((r) => {
    if (r.closed) {
      return h('div', { class: 'histx' }, [
        h('span', { class: 'histx__d', text: `${md(r.s.date)}(${wd(r.s.date)})` }),
        h('span', { class: 'histx__rule' }),
        h('span', { class: 'histx__t', text: closedLabel(r.s) }),
      ]);
    }
    const { s, nk, tp, fx, oil, sec } = r;
    const secs = sec ? [...sec.entries()].filter(([, v]) => bigSector(v.count))
      .map(([k, v]) => ({ name: k, pct: v.pct })).sort((a, b) => b.pct - a.pct) : [];
    const pre = (s.preopen || {}).implied_open;
    const vr = ((s.taibike || {}).value_rows || (s.zenba || {}).value_rows || []);
    const ah = ((s.taibike || {}).after_hours || []);
    const ups = ah.filter((x) => disclosureTone(x) === 'up');
    const chips = [];
    if (pre && isNum(pre.gap_pct)) chips.push(h('span', { class: 'badge', text: `想定 ${fmtPct(pre.gap_pct)} → 実際 ${fmtPct(nk.change_pct)}` }));
    if ((s.taibike || {}).session_shift) chips.push(h('span', { class: 'badge', text: '後場: ' + s.taibike.session_shift.verdict }));
    if (ah.length) chips.push(h('span', { class: 'badge' + (ups.length ? ' badge--up' : ''), text: `引け後開示 ${ah.length}` + (ups.length ? `・上方 ${ups.length}` : '') }));
    const secChip = (x) => h('span', { class: 'dt__sec' }, [h('span', { text: x.name }), h('b', { class: 'num ' + cls(x.pct), text: fmtPct(x.pct, 1) })]);
    return h('details', { class: 'dt' }, [
      h('summary', { class: 'dt__row' }, [
        h('div', { class: 'dt__date' }, [document.createTextNode(md(s.date)),
          h('small', { text: wd(s.date) + (histPartial(s) ? '・前場' : '') })]),
        dayCell(nk.change_pct, fmtNum(nk.close, 0), mx.nk),
        dayCell(tp && tp.change_pct, tp ? fmtNum(tp.close, 0) : '—', mx.tp),
        dayCell(fx && fx.change_pct, fx ? fmtNum(fx.last, 2) : '—', mx.fx, fx && fx.morning),
        dayCell(oil && oil.change_pct, oil ? fmtNum(oil.last, 1) : '—', mx.oil, oil && oil.morning),
        secs.length ? h('div', { class: 'dt__secs' }, [
          h('span', { class: 'dt__k up', text: '▲' }), secChip(secs[0]),
          h('span', { class: 'dt__k down', text: '▼' }), secChip(secs[secs.length - 1]),
        ]) : null,
      ]),
      h('div', { class: 'dt__body' }, [
        chips.length ? h('div', { class: 'hist__kv' }, chips) : null,
        secs.length ? h('div', { class: 'dt__secgrid' }, [
          h('div', {}, [h('div', { class: 'dt__subt', text: '強い業種' })].concat(secs.slice(0, 3).map(secChip))),
          h('div', {}, [h('div', { class: 'dt__subt', text: '弱い業種' })].concat(secs.slice(-3).reverse().map(secChip))),
        ]) : null,
        vr.length ? h('div', { class: 'card__head', style: 'padding:0 14px;margin:8px 0 4px' }, [h('h3', { class: 'card__title', text: '売買代金上位' })]) : null,
        vr.length ? stockRows(vr, { limit: 10 }) : null,
        ups.length ? h('div', { class: 'card__head', style: 'padding:0 14px;margin:10px 0 4px' }, [h('h3', { class: 'card__title', text: '上方修正・増配' })]) : null,
        ups.length ? disclosureRows(ups, 10) : null,
      ]),
    ]);
  });
  const closed = tradingRows.length - live.length;
  return card('日々の記録', `${live.length}営業日` + (closed ? `・休場 ${closed}` : ''),
    [h('div', { class: 'dt__wrap' }, [head].concat(body))],
    '上が直近。各列の上段が前日比、下段が終値、細いバーは列ごとに同じ尺度（長いほど大きく動いた日）。' +
    `▲▼はその日いちばん強い／弱い業種（日経225の業種平均。${SECTOR_MIN_COUNT}銘柄未満の業種は除く）。タップで売買代金上位などを開きます。` +
    (anyMorning ? 'ドル円・WTIは大引時点の値。* は寄り前（前夜の米国時間）の値で、前日比は前営業日の同じ時点と比べています。' : ''),
    true, 'tr-daily');
}

function renderTrend() {
  const out = [];
  if (!HIST.loaded) {
    out.push(h('section', { class: 'card' }, [h('p', { class: 'empty', text: '履歴を読み込み中…' })]));
    return out;
  }
  const sessions = histSessions();                        // 新しい順（上が直近）
  if (!sessions.length) {
    out.push(h('section', { class: 'card' }, [
      h('h2', { class: 'card__title', text: '履歴はまだありません' }),
      h('p', { class: 'hint', text: '営業日ごとのスナップショットが溜まるとここに並びます。' }),
    ]));
    return out;
  }
  out.push(indexCard(tradingDays()));
  out.push(dailyCard(sessions));
  out.push(weeklyCard());
  return out;
}

/* ==================== 銘柄の横断検索の索引（今日のランキング・開示・直近の履歴） ==================== */
function searchIndex() {
  const hits = [];
  const slots = (DATA && DATA.slots) || {};
  const seen = new Set();
  const push = (r, ctx) => {
    if (!r || !r.code) return;
    hits.push({ code: String(r.code), name: cleanName(r.name) || '', price: r.price, change_pct: r.change_pct, ctx });
  };
  const sess = (slots.taibike || slots.zenba || {}).data;
  if (sess) {
    const t = sess.tables || {};
    (((t.value || {}).rows) || []).forEach((r) => push(r, `${t.value.label || '売買代金'} ${r.rank}位`));
    (((t.gainer || {}).rows) || []).forEach((r) => push(r, `上昇率 ${r.rank}位`));
    (((t.loser || {}).rows) || []).forEach((r) => push(r, `下落率 ${r.rank}位`));
    (((t.kessan_after || {}).rows) || []).forEach((r) => push(r, `引け後開示: ${r.category}`));
    (((t.kessan_intraday || {}).rows) || []).forEach((r) => push(r, `場中開示: ${r.category}`));
    (sess.constituents || []).forEach((r) => push(r, `日経225（${r.sector}）`));
  }
  const pre = (slots.preopen || {}).data;
  if (pre) ((pre.carryover || {}).after_hours_kessan || []).forEach((r) => push(r, `前日引け後開示: ${r.category}`));
  histSessions().forEach((hs) => {
    ((hs.taibike || {}).value_rows || []).forEach((r) => push(r, `${fmtDate(hs.date)} 売買代金上位`));
    ((hs.taibike || {}).after_hours || []).forEach((r) => push(r, `${fmtDate(hs.date)} ${r.category}`));
  });
  void seen;
  return hits;
}

/* ==================== 銘柄シート ====================
   どの画面でも銘柄を押すと開く。上から:
     値段 → 日足（注文・保有の価格の横線つき）→ 主な数字 → 押し目買いのルールでの位置（注文なら計画と「買えた」）→
     保有していれば計画／判定（していなければ「持っているなら」の判定と追加）→ 今日の材料（開示・ランキング・発掘・見出し）→
     買う前／売る前チェック → ☆ウォッチ・株探
   同じ銘柄に画面ごとに違う計画を出さないよう、注文・保有の行は売買タブと同じ部品（orderCard・positionRow・holdRow）で描く。
   右上の虫眼鏡から開くと検索になり、選ぶとその銘柄に切り替わる（戻るで検索に戻る）。 */
let sheetState = null;              // { mode: 'search'|'stock', code, intent, q, back, msg }

function readRecent() { return lsJson(LS.recent).filter((c) => typeof c === 'string'); }
function pushRecent(code) { lsSave(LS.recent, [code].concat(readRecent().filter((c) => c !== code)).slice(0, 12)); }

function openStock(code, intent) {
  if (!code) return;
  const c = String(code);
  pushRecent(c);
  const back = sheetState && sheetState.mode === 'search' && $('stockSheet').open ? sheetState.q : null;
  sheetState = { mode: 'stock', code: c, intent: intent || null, back };
  drawSheetBody(true);
  showSheet();
}
function openSearch(q) {
  sheetState = { mode: 'search', q: q || '' };
  drawSheetBody(true);
  showSheet();
  setTimeout(() => { const i = $('stockBody').querySelector('input[type="search"]'); if (i) i.focus(); }, 60);
}
/* 開くときに履歴を1つ積み、閉じるときはその履歴を戻して閉じる（端末の「戻る」でも閉じられるように） */
function showSheet() {
  const dlg = $('stockSheet');
  if (dlg.open) return;
  dlg.showModal();
  try { history.pushState({ sheet: 1 }, ''); } catch (e) { /* 非対応環境は無視 */ }
}
function closeSheet() {
  const dlg = $('stockSheet');
  if (!dlg.open) return;
  if (history.state && history.state.sheet) { try { history.back(); return; } catch (e) { /* 非対応環境は下で閉じる */ } }
  dlg.close();
}
function drawSheetBody(top) {
  const body = $('stockBody');
  const y = body.scrollTop;
  body.textContent = '';
  if (!sheetState) return;
  body.classList.toggle('is-search', sheetState.mode === 'search');
  body.appendChild(sheetState.mode === 'search' ? searchView() : stockView(sheetState.code));
  body.scrollTop = top ? 0 : y;
}
function sheetHead(title, sub, back) {
  return h('div', { class: 'sheet__head sd__head' }, [
    back ? h('button', { class: 'iconbtn', type: 'button', 'aria-label': '検索に戻る', text: '‹', onclick: back }) : null,
    h('div', { class: 'sd__title' }, [h('h2', { class: 'sheet__title', text: title }), sub ? h('span', { class: 'sd__sub', text: sub }) : null]),
    h('button', { class: 'btn btn--ghost', type: 'button', 'aria-label': '閉じる', text: '✕', onclick: closeSheet }),
  ]);
}

/* ---- 検索 ---- */
function stockUniverse() {
  const map = new Map();
  const put = (code, name, price, d1, ctx) => {
    if (!code) return;
    const c = String(code);
    const x = map.get(c) || { code: c, name: '', price: null, change_pct: null, ctx: [] };
    if ((!x.name || !JA_RE.test(x.name)) && name) x.name = cleanName(name);
    if (!isNum(x.price) && isNum(price)) x.price = price;
    if (!isNum(x.change_pct) && isNum(d1)) x.change_pct = d1;
    if (ctx && !x.ctx.includes(ctx)) x.ctx.push(ctx);
    map.set(c, x);
  };
  readPos().concat(readHold()).forEach((p) => put(p.code, p.name, null, null, '保有'));
  effectiveWatchlist(sessData()).forEach((w) => put(w.code, w.name, w.price, w.change_pct, 'ウォッチ'));
  Object.entries((THERMO || {}).stocks || {}).forEach(([c, s]) => put(c, s.n, s.price, s.d1, s.s || null));
  searchIndex().forEach((x) => put(x.code, x.name, x.price, x.change_pct, x.ctx));
  return map;
}

function searchStocks(q) {
  const qn = normText(q);
  if (!qn) return [];
  const rank = (x) => (x.code === qn ? 0 : x.code.startsWith(qn) ? 1 : normText(x.name).startsWith(qn) ? 2 : 3) - (x.ctx.includes('保有') || x.ctx.includes('ウォッチ') ? 0.5 : 0);
  return [...stockUniverse().values()]
    .filter((x) => x.code.startsWith(qn) || normText(x.name).includes(qn))
    .sort((a, b) => rank(a) - rank(b) || a.code.localeCompare(b.code));
}

function searchView() {
  const input = h('input', { type: 'search', placeholder: 'コード・社名（例: 7203、トヨタ）', autocomplete: 'off', inputmode: 'search', value: sheetState.q || '' });
  const result = h('div', { class: 'sd__results' });
  const run = () => {
    const q = input.value.trim();
    sheetState.q = q;
    result.textContent = '';
    if (!q) { result.appendChild(quickPicks()); return; }
    const hits = searchStocks(q);
    if (!hits.length) {
      result.appendChild(h('div', { class: 'empty', text: '見つかりません（日足のある銘柄と、今日のランキング・開示・直近の履歴の範囲で探しています）。4桁のコードなら Enter で開けます。' }));
      return;
    }
    result.appendChild(h('div', { class: 'rows' }, hits.slice(0, 40).map((c) => stockRow(c, 0, { rank: false, meta: () => c.ctx.slice(0, 3) }))));
  };
  input.addEventListener('input', run);
  input.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const q = input.value.trim().toUpperCase();
    const hits = searchStocks(q);
    if (hits.length) openStock(hits[0].code);
    else if (/^[0-9][0-9A-Z]{3}$/.test(q)) openStock(q);
  });
  run();
  return h('div', {}, [
    sheetHead('銘柄を探す', '押すと詳しい画面'),
    h('div', { class: 'search' }, [input]),
    result,
  ]);
}

/* 検索が空のとき: 最近見た銘柄と、保有・注文・ウォッチの銘柄 */
function quickPicks() {
  const uni = stockUniverse();
  const chip = (c) => h('button', { class: 'chip', type: 'button', text: `${cleanName((uni.get(c) || {}).name) || c}`, onclick: () => openStock(c) });
  const recent = readRecent();
  const mine = [...myStocks().keys()].filter((c) => !recent.includes(c));
  const kids = [];
  if (recent.length) kids.push(h('div', { class: 'sd__k', text: '最近見た銘柄' }), h('div', { class: 'hold__chips' }, recent.map(chip)));
  if (mine.length) kids.push(h('div', { class: 'sd__k', text: '保有・注文・ウォッチ' }), h('div', { class: 'hold__chips' }, mine.map(chip)));
  if (!kids.length) kids.push(h('p', { class: 'hint', text: `日足のある ${Object.keys((THERMO || {}).stocks || {}).length}銘柄（日経225・テーマ辞書・発掘・ウォッチ）と、今日のランキング・開示・直近の履歴に出た銘柄を探せます。` }));
  return h('div', { style: 'margin-top:12px' }, kids);
}

/* ---- 銘柄の詳しい画面 ---- */
function contextLines(code) {
  const s = stockOf(code) || {};
  const sw = s.sw || {};
  const pos = readPos().find((p) => p.code === code);
  const own = readHold().find((p) => p.code === code);
  const order = [...((SW() || {}).orders || []), ...((SW() || {}).more || [])].find((o) => o.code === code);
  const mo = midOrderOf(code);
  if (pos && isMidPos(pos)) return [['entry', pos.entry, '買値'], ['stop', pos.stop, '損切'], ['target', pos.hi, '高値']];
  if (pos) { const ps = posSell(pos, sw); return [['entry', pos.entry, '買値'], ['stop', pos.stop, '損切'], ['target', ps.ready ? ps.v : null, '売り']]; }
  if (own && s.hd) { const stp = holdStop(own, s.hd); return [['entry', own.entry, '買値'], ['stop', stp.v, '撤退'], ['target', s.hd.sell, '売り']]; }
  if (mo) return [['entry', mo.limit, '指値'], ['stop', mo.stop, '損切'], ['target', mo.hi, '高値']];
  if (order) return [['entry', order.limit, '指値'], ['stop', order.stop, '損切'], ['target', order.sell, '売り']];
  if (sw.md && isNum(sw.md.trig)) return [['entry', sw.md.trig, '待つ'], ['target', sw.md.hi, '高値']];
  if (isNum(sw.trig)) return [['entry', sw.trig, '待つ']];
  return [];
}

function sdSection(title, kids, sub) {
  return h('section', { class: 'sd__sec' }, [
    h('div', { class: 'sd__sh' }, [h('h3', { text: title }), sub ? h('small', { text: sub }) : null]),
  ].concat([].concat(kids).filter(Boolean)));
}

/* 今日の材料: 開示・ランキング・発掘の理由・テーマ・決算の反応・見出し */
function stockMaterials(code, name, s) {
  const lines = [];
  const feed = gatherNews();
  const disc = [];
  [['after', '引け後'], ['intraday', '場中'], ['carry', '前営業日の引け後']].forEach(([k, when]) =>
    (feed.disc[k] || []).forEach((r) => { if (String(r.code) === code) disc.push({ ...r, time: when }); }));
  const tags = [];
  const t = (latestSession() || {}).tables || {};
  Object.values(t).forEach((tb) => (tb.rows || []).forEach((r, i) => {
    if (String(r.code) === code && !/開示/.test(tb.label || '')) tags.push(`${tb.label} ${r.rank || i + 1}位`);
  }));
  const le = ((LEDGER && LEDGER.entries) || []).find((e) => e.code === code && e.status !== 'closed');
  if (tags.length) lines.push(h('div', { class: 'chips' }, tags.map((x) => h('span', { class: 'badge badge--accent', text: x }))));
  if (s && (s.th || []).length) lines.push(h('div', { class: 'sd__line' }, [h('b', { text: 'テーマ ' }), document.createTextNode(s.th.map((x) => '#' + x).join(' '))]));
  // 保有の行（holdRow・positionRow）が同じ一行を出すので、持っていない銘柄だけ
  if (s && s.cw && !readPos().some((p) => p.code === code) && !readHold().some((p) => p.code === code)) lines.push(h('div', { class: 'guard__note guard__note--warn', text: crowdNote(s) }));
  if (s && s.ev && s.ev.label) lines.push(h('div', { class: 'guard__note guard__note--' + (s.ev.dir === 'down' ? 'warn' : 'info'), text: `${s.ev.label}（${md(s.ev.date)}・その後 ${fmtPct(s.ev.since, 1)}）` }));
  if (le) {
    lines.push(h('div', { class: 'sd__line' }, [h('b', { text: `発掘（${md(le.first_seen)}〜） ` }),
      document.createTextNode([(le.signals || []).join('・'), (le.why || []).join('／')].filter(Boolean).join('：'))]));
    if (le.notes) lines.push(h('div', { class: 'sd__line sd__line--dim', text: le.notes }));
  }
  const er = earnFor(code);
  er.self.forEach(({ src, it, ai }) => {
    lines.push(h('button', { class: 'sd__line sd__line--btn', type: 'button', onclick: earnJump }, [
      h('b', { text: `決算（${md(src.e.asof)}）${EARN_DIR[it.dir] ? EARN_DIR[it.dir][0] : ''} ` }),
      document.createTextNode(((it.flash || {}).headline || (it.titles || [])[0] || '') + (ai && ai.read ? `　${ai.read.split('。')[0]}。` : '') + ' ›'),
    ]));
  });
  er.peerOf.slice(0, 3).forEach(({ src, it, why }) => {
    lines.push(h('button', { class: 'sd__line sd__line--btn', type: 'button', onclick: earnJump }, [
      h('b', { text: `類似銘柄の決算（${md(src.e.asof)}） ` }),
      document.createTextNode(`${cleanName(it.name)}${EARN_DIR[it.dir] ? '（' + EARN_DIR[it.dir][0] + '）' : ''}: ` +
        ((it.flash || {}).headline || (it.titles || [])[0] || '') + (why && why.why ? `　${why.why}` : '') + ' ›'),
    ]));
  });
  const news = feed.items.filter((x) => titleHas(x.title, name, code)).slice(0, 6);
  const kids = [];
  if (disc.length) kids.push(disclosureRows(disc, 10));
  if (lines.length) kids.push(h('div', { class: 'sd__pad' }, lines));
  if (news.length) kids.push(h('div', { class: 'feed' }, news.map((x) => newsItem(x))));
  if (!kids.length) kids.push(h('p', { class: 'hint sd__pad', text: '今日の開示・ランキング・見出しには出ていません。' }));
  return kids;
}

/* 買う前／売る前チェック（impulseCheck の結果を出して、判断メモに残せる） */
function checkBox(code) {
  const result = h('div', { class: 'check__result' });
  const draw = (intent) => {
    sheetState.intent = intent;
    result.textContent = '';
    const r = impulseCheck(code, intent);
    if (!r) {
      result.appendChild(h('div', { class: 'empty', text: 'この銘柄の日足がまだありません（日経225採用・ウォッチリスト・テーマ辞書・台帳の銘柄が対象。ウォッチリストに入れると次の更新で入ります）' }));
      return;
    }
    result.appendChild(h('div', { class: 'verdict verdict--' + r.level, text: r.title }));
    const list = (items, tone, head) => items.length ? h('div', { class: 'check__list' }, [
      h('div', { class: 'check__lh', text: head }),
      h('ul', { class: 'reasons' }, items.map((x) => h('li', { class: 'guard__note guard__note--' + tone, text: x }))),
    ]) : null;
    [list(r.bad, 'warn', intent === 'buy' ? '待ったほうがよい理由' : 'いま売らないほうがよい理由'),
      list(r.good, 'chance', intent === 'buy' ? '買ってよい理由' : '売る理由として正当なもの'),
      list(r.plan, 'info', '具体的には')].forEach((n) => n && result.appendChild(n));
    result.appendChild(h('button', { class: 'btn btn--primary', type: 'button', text: 'この判断をメモする', onclick: (e) => {
      const log = readLog();
      log.push({ at: (THERMO && THERMO.asof) || (DATA && DATA.date), code, name: r.s.n, intent, verdict: r.title, level: r.level, price: r.s.price });
      writeLog(log);
      dirty.add('verify');
      e.target.textContent = 'メモしました（検証タブの「自分の記録」）';
      e.target.disabled = true;
    } }));
  };
  const box = h('div', {}, [
    h('p', { class: 'hint', style: 'margin:0 0 8px', text: '上がると買いたくなる・下がると売りたくなる、の逆に立つための確認です。いまの気持ちに近いほうを押してください。' }),
    h('div', { class: 'check__btns' }, [
      h('button', { class: 'btn btn--buy', type: 'button', text: '買いたい', onclick: () => draw('buy') }),
      h('button', { class: 'btn btn--sell', type: 'button', text: '売りたい', onclick: () => draw('sell') }),
    ]),
    result,
    h('p', { class: 'hint', text: '買う側は短期の押し目買いのルールでの位置だけで判定します（注文対象か、いくらまで待つか）。売る側は、押し目買いで買った銘柄なら計画（売り指値・損切り・期限）を、' +
      'それ以外は保有株の判定（20日の騰落と2日RSI、4年で検証）と、狼狽売りになりやすい形かを見ます。売買の推奨ではありません。' }),
  ]);
  if (sheetState.intent) draw(sheetState.intent);
  return box;
}

function stockView(code) {
  const s = stockOf(code);
  const uni = stockUniverse().get(code) || { code, name: '', ctx: [] };
  const name = cleanName((s && JA_RE.test(s.n || '') ? s.n : null) || uni.name || (s || {}).n) || code;
  const price = s && isNum(s.price) ? s.price : uni.price;
  const d1 = s && isNum(s.d1) ? s.d1 : uni.change_pct;
  const sw = (s || {}).sw || {};
  const swx = SW() || {};
  const oi = (swx.orders || []).findIndex((o) => o.code === code);
  const order = oi >= 0 ? swx.orders[oi] : (swx.more || []).find((o) => o.code === code) || null;
  const pos = readPos().find((p) => p.code === code) || null;
  const own = readHold().find((p) => p.code === code) || null;
  const wl = effectiveWatchlist(sessData()).find((w) => String(w.code) === code) || null;
  const le = ((LEDGER && LEDGER.entries) || []).find((e) => e.code === code && e.status !== 'closed');
  const back = sheetState.back !== null && sheetState.back !== undefined ? () => openSearch(sheetState.back) : null;
  const sector = (s && s.s) || null;
  const kids = [sheetHead(name, [code, sector, sw.g && sw.g !== sector ? sw.g : null].filter(Boolean).join('・'), back)];

  const mo = midOrderOf(code);
  const moi = mo ? ((MID() || {}).orders || []).indexOf(mo) : -1;
  const badges = [];
  if (order) badges.push(h('span', { class: 'badge badge--ok', text: oi >= 0 ? `注文 ${oi + 1}番目` : '注文の次点' }));
  if (mo) badges.push(h('span', { class: 'badge badge--ok', text: `中期の注文 ${moi + 1}番目` }));
  if (pos || own) badges.push(h('span', { class: 'badge badge--accent', text: pos ? (isMidPos(pos) ? '保有（中期の押し目）' : '保有（押し目買い）') : '保有（自分の判断）' }));
  if (wl) badges.push(h('span', { class: 'badge', text: 'ウォッチ' }));
  if (le) badges.push(h('span', { class: 'badge', text: '発掘' }));
  kids.push(h('div', { class: 'sd__price' }, [
    h('span', { class: 'sd__last num', text: isNum(price) ? fmtPrice(price) + '円' : '—' }),
    h('span', { class: 'sd__chg num ' + cls(d1), text: isNum(d1) ? fmtPct(d1) : '' }),
    s && THERMO ? h('span', { class: 'sd__asof', text: `${md(THERMO.asof)} 時点` }) : null,
  ]));
  if (badges.length) kids.push(h('div', { class: 'chips sd__badges' }, badges));

  if (s) {
    kids.push(priceChart(code, contextLines(code)));
    kids.push(h('div', { class: 'plan__grid sd__stats' }, [
      cell('2日RSI', isNum(sw.rsi2) ? String(Math.round(sw.rsi2)) : '—', `${rules().rsi_max}未満で押し`),
      cell('RSI(14)', isNum(s.rsi) ? String(Math.round(s.rsi)) : '—'),
      cell('25日線から', fmtPct(s.dev25, 1), null, cls(s.dev25)),
      cell('60日高値から', fmtPct(s.from_hi, 1), null, cls(s.from_hi)),
      cell('5日', fmtPct(s.r5, 1), null, cls(s.r5)),
      cell('20日', fmtPct(s.r20, 1), null, cls(s.r20)),
      cell('60日', fmtPct(s.r60, 1), null, cls(s.r60)),
      cell('売買代金', isNum(sw.tv) ? `${sw.tv >= 100 ? Math.round(sw.tv) : sw.tv.toFixed(1)}億` : '—', '20日平均'),
    ]));
  }

  // ルールでの位置（買う候補を出すのは短期の押し目買いと中期の押し目の2つだけ。1銘柄に1つの計画）
  const ws = watchState(code);
  const [wlabel, wtone] = WATCH_STATE[ws.key] || ['—', ''];
  const pi = PEER_INFO[sw.pc];
  const mdx = sw.md;
  kids.push(sdSection(mdx ? '中期の押し目のルール' : '押し目買いのルール', [
    h('div', { class: 'sd__pad' }, [
      h('div', { class: 'sd__state' }, [h('span', { class: 'badge' + (wtone ? ' badge--' + wtone : ''), text: wlabel }), h('span', { text: ws.text })]),
      mdx ? h('div', { class: 'order__peer' }, [h('span', { class: 'badge badge--accent', text: '中期' }),
        h('span', { class: 'order__peer-t', text: `1年の強さ 上位${Math.max(1, 100 - mdx.rank)}%・60日高値 ${fmtPrice(mdx.hi)}円・調整の安値 ${fmtPrice(mdx.low)}円。` +
          '1年で強く上げた銘柄の1〜3か月の調整。押した日に指値、調整の安値を割ったら手仕舞い、60営業日持つ' })]) : null,
      // 注文があれば業種の行は注文の中に出る
      !order && !mdx && sw.g && pi && !['plain', 'none'].includes(sw.pc) ? h('div', { class: 'order__peer' }, [peerBadge(sw.pc), h('span', { class: 'order__peer-t', text: peerLine(sw.g, sw.g20, sw.rel) })]) : null,
      !order && sw.g && strengthOf(sw.g) ? h('div', { class: 'order__peer' }, [quadBadge(strengthOf(sw.g).quad), h('span', { class: 'order__peer-t', text: strengthLine(sw.g) + '（目安。注文の条件には使わない）' })]) : null,
    ]),
    mo ? h('div', { class: 'orders sd__order' }, [midOrderCard(mo, moi + 1, orderDay(swx.asof),
      moi >= rules().mid_slots ? `中期の枠（${rules().mid_slots}銘柄）を超える分。保有が減れば同じ条件で使えます` : null, true)]) : null,
    order ? h('div', { class: 'orders sd__order' }, [orderCard(order, oi >= 0 ? oi + 1 : null, orderDay(swx.asof), oi >= 0 ? null : '上限で外れた次点。枠が空いていれば同じ条件で使えます', true)]) : null,
  ]));

  // 保有
  const holdKids = [];
  if (pos) holdKids.push(h('div', { class: 'orders' }, [positionRow(pos, true)]));
  else if (own) holdKids.push(h('div', { class: 'orders' }, [holdRow(own, true)]));
  else {
    // 持っていない銘柄に保有の判定は出さない（買いの注文と並ぶと矛盾して見える。持っているなら下の「売りたい」で判定が出る）
    const box = h('div', { class: 'sd__pad' });
    box.appendChild(h('p', { class: 'hint', style: 'margin:0 0 6px', text: mo ? '注文が約定したら、上の「買えた」で保有銘柄に入ります（中期の押し目の計画で手仕舞い）。'
      : order ? '注文が約定したら、上の「買えた」で保有銘柄に入ります（押し目買いの計画で手仕舞い）。' : '持っていません。' }));
    box.appendChild(h('button', { class: 'btn btn--ghost', type: 'button', text: '＋ 保有銘柄に入れる（自分の判断で持っている）', onclick: (e) => {
      e.currentTarget.hidden = true;
      box.appendChild(holdForm({ code, name, entry: null, shares: null, stop: null, fresh: true }));
    } }));
    holdKids.push(box);
  }
  kids.push(sdSection('保有', holdKids));
  kids.push(sdSection('今日の材料', stockMaterials(code, name, s)));
  kids.push(sdSection('買う前／売る前チェック', h('div', { class: 'sd__pad' }, [checkBox(code)])));

  // ☆ウォッチ・株探
  const msg = h('div', { class: 'status' + (sheetState.msg ? ' is-ok' : ''), text: sheetState.msg || '' });
  kids.push(h('div', { class: 'sd__acts' }, [
    h('button', { class: 'btn' + (wl ? ' btn--on' : ''), type: 'button', text: wl ? '★ ウォッチ中（外す）' : '☆ ウォッチに入れる', onclick: async (e) => {
      e.currentTarget.disabled = true;
      const r = await toggleWatch(code);
      sheetState.msg = r.msg;
      VIEWS.forEach((v) => dirty.add(v));
      refreshPlan();
    } }),
    h('a', { class: 'btn btn--ghost', href: stockUrl(code), target: '_blank', rel: 'noopener', text: '株探で開く ↗' }),
    msg,
  ]));
  sheetState.msg = null;
  return h('div', {}, kids);
}

/* ==================== 検証 ====================
   ルールと判定が効いているかを1か所で見る。各画面には結論の一行（比べる相手・偏りつき）だけを置き、表はここに集める。 */
function renderVerify() {
  const th = THERMO || {};
  const out = [];
  if (!THERMO) {
    out.push(card('検証', null, h('div', { class: 'empty', text: 'データがまだありません。次の自動更新で作られます。' })));
  } else {
    out.push(statsCard());
    out.push(midStatsCard());
    out.push(ruleCard());
    out.push(holdProofCard());
    out.push(crowdProofCard());
    out.push(backtestCard(th.backtest));
    out.push(trackCard(th.track));
    const st = ST();
    if (st && (st.rows || []).length) out.push(strengthGuideCard(st));
    out.push(ledgerCard());
  }
  out.push(myRecordCard());
  if (THERMO) {
    const cov = th.coverage || {};
    out.push(h('p', { class: 'hint', style: 'margin:4px 4px 12px', text:
      `${SLOT_LABEL[th.slot] || ''} ${String(th.generated_at || '').slice(5, 16).replace('T', ' ')} 生成。四本値: ${cov.ohlc_stocks ?? '—'}銘柄（${cov.ohlc_days ?? '—'}営業日）、` +
      `温度計の日足: マクロ ${cov.macro ?? '—'}系列・個別株 ${cov.stocks ?? '—'}銘柄、予想EPS ${cov.eps_days ?? '—'}日分、見出し ${cov.news_titles ?? '—'}本。` +
      '規則と閾値は dashboard/swing.py・hold.py・sectors.py・thermo.py に公開しています。' }));
  }
  return out;
}

/* ==================== 編集シート ==================== */
let sheetCodes = [];
let sheetNames = new Map();

function openSheet(d) {
  const items = effectiveWatchlist(d);
  sheetCodes = items.map((s) => String(s.code));
  sheetNames = new Map(items.map((s) => [String(s.code), s.name || '']));
  $('wlRepo').value = localStorage.getItem(LS.repo) || DEFAULT_REPO;
  $('wlToken').value = localStorage.getItem(LS.token) || '';
  $('wlStatus').textContent = '';
  $('wlStatus').className = 'status';
  // 端末だけの登録が残っていてトークンも無いなら、最初から連携設定を開いておく
  $('wlSettingsBox').hidden = !(items.some((s) => s.pending) && !hasGitHubToken());
  drawSheet();
  $('wlSheet').showModal();
}

function drawSheet() {
  const list = $('wlList');
  list.textContent = '';
  if (!sheetCodes.length) {
    list.appendChild(h('p', { class: 'hint', text: 'まだ登録がありません。証券コードを追加してください。' }));
    return;
  }
  sheetCodes.forEach((code, i) => {
    list.appendChild(h('div', { class: 'editrow' }, [
      h('span', { class: 'editrow__code num', text: code }),
      h('span', { class: 'editrow__name', text: cleanName(sheetNames.get(code)) || '' }),
      h('button', { class: 'editrow__del', type: 'button', 'aria-label': code + 'を削除', text: '✕',
                    onclick: () => { sheetCodes.splice(i, 1); drawSheet(); } }),
    ]));
  });
}

function setStatus(msg, kind) {
  const el = $('wlStatus');
  el.textContent = msg;
  el.className = 'status' + (kind ? ' is-' + kind : '');
}

function b64utf8(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin);
}

async function pushToGitHub(codes) {
  const token = localStorage.getItem(LS.token);
  const repo = localStorage.getItem(LS.repo) || DEFAULT_REPO;
  if (!token) return { synced: false };
  const path = 'docs/data/watchlist.json';
  const api = `https://api.github.com/repos/${repo}/contents/${path}`;
  const headers = { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' };
  let sha;
  const cur = await fetch(api, { headers });
  if (cur.ok) sha = (await cur.json()).sha;
  else if (cur.status !== 404) throw new Error(`読み込み失敗 (${cur.status})`);
  const body = {
    message: 'ウォッチリストを更新（ダッシュボードから）',
    content: b64utf8(JSON.stringify({ codes, updated_at: new Date().toISOString() }, null, 2) + '\n'),
  };
  if (sha) body.sha = sha;
  const res = await fetch(api, { method: 'PUT', headers, body: JSON.stringify(body) });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`保存失敗 (${res.status}) ${t.slice(0, 120)}`);
  }
  return { synced: true };
}

function bindSheet() {
  $('wlAdd').addEventListener('click', () => {
    const input = $('wlInput');
    const code = input.value.trim().toUpperCase();
    if (!/^[0-9]{3}[0-9A-Z]$/.test(code)) { setStatus('証券コードは4桁（例: 7203）で入力してください', 'err'); return; }
    if (sheetCodes.includes(code)) { setStatus('すでに登録されています', 'err'); return; }
    if (sheetCodes.length >= 40) { setStatus('登録は40銘柄までです', 'err'); return; }
    sheetCodes.push(code);
    input.value = '';
    setStatus('');
    drawSheet();
  });
  $('wlInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('wlAdd').click(); } });
  $('wlSave').addEventListener('click', async () => {
    localStorage.setItem(LS.codes, JSON.stringify(sheetCodes));
    setStatus('この端末に保存しました。反映中…');
    try {
      const r = await pushToGitHub(sheetCodes);
      if (r.synced) {
        setStatus('リポジトリに保存しました。株価の取得が走ります（数分後に再読み込みしてください）', 'ok');
      } else {
        // トークンが無いとサーバは端末の編集内容を知る手段がない（自動更新を待っても反映されない）
        setStatus('この端末にだけ保存しました。サーバ側の収集に載せるには、下の連携設定でトークンを保存してから、もう一度「保存して反映」を押してください', 'err');
        $('wlSettingsBox').hidden = false;
      }
    } catch (e) {
      setStatus('端末には保存しましたが、リポジトリ側は失敗しました: ' + e.message, 'err');
    }
    refreshPlan();
  });
  $('wlSettings').addEventListener('click', () => { const box = $('wlSettingsBox'); box.hidden = !box.hidden; });
  $('wlTokenSave').addEventListener('click', () => {
    const t = $('wlToken').value.trim();
    const r = $('wlRepo').value.trim() || DEFAULT_REPO;
    if (!t) { setStatus('トークンが空です', 'err'); return; }
    localStorage.setItem(LS.token, t);
    localStorage.setItem(LS.repo, r);
    setStatus('連携設定を保存しました', 'ok');
  });
  $('wlTokenClear').addEventListener('click', () => {
    localStorage.removeItem(LS.token);
    $('wlToken').value = '';
    setStatus('トークンを削除しました', 'ok');
  });
}

/* ==================== 上端: 時間帯とジャンプ ==================== */
const JUMPS = {
  trade: [['sw-orders', '短期'], ['sw-mid', '中期'], ['sw-pos', '保有銘柄'], ['sw-watch', '監視']],
  preopen: [['sec-summary', '要点'], ['sec-open', '想定オープン'], ['sec-us', '米国'], ['sec-macro', 'マクロ'], ['sec-risk', 'リスク'],
    ['sec-outlook', '連想'], ['sec-temp', '温度']],
  session: [['sec-summary', '要点'], ['sec-index', '指数'], ['sec-macro', 'マクロ'], ['sec-sector', '業種'], ['sec-theme', 'テーマ'], ['sec-rank', 'ランキング'],
    ['sec-heat', 'ヒートマップ'], ['sec-temp', '温度']],
  trend: [['tr-index', '指数の推移'], ['tr-daily', '日々の記録'], ['tr-weekly', '週報']],
  sectors: [['sc-hero', '追い風'], ['sc-map', '4象限'], ['sc-list', '全業種'], ['sc-nikkei', '日経の業種'], ['sc-themes', 'テーマ']],
  news: [['nw-mine', '自分の銘柄'], ['nw-earn', '決算から読む'], ['nw-disc', '開示'], ['nw-feed', 'ニュース']],
  verify: [['v-swing', '成績'], ['v-mid', '中期'], ['v-rule', 'ルール'], ['v-hold', '保有株の判定'], ['v-thermo', '温度計'], ['v-track', '警告'],
    ['v-sector', '業種'], ['v-ledger', '発掘'], ['v-mine', '自分の記録']],
};

function drawSlotBar() {
  const bar = $('slotBar');
  bar.textContent = '';
  if (activeView !== 'market') return;
  const slots = (DATA && DATA.slots) || {};
  MARKET_TABS.forEach((s) => {
    const entry = slots[s];
    const time = s === 'trend' ? (HIST.dates.length ? `${HIST.dates.length}営業日` : '読み込み中')
      : entry ? (entry.updated_at || '').slice(11, 16) + ' 更新' : '未取得';
    bar.appendChild(h('button', { class: 'slot' + (s === 'trend' || entry ? '' : ' slot--missing'), role: 'tab',
      'aria-selected': String(s === activeSlot), onclick: () => selectSlot(s) }, [
      document.createTextNode(SLOT_LABEL[s]), h('span', { class: 'slot__time', text: time }),
    ]));
  });
}

function drawJumpBar() {
  const bar = $('jumpBar');
  bar.textContent = '';
  const key = activeView === 'market' ? (activeSlot === 'preopen' ? 'preopen' : activeSlot === 'trend' ? 'trend' : 'session') : activeView;
  const panel = $('view-' + activeView);
  const btns = (JUMPS[key] || []).map(([id, label]) => {
    const target = panel.querySelector('#' + id);
    return target ? h('button', { class: 'jump__btn', type: 'button', text: label,
      onclick: () => target.scrollIntoView({ behavior: 'smooth', block: 'start' }) }) : null;
  }).filter(Boolean);
  if (btns.length >= 2) btns.forEach((b) => bar.appendChild(b));
}

/* ==================== 描画・ブート ==================== */
function renderMarket() {
  if (activeSlot === 'trend') return renderTrend();
  const entry = (DATA.slots || {})[activeSlot];
  if (!entry) return [noData(activeSlot)];
  return activeSlot === 'preopen' ? renderPreopen(entry.data || {}, entry.updated_at)
    : renderSession(entry.data || {}, activeSlot, entry.updated_at);
}
const RENDER = { trade: renderTrade, market: renderMarket, sectors: renderSectors, news: renderNews, verify: renderVerify };

/* 開いている画面だけを描く（ほかの画面は開いたときに描く） */
function render() {
  if (!DATA) return;
  const dt = DATA.date ? new Date(DATA.date + 'T00:00:00+09:00') : null;
  $('headDate').textContent = dt
    ? dt.toLocaleDateString('ja-JP', { month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Tokyo' }) : '—';
  if (dirty.has(activeView)) {
    const el = $('view-' + activeView);
    el.textContent = '';
    RENDER[activeView]().forEach((n) => n && el.appendChild(n));
    dirty.delete(activeView);
  }
  drawSlotBar();
  drawJumpBar();
  updateFreshness();
  $('footMeta').textContent = 'データ生成: ' + (DATA.generated_at || '—');
}

function updateFreshness() {
  const dot = $('freshDot');
  let upd = null, label = '';
  if (activeView === 'market' && activeSlot !== 'trend') {
    const entry = (DATA.slots || {})[activeSlot];
    if (!entry) { $('headUpdated').textContent = '未取得'; dot.className = 'dot'; return; }
    upd = entry.updated_at || '';
    label = SLOT_LABEL[activeSlot] + ' ' + upd.slice(11, 16) + ' 更新';
  } else if (activeView === 'trade' || activeView === 'verify' || activeView === 'sectors') {
    upd = (THERMO && THERMO.generated_at) || DATA.generated_at || '';
    label = upd ? `${upd.slice(0, 10) !== isoToday() ? md(upd) + ' ' : ''}${upd.slice(11, 16)} 更新` : '—';
  } else {
    const ls = latestSlot();
    upd = ls ? DATA.slots[ls].updated_at : DATA.generated_at || '';
    label = upd ? `${ls ? SLOT_LABEL[ls] + ' ' : ''}${String(upd).slice(11, 16)} 更新` : '—';
  }
  $('headUpdated').textContent = label;
  const age = upd ? (Date.now() - new Date(upd).getTime()) / 3600000 : null;
  dot.className = 'dot' + (isNum(age) ? (age < 12 ? ' dot--fresh' : ' dot--stale') : '');
}

function selectSlot(slot) {
  activeSlot = slot;
  try { sessionStorage.setItem(LS.tab, slot); } catch (e) { /* 非対応環境は無視 */ }
  dirty.add('market');
  render();
  window.scrollTo({ top: 0 });
}

/* 画面を切り替える。anchor を渡すとその見出しまで送る（「検証を見る」など） */
function selectView(view, anchor) {
  activeView = view;
  try { sessionStorage.setItem(LS.view, view); } catch (e) { /* 非対応環境は無視 */ }
  VIEWS.forEach((v) => { $('view-' + v).hidden = v !== view; });
  document.querySelectorAll('.bottomnav__btn').forEach((b) => {
    if (b.dataset.view === view) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  $('viewTitle').textContent = VIEW_TITLE[view];
  closeSheet();
  if (DATA) render(); else { drawSlotBar(); drawJumpBar(); }
  const target = anchor ? document.getElementById(anchor) : null;
  if (target) target.scrollIntoView({ block: 'start' });
  else window.scrollTo({ top: 0 });
}

function defaultSlot() {
  const slots = DATA.slots || {};
  const saved = (() => { try { return sessionStorage.getItem(LS.tab); } catch (e) { return null; } })();
  if (saved && MARKET_TABS.includes(saved) && (saved === 'trend' || slots[saved])) return saved;
  const n = jstNow();
  const mins = n.getHours() * 60 + n.getMinutes();
  const wanted = mins < 10 * 60 ? 'preopen' : mins < 15 * 60 + 30 ? 'zenba' : 'taibike';
  if (slots[wanted]) return wanted;
  const available = SLOTS.filter((s) => slots[s]);
  return available.length ? available[available.length - 1] : wanted;
}

async function fetchJson(path) {
  try {
    const res = await fetch(path + (path.includes('?') ? '&' : '?') + 't=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) { return null; }
}

async function loadHistory() {
  const idx = await fetchJson('data/history/index.json');
  const dates = (idx && Array.isArray(idx.dates) ? idx.dates : []).slice(-HISTORY_DAYS);
  const files = await Promise.all(dates.map((d) => HIST.byDate.has(d) ? HIST.byDate.get(d) : fetchJson(`data/history/${d}.json`)));
  HIST.dates = dates.filter((d, i) => files[i]);
  HIST.byDate = new Map(HIST.dates.map((d) => [d, files[dates.indexOf(d)]]));
  HIST.loaded = true;
}

let loadedKey = null;
async function load() {
  const [latest, ledger, weekly, thermo] = await Promise.all([
    fetchJson('data/latest.json'), fetchJson('data/ledger.json'), fetchJson('data/weekly.json'),
    fetchJson('data/thermo.json')]);
  // 中身が変わっていなければ描き直さない（アプリを行き来しても、入力中の「買えた」やチェックが消えないように）
  const key = [latest && latest.generated_at, thermo && thermo.generated_at, ledger && ledger.updated_at,
    weekly && weekly.generated_at].join('|');
  if (key === loadedKey && HIST.loaded) return;
  loadedKey = key;
  if (latest) DATA = latest;
  else if (!DATA) { DATA = { slots: {} }; $('headDate').textContent = 'データを読み込めませんでした'; }
  LEDGER = ledger;
  WEEKLY = weekly;
  THERMO = thermo;
  if (!activeSlot) activeSlot = defaultSlot();
  VIEWS.forEach((v) => dirty.add(v));
  render();
  if ($('stockSheet').open) drawSheetBody(false);
  await loadHistory();
  // 履歴を使う画面（市況の推移・業種の8日・検索）だけ描き直し印を付ける
  ['market', 'sectors'].forEach((v) => dirty.add(v));
  render();
}

/* 以前の画面の名前（sessionStorage に残っていることがある）を新しい画面に読み替える */
const OLD_VIEW = { today: 'market', thermo: 'trade', discover: 'trade', stocks: 'trade', history: 'market' };

function boot() {
  document.querySelectorAll('.bottomnav__btn').forEach((b) => b.addEventListener('click', () => selectView(b.dataset.view)));
  $('reloadBtn').addEventListener('click', load);
  $('searchBtn').addEventListener('click', () => openSearch(''));
  const sheet = $('stockSheet');
  sheet.addEventListener('click', (e) => { if (e.target === sheet) closeSheet(); });       // 外側を押すと閉じる
  sheet.addEventListener('cancel', (e) => { e.preventDefault(); closeSheet(); });          // Esc
  sheet.addEventListener('close', () => { if (!sheet.open) sheetState = null; });
  window.addEventListener('popstate', () => { if (sheet.open) sheet.close(); });            // 戻る（端末のボタンも）で閉じる
  // シートを開いたまま再読み込みすると履歴の印だけが残るので消す（閉じたときに余分に戻らないように）
  if (history.state && history.state.sheet) { try { history.replaceState(null, ''); } catch (e) { /* 非対応環境は無視 */ } }
  bindSheet();
  const saved = (() => { try { return sessionStorage.getItem(LS.view); } catch (e) { return null; } })();
  if (saved === 'history') { try { sessionStorage.setItem(LS.tab, 'trend'); } catch (e) { /* 非対応環境は無視 */ } }
  const view = OLD_VIEW[saved] || saved;
  selectView(view && VIEWS.includes(view) ? view : 'trade');
  load();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden) load(); }, 5 * 60 * 1000);
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* 未対応・ローカル環境は無視 */ });
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
})();
