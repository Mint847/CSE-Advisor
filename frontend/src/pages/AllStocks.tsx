// ALL: every security the CSE lists, with its latest price, and where it sits for you (lists, held, candidate).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmt, fmtBig, type AllStock, type AllStocksData } from '../api'
import ExportCsv from '../components/ExportCsv'
import QuantCells from '../components/QuantCells'
import type { CsvColumn } from '../export/csv'
import { ErrorBox, Spinner } from '../components/ui'

type Key = 'ticker' | 'name' | 'sector' | 'last' | 'chg' | 'volume' | 'turnover' | 'vol' | 'sharpe' | 'beta' | 'mdd'
type Filter = 'all' | 'mine' | 'cand' | 'traded'
/** JKH for an ordinary share; TKYO.X for the non-voting share (same company, another security). */
const CSV: CsvColumn<AllStock>[] = [
  { header: 'Ticker', get: r => r.symbol.split('.')[0] }, { header: 'Name', get: r => r.name }, { header: 'Sector', get: r => r.sector }, { header: 'Industry', get: r => r.industry },
  { header: 'Price', get: r => r.price }, { header: 'Change today %', get: r => r.change_pct }, { header: 'Volume', get: r => r.volume }, { header: 'Turnover (LKR)', get: r => r.turnover },
  { header: 'Market cap (LKR)', get: r => r.market_cap }, { header: 'Volatility 1Y %', get: r => (r.quant?.vol == null ? null : r.quant.vol * 100) }, { header: 'Sharpe 1Y', get: r => r.quant?.sharpe },
  { header: 'Beta 1Y', get: r => r.quant?.beta }, { header: 'Worst fall 1Y %', get: r => (r.quant?.mdd == null ? null : r.quant.mdd * 100) },
  { header: 'Meets the candidate rule', get: r => r.candidate?.qualifies }, { header: 'Held', get: r => r.held }, { header: 'On a list', get: r => r.lists.length > 0 },
]
const label = (symbol: string) => { const [t, c] = symbol.split('.'); return c && c !== 'N0000' ? t + '.' + c[0] : t }
const FILTERS: [Filter, string][] = [['all', 'All'], ['mine', 'On my lists'], ['cand', 'Candidates'], ['traded', 'Traded']]

