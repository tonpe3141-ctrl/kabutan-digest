/* マーケットダッシュボード — 描画ロジック
   外部ライブラリなし。データは docs/data/latest.json から読む。
   本文はスクレイピング由来のため、DOM 生成は textContent 経由で行う。 */
(() => {
'use strict';

const SLOTS = ['preopen', 'zenba', 'taibike'];
const SLOT_LABEL = { preopen: '寄り前', zenba: '前場', taibike: '大引' };
const DEFAULT_REPO = 'tonpe3141-ctrl/kabutan-digest';
const LS = { codes: 'md.watchlist.codes', token: 'md.gh.token', repo: 'md.gh.repo', tab: 'md.tab' };

let DATA = null;
let activeTab = null;

/* ==================== 小道具 ==================== */
const jstNow = () => new Date(Date.now() + new Date().getTimezoneOffset() * 60000 + 9 * 3600000);

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

function fmtNum(v, digits = 2) {
  if (!isNum(v)) return '—';
  return v.toLocaleString('ja-JP', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtAuto(v) {
  if (!isNum(v)) return '—';
  const d = Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 10 ? 2 : 3;
  return fmtNum(v, d);
}
function fmtPct(v, digits = 2) {
  if (!isNum(v)) return '—';
  return (v > 0 ? '+' : '') + v.toFixed(digits) + '%';
}
function fmtSigned(v, digits = 2) {
  if (!isNum(v)) return '—';
  return (v > 0 ? '+' : '') + fmtNum(v, digits);
}

function sparkline(series) {
  if (!Array.isArray(series) || series.length < 3) return null;
  const w = 100, hgt = 22, pad = 2;
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
function card(title, sub, body, note, flush) {
  const kids = [];
  if (title) {
    kids.push(h('div', { class: 'card__head' }, [
      h('h2', { class: 'card__title', text: title }),
      sub ? (typeof sub === 'string' ? h('span', { class: 'card__sub', text: sub }) : sub) : null,
    ]));
  }
  [].concat(body).forEach((b) => b && kids.push(b));
  if (note) kids.push(h('div', { class: 'card__note', text: note }));
  return h('section', { class: 'card' + (flush ? ' card--flush' : '') }, kids);
}

function tile(label, value, delta, deltaPct, series) {
  return h('div', { class: 'tile' }, [
    h('div', { class: 'tile__label', text: label }),
    h('div', { class: 'tile__value num', text: value }),
    h('div', { class: 'tile__delta num ' + cls(deltaPct), text: delta }),
    sparkline(series),
  ]);
}

function quoteTiles(map, order) {
  const keys = order || Object.keys(map);
  const items = keys.filter((k) => map[k]).map((k) => {
    const q = map[k];
    const digits = q.digits !== undefined ? q.digits : (Math.abs(q.last) >= 1000 ? 0 : 2);
    const deltaText = fmtSigned(q.change, digits) + '  ' + fmtPct(q.change_pct);
    return tile(q.label || k, fmtNum(q.last, digits), deltaText, q.change_pct, q.series);
  });
  if (!items.length) return h('div', { class: 'empty', text: 'データを取得できませんでした' });
  return h('div', { class: 'tiles' }, items);
}

function barList(items, opts = {}) {
  const max = Math.max(0.5, ...items.map((i) => Math.abs(i.value)));
  return h('div', { class: 'bars' }, items.map((it) => {
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

function stockRows(rows, opts = {}) {
  const list = (rows || []).slice(0, opts.limit || 12);
  if (!list.length) return h('div', { class: 'empty', text: 'データなし' });
  return h('div', { class: 'rows' }, list.map((r, i) => {
    const meta = [];
    if (r.code) meta.push(r.code);
    if (opts.meta) [].concat(opts.meta(r)).filter(Boolean).forEach((m) => meta.push(m));
    const inner = [
      h('div', { class: 'row__rank num', text: opts.rank === false ? '' : String(i + 1) }),
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name', text: r.name || r.code || '—' }),
        meta.length ? h('div', { class: 'row__meta' }, meta.map((m) => h('span', { text: m }))) : null,
      ]),
      h('div', { class: 'row__right' }, [
        isNum(r.price) ? h('div', { class: 'row__price num', text: fmtNum(r.price, r.price >= 1000 ? 0 : 1) }) : null,
        h('div', { class: 'row__delta num ' + cls(r.change_pct), text: fmtPct(r.change_pct) }),
      ]),
    ];
    return r.code
      ? h('a', { class: 'row', href: `https://kabutan.jp/stock/?code=${encodeURIComponent(r.code)}`,
                 target: '_blank', rel: 'noopener' }, inner)
      : h('div', { class: 'row' }, inner);
  }));
}

/* 株価が動きやすい開示。自己株式取得や月次は件数が多く埋もれるので後回しにする */
const MATERIAL = ['業績予想の修正', '決算短信', '配当予想の修正'];

function splitMaterial(rows) {
  const material = [], rest = [];
  (rows || []).forEach((r) => (MATERIAL.includes(r.category) ? material : rest).push(r));
  return { material, rest };
}

/* 適時開示（TDnet）の行。値動きではなく「何が出たか」を見せる */
function disclosureRows(rows, limit) {
  const list = (rows || []).slice(0, limit || 20);
  if (!list.length) return h('div', { class: 'empty', text: '開示なし' });
  return h('div', { class: 'rows' }, list.map((r) =>
    h('a', {
      class: 'row', href: `https://finance.yahoo.co.jp/quote/${encodeURIComponent(r.code)}.T`,
      target: '_blank', rel: 'noopener',
    }, [
      h('div', { class: 'row__rank num', text: (r.time || '').slice(0, 5) }),
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name', text: r.name || r.code }),
        h('div', { class: 'row__meta' }, [h('span', { text: r.title || '' })]),
      ]),
      h('div', { class: 'row__right' }, [
        r.category ? h('span', {
          class: 'badge' + (r.category === '業績予想の修正' ? ' badge--warn' : ''),
          text: r.category,
        }) : null,
      ]),
    ])));
}

function accordion(title, badge, bodyText, url) {
  return h('details', { class: 'acc' }, [
    h('summary', {}, [
      h('span', { text: title }),
      badge ? h('span', { class: 'badge', text: badge }) : null,
    ]),
    h('div', { class: 'acc__body' }, [
      document.createTextNode(bodyText || '本文を取得できませんでした'),
      url ? h('div', { style: 'margin-top:10px' }, [
        h('a', { href: url, target: '_blank', rel: 'noopener', text: '株探で開く →' }),
      ]) : null,
    ]),
  ]);
}

/* 日経225ヒートマップ。並べ替えは騰落順と業種順を切り替えられる */
function heatCell(r) {
  const v = r.change_pct;
  const cap = 4;                                     // ±4% で色を振り切らせる
  const a = isNum(v) ? Math.min(1, Math.abs(v) / cap) * 0.82 + 0.10 : 0.06;
  const cell = h('a', {
    class: 'heat__cell', title: `${r.name || ''} ${r.code} ${fmtPct(v)}`,
    href: `https://finance.yahoo.co.jp/quote/${encodeURIComponent(r.code)}.T`,
    target: '_blank', rel: 'noopener',
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
  const seg = h('div', { class: 'seg' });
  const draw = (mode) => {
    body.textContent = '';
    const grid = h('div', { class: 'heat' });
    if (mode === 'sector') {
      // 業種ごとにまとめ、業種の平均が高い順に並べる
      const groups = new Map();
      list.forEach((r) => {
        const k = r.sector || 'その他';
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(r);
      });
      [...groups.entries()]
        .map(([k, v]) => [k, v, v.reduce((a, r) => a + r.change_pct, 0) / v.length])
        .sort((a, b) => b[2] - a[2])
        .forEach(([name, items, avg]) => {
          grid.appendChild(h('div', { class: 'heat__group',
                                      text: `${name}　${fmtPct(avg, 1)}` }));
          items.sort((a, b) => b.change_pct - a.change_pct)
               .forEach((r) => grid.appendChild(heatCell(r)));
        });
    } else {
      [...list].sort((a, b) => b.change_pct - a.change_pct)
               .forEach((r) => grid.appendChild(heatCell(r)));
    }
    body.appendChild(grid);
    [...seg.children].forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.k === mode)));
  };
  [['rank', '騰落順'], ['sector', '業種順']].forEach(([k, label]) => {
    const b = h('button', { class: 'seg__btn', type: 'button', 'aria-pressed': 'false', text: label });
    b.dataset.k = k;
    b.addEventListener('click', () => draw(k));
    seg.appendChild(b);
  });
  draw('rank');

  return card('日経225 ヒートマップ', seg, [
    body,
    h('div', { class: 'legend' }, [
      h('span', { text: '下落' }), h('div', { class: 'legend__bar' }), h('span', { text: '上昇' }),
    ]),
  ], breadth ? `上昇 ${breadth.up} / 下落 ${breadth.down}（${breadth.up_ratio}% が上昇）。${breadth.comment}` : null);
}

/* アナリスト分析。ai_commentary（Claudeによる分析）があればそれを、
   無ければ commentary（ルールベースの機械的な文章化）を表示する */
function analysisCard(ruleBased, ai) {
  const c = ai && ai.sections && ai.sections.length ? ai : ruleBased;
  if (!c || !c.sections || !c.sections.length) return null;

  const isAi = c === ai;
  const note = isAi
    ? '生成AIによる分析です。数値データと相場振り返り記事をもとに作成していますが、'
      + '誤りを含む可能性があります。売買を推奨するものではありません。'
    : '各カードの数値を組み合わせて機械的に文章化したもの。売買を推奨するものではありません。'
      + '（AI分析は準備中です）';

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

  return card('相場の見立て', isAi ? (c.method || 'AI分析') : '簡易分析', body, note);
}

function hero(label, value, deltaText, deltaVal, aside, verdict, tone) {
  return h('section', { class: 'card hero' }, [
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
  return h('section', { class: 'card' }, [
    h('h2', { class: 'card__title', text: SLOT_LABEL[slot] + 'のデータはまだありません' }),
    h('p', { class: 'hint', text: '自動更新（' +
      ({ preopen: '平日 07:00', zenba: '平日 12:00', taibike: '平日 17:30' })[slot] +
      ' 頃）の実行後に表示されます。' }),
  ]);
}

/* ==================== 寄り前タブ ==================== */
function renderPreopen(d) {
  const out = [];
  const io = d.implied_open;

  if (io) {
    const gapTxt = isNum(io.gap) ? `${fmtSigned(io.gap, 0)}円` : '';
    const aside = [h('div', {}, [h('span', { class: 'badge badge--accent', text: io.method_label || '' })])];
    if (isNum(io.prev_close)) aside.push(h('div', { class: 'num', text: `前日終値 ${fmtNum(io.prev_close, 0)}` }));
    let note = null;
    if (io.method === 'model' && io.contributions) {
      note = '内訳: ' + io.contributions.map((c) => `${c.driver} ${c.display || ''} → ${fmtSigned(c.value)}pt`).join('　/　') +
             (io.formula ? `　（係数: ${io.formula}）` : '');
    }
    out.push(hero('日経平均 想定オープン', fmtPct(io.gap_pct), gapTxt, io.gap_pct, aside, note, 'neutral'));
  }

  const preAnalysis = analysisCard(d.commentary, d.ai_commentary);
  if (preAnalysis) out.push(preAnalysis);

  if (d.risk) {
    out.push(card('リスク環境',
      h('span', { class: 'badge badge--' + (d.risk.tone === 'positive' ? 'up' : d.risk.tone === 'negative' ? 'down' : 'accent'),
                  text: d.risk.label }),
      h('ul', { class: 'reasons' }, (d.risk.reasons || []).map((r) => h('li', { text: r })))));
  }

  if (d.us && Object.keys(d.us).length) {
    out.push(card('米国市場', d.freshness && d.freshness.us_asof ? d.freshness.us_asof + ' 終値' : null,
      quoteTiles(d.us, ['spx', 'ndq', 'dji', 'sox', 'rut', 'vix'])));
  }

  const so = d.sector_outlook;
  if (so && so.tailwind && so.tailwind.length) {
    const mk = (arr) => arr.map((s) => ({
      label: s.sector, value: s.score,
      sub: (s.drivers || []).map((x) => `${x.driver} ${x.display || fmtPct(x.raw)}`).join(' · '),
    }));
    out.push(card('今日 追い風になりそうな業種', '米国市場からの連想',
      barList(mk(so.tailwind), { raw: true }),
      '各業種を説明する米国側の指標（半導体・金利・為替など）の前日変化を重み付けして算出した相対スコア。' +
      '株価の予測値ではなく、どの物色テーマに追い風が吹いているかの並び順として見る。'));
    if (so.headwind && so.headwind.length) {
      out.push(card('向かい風になりそうな業種', null, barList(mk(so.headwind), { raw: true })));
    }
  }

  if (d.sectors_us && Object.keys(d.sectors_us).length) {
    const items = Object.values(d.sectors_us)
      .filter((s) => isNum(s.change_pct))
      .sort((a, b) => b.change_pct - a.change_pct)
      .map((s) => ({ label: s.label, value: s.change_pct }));
    out.push(card('米国セクターローテーション', '前日比', barList(items)));
  }

  if (d.macro && Object.keys(d.macro).length) {
    out.push(card('為替・金利・商品', null, quoteTiles(d.macro, ['usdjpy', 'us10y', 'wti', 'gold', 'eurjpy'])));
  }

  const co = d.carryover || {};
  if (co.after_hours_kessan && co.after_hours_kessan.length) {
    out.push(card('前営業日の引け後 開示', '今日の寄りで動きやすい',
      disclosureRows(co.after_hours_kessan, 15), null, true));
  }
  if (co.prev_session && co.prev_session.session_shift) {
    out.push(card('前営業日の引け方', co.prev_session.date,
      h('p', { class: 'hint', style: 'font-size:13px;color:var(--text-dim)',
               text: co.prev_session.session_shift.verdict })));
  }

  out.push(watchlistCard(d));
  return out;
}

/* ==================== 前場・大引タブ ==================== */
function indexTiles(indices) {
  const items = Object.values(indices || {}).map((v) =>
    tile(v.label, fmtNum(v.close, v.close >= 1000 ? 2 : 2),
         fmtSigned(v.change, 2) + '  ' + fmtPct(v.change_pct), v.change_pct));
  if (!items.length) return h('div', { class: 'empty', text: '指数を取得できませんでした' });
  return h('div', { class: 'tiles' }, items);
}

function renderSession(d, slot) {
  const out = [];
  const nk = (d.indices || {}).nikkei;

  let verdict = null, tone = 'neutral';
  if (slot === 'taibike' && d.session_shift) {
    verdict = '後場: ' + d.session_shift.verdict +
      (isNum(d.session_shift.indices?.[0]?.diff)
        ? `（前場終値比 ${fmtSigned(d.session_shift.indices[0].diff, 0)}円）` : '');
    tone = d.session_shift.tone;
  } else if (slot === 'zenba' && d.verify_open) {
    verdict = `寄り前の想定 ${fmtPct(d.verify_open.expected_pct)} に対し実際 ${fmtPct(d.verify_open.actual_pct)}。` +
      d.verify_open.verdict;
    tone = d.verify_open.tone;
  }

  if (nk) {
    out.push(hero(`日経平均（${SLOT_LABEL[slot]}）`, fmtNum(nk.close, 2),
      fmtSigned(nk.change, 2) + '  ' + fmtPct(nk.change_pct), nk.change_pct,
      d.divergence ? [
        h('div', { class: 'badge badge--' + (d.divergence.tone === 'warn' ? 'warn' : 'accent'),
                   text: d.divergence.label }),
        h('div', { class: 'num', text: `日経 ${fmtPct(d.divergence.nikkei_pct)} / TOPIX ${fmtPct(d.divergence.topix_pct)}` }),
        d.breadth ? h('div', { class: 'num', text: `225中 ${d.breadth.up} 銘柄が上昇` }) : null,
      ] : null, verdict, tone));
  }

  const analysis = analysisCard(d.commentary, d.ai_commentary);
  if (analysis) out.push(analysis);

  out.push(card('指数', (d.indices.nikkei || {}).asof || null, [
    indexTiles(d.indices),
    // 見立てのカードで同じ指摘をしているときは繰り返さない
    (d.divergence && !analysis) ? h('div', { class: 'card__note', text: d.divergence.comment }) : null,
  ]));

  if (d.sectors_jp && d.sectors_jp.length >= 4) {
    const items = d.sectors_jp.map((x) => ({
      label: `${x.sector}（${x.count}）`,
      value: x.avg_pct,
      sub: x.best && x.worst
        ? `高 ${x.best.name || x.best.code} ${fmtPct(x.best.change_pct, 1)} ／ `
          + `安 ${x.worst.name || x.worst.code} ${fmtPct(x.worst.change_pct, 1)}`
        : null,
    }));
    out.push(card('業種別 騰落率', '日経225採用銘柄の平均', barList(items),
      '日経の業種区分で採用銘柄をまとめた単純平均。時価総額加重の東証33業種指数とは一致しません。'));
  }

  const heat = heatmapCard(d.constituents, d.breadth);
  if (heat) out.push(heat);

  const t = d.tables || {};

  if (t.value && t.value.rows && t.value.rows.length) {
    const dl = d.ranking_delta || {};
    const newSet = new Set((dl.new || []).map((n) => n.code));
    const upSet = new Map((dl.rank_up || []).map((n) => [n.code, n]));
    const streakMap = new Map((d.streaks || []).map((s) => [s.code, s]));
    const valueLabel = t.value.label || '売買代金';
    out.push(card(valueLabel + ' 上位',
      valueLabel === '売買代金' ? '資金が向かった先' : '商いが膨らんだ銘柄',
      stockRows(t.value.rows, {
        limit: 30,
        meta: (r) => {
          const m = [];
          if (newSet.has(r.code)) m.push('🆕 新規ランクイン');
          if (upSet.has(r.code)) m.push(`▲${upSet.get(r.code).jump}位上昇`);
          if (streakMap.has(r.code)) m.push(`${streakMap.get(r.code).days}日連続`);
          return m;
        },
      }),
      (dl.new && dl.new.length)
        ? `前営業日から新たに上位入り: ${dl.new.slice(0, 6).map((n) => n.name || n.code).join('、')}`
        : null, true));
  }

  if ((t.gainer && t.gainer.rows.length) || (t.loser && t.loser.rows.length)) {
    const body = h('div', {});
    const seg = h('div', { class: 'seg' });
    const render = (which) => {
      body.textContent = '';
      body.appendChild(stockRows((t[which] || {}).rows, { limit: 12 }));
      [...seg.children].forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.k === which)));
    };
    ['gainer', 'loser'].forEach((k) => {
      if (!t[k] || !t[k].rows.length) return;
      const b = h('button', { class: 'seg__btn', type: 'button', 'aria-pressed': 'false',
                              text: k === 'gainer' ? '上昇率' : '下落率' });
      b.dataset.k = k;
      b.addEventListener('click', () => render(k));
      seg.appendChild(b);
    });
    render(t.gainer && t.gainer.rows.length ? 'gainer' : 'loser');
    out.push(card('値動きの大きかった銘柄', seg, body, null, true));
  }

  if (d.disclosure_summary) {
    out.push(card('今日の適時開示', `${d.disclosure_summary.total}件`,
      h('div', { class: 'chips' }, d.disclosure_summary.items.map((i) =>
        h('span', { class: 'badge' + (i.label === '業績予想の修正' ? ' badge--warn' : ''),
                    text: `${i.label} ${i.count}` }))),
      d.disclosure_summary.headline));
  }

  const discCard = (table, title, note) => {
    if (!table || !table.rows.length) return null;
    const { material, rest } = splitMaterial(table.rows);
    const body = [disclosureRows(material.length ? material : rest, 20)];
    if (material.length && rest.length) {
      body.push(h('details', { class: 'acc' }, [
        h('summary', {}, [h('span', { text: `その他の開示 ${rest.length}件` })]),
        disclosureRows(rest, 30),
      ]));
    }
    return card(title, `${table.rows.length}件`, body, note, true);
  };

  const after = discCard(t.kessan_after, '引け後の開示',
    '翌営業日の寄りで値が飛びやすい。ウォッチリスト銘柄が含まれていないか確認する。');
  if (after) out.push(after);
  const intraday = discCard(t.kessan_intraday, '場中の開示', null);
  if (intraday) out.push(intraday);

  out.push(watchlistCard(d));
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
  // これを消しておかないと、別端末からの変更をこの端末が握りつぶしてしまう。
  if (local && local.length === serverCodes.length &&
      local.every((c) => byCode.has(String(c)))) {
    try { localStorage.removeItem(LS.codes); } catch (e) { /* 非対応環境は無視 */ }
    local = null;
  }

  const codes = local || serverCodes;
  return codes.map((c) => byCode.get(String(c)) || { code: String(c), pending: true });
}

function watchlistCard(d) {
  const items = effectiveWatchlist(d);
  const editBtn = h('button', { class: 'btn btn--ghost', type: 'button', text: '編集',
                                onclick: () => openSheet(d) });

  if (!items.length) {
    return card('ウォッチリスト', editBtn,
      h('p', { class: 'hint', text: '証券コードを登録すると、保有・注目銘柄の値動きと、その銘柄がランキングやニュースに出たかをここにまとめます。' }));
  }

  const rows = h('div', { class: 'rows' }, items.map((s) => {
    const meta = [s.code];
    if (s.sector) meta.push(s.sector);
    (s.tags || []).forEach((t) => meta.push(t));
    (s.disclosures || []).forEach((m) => meta.push('📄 ' + m));
    if (s.pending) meta.push('次回更新後に反映');
    const inner = [
      h('div', { class: 'row__rank' }, []),
      h('div', { class: 'row__main' }, [
        h('div', { class: 'row__name', text: s.name || s.code }),
        h('div', { class: 'row__meta' }, meta.map((m) => h('span', { text: m }))),
      ]),
      h('div', { class: 'row__right' }, [
        isNum(s.price) ? h('div', { class: 'row__price num', text: fmtNum(s.price, s.price >= 1000 ? 0 : 1) }) : null,
        h('div', { class: 'row__delta num ' + cls(s.change_pct), text: s.pending ? '—' : fmtPct(s.change_pct) }),
      ]),
    ];
    return h('a', { class: 'row', href: `https://kabutan.jp/stock/?code=${encodeURIComponent(s.code)}`,
                    target: '_blank', rel: 'noopener' }, inner);
  }));

  const hits = items.filter((s) => (s.tags || []).length || (s.mentions || []).length);
  return card('ウォッチリスト', editBtn, rows,
    hits.length ? `${hits.length}銘柄が今日のランキング／ニュースに登場しています。` : null, true);
}

/* ==================== 編集シート ==================== */
const $ = (id) => document.getElementById(id);
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
      h('span', { class: 'editrow__name', text: sheetNames.get(code) || '' }),
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
  $('wlInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('wlAdd').click(); }
  });

  $('wlSave').addEventListener('click', async () => {
    localStorage.setItem(LS.codes, JSON.stringify(sheetCodes));
    setStatus('この端末に保存しました。反映中…');
    try {
      const r = await pushToGitHub(sheetCodes);
      setStatus(r.synced
        ? 'リポジトリに保存しました。株価の取得が走ります（数分後に再読み込みしてください）'
        : 'この端末に保存しました。株価は次回の自動更新で取得されます', 'ok');
    } catch (e) {
      setStatus('端末には保存しましたが、リポジトリ側は失敗しました: ' + e.message, 'err');
    }
    render();
  });

  $('wlSettings').addEventListener('click', () => {
    const box = $('wlSettingsBox');
    box.hidden = !box.hidden;
  });
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

/* ==================== 描画・ブート ==================== */
function render() {
  if (!DATA) return;
  const slots = DATA.slots || {};

  const dt = DATA.date ? new Date(DATA.date + 'T00:00:00+09:00') : null;
  $('headDate').textContent = dt
    ? dt.toLocaleDateString('ja-JP', { month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Tokyo' })
    : '—';

  SLOTS.forEach((slot) => {
    const panel = $('panel-' + slot);
    panel.textContent = '';
    const entry = slots[slot];
    const nodes = !entry ? [noData(slot)]
      : slot === 'preopen' ? renderPreopen(entry.data || {})
      : renderSession(entry.data || {}, slot);
    nodes.forEach((n) => n && panel.appendChild(n));
  });

  selectTab(activeTab);
  $('footMeta').textContent = 'データ生成: ' + (DATA.generated_at || '—');
}

function selectTab(slot) {
  activeTab = slot;
  SLOTS.forEach((s) => {
    $('tab-' + s).setAttribute('aria-selected', String(s === slot));
    $('panel-' + s).hidden = s !== slot;
  });
  try { sessionStorage.setItem(LS.tab, slot); } catch (e) { /* 非対応環境は無視 */ }

  const entry = (DATA.slots || {})[slot];
  const dot = $('freshDot');
  if (!entry) {
    $('headUpdated').textContent = '未取得';
    dot.className = 'dot';
    return;
  }
  const upd = entry.updated_at || '';
  $('headUpdated').textContent = SLOT_LABEL[slot] + ' ' + upd.slice(11, 16) + ' 更新';
  const age = (Date.now() - new Date(upd).getTime()) / 3600000;
  dot.className = 'dot ' + (age < 12 ? 'dot--fresh' : 'dot--stale');
}

function defaultTab() {
  const slots = DATA.slots || {};
  const saved = (() => { try { return sessionStorage.getItem(LS.tab); } catch (e) { return null; } })();
  if (saved && SLOTS.includes(saved)) return saved;

  const n = jstNow();
  const mins = n.getHours() * 60 + n.getMinutes();
  const wanted = mins < 10 * 60 ? 'preopen' : mins < 15 * 60 + 30 ? 'zenba' : 'taibike';
  if (slots[wanted]) return wanted;
  const available = SLOTS.filter((s) => slots[s]);
  return available.length ? available[available.length - 1] : wanted;
}

async function load() {
  try {
    const res = await fetch('data/latest.json?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error(String(res.status));
    DATA = await res.json();
  } catch (e) {
    DATA = { slots: {} };
    $('headDate').textContent = 'データを読み込めませんでした';
  }
  if (!activeTab) activeTab = defaultTab();
  render();
}

function boot() {
  SLOTS.forEach((s) => $('tab-' + s).addEventListener('click', () => selectTab(s)));
  $('reloadBtn').addEventListener('click', load);
  bindSheet();
  load();
  // 表示中に古くならないよう、復帰時と5分ごとに読み直す
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden) load(); }, 5 * 60 * 1000);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
})();
