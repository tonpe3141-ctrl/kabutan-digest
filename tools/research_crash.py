"""急落の局面で押し目買いを置くか（DESIGN.md 36章）。本番の swing.py と同じ数え方で、注文に条件を足した口座を比べる。

使い方（自宅回線から。リポジトリの直下で）:
    python tools/research_crash.py ohlc4y --fetch    # 4年の四本値を CNBC から取る（510銘柄で約5分）
    python tools/research_crash.py ohlc4y            # 今のルールと4つの案を、前半・後半・直近の口座で比べる
案は先に決めてある（成績を見ながら閾値を動かさない）。入れる基準: 前半で年率を2割以上落とさずに最大の目減りが2割以上浅くなり、後半も同じ向き。
結果（2026-11-15）: どの案も基準に届かなかった。ルールは変えていない。"""
import bisect, glob, json, os, pickle, sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from statistics import mean
from dashboard import swing as W
S = sys.argv[1] if len(sys.argv) > 1 else 'ohlc4y'   # 四本値の置き場所（{S}/ohlc/{コード}.json。行は [日付, 始値, 高値, 安値, 終値, 出来高(株)]。N225.json も）
SPLIT='2025-01-01'
def load():
    stocks={}
    for p in glob.glob(S+'/ohlc/*.json'):
        code=os.path.basename(p)[:-5]
        rows=[tuple(r) for r in json.load(open(p))]
        rows=[(d,o,max(h,o,c),min(l,o,c),c,v) for d,o,h,l,c,v in rows if c and c>0]
        stocks[code]=rows
    return stocks
def build():
    stocks=load()
    nk=stocks.pop('N225')
    members={c['code']:c for c in json.load(open('docs/data/nikkei225.json'))['components']}
    sector_of={c:i.get('sector') for c,i in members.items()}
    themes=json.load(open('docs/data/themes.json')).get('stocks') or {}
    groups=W.peer_groups(list(stocks), themes, sector_of)
    inds={c:W.indicators(b) for c,b in stocks.items() if len(b)>W.PEER_N}
    rank_at=W.mid_context(inds)
    rets_on={}
    for code,ind in inds.items():
        if code in groups:
            for i in range(W.PEER_N,len(ind['c'])):
                rets_on.setdefault(ind['d'][i],{})[code]=W.ret_n(ind,i)
    ctx_on={}
    def peer_at(d,code):
        if code not in groups: return None
        if d not in ctx_on: ctx_on[d]=W.peer_context(rets_on.get(d,{}),groups)
        return ctx_on[d].get(code)
    cal=set(); orders_on={}; mid_on={}; closes={}
    for code,ind in inds.items():
        n=len(ind['c'])
        if n<W.WARMUP+5: continue
        cal.update(ind['d'][W.WARMUP-1:])
        closes[code]=dict(zip(ind['d'],ind['c']))
        for i in range(W.WARMUP-1,n-1):
            rs=ind['rsi'][i]; is_mid=False
            atrp=ind['atr'][i]/ind['c'][i]*100 if ind['atr'][i] else None
            if rs is not None and rs<W.RSI_MAX and i>=W.MID_MOM_N:
                st=W.mid_state(ind,i); rk=rank_at(ind['d'][i],code)
                if st and rk is not None and rk>=W.MID_TOP and st['up'] and ind['atr'][i] and W.mid_liquid(ind,i):
                    is_mid=W.is_mid_signal(ind,i,st,rk)
                    mres=W.simulate_mid(ind,i,st['low'])
                    if is_mid and mres is not None:
                        mid_on.setdefault(ind['d'][i],[]).append({'code':code,'res':mres,'key':W.mid_key(ind,i),'atrp':atrp})
            if not W.is_signal(ind,i): continue
            pc=peer_at(ind['d'][i],code); k=pc['cls'] if pc else 'none'
            res=W.simulate(ind,i)
            if k not in W.PEER_SKIP and res is not None and not is_mid:
                m25=W._ma(ind,i,25)
                orders_on.setdefault(ind['d'][i],[]).append({'code':code,'res':res,'atrp':atrp,
                    'key':W.rank_key({'dev25':W.r1((ind['c'][i]/m25-1)*100) if m25 else None,'peer':pc})})
    for on in (orders_on,mid_on):
        for rows in on.values(): rows.sort(key=lambda o:(o['key'],o['code']))
    return dict(days=sorted(cal),orders_on=orders_on,mid_on=mid_on,closes=closes,sector_of=sector_of,nk=nk)
def get():
    p=S+'/built.pkl'
    if os.path.exists(p): return pickle.load(open(p,'rb'))
    b=build(); pickle.dump(b,open(p,'wb')); return b

