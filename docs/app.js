/* マーケット — 描画ロジック
   外部ライブラリなし。データは docs/data/ 配下の JSON から読む。
   本文はスクレイピング由来のため、DOM 生成は textContent 経由で行う
   （h() の html: は自前で組み立てた SVG 専用）。

   画面は4つ:
     今日   … 寄り前 / 前場 / 大引 を切り替えて読む（長い表は畳んで出す）
     発掘   … 連続ランクイン・新規流入・業績修正など「候補」を溜めて見る
     履歴   … 過去の営業日を1行ずつ。日経の推移と当日の顔ぶれ
     銘柄   … ウォッチリストと、コード／社名での横断検索 */
(() => {
'use strict';

const SLOTS = ['preopen', 'zenba', 'taibike'];
const SLOT_LABEL = { preopen: '寄り前', zenba: '前場', taibike: '大引' };
const VIEWS = ['today', 'discover', 'history', 'stocks'];
const VIEW_TITLE = { today: '今日', discover: '発掘', history: '履歴', stocks: '銘柄' };
const DEFAULT_REPO = 'tonpe3141-ctrl/kabutan-digest';
const LS = { codes: 'md.watchlist.codes', token: 'md.gh.token', repo: 'md.gh.repo', tab: 'md.tab', view: 'md.view' };
const HISTORY_DAYS = 15;            // 履歴タブと発掘タブが読み込む営業日数

let DATA = null;
let LEDGER = null;                  // docs/data/ledger.json（サーバ側の台帳。あれば優先）
let WEEKLY = null;                  // docs/data/weekly.json（金曜の週報）
let HIST = { dates: [], byDate: new Map(), loaded: false };
let activeSlot = null;
let activeView = 'today';

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
      h('div', { class: 'row__delta num ' + cls(r.change_pct), text: fmtPct(r.change_pct) }),
    ]),
  ];
  return r.code
    ? h('a', { class: 'row', href: stockUrl(r.code), target: '_blank', rel: 'noopener' }, inner)
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
    return h('a', { class: 'row', href: stockUrl(r.code), target: '_blank', rel: 'noopener' }, [
      h('div', { class: 'row__rank num', text: (r.time || '').slice(0, 5) }),
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name', text: cleanName(r.name) || r.code }),
        h('div', { class: 'row__meta' }, [h('span', { text: r.code }), h('span', { text: r.title || '' })]),
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
  const cell = h('a', {
    class: 'heat__cell', title: `${r.name || ''} ${r.code} ${fmtPct(v)}`,
    href: stockUrl(r.code), target: '_blank', rel: 'noopener',
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

function analysisCard(d) {
  const picked = pickCommentary(d);
  if (!picked) return null;
  const { c, isAi } = picked;
  const note = isAi
    ? '生成AIによる分析です。数値データと相場振り返り記事をもとに作成していますが、誤りを含む可能性があります。売買を推奨するものではありません。'
    : '各カードの数値を組み合わせて機械的に文章化したもの。同じ数字からは同じ文章が出ます。売買を推奨するものではありません。';

  const body = [
    h('p', { class: 'analysis__headline', text: c.headline || '' }),
    h('div', {}, c.sections.map((s) => h('div', { class: 'analysis__sec' }, [
      h('div', { class: 'analysis__t', text: s.title }),
      h('div', { class: 'analysis__b', text: s.body }),
    ]))),
  ];
  if (isAi && Array.isArray(c.sources) && c.sources.length) {
    body.push(h('div', { class: 'analysis__srcs' }, c.sources.map((src) =>
      h('a', { href: src.url, target: '_blank', rel: 'noopener', text: '📰 ' + src.title }))));
  }
  return card('相場の見立て', isAi ? (c.method || 'AI分析') : 'ルールベース', body, note, false, 'sec-analysis');
}

function newsCard(news) {
  if (!Array.isArray(news) || !news.length) return null;
  return card('相場ニュース', `${news.length}本`,
    h('div', {}, news.map((n) => accordion(n.headline || '（見出しなし）',
      [n.category, n.timestamp].filter(Boolean).join('　'), n.body, n.url, n.source || '元記事を開く'))),
    null, true, 'sec-news');
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
      chips.push(h('span', { class: 'badge badge--warn', text: '候補入り: ' + lt.added.slice(0, 3).map((a) => a.name).join('・') }));
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
      chips.push(h('span', { class: 'badge' + (rev && rev.count >= 5 ? ' badge--warn' : ''), text: `開示 ${ds.total}件` + (rev ? `・修正 ${rev.count}` : '') }));
    }
  }

  return h('section', { class: 'card summary', id: 'sec-summary' }, [
    headline ? h('p', { class: 'summary__head', text: headline }) : null,
    stats.length ? h('div', { class: 'summary__stats' }, stats) : null,
    chips.length ? h('div', { class: 'summary__line' }, chips) : null,
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


/* ==================== 東証33業種・テーマ・時間軸・株探 ==================== */
function sectors33Card(sec) {
  const rows = (sec && sec.rows) || [];
  if (rows.length < 20) return null;
  const mk = (r) => ({
    label: r.sector, value: r.change_pct,
    sub: (r.leaders || []).map((l) => l.name).join('・') || null,
  });
  const all = rows.map(mk);
  const short = all.slice(0, 6).concat([{ gap: true }], all.slice(-6));
  const sub = isNum(sec.up) ? `上昇 ${sec.up} / 下落 ${sec.down} 業種` : (sec.timestamp || null);
  const pr = sec.prime;
  return card('東証33業種 騰落率', sub,
    foldable((n) => barList(n >= all.length ? all : short), all.length, short.length, '全33業種'),
    (pr ? `プライム ${pr.total}銘柄中 上昇 ${pr.up} / 下落 ${pr.down}。` : '') +
    '出典: 株探「本日の【業種】騰落ランキング」（Yahoo!ファイナンス配信）。業種名の下は上昇率上位の銘柄。',
    false, 'sec-sector33');
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

function trendCard(tr) {
  const rows = (tr && tr.rows) || [];
  if (rows.length < 4) return null;
  const fmt = (v) => (isNum(v) ? fmtPct(v, 1) : '—');
  const mk = (r) => h('div', { class: 'row' }, [
    h('div', { class: 'row__rank' }, []),
    h('div', { class: 'row__main' }, [
      h('div', { class: 'row__name', text: r.sector }),
      h('div', { class: 'row__meta' }, [
        h('span', { class: 'num', text: `今日 ${fmt(r.d0)}` }),
        h('span', { class: 'num', text: `20日 ${fmt(r.d20)}` }),
        r.label ? h('span', { class: 'tag', text: r.label }) : null,
      ]),
    ]),
    h('div', { class: 'row__right' }, [
      h('div', { class: 'row__price', style: 'font-size:10.5px;color:var(--text-faint);font-weight:500', text: '5日' }),
      h('div', { class: 'row__delta num ' + cls(r.d5), text: fmt(r.d5) }),
    ]),
  ]);
  const short = rows.slice(0, 5).concat(rows.slice(-5));
  return card('業種の時間軸', `${tr.days}営業日`,
    foldable((n) => h('div', { class: 'rows' }, (n >= rows.length ? rows : short).map(mk)), rows.length, short.length, '全業種'),
    tr.note, true, 'sec-trend');
}

function kabutanCards(kb) {
  const out = [];
  const heads = (kb && kb.headlines) || [];
  const arts = (kb && kb.articles) || [];
  if (heads.length) {
    out.push(card('株探の見出し', `${heads.length}本`,
      foldable((n) => h('div', { class: 'rows' }, heads.slice(0, n).map((x) => {
        const inner = [
          h('div', { class: 'row__rank num', text: (x.published || '').slice(11, 16) }),
          h('div', { class: 'row__main' }, [h('div', { class: 'row__name', style: 'white-space:normal', text: x.title })]),
          h('div', { class: 'row__right' }, []),
        ];
        return x.url ? h('a', { class: 'row', href: x.url, target: '_blank', rel: 'noopener' }, inner) : h('div', { class: 'row' }, inner);
      })), heads.length, 8, '全件'),
      '株探の記事見出し（Google ニュース経由）。本文は株探で読む。見出しの左は配信時刻。', true, 'sec-kabutan'));
  }
  if (arts.length) {
    out.push(card('株探の記事', `${arts.length}本`,
      h('div', {}, arts.map((a) => accordion(a.headline || '（見出しなし）', a.timestamp || null, a.body, a.url, 'Yahoo!ファイナンスで開く'))),
      null, true, out.length ? null : 'sec-kabutan'));
  }
  return out;
}

/* ==================== 今日: 寄り前 ==================== */
function renderPreopen(d) {
  const out = [];
  out.push(summaryCard(d, 'preopen'));
  const analysis = analysisCard(d);
  if (analysis) out.push(analysis);

  const io = d.implied_open;
  if (io) {
    const gapTxt = isNum(io.gap) ? `${fmtSigned(io.gap, 0)}円` : '';
    const aside = [h('span', { class: 'badge badge--accent', text: io.method_label || '' })];
    if (isNum(io.prev_close)) aside.push(h('div', { class: 'num', text: `前日終値 ${fmtNum(io.prev_close, 0)}` }));
    let note = null;
    if (io.method === 'model' && io.contributions) {
      note = '内訳: ' + io.contributions.map((c) => `${c.driver} ${c.display || ''} → ${fmtSigned(c.value)}pt`).join('　/　') +
             (io.formula ? `　（係数: ${io.formula}）` : '');
    }
    out.push(hero('日経平均 想定オープン', fmtPct(io.gap_pct), gapTxt, io.gap_pct, aside, note, 'neutral', 'sec-open'));
  }

  if (d.us && Object.keys(d.us).length) {
    out.push(card('米国市場', d.freshness && d.freshness.us_asof ? d.freshness.us_asof + ' 終値' : null,
      quoteTiles(d.us, ['spx', 'ndq', 'dji', 'sox', 'rut', 'vix']), null, false, 'sec-us'));
  }
  if (d.macro && Object.keys(d.macro).length) {
    out.push(card('為替・金利・商品', null, quoteTiles(d.macro, ['usdjpy', 'us10y', 'us2y', 'wti', 'gold']), null, false, 'sec-macro'));
  }

  if (d.risk) {
    out.push(card('リスク環境',
      h('span', { class: 'badge badge--' + (d.risk.tone === 'positive' ? 'up' : d.risk.tone === 'negative' ? 'down' : 'accent'), text: d.risk.label }),
      h('ul', { class: 'reasons' }, (d.risk.reasons || []).map((r) => h('li', { text: r }))), null, false, 'sec-risk'));
  }

  const so = d.sector_outlook;
  if (so && so.tailwind && so.tailwind.length) {
    const mk = (arr) => arr.map((s) => ({
      label: s.sector, value: s.score,
      sub: (s.drivers || []).map((x) => `${x.driver} ${x.display || fmtPct(x.raw)}`).join(' · '),
    }));
    const tw = mk(so.tailwind), hw = mk(so.headwind || []);
    const all = tw.slice(0, 4).concat(hw.length ? [{ gap: true }] : [], hw.slice(0, 4));
    out.push(card('米国からの連想: 追い風 / 向かい風', '相対スコア',
      foldable((n) => barList(n >= all.length ? tw.concat([{ gap: true }], hw) : all, { raw: true }),
               tw.length + hw.length + 1, all.length, '全業種'),
      '各業種を説明する米国側の指標（半導体・金利・為替など）の前日変化を重み付けした相対スコア。株価の予測ではなく、どの物色テーマに追い風が吹いているかの並び順。',
      false, 'sec-outlook'));
  }

  if (d.sectors_us && Object.keys(d.sectors_us).length) {
    const items = Object.values(d.sectors_us).filter((s) => isNum(s.change_pct))
      .sort((a, b) => b.change_pct - a.change_pct).map((s) => ({ label: s.label, value: s.change_pct }));
    out.push(card('米国セクターローテーション', '前日比',
      foldable((n) => barList(items.slice(0, n)), items.length, 6, '12セクター'), null, false, 'sec-ussector'));
  }

  const co = d.carryover || {};
  if (co.after_hours_kessan && co.after_hours_kessan.length) {
    const rows = co.after_hours_kessan;
    out.push(card('前営業日の引け後 開示', '今日の寄りで動きやすい',
      foldable((n) => disclosureRows(rows, n), rows.length, 8, '全件'), null, true, 'sec-disc'));
  }
  if (co.prev_session && co.prev_session.session_shift) {
    out.push(card('前営業日の引け方', fmtDate(co.prev_session.date),
      h('p', { class: 'hint', style: 'font-size:13px;color:var(--text-dim);margin:0', text: co.prev_session.session_shift.verdict })));
  }

  kabutanCards(d.kabutan).forEach((c) => out.push(c));
  const news = newsCard(d.news);
  if (news) out.push(news);
  out.push(watchlistCard(d, 'sec-watch'));
  return out;
}

/* ==================== 今日: 前場・大引 ==================== */
function indexTiles(indices) {
  const items = Object.values(indices || {}).map((v) =>
    tile(v.label + (v.stale ? '（前日）' : ''), fmtNum(v.close, 2),
         fmtSigned(v.change, 2) + '  ' + fmtPct(v.change_pct), v.change_pct));
  if (!items.length) return h('div', { class: 'empty', text: '指数を取得できませんでした' });
  return h('div', { class: 'tiles tiles--3' }, items);
}

function renderSession(d, slot) {
  const out = [];
  out.push(summaryCard(d, slot));
  const analysis = analysisCard(d);
  if (analysis) out.push(analysis);

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

  const s33 = sectors33Card(d.sectors33);
  if (s33) out.push(s33);

  if (d.sectors_jp && d.sectors_jp.length >= 4) {
    const mk = (x) => ({
      label: `${x.sector}（${x.count}）`, value: x.avg_pct,
      sub: x.best && x.worst
        ? `高 ${x.best.name || x.best.code} ${fmtPct(x.best.change_pct, 1)} ／ 安 ${x.worst.name || x.worst.code} ${fmtPct(x.worst.change_pct, 1)}` : null,
    });
    const all = d.sectors_jp.map(mk);
    const short = all.slice(0, 5).concat([{ gap: true }], all.slice(-5));
    out.push(card(s33 ? '日経225採用銘柄の業種平均' : '業種別 騰落率', '225採用銘柄の平均',
      foldable((n) => barList(n >= all.length ? all : short), all.length, short.length, `全${all.length}業種`),
      '日経の業種区分で採用銘柄をまとめた単純平均。時価総額加重の東証33業種指数とは一致しません。', false, s33 ? 'sec-sector225' : 'sec-sector33'));
  }

  const theme = themeCard(d.theme_flow);
  if (theme) out.push(theme);
  const tr = trendCard(d.sector_trend);
  if (tr) out.push(tr);

  const heat = heatmapCard(d.constituents, d.breadth);
  if (heat) out.push(heat);

  const t = d.tables || {};
  if (t.value && t.value.rows && t.value.rows.length) {
    const dl = d.ranking_delta || {};
    const newSet = new Set((dl.new || []).map((n) => n.code));
    const upMap = new Map((dl.rank_up || []).map((n) => [n.code, n]));
    const streakMap = new Map((d.streaks || []).map((s) => [s.code, s]));
    const valueLabel = t.value.label || '売買代金';
    const rows = t.value.rows;
    out.push(card(valueLabel + ' 上位', valueLabel === '売買代金' ? '資金が向かった先' : '商いが膨らんだ銘柄',
      foldable((n) => stockRows(rows, {
        limit: n,
        meta: (r) => {
          const m = [];
          if (newSet.has(r.code)) m.push('🆕 新規');
          if (upMap.has(r.code)) m.push(`▲${upMap.get(r.code).jump}位`);
          if (streakMap.has(r.code)) m.push(`${streakMap.get(r.code).days}日連続`);
          return m;
        },
      }), rows.length, 10, `${rows.length}位まで`),
      (dl.new && dl.new.length) ? `前営業日から新たに上位入り: ${dl.new.slice(0, 6).map((n) => cleanName(n.name) || n.code).join('、')}` : null,
      true, 'sec-value'));
  }

  if ((t.gainer && t.gainer.rows.length) || (t.loser && t.loser.rows.length)) {
    const body = h('div', {});
    const draw = (which) => {
      body.textContent = '';
      const rows = (t[which] || {}).rows || [];
      body.appendChild(foldable((n) => stockRows(rows, { limit: n, meta: (r) => r.market ? [h('span', { text: r.market })] : [] }),
                                rows.length, 8, `${rows.length}位まで`));
    };
    const opts = ['gainer', 'loser'].filter((k) => t[k] && t[k].rows.length).map((k) => [k, k === 'gainer' ? '上昇率' : '下落率']);
    const seg = segmented(opts, draw, opts[0][0]);
    draw(opts[0][0]);
    out.push(card('値動きの大きかった銘柄', seg, body, null, true, 'sec-moves'));
  }

  if (t.ytd_high && t.ytd_high.rows && t.ytd_high.rows.length) {
    const rows = t.ytd_high.rows;
    out.push(card('年初来高値を更新した銘柄', `${rows.length}銘柄`,
      foldable((n) => stockRows(rows, { limit: n, meta: (r) => [r.market, isNum(r.metric) ? `前高値 ${fmtPrice(r.metric)}` : null].filter(Boolean).map((m) => h('span', { text: m })) }), rows.length, 8, '全件'),
      '右の％は前営業日までの年初来高値をどれだけ上抜けたか（前日比ではない）。ETF・REIT は除外。', true, 'sec-ytd'));
  }
  if (t.vol_surge && t.vol_surge.rows && t.vol_surge.rows.length) {
    const rows = t.vol_surge.rows;
    out.push(card('出来高が急増した銘柄', `${rows.length}銘柄`,
      foldable((n) => h('div', { class: 'rows' }, rows.slice(0, n).map((r, i) => h('a', { class: 'row', href: stockUrl(r.code), target: '_blank', rel: 'noopener' }, [
        h('div', { class: 'row__rank num', text: String(i + 1) }),
        h('div', { class: 'row__main' }, [
          h('div', { class: 'row__name', text: cleanName(r.name) || r.code }),
          h('div', { class: 'row__meta' }, [h('span', { text: r.code }), r.market ? h('span', { text: r.market }) : null,
            isNum(r.volume) ? h('span', { text: `出来高 ${fmtNum(r.volume, 0)}` }) : null]),
        ]),
        h('div', { class: 'row__right' }, [
          isNum(r.price) ? h('div', { class: 'row__price num', text: fmtPrice(r.price) }) : null,
          h('div', { class: 'row__delta num', text: isNum(r.metric) ? `×${fmtNum(r.metric, 1)}` : '—' }),
        ]),
      ]))), rows.length, 8, '全件'),
      '右は前日出来高に対する倍率。ETF・REIT は除外。低位株や小型株が多いので、売買代金上位との重なりを見る。', true, 'sec-volsurge'));
  }

  const discCard = (table, title, note, id) => {
    if (!table || !table.rows.length) return null;
    const material = table.rows.filter((r) => MATERIAL.includes(r.category));
    const rest = table.rows.filter((r) => !MATERIAL.includes(r.category));
    const main = material.length ? material : rest;
    const body = [foldable((n) => disclosureRows(main, n), main.length, 8, '全件')];
    if (material.length && rest.length) {
      body.push(h('details', { class: 'acc' }, [
        h('summary', {}, [h('span', { class: 'acc__title', text: `その他の開示 ${rest.length}件（自己株式取得・月次など）` })]),
        disclosureRows(rest, 40),
      ]));
    }
    const sum = d.disclosure_summary;
    const sub = sum ? h('div', { class: 'chips' }, sum.items.slice(0, 3).map((i) =>
      h('span', { class: 'badge' + (i.label === '業績予想の修正' ? ' badge--warn' : ''), text: `${i.label} ${i.count}` }))) : `${table.rows.length}件`;
    return card(title, sub, body, note, true, id);
  };
  const after = discCard(t.kessan_after, '引け後の開示', '翌営業日の寄りで値が飛びやすい。ウォッチリスト銘柄が含まれていないか確認する。', 'sec-disc');
  if (after) out.push(after);
  const intraday = discCard(t.kessan_intraday, '場中の開示', null, after ? 'sec-disc2' : 'sec-disc');
  if (intraday) out.push(intraday);

  kabutanCards(d.kabutan).forEach((c) => out.push(c));
  const news = newsCard(d.news);
  if (news) out.push(news);
  out.push(watchlistCard(d, 'sec-watch'));
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
  return codes.map((c) => byCode.get(String(c)) || { code: String(c), pending: true });
}

function watchlistCard(d, id) {
  const items = effectiveWatchlist(d);
  const editBtn = h('button', { class: 'btn btn--ghost', type: 'button', text: '編集', onclick: () => openSheet(d) });

  if (!items.length) {
    return card('ウォッチリスト', editBtn,
      h('p', { class: 'hint', style: 'margin:0', text: '証券コードを登録すると、保有・注目銘柄の値動きと、その銘柄がランキングや開示に出たかをここにまとめます。' }),
      null, false, id);
  }
  const rows = h('div', { class: 'rows' }, items.map((s) => {
    const meta = [h('span', { text: s.code })];
    if (s.sector) meta.push(h('span', { text: s.sector }));
    (s.tags || []).forEach((t) => meta.push(h('span', { class: 'tag', text: t })));
    (s.disclosures || []).forEach((m) => meta.push(h('span', { class: 'tag tag--warn', text: '📄 ' + m })));
    if (s.pending) meta.push(h('span', { text: '次回更新後に反映' }));
    return h('a', { class: 'row', href: stockUrl(s.code), target: '_blank', rel: 'noopener' }, [
      h('div', { class: 'row__rank' }, []),
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name', text: cleanName(s.name) || s.code }),
        h('div', { class: 'row__meta' }, meta),
      ]),
      h('div', { class: 'row__right' }, [
        isNum(s.price) ? h('div', { class: 'row__price num', text: fmtPrice(s.price) }) : null,
        h('div', { class: 'row__delta num ' + cls(s.change_pct), text: s.pending ? '—' : fmtPct(s.change_pct) }),
      ]),
    ]);
  }));
  const hits = items.filter((s) => (s.tags || []).length || (s.disclosures || []).length);
  return card('ウォッチリスト', editBtn, rows,
    hits.length ? `${hits.length}銘柄が今日のランキング／開示に登場しています。` : null, true, id);
}

/* ==================== 発掘 ==================== */
/* サーバ側の台帳（ledger.json）が無い間は、端末側で直近の履歴から候補を組み立てる。
   入口の判定は機械的に行い、理由は「どの数字に引っかかったか」だけを書く。 */
function latestSession() {
  const slots = (DATA && DATA.slots) || {};
  return (slots.taibike || slots.zenba || {}).data || null;
}

function histSessions() {
  return HIST.dates.slice().reverse().map((d) => HIST.byDate.get(d)).filter(Boolean);
}

function buildSignals() {
  const d = latestSession();
  const cands = new Map();          // code → {code, name, price, change_pct, signals:[], why:[]}
  const add = (r, signal, why, weight) => {
    if (!r || !r.code) return;
    const c = cands.get(r.code) || { code: r.code, name: cleanName(r.name) || r.code, price: r.price, change_pct: r.change_pct, signals: [], why: [], score: 0 };
    if (!isNum(c.change_pct) && isNum(r.change_pct)) c.change_pct = r.change_pct;
    if (!isNum(c.price) && isNum(r.price)) c.price = r.price;
    if (!c.signals.includes(signal)) { c.signals.push(signal); c.score += weight || 1; }
    if (why && !c.why.includes(why)) c.why.push(why);
    cands.set(r.code, c);
  };

  if (d) {
    const valueRows = ((d.tables || {}).value || {}).rows || [];
    const priceOf = new Map(valueRows.map((r) => [r.code, r]));
    (d.streaks || []).filter((s) => s.days >= 3).forEach((s) =>
      add({ ...s, ...(priceOf.get(s.code) || {}) }, '資金流入の継続', `売買代金上位に ${s.days}営業日連続`, 2));
    const dl = d.ranking_delta || {};
    (dl.new || []).forEach((n) => add({ ...n, ...(priceOf.get(n.code) || {}) }, '新規の資金流入',
      `売買代金 ${n.rank}位に新規ランクイン` + (isNum(n.change_pct) ? `（${fmtPct(n.change_pct)}）` : ''), isNum(n.change_pct) && n.change_pct > 0 ? 2 : 1));
    (dl.rank_up || []).filter((n) => n.jump >= 8).forEach((n) => add({ ...n, ...(priceOf.get(n.code) || {}) }, '順位の急上昇',
      `売買代金 ${n.prev_rank}位 → ${n.rank}位`, 1));
    const gainerSet = new Set((((d.tables || {}).gainer || {}).rows || []).map((r) => r.code));
    const discl = [].concat(((d.tables || {}).kessan_after || {}).rows || [], ((d.tables || {}).kessan_intraday || {}).rows || []);
    discl.forEach((r) => {
      const tone = disclosureTone(r);
      if (tone === 'up') add(r, '上方修正・増配', `${r.time || ''} ${r.title}`.trim(), gainerSet.has(r.code) ? 3 : 2);
      if (r.category === '決算短信' && (priceOf.has(r.code) || gainerSet.has(r.code))) add({ ...r, ...(priceOf.get(r.code) || {}) }, '決算で商い', '決算短信の当日に売買代金／上昇率上位', 2);
    });
    (((d.tables || {}).gainer || {}).rows || []).forEach((r) => {
      if (priceOf.has(r.code)) add(r, '上昇率×売買代金', `上昇率 ${r.rank}位、かつ売買代金上位`, 2);
    });
  }

  // 過去の引け後開示（上方修正）が、その後に売買代金上位へ入っているか
  const sessions = histSessions();
  const seenValue = new Map();      // code → [dates]
  sessions.forEach((hs) => {
    const rows = ((hs.taibike || {}).value_rows || (hs.zenba || {}).value_rows || []);
    rows.forEach((r) => { if (!seenValue.has(r.code)) seenValue.set(r.code, []); seenValue.get(r.code).push(hs.date); });
  });
  sessions.forEach((hs) => {
    ((hs.taibike || {}).after_hours || []).forEach((r) => {
      if (disclosureTone(r) !== 'up') return;
      const later = (seenValue.get(r.code) || []).filter((dt) => dt > hs.date);
      if (later.length) add(r, '修正後に資金流入', `${fmtDate(hs.date)} 上方修正 → その後 ${later.length}営業日 売買代金上位`, 3);
    });
  });

  return [...cands.values()].sort((a, b) => b.score - a.score || (b.change_pct || 0) - (a.change_pct || 0));
}

function signalRow(c) {
  return h('a', { class: 'signal', href: stockUrl(c.code), target: '_blank', rel: 'noopener' }, [
    h('div', { class: 'signal__name', text: `${c.name}` }),
    h('div', { class: 'signal__right num ' + cls(c.change_pct) }, [
      document.createTextNode(fmtPct(c.change_pct)),
      h('small', { text: c.code + (isNum(c.price) ? '　' + fmtPrice(c.price) : '') }),
    ]),
    h('div', { class: 'signal__tags' }, c.signals.map((s) => h('span', { class: 'badge badge--accent', text: s }))),
    h('div', { class: 'signal__why', text: c.why.join('。') }),
  ]);
}

function ledgerRow(e) {
  const tr = e.track || {};
  const fmtT = (v) => (isNum(v) ? fmtPct(v, 1) : '—');
  return h('a', { class: 'signal', href: stockUrl(e.code), target: '_blank', rel: 'noopener' }, [
    h('div', { class: 'signal__name', text: cleanName(e.name) || e.code }),
    h('div', { class: 'signal__right num' }, [
      h('span', { class: 'track' }, [
        h('span', {}, ['d1 ', h('b', { class: cls(tr.d1), text: fmtT(tr.d1) })]),
        h('span', {}, ['d5 ', h('b', { class: cls(tr.d5), text: fmtT(tr.d5) })]),
        h('span', {}, ['d20 ', h('b', { class: cls(tr.d20), text: fmtT(tr.d20) })]),
      ]),
      h('small', { text: `${e.code}　${fmtDate(e.first_seen)} から` + (e.status === 'closed' ? '（追跡終了）' : '') }),
    ]),
    h('div', { class: 'signal__tags' }, [].concat(e.signals || []).map((s) => h('span', { class: 'badge badge--accent', text: s }))
      .concat([].concat(e.themes || []).map((t) => h('span', { class: 'badge', text: '#' + t })))),
    e.notes ? h('div', { class: 'signal__why', text: e.notes }) : null,
  ]);
}

function renderDiscover() {
  const out = [];
  if (LEDGER && Array.isArray(LEDGER.entries) && LEDGER.entries.length) {
    const open = LEDGER.entries.filter((e) => e.status !== 'closed');
    const closed = LEDGER.entries.filter((e) => e.status === 'closed');
    out.push(card('発掘ノート', `${open.length}銘柄を追跡中`,
      foldable((n) => h('div', {}, open.slice(0, n).map(ledgerRow)), open.length, 12, '全件'),
      '入口の判定は機械（連続ランクイン・修正・決算後の続伸など）、理由づけはAI、検証は週報。d1/d5/d20 はフラグ日からの騰落率。', true));
    if (closed.length) {
      out.push(card('追跡を終えた候補', `${closed.length}銘柄`,
        foldable((n) => h('div', {}, closed.slice(0, n).map(ledgerRow)), closed.length, 8, '全件'), null, true));
    }
    if (LEDGER.stats && Array.isArray(LEDGER.stats.by_signal) && LEDGER.stats.by_signal.length) {
      out.push(card('シグナル別の成績', LEDGER.stats.asof ? fmtDate(LEDGER.stats.asof) + ' 時点' : null,
        h('div', { class: 'rows' }, (LEDGER.stats.by_signal || []).map((s) => h('div', { class: 'row' }, [
          h('div', { class: 'row__rank' }, []),
          h('div', { class: 'row__main' }, [h('div', { class: 'row__name', text: s.signal }),
            h('div', { class: 'row__meta' }, [h('span', { text: `${s.count}件` })])]),
          h('div', { class: 'row__right' }, [h('div', { class: 'row__delta num ' + cls(s.d5_median), text: 'd5 中央値 ' + fmtPct(s.d5_median, 1) })]),
        ]))), null, true));
    }
  } else {
    const sigs = buildSignals();
    const d = latestSession();
    if (!d) {
      out.push(h('section', { class: 'card' }, [
        h('h2', { class: 'card__title', text: '候補を出す材料がまだありません' }),
        h('p', { class: 'hint', text: '前場または大引のデータが入ると、売買代金・開示・履歴から候補を組み立てます。' }),
      ]));
    } else {
      const strong = sigs.filter((c) => c.signals.length >= 2 || c.score >= 3);
      const rest = sigs.filter((c) => !strong.includes(c));
      out.push(card('複数シグナルが重なった銘柄', `${strong.length}銘柄`,
        strong.length ? foldable((n) => h('div', {}, strong.slice(0, n).map(signalRow)), strong.length, 10, '全件')
                      : h('div', { class: 'empty', text: '今日は重なりなし' }),
        '直近の売買代金上位・開示・上昇率と、端末が読み込んだ履歴から機械的に抽出。予測ではなく「数字に引っかかった順」。', true));
      out.push(card('単独シグナル', `${rest.length}銘柄`,
        rest.length ? foldable((n) => h('div', {}, rest.slice(0, n).map(signalRow)), rest.length, 10, '全件')
                    : h('div', { class: 'empty', text: 'なし' }), null, true));
    }
    out.push(h('section', { class: 'card' }, [
      h('h2', { class: 'card__title', text: 'この画面について' }),
      h('p', { class: 'hint', style: 'margin:0', text:
        'サーバ側の台帳（候補を溜めて d1/d5/d20 の騰落を追い、シグナルごとの成績を週次で出す仕組み）が整うまでの暫定版です。' +
        '端末が読み込める履歴（直近' + HISTORY_DAYS + '営業日）の範囲で組み立てています。' }),
    ]));
  }

  return out;
}

/* ==================== 履歴 ====================
   投資家が最初の1画面で「最近の指数の向き」と「強い業種・弱い業種」を掴むための画面。
     1. 指数の推移 … 日経とTOPIXを期間の初日=0% にそろえた線（1本の軸）。ねじれが線の開きで見える
     2. 業種の強弱 … 業種×日のヒートマップ。期間合計で並べるので、続く強さと一日だけの強さが分かれる
     3. 日々の記録 … 列をそろえた表。同じ列は同じ尺度のバーなので、上下に目を動かすだけで比べられる
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
  ], (w.method || 'Claude による週次総括') + '。売買を推奨するものではありません。');
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
    '指でなぞるとその日の終値に切り替わります。休場日は詰めて描いています。');
}

/* ---- 2. 業種の強弱（ヒートマップ） ---- */
function heatCell(v) {
  if (!isNum(v)) return h('div', { class: 'hm__c hm__c--na', text: '' });
  const p = Math.min(Math.abs(v) / HEAT_SCALE, 1);
  const el = h('div', { class: 'hm__c num' + (p > 0.55 ? ' hm__c--strong' : ''), text: (v > 0 ? '+' : '') + v.toFixed(1) });
  el.style.background = `color-mix(in oklab, ${v >= 0 ? 'var(--up)' : 'var(--down)'} ${Math.round(p * 100)}%, var(--heat-0))`;
  return el;
}

function sectorCard(days) {
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
        const c = heatCell(v);
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
  return card('業種の強弱', `${md(span[0].date)}〜${md(span[span.length - 1].date)}の合計順`, [legend, grid, btn],
    '日経225採用銘柄を業種ごとに単純平均した大引の騰落率。期間の合計が大きい順に、上位5と下位5を出しています。' +
    `業種名の横の小さな数字は採用銘柄数で、${SECTOR_MIN_COUNT}銘柄未満の業種は1銘柄の動きで大きく振れるため上位・下位からは外し、「すべて」にだけ薄く出します。` +
    '合計は日々の騰落率を足したもの（複利の累積とは少し違う）。その下の「6/8日↑」は期間中に上げた日数。', false);
}

/* ---- 3. 日々の記録（列をそろえた表） ---- */
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
    true);
}

function renderHistory() {
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
  const days = tradingDays();
  const ix = indexCard(days);
  if (ix) out.push(ix);
  const sc = sectorCard(days);
  if (sc) out.push(sc);
  const wk = weeklyCard();                                // 見出しだけ。本文は畳んである
  if (wk) out.push(wk);
  out.push(dailyCard(sessions));
  return out;
}

/* ==================== 銘柄 ==================== */
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

function renderStocks() {
  const out = [];
  const d = latestSession() || ((DATA.slots || {}).preopen || {}).data || {};
  out.push(watchlistCard(d));

  const input = h('input', { type: 'search', placeholder: 'コードまたは社名で横断検索', autocomplete: 'off', inputmode: 'search' });
  const result = h('div', {});
  const run = () => {
    const q = input.value.trim();
    result.textContent = '';
    if (!q) return;
    const ql = q.toUpperCase();
    const all = searchIndex().filter((x) => x.code.includes(ql) || x.name.includes(q));
    if (!all.length) { result.appendChild(h('div', { class: 'empty', text: '見つかりません（今日のデータと直近の履歴の範囲で検索しています）' })); return; }
    const byCode = new Map();
    all.forEach((x) => {
      const c = byCode.get(x.code) || { code: x.code, name: x.name, price: x.price, change_pct: x.change_pct, ctx: [] };
      if (!isNum(c.change_pct) && isNum(x.change_pct)) c.change_pct = x.change_pct;
      if (!isNum(c.price) && isNum(x.price)) c.price = x.price;
      if (!c.name && x.name) c.name = x.name;
      if (!c.ctx.includes(x.ctx)) c.ctx.push(x.ctx);
      byCode.set(x.code, c);
    });
    const list = [...byCode.values()].slice(0, 30);
    result.appendChild(h('div', { class: 'rows' }, list.map((c, i) => stockRow(c, i, { rank: false, meta: () => c.ctx.slice(0, 4) }))));
  };
  input.addEventListener('input', run);
  out.push(card('銘柄を探す', null, [h('div', { class: 'search' }, [input]), result],
    '今日の指数採用銘柄・ランキング・開示と、直近の履歴に出てきた銘柄が対象。タップで株探の銘柄ページを開きます。'));
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
  $('wlSettingsBox').hidden = true;
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
      setStatus(r.synced ? 'リポジトリに保存しました。株価の取得が走ります（数分後に再読み込みしてください）'
                         : 'この端末に保存しました。株価は次回の自動更新で取得されます', 'ok');
    } catch (e) {
      setStatus('端末には保存しましたが、リポジトリ側は失敗しました: ' + e.message, 'err');
    }
    render();
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
const JUMP_LABELS = [
  ['sec-summary', '要点'], ['sec-analysis', '見立て'], ['sec-open', '想定'], ['sec-index', '指数'],
  ['sec-us', '米国'], ['sec-macro', '為替金利'], ['sec-risk', 'リスク'], ['sec-outlook', '連想'], ['sec-ussector', '米セクター'],
  ['sec-sector33', '業種'], ['sec-theme', 'テーマ'], ['sec-trend', '時間軸'], ['sec-heat', 'ヒートマップ'],
  ['sec-value', '売買代金'], ['sec-moves', '値動き'], ['sec-ytd', '高値更新'], ['sec-volsurge', '出来高'],
  ['sec-disc', '開示'], ['sec-kabutan', '株探'], ['sec-news', 'ニュース'], ['sec-watch', 'ウォッチ'],
];

function drawSlotBar() {
  const bar = $('slotBar');
  bar.textContent = '';
  if (activeView !== 'today') return;
  const slots = (DATA && DATA.slots) || {};
  SLOTS.forEach((s) => {
    const entry = slots[s];
    const b = h('button', { class: 'slot' + (entry ? '' : ' slot--missing'), role: 'tab',
      'aria-selected': String(s === activeSlot), onclick: () => selectSlot(s) }, [
      document.createTextNode(SLOT_LABEL[s]),
      h('span', { class: 'slot__time', text: entry ? (entry.updated_at || '').slice(11, 16) + ' 更新' : '未取得' }),
    ]);
    bar.appendChild(b);
  });
}

function drawJumpBar() {
  const bar = $('jumpBar');
  bar.textContent = '';
  if (activeView !== 'today') return;
  const panel = $('view-today');
  JUMP_LABELS.forEach(([id, label]) => {
    const target = panel.querySelector('#' + id);
    if (!target) return;
    bar.appendChild(h('button', { class: 'jump__btn', type: 'button', text: label,
      onclick: () => { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); } }));
  });
}

/* ==================== 描画・ブート ==================== */
function render() {
  if (!DATA) return;
  const dt = DATA.date ? new Date(DATA.date + 'T00:00:00+09:00') : null;
  $('headDate').textContent = dt
    ? dt.toLocaleDateString('ja-JP', { month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Tokyo' }) : '—';

  const today = $('view-today');
  today.textContent = '';
  const entry = (DATA.slots || {})[activeSlot];
  const nodes = !entry ? [noData(activeSlot)]
    : activeSlot === 'preopen' ? renderPreopen(entry.data || {})
    : renderSession(entry.data || {}, activeSlot);
  nodes.forEach((n) => n && today.appendChild(n));

  const fill = (id, fn) => { const el = $(id); el.textContent = ''; fn().forEach((n) => n && el.appendChild(n)); };
  fill('view-discover', renderDiscover);
  fill('view-history', renderHistory);
  fill('view-stocks', renderStocks);

  drawSlotBar();
  drawJumpBar();
  updateFreshness();
  $('footMeta').textContent = 'データ生成: ' + (DATA.generated_at || '—');
}

function updateFreshness() {
  const dot = $('freshDot');
  const entry = (DATA.slots || {})[activeSlot];
  if (activeView !== 'today') {
    $('headUpdated').textContent = DATA.generated_at ? DATA.generated_at.slice(5, 16).replace('T', ' ') + ' 生成' : '—';
    dot.className = 'dot';
    return;
  }
  if (!entry) { $('headUpdated').textContent = '未取得'; dot.className = 'dot'; return; }
  const upd = entry.updated_at || '';
  $('headUpdated').textContent = SLOT_LABEL[activeSlot] + ' ' + upd.slice(11, 16) + ' 更新';
  const age = (Date.now() - new Date(upd).getTime()) / 3600000;
  dot.className = 'dot ' + (age < 12 ? 'dot--fresh' : 'dot--stale');
}

function selectSlot(slot) {
  activeSlot = slot;
  try { sessionStorage.setItem(LS.tab, slot); } catch (e) { /* 非対応環境は無視 */ }
  render();
  window.scrollTo({ top: 0 });
}

function selectView(view) {
  activeView = view;
  try { sessionStorage.setItem(LS.view, view); } catch (e) { /* 非対応環境は無視 */ }
  VIEWS.forEach((v) => { $('view-' + v).hidden = v !== view; });
  document.querySelectorAll('.bottomnav__btn').forEach((b) => {
    if (b.dataset.view === view) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  $('viewTitle').textContent = VIEW_TITLE[view];
  drawSlotBar();
  drawJumpBar();
  if (DATA) updateFreshness();
  window.scrollTo({ top: 0 });
}

function defaultSlot() {
  const slots = DATA.slots || {};
  const saved = (() => { try { return sessionStorage.getItem(LS.tab); } catch (e) { return null; } })();
  if (saved && SLOTS.includes(saved) && slots[saved]) return saved;
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

async function load() {
  const [latest, ledger, weekly] = await Promise.all([
    fetchJson('data/latest.json'), fetchJson('data/ledger.json'), fetchJson('data/weekly.json')]);
  if (latest) DATA = latest;
  else if (!DATA) { DATA = { slots: {} }; $('headDate').textContent = 'データを読み込めませんでした'; }
  LEDGER = ledger;
  WEEKLY = weekly;
  if (!activeSlot) activeSlot = defaultSlot();
  render();
  await loadHistory();
  render();
}

function boot() {
  document.querySelectorAll('.bottomnav__btn').forEach((b) => b.addEventListener('click', () => selectView(b.dataset.view)));
  $('reloadBtn').addEventListener('click', load);
  bindSheet();
  const savedView = (() => { try { return sessionStorage.getItem(LS.view); } catch (e) { return null; } })();
  selectView(savedView && VIEWS.includes(savedView) ? savedView : 'today');
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
