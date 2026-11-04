import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmt, ticker, type WatchItem } from '../api'
import { AgreeBadge, ErrorBox, Sparkline, Spinner, VerdictBadge } from '../components/ui'
import { useLive } from '../useLive'

const ORDER: Record<string, number> = { BUY: 0, SELL: 1, TRIM: 2, HOLD: 3, WATCH: 4, AVOID: 5 }
type Key = 'ticker' | 'last' | 'chg' | 'orig' | 'anly' | 'smc' | 'agree' | 'trend' | 'liq' | 'added'
const AGREE_ORDER: Record<string, number> = { agree: 0, mixed: 1, differ: 2 }

/** Nearest planned level: the Levels Buy zone 1, the Classic limit or the SMC entry zone, whichever is closest. */
function nextLevel(it: WatchItem, price: number | null) {
  const c: { tag: string; text: string; ref: number }[] = []
  if (it.zone1) c.push({ tag: 'LVL Z1', text: `${fmt(it.zone1.low)}–${fmt(it.zone1.high)}`, ref: it.zone1.high })
  if (it.plan && it.plan.actionable) c.push({ tag: 'CLS LMT', text: fmt(it.plan.ideal), ref: it.plan.ideal })
  if (it.plan_smc) c.push({ tag: `SMC ${it.plan_smc.label === 'FVG entry' ? 'FVG' : it.plan_smc.label === 'Break & retest' ? 'RTST' : 'OB'}`,
    text: `${fmt(it.plan_smc.zone.low)}–${fmt(it.plan_smc.zone.high)}`, ref: it.plan_smc.zone.high })
  if (!c.length || price == null) return c[0] ?? null
  return c.sort((a, b) => Math.abs(price - a.ref) - Math.abs(price - b.ref))[0]
}

