// CN · Catalysts: news, results and events for one stock, as evidence beside the engines.
import { useEffect, useMemo, useState } from 'react'
import { api, fmt, type Catalysts, type Overall, type Reaction } from '../api'
import { ErrorBox, Section, Spinner, Stat } from './ui'

const OVERALL_CLS: Record<Overall, string> = { Supportive: 'bg-bull text-black', Neutral: 'bg-amber text-black', Cautious: 'bg-bear text-black' }
const DIR = { 1: { icon: '▲', cls: 'text-bull', word: 'supportive' }, 0: { icon: '●', cls: 'text-muted', word: 'neutral' }, [-1]: { icon: '▼', cls: 'text-bear', word: 'cautious' } } as const
const mn = (v: number | null | undefined) => v == null ? '–' : Math.abs(v) >= 1e9 ? `${(v / 1e9).toFixed(2)}bn` : `${(v / 1e6).toLocaleString(undefined, { maximumFractionDigits: 0 })}M`
const pct = (v: number | null | undefined, nd = 1) => v == null ? '–' : `${v >= 0 ? '+' : ''}${v.toFixed(nd)}%`
const tone = (v: number | null | undefined) => (v ?? 0) > 0 ? 'text-bull' : (v ?? 0) < 0 ? 'text-bear' : 'text-muted'
const label = (k: string) => k.replace(/_/g, ' ')

export function OverallBadge({ o, small = false }: { o: Overall; small?: boolean }) {
  return <span className={`chip ${OVERALL_CLS[o]} ${small ? '' : '!px-2 !py-0.5 text-[12px]'}`} title="Catalysts: news, results and events (not part of any engine's score)">{o.toUpperCase()}</span>
}

function ReactionChip({ r }: { r: Reaction | null | undefined }) {
  if (!r) return null
  const x = r.x5 ?? r.r5
  return (
    <span className="chip shrink-0 bg-panel-2 num" title={`Session ${r.session}: ${pct(r.r0)} that day; ${pct(r.r5)} over 5 sessions${r.x5 != null ? `, ${pct(r.x5)} vs ASPI` : ''}`}>
      <span className={tone(r.r0)}>{pct(r.r0)}</span><span className="text-muted"> · 5d </span><span className={tone(x)}>{pct(x)}</span>
    </span>
  )
}

