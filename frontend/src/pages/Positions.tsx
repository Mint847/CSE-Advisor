import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, ticker, type Portfolio, type PositionRow, type WatchItem } from '../api'
import type { CsvColumn } from '../export/csv'
import ExportCsv from '../components/ExportCsv'
import NotesPanel from '../components/NotesPanel'
import { CloseDialog, EditDialog, useCosts } from '../components/PositionDialogs'
import PortfolioQuant from '../components/PortfolioQuant'
import { ErrorBox, Section, Spinner, Stat, VerdictBadge } from '../components/ui'

const lkr = (v: number | null | undefined) => v == null ? '–' : `${v < 0 ? '−' : ''}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`

const today = () => new Date().toISOString().slice(0, 10)

export default function Positions() {
  const [rows, setRows] = useState<PositionRow[] | null>(null)
  const [wl, setWl] = useState<WatchItem[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [pf, setPf] = useState<Portfolio | null>(null)
  const [f, setF] = useState({ symbol: '', entry_date: today(), entry_price: '', quantity: '', stop: '', target: '' })
  const load = () => { api.positions().then(setRows).catch(e => setErr(String(e.message))); api.portfolio().then(setPf).catch(() => {}) }
  useEffect(() => { load(); api.watchlist().then(setWl).catch(() => {}) }, [])

  const add = async () => {
    setErr(null)
    try {
      await api.addPosition({ symbol: f.symbol, entry_date: f.entry_date, entry_price: Number(f.entry_price), quantity: Number(f.quantity),
        stop: f.stop ? Number(f.stop) : null, target: f.target ? Number(f.target) : null })
      setF({ ...f, entry_price: '', quantity: '', stop: '', target: '' }); load()
    } catch (e) { setErr(String((e as Error).message)) }
  }
  const [closing, setClosing] = useState<PositionRow | null>(null)
  const [editing, setEditing] = useState<PositionRow | null>(null)
  const costs = useCosts()
  const [notesFor, setNotesFor] = useState<number | null>(null)
  const open = rows?.filter(r => !r.closed) ?? []
  const closed = rows?.filter(r => r.closed) ?? []
  const realised = pf?.realised_net ?? closed.reduce((s, r) => s + ((r.exit_price ?? 0) - r.entry_price) * r.quantity, 0)
  const feeRate = (pf?.fee_pct_per_side ?? costs?.per_side_pct ?? 0) / 100
  const csvCols: CsvColumn<PositionRow>[] = [
    { header: 'Stock', get: r => ticker(r.symbol) }, { header: 'Status', get: r => (r.closed ? 'closed' : 'open') }, { header: 'Bought on', get: r => r.entry_date },
    { header: 'Buy price', get: r => r.entry_price }, { header: 'Shares', get: r => r.quantity }, { header: 'Sold on', get: r => r.exit_date }, { header: 'Sell price', get: r => r.exit_price },
    { header: 'Last price', get: r => (r.closed ? null : r.price) }, { header: 'Stop', get: r => r.stop }, { header: 'Target', get: r => r.target },
    { header: 'Cost incl. buying fee', get: r => pf?.rows[String(r.id)]?.cost ?? Math.round(r.quantity * r.entry_price * (1 + feeRate) * 100) / 100 },
    { header: 'Fees paid (buy + sale)', get: r => Math.round((r.quantity * r.entry_price + (r.closed ? r.quantity * (r.exit_price ?? 0) : 0)) * feeRate * 100) / 100 },
    { header: 'Profit or loss after fees', get: r => (r.closed ? pf?.rows[String(r.id)]?.realised_net : pf?.rows[String(r.id)]?.unrealised_net) },
    { header: 'Profit or loss %', get: r => (r.closed ? pf?.rows[String(r.id)]?.realised_net_pct : pf?.rows[String(r.id)]?.unrealised_net_pct) },
    { header: 'Notes', get: r => r.notes },
  ]

  return (
    <div className="mx-auto max-w-[1700px] space-y-2 px-3 py-3">
      <h1 className="text-[14px] font-semibold uppercase tracking-wider text-amber">PORT · Positions</h1>
      <p className="text-sm text-muted">Record what you actually bought. Each evening the analysis switches that stock to HOLD / TRIM / SELL, trails the stop and alerts you on Telegram.</p>
      {err && <ErrorBox msg={err} />}
      {pf && pf.open_positions > 0 && (
        <Section title={`Portfolio · after ${pf.fee_pct_per_side}% fees each way`}>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-7 [&>*]:min-w-0">
            <Stat label="Invested (incl. fees)" value={`LKR ${lkr(pf.invested)}`} sub={pf.invested_pct_of_capital != null ? `${pf.invested_pct_of_capital}% of capital` : undefined} />
            <Stat label="Market value" value={`LKR ${lkr(pf.market_value)}`} sub={pf.cash_estimate != null ? `cash ≈ LKR ${lkr(pf.cash_estimate)}` : undefined} />
            <Stat label="Unrealised, if sold now" value={`LKR ${lkr(pf.unrealised_net)}`} sub={pf.unrealised_net_pct != null ? `${pf.unrealised_net_pct >= 0 ? '+' : ''}${pf.unrealised_net_pct.toFixed(1)}% after selling fees` : undefined}
                  tone={pf.unrealised_net >= 0 ? 'bull' : 'bear'} />
            <Stat label="Realised (closed)" value={`LKR ${lkr(pf.realised_net)}`} sub="after fees both ways" tone={pf.realised_net >= 0 ? 'bull' : 'bear'} />
            <Stat label="Fees paid so far" value={`LKR ${lkr(pf.fees_paid)}`} sub={pf.fees_to_sell_open ? `+ LKR ${lkr(pf.fees_to_sell_open)} to sell what you hold` : 'on every buy and sale'} />
            <Stat label="At risk to the stops" value={`LKR ${lkr(pf.risk_to_stops)}`} tone={(pf.risk_to_stops_pct_of_capital ?? 0) > 6 ? 'bear' : 'warn'}
                  sub={pf.risk_to_stops_pct_of_capital != null ? `${pf.risk_to_stops_pct_of_capital}% of capital if every stop is hit` : 'from today’s value'} />
            <Stat label="Largest position" value={pf.largest ? ticker(pf.largest.symbol) : '–'} sub={pf.largest?.pct != null ? `${pf.largest.pct}% of the book` : undefined}
                  tone={(pf.largest?.pct ?? 0) > 40 ? 'warn' : undefined} />
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
            <span className="label">By sector</span>
            {pf.by_sector.map(x => <span key={x.sector}><span className="text-amber">{x.sector}</span> <span className="num">{x.pct}%</span></span>)}
            {pf.by_sector.some(x => x.sector === 'unassigned') && <span className="text-muted">(set sectors in SET &lt;GO&gt;)</span>}
          </div>
          {pf.positions_without_stop.length > 0 && <p className="mt-1 text-[12px] text-warn">No stop for {pf.positions_without_stop.map(ticker).join(', ')}: their risk isn't counted above.</p>}
        </Section>
      )}
      <Section title="Add a position">
        <form className="grid grid-cols-2 gap-2 sm:grid-cols-7" onSubmit={e => { e.preventDefault(); add() }}>
          <select className="input sm:col-span-2" value={f.symbol} onChange={e => setF({ ...f, symbol: e.target.value })} required aria-label="Stock">
            <option value="">Stock…</option>{wl.map(w => <option key={w.symbol} value={w.symbol}>{w.symbol}</option>)}
          </select>
          <input className="input" type="date" value={f.entry_date} onChange={e => setF({ ...f, entry_date: e.target.value })} aria-label="Entry date" />
          <input className="input num" placeholder="Entry price" inputMode="decimal" value={f.entry_price} onChange={e => setF({ ...f, entry_price: e.target.value })} required />
          <input className="input num" placeholder="Quantity" inputMode="numeric" value={f.quantity} onChange={e => setF({ ...f, quantity: e.target.value })} required />
          <input className="input num" placeholder="Stop (opt.)" inputMode="decimal" value={f.stop} onChange={e => setF({ ...f, stop: e.target.value })} />
          <button className="btn btn-primary">Add</button>
          {costs && Number(f.entry_price) > 0 && Number(f.quantity) > 0 && (() => {
            const gross = Number(f.entry_price) * Number(f.quantity), fee = gross * costs.per_side_pct / 100
            return <p className="col-span-full text-[11px] text-muted" aria-label="What the purchase costs">Shares {lkr(gross)} + buying fee {lkr(fee)} ({costs.per_side_pct}%: {costs.parts.map(x => `${x.label.toLowerCase()} ${x.pct}%`).join(', ')}) = <b className="text-fg">LKR {lkr(gross + fee)}</b></p>
          })()}
        </form>
      </Section>
      {open.some(p => (p.warnings?.length ?? 0) > 0) && (
        <Section title="Consider trimming: worth a look (information, not a sell signal)">
          <ul className="space-y-2 text-[12px]">
            {open.filter(p => (p.warnings?.length ?? 0) > 0).flatMap(p => p.warnings!.map(w => (
              <li key={`${p.id}-${w.id}`} className="flex gap-2">
                <Link className="w-14 shrink-0 font-semibold text-amber" to={`/stock/${p.symbol}`}>{ticker(p.symbol)}</Link>
                <span><b className="text-fg">{w.title}.</b> <span className="text-muted">{w.text}</span></span>
              </li>
            )))}
          </ul>
          <p className="mt-2 text-[11px] text-muted">Only bearish patterns that the 14-year study found to fall more often than comparable days are listed, plus a close below the 50-day average. The HOLD / TRIM / SELL columns below are unchanged.</p>
        </Section>
      )}
      {open.some(p => (p.corp_since_entry?.length ?? 0) > 0) && (
        <Section title="Check these positions: a split, bonus or rights issue happened after you bought">
          <ul className="space-y-2 text-[12px]">
            {open.filter(p => (p.corp_since_entry?.length ?? 0) > 0).map(p => (
              <li key={p.id} className="flex flex-wrap items-baseline gap-2">
                <Link className="w-14 shrink-0 font-semibold text-amber" to={`/stock/${p.symbol}`}>{ticker(p.symbol)}</Link>
                <span className="min-w-0 flex-1">{p.corp_since_entry!.map(h => h.text).join(' ')}</span>
                <button className="btn !py-0.5 text-xs" onClick={() => setEditing(p)}>Edit position</button>
                <button className="btn !py-0.5 text-xs" title="I have checked this: stop showing it" onClick={async () => { await api.dismissCorp(p.id); load() }}>Dismiss</button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-muted">The app only knows the corporate actions it has recorded. Your P/L below uses the quantity and price you entered, so correct them if your broker adjusted your holding.</p>
        </Section>
      )}
      <Section title={`Open (${open.length})`} right={rows && rows.length > 0 ? <ExportCsv name="positions" columns={csvCols} rows={rows} /> : undefined}>
        {!rows ? <Spinner /> : open.length === 0 ? <p className="text-sm text-muted">No open positions.</p> : (
          <div className="overflow-x-auto">
            <table className="bb-table num">
              <thead><tr><th>Stock</th><th>Qty</th><th>Entry</th><th>Price</th><th>P/L after fees</th><th>Stop</th><th>CLS action</th><th>LVL action</th><th>SMC action</th><th /></tr></thead>
              <tbody>
                {open.map(p => {
                  const fig = pf?.rows[String(p.id)]
                  const pl = fig?.unrealised_net ?? (p.price != null ? (p.price - p.entry_price) * p.quantity : null)
                  const plp = fig?.unrealised_net_pct ?? (p.price != null ? (p.price / p.entry_price - 1) * 100 : null)
                  return (
                    <Fragment key={p.id}>
                    <tr className="border-t border-line/60 align-top">
                      <td className="py-2"><Link className="font-semibold text-amber" to={`/stock/${p.symbol}`}>{ticker(p.symbol)}</Link><div className="text-xs text-muted">{p.entry_date}</div></td>
                      <td>{p.quantity.toLocaleString()}</td><td>{fmt(p.entry_price)}</td><td>{fmt(p.price)}</td>
                      <td className={pl != null && pl >= 0 ? 'text-bull' : 'text-bear'}>{pl != null ? `${pl.toLocaleString(undefined, { maximumFractionDigits: 0 })} (${plp!.toFixed(1)}%)` : '–'}</td>
                      <td>{fmt(p.review?.suggested_stop ?? p.stop)}</td>
                      <td className="max-w-xs whitespace-normal"><VerdictBadge v={p.review?.action ?? p.verdict} /><div className="text-[11px] text-muted">{p.review?.reasons.join('; ')}</div></td>
                      <td className="max-w-xs whitespace-normal"><VerdictBadge v={p.review_analyst?.action ?? p.verdict_analyst} /><div className="text-[11px] text-muted">{p.review_analyst?.reasons.join('; ')}{p.review_analyst?.suggested_stop ? ` · stop ${fmt(p.review_analyst.suggested_stop)}` : ''}</div></td>
                      <td className="max-w-xs whitespace-normal"><VerdictBadge v={p.review_smc?.action ?? p.verdict_smc} /><div className="text-[11px] text-muted">{p.review_smc?.reasons.join('; ')}{p.review_smc?.suggested_stop ? ` · stop ${fmt(p.review_smc.suggested_stop)}` : ''}</div></td>
                      <td className="whitespace-nowrap"><button className="btn !py-0.5 text-xs" onClick={() => setClosing(p)}>Sell</button>{' '}
                        <button className="btn !py-0.5 text-xs" onClick={() => setEditing(p)}>Edit</button>{' '}
                        <button className="btn !py-0.5 text-xs" aria-pressed={notesFor === p.id} onClick={() => setNotesFor(notesFor === p.id ? null : p.id)}>Notes</button></td>
                    </tr>
                    {notesFor === p.id && <tr><td colSpan={10} className="!whitespace-normal bg-panel-2 !p-0"><NotesPanel symbol={p.symbol} positionId={p.id} /></td></tr>}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      {closed.length > 0 && (
        <Section title={`Closed (${closed.length})`} right={<span className={`text-sm font-semibold num ${realised >= 0 ? 'text-bull' : 'text-bear'}`}>realised LKR {realised.toLocaleString(undefined, { maximumFractionDigits: 0 })} {pf ? '(after fees)' : '(before fees)'}</span>}>
          <div className="overflow-x-auto"><table className="bb-table num">
            <tbody>
              {closed.map(p => (
                <Fragment key={p.id}>
                <tr className="border-t border-line/60">
                  <td className="py-1">{ticker(p.symbol)}</td><td>{p.entry_date} → {p.exit_date}</td><td>{fmt(p.entry_price)} → {fmt(p.exit_price)}</td>
                  <td className={(p.exit_price ?? 0) >= p.entry_price ? 'text-bull' : 'text-bear'}>{(((p.exit_price ?? 0) / p.entry_price - 1) * 100).toFixed(1)}%</td>
                  <td className="whitespace-nowrap"><button className="mr-3 text-xs text-muted hover:text-amber" onClick={() => setEditing(p)}>edit</button>
                    <button className="mr-3 text-xs text-muted hover:text-amber" onClick={() => setNotesFor(notesFor === p.id ? null : p.id)}>notes</button>
                    <button className="text-xs text-muted hover:text-bear" onClick={async () => { await api.deletePosition(p.id); load() }}>delete</button></td>
                </tr>
                {notesFor === p.id && <tr><td colSpan={5} className="!whitespace-normal bg-panel-2 !p-0"><NotesPanel symbol={p.symbol} positionId={p.id} /></td></tr>}
                </Fragment>
              ))}
            </tbody>
          </table></div>
        </Section>
      )}
      {rows && <PortfolioQuant key={rows.map(r => `${r.id}:${r.closed ? 1 : 0}`).join(',')} />}
      {closing && <CloseDialog p={closing} onClose={() => setClosing(null)} onDone={() => { setClosing(null); load() }} />}
      {editing && <EditDialog p={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); load() }} />}
    </div>
  )
}