def market(nk):
    d=[r[0] for r in nk]; c=[r[4] for r in nk]; ix={x:i for i,x in enumerate(d)}
    def f(day):
        i=ix.get(day)
        if i is None or i<200: return None
        since=next((i-j for j in range(i,i-40,-1) if c[j]/c[j-1]-1<=-0.03),99)
        return {'since':since,'a200':c[i]>sum(c[i-199:i+1])/200,'a75':c[i]>sum(c[i-74:i+1])/75,
                'dd60':(c[i]/max(c[i-59:i+1])-1)*100}
    return f

def run(B, keep, label, lo=None, hi=None, mid=True, show=True):
    """keep(day, order, kind) -> 置くか。lo〜hi の営業日だけで口座を数える。"""
    days=[d for d in B['days'] if (lo is None or d>=lo) and (hi is None or d<hi)]
    oo={d:[o for o in B['orders_on'].get(d,[]) if keep(d,o,'s')] for d in days}
    mo={d:[o for o in B['mid_on'].get(d,[]) if keep(d,o,'m')] for d in days}
    close_of=lambda c,d:B['closes'].get(c,{}).get(d)
    a=W.account(days,oo,close_of,B['sector_of'],mid_on=mo if mid else None)
    return a
def fmt(a): return f"年率{a['cagr']:+6.1f}% 目減り{a['dd']:6.1f}% シャープ{a['sharpe']:.2f} 稼働{a['util']}% 短期{a['n']}回 勝{a['win']}% 平均{a['avg']:+.2f} 中期{a['mid_n']}回 勝{a['mid_win']}% 平均{a['mid_avg'] if a['mid_avg'] is not None else 0:+.2f} 月+{a['m_up']}%"


def fetch():
    from concurrent.futures import ThreadPoolExecutor
    from dashboard.sources import cnbc
    os.makedirs(S + '/ohlc', exist_ok=True)
    codes = list(json.load(open('docs/data/cache/ohlc.json'))['stocks'])

    def one(code):
        p = f'{S}/ohlc/{code}.json'
        if os.path.exists(p):
            return 1
        rows = None
        for _ in range(3):
            rows = cnbc.fetch_ohlc('.N225' if code == 'N225' else code + '.T', date(2021, 9, 1), date.today())
            if rows:
                break
        if not rows:
            return 0
        json.dump(rows, open(p, 'w'))
        return 1
    with ThreadPoolExecutor(4) as ex:
        r = list(ex.map(one, ['N225'] + codes))
    print('取れた', sum(r), '／', len(r))


def main():
    if '--fetch' in sys.argv:
        fetch()
        return
    B=get(); mk=market(B['nk'])
    print(B['days'][0],B['days'][-1],len(B['days']), sum(len(v) for v in B['orders_on'].values()), sum(len(v) for v in B['mid_on'].values()))
    # 損切りの後の休み: 銘柄ごとに、過去のシグナルの損切り日
    didx={d:i for i,d in enumerate(B['days'])}
    stops={}
    for on,k in ((B['orders_on'],'s'),(B['mid_on'],'m')):
        for d,rows in on.items():
            for o in rows:
                r=o['res']
                if r.get('filled') and r.get('why')=='stop': stops.setdefault((k,o['code']),[]).append(r['out'])
    for v in stops.values(): v.sort()
    def c3(n):
        def keep(d,o,k):
            i=didx[d]
            for out in stops.get((k,o['code']),[]):
                if out<=d and i-didx.get(out,-999)<=n: return False
            return True
        return keep
    def c1(n): return lambda d,o,k: (mk(d) or {'since':99})['since']>n
    c2=lambda d,o,k: (mk(d) or {'a200':True})['a200']
    def c4(x): return lambda d,o,k: o['atrp'] is None or o['atrp']<x
    def c4s(x): return lambda d,o,k: k=='m' or o['atrp'] is None or o['atrp']<x
    cands=[('今のルール',lambda d,o,k:True),('案1 急落+5日は置かない',c1(5)),('案2 日経200日線の下は置かない',c2),('案3 損切り後10日は同じ銘柄を置かない',c3(10)),
     ('案4 ATR4%未満だけ',c4(4)),('案4 ATR5%未満だけ',c4(5)),('案4 ATR6%未満だけ',c4(6))]
    for nm,lo,hi in (('前半',None,SPLIT),('後半',SPLIT,None),('直近 2026-09-25〜','2026-09-25',None)):
        print('==',nm)
        for label,keep in cands:
            a=run(B,keep,label,lo,hi)
            if a: print(f"{label:<22}", fmt(a) if a['cagr'] is not None else f"最終{a['final']:.3f} 目減り{a['dd']} 短期{a['n']}回 勝{a['win']} 平均{a['avg']} 中期{a['mid_n']}回 平均{a['mid_avg']}")


if __name__ == '__main__':
    main()
