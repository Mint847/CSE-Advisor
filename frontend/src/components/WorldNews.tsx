import { useEffect, useState } from 'react'
import { api, type GlobalHeadline, type GlobalNews, type GlobalQuote } from '../api'
import { big, gpct, gpx, gtone } from '../pages/Global'
import { ErrorBox, Spinner } from './ui'

const day = (d: string) => new Date(d + 'T00:00:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
const stamp = (p: string | null) => p ? new Date(p).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''

function Line({ h }: { h: GlobalHeadline }) {
  const t = h.tone == null ? 0 : h.tone
  return (
    <li className="flex gap-2 py-0.5">
      <span className={`w-3 shrink-0 text-center ${t > 0.15 ? 'text-bull' : t < -0.15 ? 'text-bear' : 'text-muted'}`} aria-label={t > 0.15 ? 'positive tone' : t < -0.15 ? 'negative tone' : 'neutral tone'}>{t > 0.15 ? '▲' : t < -0.15 ? '▼' : '·'}</span>
      <span className="min-w-0 flex-1">
        <a href={h.link} target="_blank" rel="noreferrer noopener" className={`hover:text-yellow hover:underline ${h.market ? 'text-fg' : 'text-muted'}`}>{h.title}</a>
        <span className="block text-[10px] text-muted">{h.outlet ?? ''}{h.published ? ` · ${stamp(h.published)}` : ''}</span>
      </span>
    </li>
  )
}

/** GLB stock page: every big move of the last year with the headlines published from the day before to the day after,
 *  and the latest headlines. Moves whose news hasn't been searched yet are searched in the background while you look. */
export default function WorldNews({ symbol }: { symbol: string }) {
  const [d, setD] = useState<GlobalNews | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  useEffect(() => {
    let live = true
    let t: number | undefined
    const load = () => api.worldNews(symbol).then(x => {
      if (!live) return
      setD(x); setErr(null)
      if (x.searching) t = window.setTimeout(load, 8000)
    }).catch(e => { if (live) setErr(String(e.message ?? e)) })
    load()
    return () => { live = false; window.clearTimeout(t) }
  }, [symbol])
  if (err) return <ErrorBox msg={err} />
  if (!d) return <Spinner />
  const moves = all ? d.moves : d.moves.slice(0, 12)
  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] [&>*]:min-w-0">
      <section className="card">
        <div className="panel-title"><h2 className="flex-1">Big moves and the news around them <span className="font-normal normal-case text-muted">{d.move_pct}% or more in a session, last 12 months</span></h2>
          {d.searching && <span className="text-[11px] font-normal normal-case text-amber" role="status">searching older moves…</span>}</div>
        {d.moves.length === 0 ? <p className="p-4 text-[12px] text-muted">No session moved {d.move_pct}% or more in the last year.</p> : (
          <ol>
            {moves.map(m => (
              <li key={m.date} className="border-t border-line/60 px-2.5 py-2 text-[12px] first:border-t-0">
                <div className="mb-1 flex items-baseline gap-2">
                  <b className={`chip ${m.pct >= 0 ? 'bg-bull text-black' : 'bg-bear text-black'} num`}>{gpct(m.pct)}</b>
                  <span className="text-fg">{day(m.date)}</span>
                </div>
                {m.headlines.length ? <ul>{m.headlines.slice(0, 5).map(h => <Line key={h.link} h={h} />)}</ul>
                  : <p className="text-[11px] text-muted">{m.checked ? 'No headline naming the company was found around this day.' : 'Searching for the headlines around this day…'}</p>}
              </li>
            ))}
          </ol>
        )}
        {d.moves.length > 12 && <button className="w-full border-t border-line px-2.5 py-1.5 text-left text-[11px] text-amber hover:bg-panel-3" onClick={() => setAll(a => !a)}>{all ? 'Show the latest 12' : `Show all ${d.moves.length} moves`}</button>}
        <p className="border-t border-line px-2.5 py-1.5 text-[11px] text-muted">Headlines from the day before to the day after each move, found by name ({d.aliases.join(', ')}); those that read like market news, and that match the move’s direction, come first. This shows what was published, not what caused the move.</p>
      </section>
      <section className="card">
        <div className="panel-title"><h2>Latest headlines</h2></div>
        {d.recent.length === 0 ? <p className="p-4 text-[12px] text-muted">None yet; they are read each night.</p>
          : <ul className="px-2.5 py-1.5 text-[12px]">{d.recent.map(h => <Line key={h.link} h={h} />)}</ul>}
      </section>
    </div>
  )
}

function Cell({ label, children, title }: { label: string; children: React.ReactNode; title?: string }) {
  return <div className="min-w-0 border-l border-line px-2.5 py-1 first:border-l-0" title={title}><div className="label !text-[10px] text-muted">{label}</div><div className="num text-[12px] text-fg">{children}</div></div>
}

export function WorldStats({ q }: { q: GlobalQuote }) {
  const pos = q.close != null && q.lo_52w != null && q.hi_52w != null && q.hi_52w > q.lo_52w ? (100 * (q.close - q.lo_52w)) / (q.hi_52w - q.lo_52w) : null
  return (
    <div className="grid grid-cols-2 gap-y-px border-y border-line bg-panel sm:grid-cols-3 lg:grid-cols-6 [&>*]:min-w-0" aria-label="Key statistics">
      <Cell label="Day range">{gpx(q.low)} – {gpx(q.high)}</Cell>
      <Cell label="52-week range">{gpx(q.lo_52w)} – {gpx(q.hi_52w)}
        {pos != null && <span className="mt-0.5 block h-1 w-full bg-panel-3"><span className="block h-1 bg-amber" style={{ width: `${Math.max(2, Math.min(100, pos))}%` }} /></span>}</Cell>
      <Cell label="From 52-wk high">{q.close && q.hi_52w ? <span className={gtone(q.close / q.hi_52w - 1 + 0.05)}>{gpct((q.close / q.hi_52w - 1) * 100)}</span> : '–'}</Cell>
      <Cell label="1W · 1M · 3M"><span className={gtone(q.perf_w)}>{gpct(q.perf_w)}</span> · <span className={gtone(q.perf_1m)}>{gpct(q.perf_1m)}</span> · <span className={gtone(q.perf_3m)}>{gpct(q.perf_3m)}</span></Cell>
      <Cell label="YTD · 1 year"><span className={gtone(q.perf_ytd)}>{gpct(q.perf_ytd)}</span> · <span className={gtone(q.perf_1y)}>{gpct(q.perf_1y)}</span></Cell>
      <Cell label="Market cap">{q.market_cap == null ? '–' : `${big(q.market_cap)} ${q.currency ?? ''}`}</Cell>
      <Cell label="P/E (12m)">{q.pe == null ? '–' : q.pe.toFixed(1)}</Cell>
      <Cell label="Dividend yield">{q.div_yield == null ? '–' : `${q.div_yield.toFixed(2)}%`}</Cell>
      <Cell label="Volume · 30d avg">{big(q.volume)} · {big(q.avg_volume_30d)}</Cell>
      <Cell label="Sector">{q.sector ?? '–'}</Cell>
      <Cell label="Industry"><span className="block truncate" title={q.industry ?? ''}>{q.industry ?? '–'}</span></Cell>
      <Cell label="Quote">{q.delay_min ? `delayed ${q.delay_min} min` : q.stored ? 'last stored close' : '–'}</Cell>
    </div>
  )
}
