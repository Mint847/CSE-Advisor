// Quant Lab › Events: what a price did around dated events (announcements, ex-dividend dates, results) or after a world shock
// (the S&P 500 fell 2%, Brent jumped...), beyond the ASPI, with how many cases and whether the average could be chance.
import { useEffect, useState } from 'react'
import { api, type EventCatalog, type EventSpec, type EventStudy } from '../api'
import ExportCsv from './ExportCsv'
import { pctf } from './QuantCharts'
import type { CsvColumn } from '../export/csv'
import { ErrorBox, Spinner } from './ui'

/** Average cumulative return from 5 sessions before the event to 20 after, with a ±2 standard-error band (SVG, hover on the days). */
function EventPath({ path, before, adjusted }: { path: NonNullable<EventStudy['path']>; before: number; adjusted: boolean }) {
  const [hi, setHi] = useState<number | null>(null)
  const pts = path.filter(p => p.mean != null && p.n > 0)
  const W = 560, H = 190, padL = 44, padB = 18
  const lo = Math.min(0, ...pts.map(p => (p.mean ?? 0) - 2 * (p.se ?? 0))), top = Math.max(0, ...pts.map(p => (p.mean ?? 0) + 2 * (p.se ?? 0)))
  const span = top - lo || 0.01
  const x = (d: number) => padL + ((d + before) / (path.length - 1)) * (W - padL - 6)
  const y = (v: number) => 8 + (1 - (v - lo) / span) * (H - 8 - padB)
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.day).toFixed(1)},${y(p.mean!).toFixed(1)}`).join(' ')
  const band = pts.map(p => `${x(p.day).toFixed(1)},${y((p.mean ?? 0) + 2 * (p.se ?? 0)).toFixed(1)}`).concat([...pts].reverse().map(p => `${x(p.day).toFixed(1)},${y((p.mean ?? 0) - 2 * (p.se ?? 0)).toFixed(1)}`)).join(' ')
  const ticks = [lo, 0, top].filter((v, i, a) => a.indexOf(v) === i && (v === 0 || Math.abs(v) > 0.14 * span))      // no label right on top of the 0 line
  const cur = hi != null ? pts[hi] : null
  return (
    <figure className="min-w-0" aria-label="Average cumulative return around the event">
      <figcaption className="flex flex-wrap gap-x-3 px-1 text-[11px]"><span className="label">{adjusted ? 'Cumulative return beyond the ASPI' : 'Cumulative return'}, from {before} sessions before day 0</span>
        {cur ? <span className="num text-fg">day {cur.day >= 0 ? '+' : ''}{cur.day}: <b>{pctf(cur.mean, 2)}</b> <span className="text-muted">(±{pctf(2 * (cur.se ?? 0), 2, false)}, {cur.n} cases)</span></span>
          : <span className="text-muted">hover a day · the shaded band is ±2 standard errors: if it covers 0 the average could be chance</span>}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" onMouseLeave={() => setHi(null)}>
        <polygon points={band} fill="var(--amber)" opacity="0.16" />
        {ticks.map(v => <g key={v}><line x1={padL} x2={W - 6} y1={y(v)} y2={y(v)} stroke="var(--text)" strokeOpacity={v === 0 ? 0.4 : 0.12} strokeDasharray={v === 0 ? undefined : '2 3'} />
          <text x={padL - 4} y={y(v) + 3} fontSize="10" textAnchor="end" fill="var(--muted)">{pctf(v, 1, false)}</text></g>)}
        <line x1={x(0)} x2={x(0)} y1={8} y2={H - padB} stroke="var(--blue)" strokeDasharray="3 3" /><text x={x(0) + 3} y={18} fontSize="10" fill="var(--blue)">day 0</text>
        <path d={line} fill="none" stroke="var(--amber)" strokeWidth="2" />
        {pts.map((p, i) => <g key={p.day} onMouseEnter={() => setHi(i)}><rect x={x(p.day) - 8} y={0} width={16} height={H - padB} fill="transparent" />
          {hi === i && <circle cx={x(p.day)} cy={y(p.mean!)} r={3.5} fill="var(--amber)" stroke="var(--bg)" strokeWidth="2" />}</g>)}
        {[-before, 0, 5, 10, 15, 20].map(d => <text key={d} x={x(d)} y={H - 4} fontSize="10" textAnchor="middle" fill="var(--muted)">{d > 0 ? '+' : ''}{d}</text>)}
      </svg>
    </figure>
  )
}

const sel = 'input !py-0.5 text-[12px]'
type EventRow = NonNullable<EventStudy['table']>[number]
const EVENT_CSV: CsvColumn<EventRow>[] = [
  { header: 'Stock', get: r => r.stock }, { header: 'Day 0', get: r => r.date }, { header: 'Reaction day return %', get: r => (r.day0 == null ? null : r.day0 * 100) },
  { header: '5 sessions return %', get: r => (r.car5 == null ? null : r.car5 * 100) }, { header: '20 sessions return %', get: r => (r.car20 == null ? null : r.car20 * 100) },
]

export default function LabEvents() {
  const [cat, setCat] = useState<EventCatalog | null>(null)
  const [kind, setKind] = useState<'company' | 'shock'>('company')
  const [event, setEvent] = useState('dividend')
  const [symbols, setSymbols] = useState<string[]>([])
  const [source, setSource] = useState('^SPX')
  const [direction, setDirection] = useState<'down' | 'up' | 'abs'>('down')
  const [thr, setThr] = useState(2)
  const [response, setResponse] = useState('^ASPI')
  const [adjust, setAdjust] = useState<'market' | 'none'>('market')
  const [out, setOut] = useState<EventStudy | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.labEventCatalog().then(c => { setCat(c); setSymbols(c.stocks.map(s => s.id)); if (c.shock_sources[0]) setSource(c.shock_sources[0].id) }).catch(e => setErr(String(e.message ?? e))) }, [])

  const run = async () => {
    setBusy(true); setErr(null)
    const spec: EventSpec = kind === 'company' ? { kind, event, symbols, adjust } : { kind, source, direction, threshold_pct: thr, response, adjust }
    try { setOut(await api.labEvents(spec)) } catch (e) { setErr(String((e as Error).message ?? e)); setOut(null) } finally { setBusy(false) }
  }
  const tog = (id: string) => setSymbols(s => (s.includes(id) ? s.filter(x => x !== id) : [...s, id]))
  if (!cat) return err ? <ErrorBox msg={err} /> : <Spinner label="Loading…" />
  const m = out?.meta

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Does a price tend to move around a kind of event? Pick the event, and see the average move before and after it, how many cases there were, and whether the average is big enough to be more than chance.
        It shows what tended to happen, not why, and not what will happen.</p>
      <section className="card space-y-2 p-3" aria-label="Choose the events">
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="flex" role="radiogroup" aria-label="Kind of event">
            {([['company', 'Company events'], ['shock', 'A world market shock']] as const).map(([k, l]) => <button key={k} type="button" role="radio" aria-checked={kind === k} className="btn !px-2 !py-0.5" onClick={() => setKind(k)}>{l}</button>)}
          </span>
        </div>
        {kind === 'company' ? (
          <div className="space-y-2 text-[12px]">
            <label className="flex flex-wrap items-center gap-2"><span className="label">Event</span>
              <select className={sel} value={event} onChange={e => setEvent(e.target.value)} aria-label="Kind of company event">
                {cat.company_kinds.map(k => <option key={k.id} value={k.id}>{k.label} ({k.count})</option>)}
              </select></label>
            <div className="flex flex-wrap items-center gap-1"><span className="label mr-1">Stocks</span>
              <button type="button" className="btn !py-0 text-[11px]" onClick={() => setSymbols(cat.stocks.map(s => s.id))}>all of mine</button>
              <button type="button" className="btn !py-0 text-[11px]" onClick={() => setSymbols([])}>none</button>
              {cat.stocks.map(s => <button key={s.id} type="button" aria-pressed={symbols.includes(s.id)} onClick={() => tog(s.id)}
                className={`border px-2 py-0.5 text-[11px] ${symbols.includes(s.id) ? 'border-amber bg-amber/20 text-fg' : 'border-line text-muted hover:text-fg'}`}>{s.label}</button>)}
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-2 text-[12px]">
            <label><span className="label block">When this moved</span>
              <select className={sel} value={source} onChange={e => setSource(e.target.value)} aria-label="Series that moved">
                <optgroup label="Markets and rates">{cat.shock_sources.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>
                {cat.world_stocks.length > 0 && <optgroup label="World stocks">{cat.world_stocks.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</optgroup>}
              </select></label>
            <label><span className="label block">Direction</span>
              <select className={sel} value={direction} onChange={e => setDirection(e.target.value as 'down' | 'up' | 'abs')} aria-label="Direction">
                <option value="down">fell by</option><option value="up">rose by</option><option value="abs">moved (either way) by</option></select></label>
            <label><span className="label block">at least …% in a day</span>
              <input className={`${sel} num !w-20`} type="number" min={0.2} max={50} step={0.1} value={thr} onChange={e => setThr(Number(e.target.value))} aria-label="Size of the move in percent" /></label>
            <label><span className="label block">look at</span>
              <select className={sel} value={response} onChange={e => setResponse(e.target.value)} aria-label="What to measure">
                <option value="^ASPI">the ASPI</option>{cat.stocks.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3 text-[12px]">
          {(kind === 'company' || response !== '^ASPI') && (
            <label className="flex items-center gap-1"><input type="checkbox" checked={adjust === 'market'} onChange={e => setAdjust(e.target.checked ? 'market' : 'none')} /> take out the ASPI’s own move (recommended)</label>
          )}
          <button type="button" className="btn btn-primary" disabled={busy || (kind === 'company' && !symbols.length)} onClick={run}>{busy ? 'Working…' : 'Study these events'}</button>
        </div>
      </section>

      {err && <ErrorBox msg={err} />}
      {out && !out.available && <p className="text-sm text-muted">{out.reason}{out.notes?.length ? ` ${out.notes.join('; ')}` : ''}</p>}
      {out?.available && m && (
        <div className="space-y-3">
          <h3 className="text-[13px] font-semibold text-amber">{m.title}</h3>
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            <span className="chip bg-panel-3 !normal-case !font-normal text-muted"><b className="mr-1 text-fg">{m.n_events}</b> events on <b className="mx-1 text-fg">{m.n_dates}</b> different sessions</span>
            <span className="chip bg-panel-3 !normal-case !font-normal text-muted">{m.start} → {m.end}</span>
            {m.reacts && <span className="chip bg-panel-3 !normal-case !font-normal text-muted">acts on {m.reacts}</span>}
            {m.n_dates != null && m.min_n != null && m.n_dates < m.min_n && <span className="chip border border-warn/60 !h-auto !whitespace-normal !normal-case text-warn">Too few cases to conclude anything</span>}
          </div>
          <section className="card px-3 py-2 text-[12px]" aria-label="In plain words"><div className="label mb-1">In plain words</div>
            <ul className="list-disc space-y-0.5 pl-5">{out.summary!.map(x => <li key={x}>{x}</li>)}</ul></section>
          <div className="grid gap-3 lg:grid-cols-2 [&>*]:min-w-0">
            <div className="card p-2"><EventPath path={out.path!} before={m.before ?? 5} adjusted={m.adjusted} /></div>
            <section className="card"><div className="panel-title"><h3>Average over the first sessions</h3></div>
              <div className="scroll-thin overflow-x-auto p-2">
                <table className="bb-table num text-[12px]" aria-label="Average return after the event">
                  <thead><tr><th>Over</th><th className="text-right">Average</th><th className="text-right">Middle case</th><th className="text-right">Up in</th><th className="text-right" title="Above about 2 (or below −2): probably not chance">t</th><th className="text-right">Cases</th></tr></thead>
                  <tbody>{out.horizons!.map(h => (
                    <tr key={h.sessions}><td>{h.sessions === 1 ? 'the reaction day' : `${h.sessions} sessions`}</td>
                      <td className={`text-right ${h.mean == null ? '' : h.mean >= 0 ? 'text-bull' : 'text-bear'}`}>{pctf(h.mean, 2)}</td><td className="text-right">{pctf(h.median, 2)}</td>
                      <td className="text-right">{h.positive == null ? '–' : `${Math.round(h.positive * 100)}%`}</td>
                      <td className={`text-right ${h.t != null && Math.abs(h.t) >= 2 ? 'font-semibold text-fg' : 'text-muted'}`}>{h.t == null ? '–' : h.t.toFixed(1)}</td><td className="text-right">{h.n}</td></tr>
                  ))}</tbody>
                </table>
                <p className="mt-1 text-[10px] text-muted">Cumulative from day 0. t is the average divided by its uncertainty: about 2 or more (in size) is the usual bar for “probably not chance”. Events on the same session are averaged first, and events less than 20 sessions after another in the same stock are left out.</p>
              </div></section>
          </div>
          {!!out.notes?.length && <ul className="list-disc space-y-0.5 pl-5 text-[11px] text-muted">{out.notes.map(n => <li key={n}>{n}</li>)}</ul>}
          <section className="card"><div className="panel-title"><h3 className="flex-1">The events <span className="font-normal normal-case text-muted">· latest {out.table!.length}</span></h3><ExportCsv name="event-study" columns={EVENT_CSV} rows={out.table!} /></div>
            <div className="scroll-thin max-h-[360px] overflow-auto p-2">
              <table className="bb-table num text-[12px]" aria-label="Events">
                <thead><tr><th>Stock</th><th>Day 0</th><th className="text-right">Reaction day</th><th className="text-right">5 sessions</th><th className="text-right">20 sessions</th></tr></thead>
                <tbody>{out.table!.map((r, i) => (
                  <tr key={i}><td className="text-amber">{r.stock}</td><td>{r.date}</td>
                    {[r.day0, r.car5, r.car20].map((v, j) => <td key={j} className={`text-right ${v == null ? 'text-muted' : v >= 0 ? 'text-bull' : 'text-bear'}`}>{v == null ? '–' : pctf(v, 2)}</td>)}</tr>
                ))}</tbody>
              </table>
            </div></section>
        </div>
      )}
    </div>
  )
}
