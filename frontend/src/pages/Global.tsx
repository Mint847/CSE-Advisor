import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type GlobalHeadline, type GlobalRow, type GlobalList } from '../api'
import { useSession } from '../auth/session'
import { when } from '../components/Inbox'
import { useSort } from '../components/sort'
import { ErrorBox, Sparkline, Spinner, VerdictBadge } from '../components/ui'

/** Prices as each market writes them: no decimals once a price is in the thousands (KRW, JPY). */
export const gpx = (v: number | null | undefined) => v == null || !Number.isFinite(v) ? '–'
  : v.toLocaleString(undefined, { minimumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2, maximumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2 })
export const gpct = (v: number | null | undefined, nd = 1) => v == null || !Number.isFinite(v) ? '–' : `${v >= 0 ? '+' : ''}${v.toFixed(nd)}%`
export const gtone = (v: number | null | undefined) => v == null ? 'text-muted' : v >= 0 ? 'text-bull' : 'text-bear'
export const big = (v: number | null | undefined) => {
  if (v == null || !Number.isFinite(v)) return '–'
  const a = Math.abs(v)
  return a >= 1e12 ? `${(v / 1e12).toFixed(2)}T` : a >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : a >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v.toLocaleString()
}
export const worldUrl = (symbol: string) => `/global/${encodeURIComponent(symbol)}`

function Head({ h }: { h: GlobalHeadline | null }) {
  if (!h) return <span className="text-muted">–</span>
  return <a href={h.link} target="_blank" rel="noreferrer noopener" onClick={e => e.stopPropagation()} className="block truncate text-fg hover:text-yellow hover:underline" title={`${h.title} · ${h.outlet ?? ''}`}>{h.title}</a>
}

const GET: Record<string, (r: GlobalRow) => number | string | null> = {
  ticker: r => r.ticker, name: r => r.name.toLowerCase(), chg: r => r.quote?.change_pct ?? null, w: r => r.quote?.perf_w ?? null,
  m: r => r.quote?.perf_1m ?? null, ytd: r => r.quote?.perf_ytd ?? null, cap: r => r.quote?.market_cap ?? null,
  hi: r => r.quote?.close && r.quote?.hi_52w ? r.quote.close / r.quote.hi_52w - 1 : null,
  move: r => r.move ? Math.abs(r.move.pct) : null, cls: r => r.analysis?.score ?? null,
}