export default function Watchlist() {
  const nav = useNavigate()
  const [items, setItems] = useState<WatchItem[] | null>(null)
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: 'orig', dir: 1 })
  const [sel, setSel] = useState(0)
  const tableRef = useRef<HTMLTableSectionElement>(null)
  const load = () => api.watchlist().then(setItems).catch(e => setErr(String(e.message ?? e)))
  useEffect(() => { load() }, [])
  const { quotes, open } = useLive(items?.map(i => i.symbol) ?? [])

  const add = async () => {
    if (!q.trim()) return
    setAdding(true); setErr(null)
    try { await api.addStock(q.trim()); setQ(''); await load() } catch (e) { setErr(String((e as Error).message)) } finally { setAdding(false) }
  }
  const remove = async (s: string) => {
    if (!confirm(`Remove ${s} from the watchlist?`)) return
    await api.removeStock(s); load()
  }

  const rows = useMemo(() => (items ?? []).map((it, i) => {
    const lq = quotes[it.symbol]
    const price = lq?.close ?? it.price
    return { it, i, price, chg: lq?.change_pct ?? it.change_pct, next: nextLevel(it, price) }
  }), [items, quotes])

  const sorted = useMemo(() => {
    const v = (r: (typeof rows)[number]): number | string => {
      switch (sort.key) {
        case 'ticker': return ticker(r.it.symbol)
        case 'last': return r.price ?? -1
        case 'chg': return r.chg ?? -999
        case 'orig': return (ORDER[r.it.verdict ?? ''] ?? 9) * 1000 - (r.it.score ?? 0)
        case 'anly': return (ORDER[r.it.verdict_analyst ?? ''] ?? 9) * 1000 - (r.it.score_analyst ?? 0)
        case 'smc': return (ORDER[r.it.verdict_smc ?? ''] ?? 9) * 1000 - (r.it.score_smc ?? 0)
        case 'agree': return AGREE_ORDER[r.it.agreement?.state ?? ''] ?? 9
        case 'trend': return r.it.trend ?? ''
        case 'liq': return r.it.liquidity_grade ?? 'Z'
        default: return r.i
      }
    }
    return [...rows].sort((a, b) => { const x = v(a), y = v(b); return (x < y ? -1 : x > y ? 1 : 0) * sort.dir })
  }, [rows, sort])

  const th = (key: Key, label: string, cls = '') => (
    <th className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className="uppercase hover:text-yellow" onClick={() => setSort(s => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
        {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  )

  const onKey = (e: React.KeyboardEvent) => {
    if (!sorted.length) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(sorted.length - 1, s + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => Math.max(0, s - 1)) }
    else if (e.key === 'Enter') { e.preventDefault(); nav(`/stock/${sorted[sel].it.symbol}`) }
  }
  useEffect(() => { tableRef.current?.querySelector<HTMLTableRowElement>(`tr[data-i="${sel}"]`)?.scrollIntoView({ block: 'nearest' }) }, [sel])

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-3">
      <section className="card">
        <div className="panel-title flex-wrap">
          <h1 className="flex-1">WL · Watchlist monitor <span className="font-normal normal-case text-muted">{items?.length ?? 0} of ~20 · {open ? <span className="text-bull">market open, live prices</span> : 'market closed'}</span></h1>
          <form className="flex gap-1" onSubmit={e => { e.preventDefault(); add() }}>
            <input className="input !py-0.5 w-48 uppercase" placeholder="ADD: JKH, COMB…" value={q} onChange={e => setQ(e.target.value)} aria-label="Stock symbol or name to add" />
            <button className="btn btn-primary !py-0.5" disabled={adding}>{adding ? 'Fetching 1y…' : 'Add'}</button>
          </form>
        </div>
        {err && <div className="p-2"><ErrorBox msg={err} /></div>}
        {!items ? <Spinner /> : items.length === 0 ? (
          <p className="p-6 text-center text-muted">Add the CSE stocks you want analysed: type <b className="text-yellow">ADD JKH &lt;GO&gt;</b> in the command line or use the box above.</p>
        ) : (
          <div className="scroll-thin overflow-x-auto" tabIndex={0} onKeyDown={onKey} aria-label="Watchlist: arrow keys move, Enter opens">
            <table className="bb-table">
              <thead>
                <tr>
                  <th className="w-8">#</th>
                  {th('ticker', 'Ticker')}
                  <th className="hidden lg:table-cell">Name</th>
                  {th('last', 'Last', 'text-right')}
                  {th('chg', 'Chg%', 'text-right')}
                  <th className="hidden md:table-cell">60D</th>
                  {th('orig', 'Cls')}
                  {th('anly', 'Lvl')}
                  {th('smc', 'SMC')}
                  {th('agree', 'Agr')}
                  {th('trend', 'Trend', 'hidden md:table-cell')}
                  {th('liq', 'Liq', 'hidden md:table-cell')}
                  <th className="hidden md:table-cell">Next level</th>
                  <th className="hidden xl:table-cell">Analysis</th>
                  <th className="w-6"><span className="sr-only">Remove</span></th>
                </tr>
              </thead>
              <tbody ref={tableRef}>
                {sorted.map(({ it, price, chg, next }, i) => (
                  <tr key={it.symbol} data-i={i} className="row-link" aria-selected={i === sel}
                      onClick={() => nav(`/stock/${it.symbol}`)} onMouseEnter={() => setSel(i)}>
                    <td className="text-muted num">{i + 1})</td>
                    <td className="font-semibold text-amber">{ticker(it.symbol)}</td>
                    <td className="hidden max-w-[220px] truncate text-muted lg:table-cell" title={it.name}>{it.name}</td>
                    <td className="text-right font-semibold num">{fmt(price)}</td>
                    <td className={`text-right num ${chg == null ? 'text-muted' : chg >= 0 ? 'text-bull' : 'text-bear'}`}>{chg == null ? '–' : `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}`}</td>
                    <td className="hidden py-0 md:table-cell"><Sparkline data={it.sparkline} w={90} h={20} /></td>
                    <td title={it.summary ?? ''}><span className="inline-flex items-center gap-1.5"><VerdictBadge v={it.verdict} /><span className="text-muted num">{it.score?.toFixed(0) ?? ''}</span></span></td>
                    <td title={it.summary_analyst ?? ''}><span className="inline-flex items-center gap-1.5"><VerdictBadge v={it.verdict_analyst} /><span className="text-muted num">{it.score_analyst?.toFixed(0) ?? ''}</span></span></td>
                    <td title={it.summary_smc ?? ''}><span className="inline-flex items-center gap-1.5"><VerdictBadge v={it.verdict_smc} /><span className="text-muted num">{it.score_smc?.toFixed(0) ?? ''}</span></span></td>
                    <td><AgreeBadge a={it.agreement} compact /></td>
                    <td className="hidden uppercase md:table-cell">{it.trend?.replace('_', ' ') ?? '–'}</td>
                    <td className="hidden md:table-cell">{it.liquidity_grade ?? '–'} <span className="text-muted">{it.liquidity_regime?.replace('_', ' ')}</span></td>
                    <td className="hidden num md:table-cell">{next ? <><span className="text-muted">{next.tag}</span> <span className="text-fg">{next.text}</span></> : <span className="text-muted">–</span>}</td>
                    <td className="hidden text-muted num xl:table-cell">{it.analysis_date ?? 'pending'}</td>
                    <td><button className="px-1 text-muted hover:text-bear" onClick={e => { e.stopPropagation(); remove(it.symbol) }} aria-label={`Remove ${it.symbol}`}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="border-t border-line px-2 py-1 text-[11px] text-muted">
          CLS = classic engine (patterns + 5-factor score) · LVL = levels engine (Fibonacci, buy zones, level entries) · SMC = smart-money engine (order blocks, premium/discount, liquidity) · AGR ● all agree ◐ mixed ○ differ · ↑↓ + ENTER to open
        </div>
      </section>
    </div>
  )
}