export default function AllStocks({ onChanged }: { onChanged: () => void }) {
  const nav = useNavigate()
  const [data, setData] = useState<AllStocksData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: 'turnover', dir: -1 })
  const [busy, setBusy] = useState<string | null>(null)
  const [sector, setSector] = useState('')

  const load = useCallback(() => api.allStocks().then(d => { setData(d); setErr(null) }).catch(e => setErr(String(e.message ?? e))), [])
  useEffect(() => {
    load()
    const t = window.setInterval(load, 60000)        // the server caches the CSE summary for a minute
    return () => window.clearInterval(t)
  }, [load])

  const listName = useMemo(() => new Map((data?.lists ?? []).map(l => [l.id, l.name])), [data])
  const mine = (r: AllStock) => r.lists.length > 0 || r.held

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const v = (r: AllStock): number | string => {
      switch (sort.key) {
        case 'ticker': return label(r.symbol)
        case 'name': return r.name.toLowerCase()
        case 'sector': return (r.sector ?? '~').toLowerCase()
        case 'last': return r.price ?? -1
        case 'chg': return r.change_pct ?? -999
        case 'volume': return r.volume ?? -1
        case 'vol': return r.quant?.vol ?? (sort.dir === 1 ? 1e9 : -1e9)            // stocks without figures always sort last
        case 'sharpe': return r.quant?.sharpe ?? (sort.dir === 1 ? 1e9 : -1e9)
        case 'beta': return r.quant?.beta ?? (sort.dir === 1 ? 1e9 : -1e9)
        case 'mdd': return r.quant?.mdd ?? (sort.dir === 1 ? 1e9 : -1e9)
        default: return r.turnover ?? -1
      }
    }
    return (data?.rows ?? [])
      .filter(r => !needle || label(r.symbol).toLowerCase().includes(needle) || r.name.toLowerCase().includes(needle))
      .filter(r => !sector || r.sector === sector)
      .filter(r => filter === 'all' ? true : filter === 'mine' ? mine(r) : filter === 'cand' ? !!r.candidate?.qualifies : (r.turnover ?? 0) > 0)
      .sort((a, b) => { const x = v(a), y = v(b); return (x < y ? -1 : x > y ? 1 : 0) * sort.dir })
  }, [data, q, filter, sort, sector])
  const sectors = useMemo(() => [...new Set((data?.rows ?? []).map(r => r.sector).filter((s): s is string => !!s))].sort(), [data])

  const th = (key: Key, label: string, cls = '') => (
    <th className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button className="uppercase hover:text-yellow" onClick={() => setSort(s => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : (key === 'ticker' || key === 'name' || key === 'sector' ? 1 : -1) }))}>
        {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  )

  const add = async (r: AllStock, listId: number) => {
    setBusy(r.symbol); setErr(null); setNote(`Adding ${label(r.symbol)}: fetching its history and analysing it (this takes a few seconds)…`)
    try {
      await api.addStock(r.symbol, listId)
      setNote(`Added ${label(r.symbol)} to "${listName.get(listId)}".`)
      await load(); onChanged()
    } catch (e) { setErr(String((e as Error).message)); setNote(null) } finally { setBusy(null) }
  }

  return (
    <section className="card">
      <div className="panel-title flex-wrap">
        <h1 className="flex-1">ALL · Listed stocks <span className="font-normal normal-case text-muted">{data ? `${data.count} securities · prices from the latest CSE session` : ''}</span></h1>
        <input className="input !py-0.5 w-48" placeholder="Search ticker or name" value={q} onChange={e => setQ(e.target.value)} aria-label="Search all stocks" />
      </div>
      <div className="flex flex-wrap items-center gap-1 border-b border-line px-2 py-1" role="group" aria-label="Filter">
        {FILTERS.map(([k, label]) => (
          <button key={k} className="btn !py-0 text-[11px]" aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}</button>
        ))}
        {sectors.length > 0 && (
          <select className="input !py-0 text-[11px]" value={sector} onChange={e => setSector(e.target.value)} aria-label="Filter by sector">
            <option value="">All sectors</option>{sectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <span className="ml-auto flex items-center gap-2 text-[11px] text-muted">{rows.length} shown<ExportCsv name="all-stocks" columns={CSV} rows={rows} /></span>
      </div>
      {err && <div className="p-2"><ErrorBox msg={err} /></div>}
      {note && !err && <p className="px-2 pt-2 text-[12px] text-amber" role="status">{note}</p>}
      {!data ? (err ? null : <Spinner />) : (
        <div className="scroll-thin overflow-x-auto">
          <table className="bb-table">
            <thead>
              <tr>
                {th('ticker', 'Ticker')}
                {th('name', 'Name', 'hidden md:table-cell')}
                {th('sector', 'Sector', 'hidden lg:table-cell')}
                {th('last', 'Last', 'text-right')}
                {th('chg', 'Chg%', 'text-right')}
                {th('volume', 'Volume', 'hidden text-right lg:table-cell')}
                {th('turnover', 'Turnover', 'hidden text-right md:table-cell')}
                {th('vol', '1Y vol', 'hidden text-right xl:table-cell')}{th('sharpe', 'Sharpe', 'hidden text-right xl:table-cell')}{th('beta', 'Beta', 'hidden text-right xl:table-cell')}{th('mdd', 'Worst fall', 'hidden text-right xl:table-cell')}
                <th title="Meets the candidate rule on the CANDIDATES page">Cand</th>
                <th>Lists</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const free = (data.lists ?? []).filter(l => !r.lists.includes(l.id))
                const open = mine(r)
                return (
                  <tr key={r.symbol} className="row-link" onClick={() => nav(open ? `/stock/${r.symbol}` : `/quote/${r.symbol}`)}
                      title={open ? undefined : 'Open the quote card (add it to a list to analyse it)'}>
                    <td className="font-semibold text-amber">{label(r.symbol)}</td>
                    <td className="hidden max-w-[260px] truncate text-muted md:table-cell" title={r.name}>{r.name}</td>
                    <td className="hidden max-w-[150px] truncate text-muted lg:table-cell" title={r.industry ?? undefined}>{r.sector ?? '–'}</td>
                    <td className="text-right font-semibold num">{fmt(r.price)}</td>
                    <td className={`text-right num ${r.change_pct == null ? 'text-muted' : r.change_pct >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {r.change_pct == null ? '–' : `${r.change_pct >= 0 ? '+' : ''}${r.change_pct.toFixed(2)}`}</td>
                    <td className="hidden text-right text-muted num lg:table-cell">{fmtBig(r.volume)}</td>
                    <td className="hidden text-right num md:table-cell">{fmtBig(r.turnover)}</td>
                    <QuantCells q={r.quant} cls="hidden text-right num xl:table-cell" />
                    <td className="text-center" title={r.candidate ? (r.candidate.qualifies ? 'Meets the candidate rule' : `Meets ${r.candidate.hits} of 3 parts of the rule`) : 'Not ranked (not liquid enough)'}>
                      {r.candidate ? <span className={r.candidate.qualifies ? 'text-bull' : 'text-muted'}>{r.candidate.qualifies ? '●' : '○'}</span> : <span className="text-muted">–</span>}</td>
                    <td onClick={e => e.stopPropagation()}>
                      <span className="inline-flex flex-wrap items-center gap-1">
                        {r.held && <span className="chip bg-blue text-black">HELD</span>}
                        {r.lists.map(id => <span key={id} className="chip border border-amber/60 text-amber">{listName.get(id) ?? id}</span>)}
                        {busy === r.symbol ? <span className="text-[11px] text-muted">adding…</span> : free.length > 0 && (
                          <select className="input !py-0 text-[11px]" value="" disabled={busy !== null} aria-label={`Add ${label(r.symbol)} to a list`}
                                  onChange={e => { if (e.target.value) add(r, Number(e.target.value)) }}>
                            <option value="">+ Add to…</option>
                            {free.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
                          </select>
                        )}
                      </span>
                    </td>
                  </tr>
                )
              })}
              {rows.length === 0 && <tr><td colSpan={13} className="p-6 text-center text-muted">No stocks match.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <div className="border-t border-line px-2 py-1 text-[11px] text-muted">
        Every security in the CSE trade summary (ordinary shares, non-voting shares and others). A stock is only analysed once it is on one of your lists: choose "+ Add to…" and it is fetched, analysed and opens in a full report. ● = meets the candidate rule (see CANDIDATES).
      </div>
    </section>
  )
}