export default function CatalystsTab({ symbol }: { symbol: string }) {
  const [c, setC] = useState<Catalysts | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [cat, setCat] = useState<string>('all')
  const [q, setQ] = useState('')
  const [all, setAll] = useState(false)
  useEffect(() => { setC(null); setErr(null); api.catalysts(symbol).then(setC).catch(e => setErr(String(e.message ?? e))) }, [symbol])
  const cats = useMemo(() => {
    const m = new Map<string, number>()
    for (const a of c?.announcements ?? []) m.set(a.category ?? 'other', (m.get(a.category ?? 'other') ?? 0) + 1)
    return [...m.entries()].sort((a, b) => b[1] - a[1])
  }, [c])
  if (err) return <ErrorBox msg={err} />
  if (!c) return <Spinner label="Reading news, results and events…" />
  const sc = c.scorecard
  const due = c.results_due
  const anns = c.announcements.filter(a => (cat === 'all' || (a.category ?? 'other') === cat)
    && (!q || `${a.title} ${a.subject ?? ''} ${a.summary ?? ''}`.toLowerCase().includes(q.toLowerCase())))

  return (
    <div className="grid gap-2 [&>*]:min-w-0">
      {/* scorecard */}
      <Section title="Catalysts scorecard" right={<span className="flex items-center gap-2"><OverallBadge o={sc.overall} /><b className="num text-fg">{sc.total >= 0 ? '+' : ''}{sc.total}</b></span>}>
        <div className="scroll-thin overflow-x-auto">
          <table className="bb-table w-full [&_td]:whitespace-normal">
            <thead><tr><th className="text-left">Signal</th><th className="text-left">Reading</th><th className="w-20 text-left">Reads as</th><th className="text-left">Why</th></tr></thead>
            <tbody>{sc.rows.map(r => {
              const d = DIR[r.direction]
              return (
                <tr key={r.signal}>
                  <td className="text-left text-amber">{r.signal}</td>
                  <td className="min-w-48 text-left">{r.reading}</td>
                  <td className={`text-left ${d.cls}`}>{d.icon} {d.word}{r.weight > 1 ? ` ×${r.weight}` : ''}</td>
                  <td className="text-left text-muted">{r.why}</td>
                </tr>
              )
            })}</tbody>
          </table>
        </div>
        <p className="mt-1.5 text-[11px] text-muted">{sc.note}</p>
      </Section>

      {c.risk_lines.length > 0 && (
        <div className="card border-warn/60 px-2.5 py-2 text-[12px]">
          <div className="label pb-1 text-warn">Event risk</div>
          <ul className="list-disc space-y-0.5 pl-5">{c.risk_lines.map(x => <li key={x}>{x}</li>)}</ul>
        </div>
      )}

      <div className="grid gap-2 lg:grid-cols-2 [&>*]:min-w-0">
        <Section title="Calendar (next 120 days)">
          {due && (
            <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-3 [&>*]:min-w-0">
              <Stat label="Next results" value={due.expected} sub={`quarter to ${due.period_end}`} tone={due.status === 'overdue' ? 'warn' : undefined} />
              <Stat label="Window" value={`${due.window[0].slice(5)} → ${due.window[1].slice(5)}`} sub={`usual lag ${due.lag_days} days`} />
              <Stat label="Status" value={due.status === 'overdue' ? 'overdue' : `${due.days} days`} sub={due.history_lags.slice(-4).join(', ') + ' d before'} tone={due.status === 'overdue' || due.days <= 7 ? 'warn' : undefined} />
            </div>
          )}
          {c.calendar.length === 0 ? <p className="text-[12px] text-muted">Nothing scheduled that CSE has published.</p> : (
            <table className="bb-table w-full [&_td]:whitespace-normal">
              <tbody>{c.calendar.map((e, i) => (
                <tr key={i}>
                  <td className="w-24 text-left num">{e.date}</td>
                  <td className="w-14 num text-muted">{e.days}d</td>
                  <td className="text-left"><b className={e.kind === 'ex_dividend' || e.kind === 'dividend_payment' ? 'text-bull' : e.kind === 'results_due' ? 'text-amber' : 'text-fg'}>{e.title}</b>
                    {e.detail && <div className="text-[11px] text-muted">{e.detail}</div>}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
          {due && <p className="mt-1 text-[11px] text-muted">Results date: {due.basis}.</p>}
        </Section>

        <Section title="Results (from the quarterly statements)">
          {c.results.length === 0 ? <p className="text-[12px] text-muted">No figures read yet. They are read from the statement PDFs each evening; see DES for details.</p> : (
            <div className="scroll-thin overflow-x-auto">
              <table className="bb-table w-full">
                <thead><tr><th className="text-left">Quarter</th><th>Revenue</th><th>Profit</th><th>EPS</th><th>Profit YoY</th><th className="text-left">Source</th></tr></thead>
                <tbody>{c.results.map(r => (
                  <tr key={r.period_end} className={r.needs_review ? 'opacity-60' : ''}>
                    <td className="text-left num">{r.period_end}</td>
                    <td className="num">{mn(r.revenue)}</td>
                    <td className={`num ${tone(r.net_profit)}`}>{mn(r.net_profit)}</td>
                    <td className="num">{r.eps != null ? r.eps.toFixed(2) : '–'}</td>
                    <td className={`num ${tone(r.profit_yoy_pct)}`}>{pct(r.profit_yoy_pct, 0)}</td>
                    <td className="text-left">
                      {r.url ? <a className="text-blue hover:underline" href={r.url} target="_blank" rel="noreferrer">{r.source === 'pdf_prior' ? 'comparative' : r.source}</a> : r.source}
                      {r.needs_review && <span className="chip ml-1 bg-warn text-black" title={(r.checks ?? []).join('\n')}>check</span>}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <p className="mt-1 text-[11px] text-muted">"comparative" = the same quarter a year earlier, from a newer report's comparison column. Rows marked check are shown but not used in any score.</p>
        </Section>
      </div>

      <div className="grid gap-2 lg:grid-cols-2 [&>*]:min-w-0">
        <Section title="Director dealings">
          <div className="mb-2 grid grid-cols-2 gap-2 [&>*]:min-w-0">
            {[c.insiders.d90, c.insiders.d365].map(w => (
              <Stat key={w.days} label={`Last ${w.days} days`} value={<span className={tone(w.net_value)}>{w.net_value ? `${w.net_value > 0 ? '+' : '−'}LKR ${mn(Math.abs(w.net_value))}` : 'none'}</span>}
                    sub={`${w.buys} buying · ${w.sells} selling notices`} tone={w.net_value > 0 ? 'bull' : w.net_value < 0 ? 'bear' : undefined} />
            ))}
          </div>
          {c.insiders.recent.length === 0 ? <p className="text-[12px] text-muted">No director dealings on record.</p> : (
            <table className="bb-table w-full [&_td]:whitespace-normal">
              <tbody>{c.insiders.recent.map((d, i) => (
                <tr key={i}>
                  <td className="w-24 text-left num">{d.date}</td>
                  <td className="text-left">{d.director}<div className="text-[11px] text-muted">{d.role}</div></td>
                  <td className="text-left text-[11px]">{d.trades.map((t, j) => <div key={j} className={t.side === 'buy' ? 'text-bull' : t.side === 'sell' ? 'text-bear' : ''}>{t.side} {t.qty.toLocaleString()} @ {fmt(t.price)}</div>)}</td>
                  <td className={`num ${tone(d.net_value)}`}>{d.net_value ? `${d.net_value > 0 ? '+' : '−'}${mn(Math.abs(d.net_value))}` : '–'}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
          {c.insiders.unknown_direction > 0 && <p className="mt-1 text-[11px] text-muted">{c.insiders.unknown_direction} notice{c.insiders.unknown_direction > 1 ? 's' : ''} from the last 3 years not read yet; they are read in the background.</p>}
        </Section>

        <Section title="Dividends">
          <div className="mb-2 grid grid-cols-2 gap-2 [&>*]:min-w-0">
            <Stat label="Last 12 months" value={c.dividends.ttm_dps ? `Rs ${c.dividends.ttm_dps}` : '–'} sub={c.dividends.ttm_yield_pct ? `yield ${c.dividends.ttm_yield_pct.toFixed(1)}%` : 'by ex-dividend date'} />
            <Stat label="XD-day drop" value={c.dividends.xd_drops.length ? `${Math.round(c.dividends.xd_drops.reduce((a, x) => a + x.drop_vs_dps_pct, 0) / c.dividends.xd_drops.length)}% of DPS` : '–'}
                  sub={c.dividends.xd_drops.length ? `average of the last ${c.dividends.xd_drops.length}` : 'no XD history yet'} />
          </div>
          {c.dividends.history.length === 0 ? <p className="text-[12px] text-muted">No cash dividends read yet.</p> : (
            <table className="bb-table w-full">
              <thead><tr><th className="text-left">FY</th><th>DPS</th><th>XD</th><th>Paid</th></tr></thead>
              <tbody>{c.dividends.history.map((d, i) => (
                <tr key={i}><td className="text-left">{d.fy ?? '–'}{d.final ? ' final' : ''}</td><td className="num text-bull">Rs {d.dps}</td><td className="num">{d.xd ?? '–'}</td><td className="num">{d.payment ?? '–'}</td></tr>
              ))}</tbody>
            </table>
          )}
          <p className="mt-1 text-[11px] text-muted">On the XD date the price usually drops by about the dividend. That drop is not weakness, and the chart is not adjusted for cash dividends.</p>
        </Section>
      </div>

      <Section title="How the price reacted (this stock's own history)">
        {c.reaction_stats.length === 0 ? <p className="text-[12px] text-muted">No dated events to study yet.</p> : (
          <div className="scroll-thin overflow-x-auto">
            <table className="bb-table w-full [&_td]:whitespace-normal">
              <thead><tr><th className="text-left">Event</th><th>n</th><th>Median day</th><th>Median 5d vs ASPI</th><th>5d positive</th><th className="text-left">Read</th></tr></thead>
              <tbody>{c.reaction_stats.map(r => (
                <tr key={r.kind}>
                  <td className="text-left text-amber">{label(r.kind)}</td><td className="num">{r.n}</td>
                  <td className={`num ${tone(r.median_day_pct)}`}>{pct(r.median_day_pct)}</td>
                  <td className={`num ${tone(r.median_5d_excess_pct)}`}>{pct(r.median_5d_excess_pct)}</td>
                  <td className="num">{r.pct_positive_5d != null ? `${r.pct_positive_5d}%` : '–'}</td>
                  <td className="text-left text-[11px] text-muted">{r.readable ? 'enough events to read' : r.note}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <p className="mt-1 text-[11px] text-muted">An announcement after the 14:30 close counts from the next session. "vs ASPI" removes the market's own move over the same sessions.</p>
      </Section>

      <Section title={`Headlines${c.headlines.n30 ? ` · ${c.headlines.n30} in 30 days` : ''}`}>
        {c.headlines.items.length === 0 ? <p className="text-[12px] text-muted">No recent EconomyNext or Daily Mirror headline names this company.</p> : (
          <ul className="divide-y divide-line/60">{c.headlines.items.map(h => (
            <li key={h.link} className="flex items-start gap-2 py-1.5 text-[12px]">
              <span className="w-28 shrink-0 text-[11px] text-muted num">{h.published?.slice(0, 10)}<div>{h.source}</div></span>
              <a className="min-w-0 flex-1 text-fg hover:text-amber hover:underline" href={h.link} target="_blank" rel="noreferrer">{h.title}</a>
              <span className={`w-12 shrink-0 text-right num ${tone(h.tone)}`} title="keyword tone of the headline">{(h.tone ?? 0) > 0 ? '+' : ''}{(h.tone ?? 0).toFixed(1)}</span>
            </li>
          ))}</ul>
        )}
        <p className="mt-1 text-[11px] text-muted">Matched by the full company name or ticker. The tone is a keyword guess, so headlines count only lightly and never raise a red flag.</p>
      </Section>

      <Section title="CSE announcements" right={<input className="input w-40 !py-0.5 text-[12px]" placeholder="search" value={q} onChange={e => setQ(e.target.value)} aria-label="Search announcements" />}>
        <div className="scroll-thin mb-2 flex gap-1 overflow-x-auto pb-1">
          {[['all', c.announcements.length] as [string, number], ...cats].map(([k, n]) => (
            <button key={k} className={`chip shrink-0 border ${cat === k ? 'border-amber bg-amber text-black' : 'border-line bg-panel-2 text-muted hover:text-fg'}`} onClick={() => setCat(k)}>{label(k)} {n}</button>
          ))}
        </div>
        {anns.length === 0 ? <p className="text-[12px] text-muted">No announcements match.</p> : (
          <ul className="divide-y divide-line/60">
            {(all || q || cat !== 'all' ? anns : anns.slice(0, 25)).map(a => (
              <li key={a.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-1.5 text-[12px] sm:flex-nowrap">
                <span className="w-24 shrink-0 text-[11px] text-muted num">{a.date?.slice(0, 10)}</span>
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-fg">{a.summary ?? a.subject ?? a.title}</span>
                  {(a.summary || a.subject) && <span className="block text-[11px] text-muted">{a.title}{a.summary && a.subject ? ` · ${a.subject}` : ''}</span>}
                </span>
                <ReactionChip r={a.reaction} />
                <span className="chip shrink-0 bg-panel-2 text-muted">{label(a.category ?? 'other')}</span>
                <span className={`w-9 shrink-0 text-right text-[11px] num ${tone(a.sentiment)}`} title={`sentiment ${a.sentiment ?? 0}, materiality ${a.materiality ?? 1}/5`}>
                  {(a.sentiment ?? 0) > 0 ? '+' : ''}{(a.sentiment ?? 0).toFixed(1)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {!all && !q && cat === 'all' && anns.length > 25 && (
          <button className="btn mt-1 !py-0.5 text-[12px]" onClick={() => setAll(true)}>Show all {anns.length}</button>
        )}
        <p className="mt-1 text-[11px] text-muted">Each notice is classified by keyword rules from its title and what CSE's detail record says: dividend amounts and dates, director trades, appointments, and the attachment's subject. The chip shows the price move that session and over 5 sessions vs the ASPI.</p>
      </Section>
    </div>
  )
}