export default function Global() {
  const go = useNavigate()
  const admin = useSession().profile?.admin ?? true
  const [d, setD] = useState<GlobalList | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [group, setGroup] = useState('All')
  const [q, setQ] = useState('')
  const [addG, setAddG] = useState('AI & chips')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => api.globalList().then(x => { setD(x); setErr(null) }).catch(e => setErr(String(e.message ?? e))), [])
  useEffect(() => { load() }, [load])
  const filling = d?.running || (d?.rows ?? []).some(r => !r.spark.length)
  useEffect(() => { const t = window.setInterval(load, filling ? 10000 : 120000); return () => window.clearInterval(t) }, [load, filling])

  const picked = useMemo(() => (d?.rows ?? []).filter(r => group === 'All' || r.group === group), [d, group])
  const { rows, th } = useSort(picked, GET)
  const groups = useMemo(() => ['All', ...(d?.groups ?? []).filter(g => (d?.rows ?? []).some(r => r.group === g))], [d])
  const delay = (d?.rows ?? []).map(r => r.quote?.delay_min).find(x => x != null)

  const add = async () => {
    if (!q.trim()) return
    setBusy(true); setErr(null); setMsg(`Looking up ${q.trim().toUpperCase()} on TradingView, then fetching about 6 years of daily bars and its news…`)
    try {
      const r = await api.worldAdd(q.trim(), addG.trim() || 'Other')
      setMsg(`Added ${r.name} (${r.symbol}).`); setQ(''); await load()
    } catch (e) { setErr(String((e as Error).message)); setMsg(null) } finally { setBusy(false) }
  }

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-3">
      <section className="card">
        <div className="panel-title flex-wrap">
          <h1 className="flex-1">GLB · World stocks <span className="font-normal normal-case text-muted">AI &amp; chips, big tech and other leaders · TradingView{delay ? `, about ${delay} min delayed` : ''}</span></h1>
          {d?.run && <span className="text-[11px] font-normal normal-case text-muted">nightly run {when(d.run.at)}{d.run.ok ? '' : ' (some failed)'}</span>}
          {admin && <button className="btn !py-0.5" disabled={d?.running} onClick={async () => { await api.worldRun(); load() }} title="Fetch prices and news for every world stock now (a minute or two)">{d?.running ? 'Updating…' : 'Update now'}</button>}
        </div>
        <div className="flex flex-wrap items-center gap-1 border-b border-line px-2 py-1" role="radiogroup" aria-label="Group">
          {groups.map(g => <button key={g} role="radio" aria-checked={group === g} aria-pressed={group === g} className="btn !py-0 text-[11px]" onClick={() => setGroup(g)}>{g}</button>)}
          <span className="ml-auto text-[11px] text-muted">{rows.length} shown · big move = {d?.move_pct ?? 4}% or more in a session</span>
        </div>
        {err && <div className="p-2"><ErrorBox msg={err} /></div>}
        {msg && !err && <p className="px-2 pt-2 text-[12px] text-amber" role="status">{msg}</p>}
        {!d ? (err ? null : <Spinner />) : (
          <>
            {filling && <p className="border-b border-line px-3 py-1.5 text-[12px] text-amber" role="status">Filling in prices, engines and news for the world stocks in the background (about 20 seconds each the first time). This page updates by itself.</p>}
            <div className="scroll-thin overflow-x-auto">
              <table className="bb-table">
                <thead>
                  <tr>
                    {th('ticker', 'Ticker', '', true)}{th('name', 'Name', 'hidden md:table-cell', true)}
                    <th className="text-right">Last</th>{th('chg', 'Chg%', 'text-right')}
                    {th('w', '1W', 'hidden text-right md:table-cell')}{th('m', '1M', 'hidden text-right md:table-cell')}{th('ytd', 'YTD', 'hidden text-right lg:table-cell')}
                    {th('hi', 'From 52W high', 'hidden text-right lg:table-cell')}{th('cap', 'Mkt cap', 'hidden text-right xl:table-cell')}
                    <th className="hidden md:table-cell">60D</th>
                    {th('cls', 'CLS · LVL · SMC', 'hidden sm:table-cell')}
                    {th('move', 'Last big move (30d)', 'hidden lg:table-cell')}
                    <th className="hidden xl:table-cell">Latest headline</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => {
                    const qq = r.quote
                    return (
                      <tr key={r.symbol} className="row-link" onClick={() => go(worldUrl(r.symbol))}>
                        <td><span className="font-semibold text-amber">{r.ticker}</span> <span className="text-[10px] text-muted">{r.exchange}</span>
                          <div className="max-w-[140px] truncate text-[10px] text-muted md:hidden">{r.name}</div></td>
                        <td className="hidden max-w-[200px] truncate text-muted md:table-cell" title={r.name}>{r.name}</td>
                        <td className="text-right font-semibold num">{gpx(qq?.close)} <span className="text-[10px] font-normal text-muted">{r.currency ?? ''}</span></td>
                        <td className={`text-right num ${gtone(qq?.change_pct)}`}>{gpct(qq?.change_pct, 2)}</td>
                        <td className={`hidden text-right num md:table-cell ${gtone(qq?.perf_w)}`}>{gpct(qq?.perf_w)}</td>
                        <td className={`hidden text-right num md:table-cell ${gtone(qq?.perf_1m)}`}>{gpct(qq?.perf_1m)}</td>
                        <td className={`hidden text-right num lg:table-cell ${gtone(qq?.perf_ytd)}`}>{gpct(qq?.perf_ytd)}</td>
                        <td className="hidden text-right num lg:table-cell">{qq?.close && qq?.hi_52w ? gpct((qq.close / qq.hi_52w - 1) * 100) : '–'}</td>
                        <td className="hidden text-right num xl:table-cell">{big(qq?.market_cap)}</td>
                        <td className="hidden md:table-cell">{r.spark.length > 1 ? <Sparkline data={r.spark} w={80} h={22} /> : <span className="text-[10px] text-muted">loading…</span>}</td>
                        <td className="hidden whitespace-nowrap sm:table-cell">{r.analysis ? <span className="inline-flex gap-0.5"><VerdictBadge v={r.analysis.verdict} /><VerdictBadge v={r.analysis.verdict_analyst} /><VerdictBadge v={r.analysis.verdict_smc} /></span> : <span className="text-[10px] text-muted">–</span>}</td>
                        <td className="hidden max-w-[300px] lg:table-cell">{r.move ? <span className="flex min-w-0 items-baseline gap-1.5"><b className={`shrink-0 num ${gtone(r.move.pct)}`}>{gpct(r.move.pct)}</b><span className="shrink-0 text-[10px] text-muted num">{r.move.date.slice(5)}</span><span className="min-w-0 flex-1 text-[11px]"><Head h={r.move.headline} /></span></span> : <span className="text-muted">–</span>}</td>
                        <td className="hidden max-w-[320px] text-[11px] xl:table-cell"><Head h={r.headline} /></td>
                      </tr>
                    )
                  })}
                  {rows.length === 0 && <tr><td colSpan={13} className="p-6 text-center text-muted">No stocks in this group.</td></tr>}
                </tbody>
              </table>
            </div>
            {admin && (
              <form className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2" onSubmit={e => { e.preventDefault(); add() }}>
                <span className="label">Add a stock</span>
                <input className="input w-44 uppercase" value={q} onChange={e => setQ(e.target.value)} placeholder="PLTR or NYSE:ORCL" aria-label="Ticker to add" />
                <input className="input w-40" list="glb-groups" value={addG} onChange={e => setAddG(e.target.value)} aria-label="Group for the new stock" />
                <datalist id="glb-groups">{(d.groups ?? []).map(g => <option key={g} value={g} />)}</datalist>
                <button className="btn btn-primary" disabled={busy || !q.trim()}>{busy ? 'Adding…' : 'Add'}</button>
                <span className="text-[11px] text-muted">US tickers work as they are; other markets need the exchange, e.g. KRX:005930 (Samsung), TSE:7203 (Toyota).</span>
              </form>
            )}
            <p className="border-t border-line px-3 py-2 text-[11px] text-muted">
              Prices from TradingView’s free feed (delayed), news from Google News (titles and links only). The CLS / LVL / SMC verdicts are the CSE engines run on these stocks: they were built and tested on CSE stocks only, so here they are <b className="text-warn">untested</b>. Open a stock to see each big move with the headlines from the day before to the day after; that lists what was published, it doesn’t prove what caused the move. <Link to="/market" className="text-amber hover:underline">MKT</Link> has the Nasdaq-100 and S&amp;P 500 beside the ASPI.
            </p>
          </>
        )}
      </section>
    </div>
  )
}
