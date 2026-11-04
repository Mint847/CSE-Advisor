import { Fragment, useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, fmt, fmtBig, type FundRow, type Fundamentals } from '../api'
import { ErrorBox, ScoreBar, Section, Spinner, Stat } from './ui'

const FIELDS: [keyof FundRow, string][] = [
  ['revenue', 'Revenue'], ['gross_profit', 'Gross profit'], ['operating_profit', 'Operating profit (EBIT)'],
  ['net_profit', 'Net profit (to equity holders)'], ['eps', 'EPS'], ['total_assets', 'Total assets'],
  ['total_liabilities', 'Total liabilities'], ['total_equity', 'Total equity'], ['current_assets', 'Current assets'],
  ['current_liabilities', 'Current liabilities'], ['total_debt', 'Total borrowings'], ['finance_cost', 'Finance cost'],
  ['operating_cash_flow', 'Operating cash flow'], ['dividend_per_share', 'Dividend per share'], ['shares_outstanding', 'Shares outstanding'],
  ['one_off_income', 'One-off gains in profit (fair value, bargain purchase, non-operating)'],
]

export default function FundamentalsTab({ symbol, onSaved }: { symbol: string; onSaved: () => void }) {
  const [f, setF] = useState<Fundamentals | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [form, setForm] = useState<Record<string, string>>({ period_type: 'Q', period_end: '' })
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [reading, setReading] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const load = () => api.fundamentals(symbol).then(setF).catch(e => setErr(String(e.message ?? e)))
  useEffect(() => { setF(null); load() }, [symbol]) // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <ErrorBox msg={err} />
  if (!f) return <Spinner />
  const m = f.market, r = f.ratios
  const pos52 = m.high_52w && m.low_52w && m.high_52w > m.low_52w ? ((m.price - m.low_52w) / (m.high_52w - m.low_52w)) * 100 : null
  const chartData = f.periods.filter(p => p.period_type === 'Q').slice(-8).map(p => ({
    period: p.period_end.slice(0, 7), Revenue: p.revenue ?? null, 'Net profit': p.net_profit ?? null,
  }))

  const save = async () => {
    if (!form.period_end) return
    setSaving(true)
    const row: FundRow = { period_end: form.period_end, period_type: form.period_type as 'Q' | 'FY' }
    for (const [k] of FIELDS) {
      const v = form[k as string]
      if (v !== undefined && v !== '') (row as unknown as Record<string, number>)[k as string] = Number(v.replace(/,/g, ''))
    }
    try { await api.saveFundamental(symbol, row); setForm({ period_type: form.period_type, period_end: '' }); await load(); onSaved() }
    catch (e) { setErr(String((e as Error).message)) } finally { setSaving(false) }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
      <Section title="Market snapshot">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="Price" value={fmt(m.price)} />
          <Stat label="Market cap" value={`LKR ${fmtBig(m.market_cap)}`} />
          <Stat label="Shares issued" value={fmtBig(m.shares)} />
          <Stat label="Beta (ASPI)" value={fmt(f.beta.aspi)} sub={`S&P SL20 ${fmt(f.beta.spsl20)}`} />
          <Stat label="YTD turnover" value={`LKR ${fmtBig(m.ytd_turnover)}`} />
          <Stat label="All-time high" value={fmt(m.all_time_high)} />
        </div>
        {pos52 != null && (
          <div className="mt-3">
            <div className="mb-1 flex justify-between text-xs text-muted num"><span>52w low {fmt(m.low_52w)}</span><span>52w high {fmt(m.high_52w)}</span></div>
            <div className="relative h-2 bg-panel-2" role="img" aria-label={`Price is ${pos52.toFixed(0)}% of the way from the 52-week low to high`}>
              <div className="absolute top-1/2 h-3.5 w-1 -translate-y-1/2 bg-accent" style={{ left: `calc(${pos52}% - 2px)` }} />
            </div>
            <div className="mt-1 text-xs text-muted">Price sits at {pos52.toFixed(0)}% of its 52-week range.</div>
          </div>
        )}
      </Section>

      <Section title="Fundamental score" right={f.basis ? <span className="text-xs text-muted">{f.basis}</span> : null}>
        {!f.has_data ? (
          <div className="text-sm text-muted">
            <p>{f.detail}</p>
            <p className="mt-2">Figures are read automatically from the quarterly statement PDFs each evening (and once when a stock is added). Statements that are scanned images can't be read: open them below and use <b>Add figures</b>. Fundamentals carry 15% of the Classic engine's score; until figures exist, that weight is spread across the other factors.</p>
          </div>
        ) : (
          <>
            <ScoreBar score={f.score} label="Fundamental score" />
            <ul className="mt-3 space-y-1 text-sm">
              {(f.components ?? []).map(c => (
                <li key={c.name} className="flex justify-between gap-2"><span className="capitalize">{c.name}</span><span className="text-muted">{c.reason}</span><b className="num">{c.score.toFixed(0)}</b></li>
              ))}
            </ul>
            {f.flags.length > 0 && <ul className="mt-2 space-y-1 text-sm text-bear">{f.flags.map(x => <li key={x}>! {x}</li>)}</ul>}
          </>
        )}
      </Section>

      {f.has_data && (
        <Section title="Key ratios">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="P/E" value={fmt(r.pe, 1)} />
            <Stat label="P/BV" value={fmt(r.pbv)} />
            <Stat label="EPS (TTM)" value={fmt(r.eps_ttm)} />
            <Stat label="ROE" value={r.roe_pct != null ? `${fmt(r.roe_pct, 1)}%` : '–'} />
            <Stat label="Net margin" value={r.net_margin_pct != null ? `${fmt(r.net_margin_pct, 1)}%` : '–'} />
            <Stat label="Debt / equity" value={fmt(r.debt_to_equity)} />
            <Stat label="Interest cover" value={r.interest_cover != null ? `${fmt(r.interest_cover, 1)}×` : '–'} />
            <Stat label="Dividend yield" value={r.dividend_yield_pct != null ? `${fmt(r.dividend_yield_pct, 1)}%` : '–'} />
            <Stat label="Profit growth" value={r.profit_growth_pct != null ? `${fmt(r.profit_growth_pct, 0)}%` : '–'} tone={r.profit_growth_pct == null ? undefined : r.profit_growth_pct >= 0 ? 'bull' : 'bear'} />
            <Stat label="Current ratio" value={fmt(r.current_ratio)} />
            <Stat label="CFO / profit" value={fmt(r.cfo_to_profit)} />
            <Stat label="NAV / share" value={fmt(r.bvps)} sub={r.price_to_nav_discount_pct != null ? `price ${r.price_to_nav_discount_pct >= 0 ? '+' : ''}${fmt(r.price_to_nav_discount_pct, 0)}% vs NAV` : undefined} />
            {r.core_eps != null && <Stat label="Core EPS" value={fmt(r.core_eps)} sub={r.core_pe != null ? `core P/E ${fmt(r.core_pe, 1)}` : undefined} />}
            {r.one_off_share_pct != null && <Stat label="One-off share of profit" value={`${fmt(r.one_off_share_pct, 0)}%`} tone={r.one_off_share_pct > 50 ? 'bear' : undefined} />}
            {f.relative?.pe_vs_market_pct != null && <Stat label="P/E vs market" value={`${f.relative.pe_vs_market_pct >= 0 ? '+' : ''}${fmt(f.relative.pe_vs_market_pct, 0)}%`} sub={`market P/E ${f.relative.market_pe}`} tone={f.relative.pe_vs_market_pct < 0 ? 'bull' : 'bear'} />}
            {f.relative?.pbv_vs_market_pct != null && <Stat label="P/BV vs market" value={`${f.relative.pbv_vs_market_pct >= 0 ? '+' : ''}${fmt(f.relative.pbv_vs_market_pct, 0)}%`} sub={`market P/BV ${f.relative.market_pbv}`} tone={f.relative.pbv_vs_market_pct < 0 ? 'bull' : 'bear'} />}
          </div>
          {f.valuation_band && (
            <div className="mt-3 bg-panel-2 p-3 text-sm">
              <b>Value range (low / mid / high):</b> {fmt(f.valuation_band.low)} / {fmt(f.valuation_band.mid)} / {fmt(f.valuation_band.high)} vs price {fmt(m.price)} ({m.price < f.valuation_band.low ? 'below the range' : m.price > f.valuation_band.high ? 'above the range' : m.price <= f.valuation_band.mid ? 'in the lower half: attractive for a value investor' : 'in the upper half'})
              <div className="text-xs text-muted">{f.valuation_band.method}</div>
            </div>
          )}
        </Section>
      )}

      {chartData.length >= 2 && (
        <Section title="Quarterly revenue & net profit (LKR)">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} barGap={2} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
                <XAxis dataKey="period" tick={{ fill: 'var(--muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={v => fmtBig(v)} tick={{ fill: 'var(--muted)', fontSize: 11 }} axisLine={false} tickLine={false} width={48} />
                <Tooltip formatter={(v) => `LKR ${fmtBig(Number(v))}`} cursor={{ fill: 'var(--panel-2)' }}
                         contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 0, color: 'var(--text)', fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12, color: 'var(--text)' }} />
                <Bar dataKey="Revenue" fill="var(--series-1)" radius={0} maxBarSize={22} />
                <Bar dataKey="Net profit" fill="var(--series-2)" radius={0} maxBarSize={22} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>
      )}

      <Section title="Figures" className="lg:col-span-2" right={<span className="flex gap-1">
        <button className="btn !py-1 text-xs" disabled={!!reading} title="Download and read the newest quarterly statements again"
                onClick={async () => { setReading('Reading…'); try { const r = await api.extractFundamentals(symbol); setReading(`${r.parsed.length} read, ${r.review.length} to check, ${r.unreadable.length} unreadable`); await load(); onSaved() } catch (e) { setReading(String((e as Error).message)) } }}>
          {reading === 'Reading…' ? 'Reading…' : 'Re-read statements'}</button>
        <button className="btn btn-primary !py-1 text-xs" onClick={() => setShowForm(s => !s)}>{showForm ? 'Close' : 'Add figures'}</button></span>}>
        {reading && reading !== 'Reading…' && <p className="mb-2 text-[12px] text-muted">{reading}</p>}
        {(f.review?.length ?? 0) > 0 && (
          <p className="mb-2 border border-warn/60 px-2 py-1 text-[12px]"><b className="text-warn">{f.review!.length} auto-read row{f.review!.length > 1 ? 's' : ''} to check.</b> Their cross-checks didn't pass, so they are shown but not used in any score. Open a row to see the lines it was read from and the PDF, then <b>Confirm</b> it or correct it with Add figures.</p>
        )}
        {showForm && (
          <div className="mb-4 border border-line p-3">
            <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
              <label className="text-xs">Period end<input type="date" className="input mt-0.5 w-full" value={form.period_end} onChange={e => setForm({ ...form, period_end: e.target.value })} /></label>
              <label className="text-xs">Type<select className="input mt-0.5 w-full" value={form.period_type} onChange={e => setForm({ ...form, period_type: e.target.value })}><option value="Q">Quarter (3 months)</option><option value="FY">Full year</option></select></label>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {FIELDS.map(([k, label]) => (
                <label key={k} className="text-xs">{label}
                  <input inputMode="decimal" className="input mt-0.5 w-full num" value={form[k as string] ?? ''} placeholder="LKR"
                         onChange={e => setForm({ ...form, [k as string]: e.target.value })} />
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted">Enter full LKR amounts (not '000s). Quarter figures should be for the 3 months only, not year-to-date. Leave unknown fields blank.</p>
            <button className="btn btn-primary mt-2" disabled={saving || !form.period_end} onClick={save}>{saving ? 'Saving…' : 'Save & re-analyse'}</button>
          </div>
        )}
        {f.periods.length + (f.review?.length ?? 0) === 0 ? <p className="text-sm text-muted">No figures yet.</p> : (
          <div className="scroll-thin overflow-x-auto">
            <table className="bb-table w-full">
              <thead><tr><th className="text-left">Period</th><th>Revenue</th><th>Net profit</th><th>Equity</th><th>EPS</th><th className="text-left">Source</th><th /></tr></thead>
              <tbody>
                {[...f.periods, ...(f.review ?? [])].sort((x, y) => y.period_end.localeCompare(x.period_end)).map(p => {
                  const d = p.detail
                  const key = `${p.period_end}${p.period_type}`
                  return (<Fragment key={key}>
                    <tr className={p.needs_review ? 'opacity-70' : ''}>
                      <td className="text-left num">{p.period_end} {p.period_type}</td><td className="num">{fmtBig(p.revenue)}</td><td className="num">{fmtBig(p.net_profit)}</td>
                      <td className="num">{fmtBig(p.total_equity)}</td><td className="num">{fmt(p.eps)}</td>
                      <td className="text-left">
                        <button className="text-blue hover:underline" onClick={() => setOpen(open === key ? null : key)} title="Show where the figures came from">
                          {p.source === 'pdf' ? 'PDF' : p.source === 'pdf_prior' ? 'comparative' : p.source ?? 'manual'}</button>
                        {p.needs_review && <span className="chip ml-1 bg-warn text-black">check</span>}
                        {d?.confirmed && <span className="chip ml-1 bg-panel-3 text-bull">confirmed</span>}
                      </td>
                      <td className="whitespace-nowrap text-right">
                        {p.needs_review && <button className="btn !py-0 text-[11px]" onClick={async () => { await api.confirmFundamental(symbol, p.id!); await load(); onSaved() }}>Confirm</button>}
                        <button className="ml-2 text-bear" aria-label="Delete period" onClick={async () => { await api.deleteFundamental(symbol, p.id!); load(); onSaved() }}>✕</button>
                      </td>
                    </tr>
                    {open === key && (
                      <tr><td colSpan={7} className="whitespace-normal bg-panel-2 text-left text-[11px]">
                        {d?.title && <div>From <a className="text-blue hover:underline" href={d.url} target="_blank" rel="noreferrer">{d.title}</a>{d.column ? ` · column: ${d.column}` : ''}{d.comparative ? ' · comparative (prior-year) column' : ''}</div>}
                        {d?.checks?.map(c => <div key={c} className={c.includes('✗') ? 'text-bear' : c.includes('✓') ? 'text-bull' : 'text-muted'}>{c}</div>)}
                        {d?.evidence && <div className="mt-1 grid gap-0.5">{Object.entries(d.evidence).map(([k, v]) => <div key={k} className="num"><span className="text-amber">{k.replace(/_/g, ' ')}</span> ← {v}</div>)}</div>}
                        {!d && <span className="text-muted">entered by hand</span>}
                      </td></tr>
                    )}
                  </Fragment>)
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-1 text-[11px] text-muted">Read by rules from the PDFs: the Group, current-quarter column; amounts in LKR. A row is used when EPS × shares matches its profit, NAV × shares matches equity, or a newer report's comparative column shows the same profit.</p>
      </Section>

      <Section title="Financial reports (CSE)" className="lg:col-span-2">
        <div className="scroll-thin max-h-96 overflow-auto">
          <table className="bb-table w-full">
            <thead><tr><th className="text-left">Report</th><th className="text-left">Period</th><th className="text-left">Filed</th><th className="text-left">Document</th></tr></thead>
            <tbody>
              {(['quarterly', 'annual'] as const).map(k => {
                const d = f.next_reports?.[k] ?? null
                const label = k === 'annual' ? 'Annual' : 'Quarter'
                return (
                  <tr key={`next-${k}`} className="bg-panel-2">
                    <td className="text-left"><span className={`chip ${k === 'annual' ? 'bg-accent/15 text-blue' : 'bg-panel-3 text-muted'}`}>{label}</span> <b className="text-amber">NEXT</b></td>
                    <td className="text-left num">{d ? d.period_end : '–'}</td>
                    <td className="text-left num" colSpan={2}>
                      {d ? (<>
                        <b className={d.status === 'overdue' ? 'text-warn' : 'text-fg'}>{d.status === 'overdue' ? 'overdue: expected about ' : 'expected about '}{d.expected}</b>
                        <span className="text-muted"> · {d.status === 'overdue' ? `${-d.days} days past` : d.days >= 0 ? `in ${d.days} days` : ''} · usual range {d.window[0]} to {d.window[1]}</span>
                        <div className="whitespace-normal text-[11px] text-muted" title={d.basis}>
                          {k === 'annual' && (d as { year_ended?: boolean }).year_ended ? 'The financial year has ended. ' : k === 'annual' ? `Financial year ends ${d.period_end}. ` : ''}Estimate from this company's own filing history: {d.basis}.
                        </div>
                      </>) : <span className="text-muted">not enough filing history to estimate</span>}
                    </td>
                  </tr>
                )
              })}
              {[...f.reports].sort((x, y) => (y.filed ?? '').localeCompare(x.filed ?? '')).map(r => (
                <tr key={r.id}>
                  <td className="text-left"><span className={`chip ${r.kind === 'annual' ? 'bg-accent/15 text-blue' : 'bg-panel-2 text-muted'}`}>{r.kind === 'annual' ? 'Annual' : 'Quarter'}</span></td>
                  <td className="text-left num">{r.period ?? '–'}</td>
                  <td className="text-left num">{r.filed ?? '–'}</td>
                  <td className="text-left"><a className="block max-w-[560px] truncate text-blue hover:underline" href={r.url} target="_blank" rel="noreferrer" title={r.title}>{r.title}</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-[11px] text-muted">Expected dates are estimates, not announcements: the next period end plus how many days this company took after its recent period ends. CSE publishes no results calendar, and companies can file earlier or later.</p>
      </Section>
    </div>
  )
}
