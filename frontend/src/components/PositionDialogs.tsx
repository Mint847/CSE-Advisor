// PORT dialogs: close (all or part of a position, with the CSE fee breakdown and the profit after fees worked out before you confirm)
// and edit (correct anything you recorded; a split or bonus issue since the buy is pointed out with the new share count to enter).
import { useEffect, useState } from 'react'
import { api, fmt, ticker, type CostInfo, type PositionRow } from '../api'

const today = () => new Date().toISOString().slice(0, 10)
const money = (v: number) => `${v < 0 ? '−' : ''}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
const signed = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`

let costsPromise: Promise<CostInfo> | null = null
/** The fee rate and its parts, fetched once per page load. */
export function useCosts(): CostInfo | null {
  const [c, setC] = useState<CostInfo | null>(null)
  useEffect(() => { costsPromise ??= api.costs(); costsPromise.then(setC).catch(() => { costsPromise = null }) }, [])
  return c
}

function Modal({ title, sub, onClose, children, label }: { title: string; sub: string; onClose: () => void; children: React.ReactNode; label: string }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-3 sm:pt-12" role="dialog" aria-modal="true" aria-label={label} onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="w-full max-w-[520px] border border-amber/60 bg-panel">
        <div className="flex items-center gap-2 bg-panel-2 px-3 py-1.5">
          <b className="text-[12px] uppercase tracking-wide text-amber">{title}</b><span className="num text-[12px] text-muted">{sub}</span>
          <button type="button" className="ml-auto text-muted hover:text-fg" onClick={onClose} aria-label="Cancel">✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** What a sale brings in and what it earns after the CSE's fees, line by line. */
export function SalePreview({ qty, price, entry, costs }: { qty: number; price: number; entry: number; costs: CostInfo | null }) {
  if (!costs || !(qty > 0) || !(price > 0)) return null
  const gross = qty * price
  const fees = gross * costs.per_side_pct / 100
  const net = gross - fees
  const cost = qty * entry * (1 + costs.per_side_pct / 100)
  const pl = net - cost
  return (
    <table className="num w-full text-[12px]" aria-label="What the sale brings in">
      <tbody>
        <tr><td>Sale value ({qty.toLocaleString()} × {fmt(price)})</td><td className="text-right">{money(gross)}</td></tr>
        {costs.parts.map(p => <tr key={p.label} className="text-muted"><td className="pl-3">− {p.label} {p.pct}%</td><td className="text-right">{money(gross * p.pct / 100)}</td></tr>)}
        <tr className="border-t border-line/60"><td>Fees on the sale ({costs.per_side_pct}%)</td><td className="text-right">−{money(fees)}</td></tr>
        <tr><td className="font-semibold text-fg">You receive</td><td className="text-right font-semibold text-fg">{money(net)}</td></tr>
        <tr className="text-muted"><td>What these shares cost, with the buying fee ({fmt(entry)} + {costs.per_side_pct}%)</td><td className="text-right">{money(cost)}</td></tr>
        <tr className="border-t border-line/60"><td className="font-semibold">Profit or loss after fees</td>
          <td className={`text-right font-semibold ${pl >= 0 ? 'text-bull' : 'text-bear'}`}>{signed(pl)} ({pl >= 0 ? '+' : '−'}{Math.abs(pl / cost * 100).toFixed(1)}%)</td></tr>
      </tbody>
    </table>
  )
}

/** Sell all of a position, or part of it. */
export function CloseDialog({ p, onClose, onDone }: { p: PositionRow; onClose: () => void; onDone: () => void }) {
  const costs = useCosts()
  const [px, setPx] = useState(p.price != null ? String(p.price) : '')
  const [qty, setQty] = useState(String(p.quantity))
  const [date, setDate] = useState(today())
  const [review, setReview] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const q = Number(qty)
  const valid = Number.isInteger(q) && q >= 1 && q <= p.quantity && Number(px) > 0
  const submit = async () => {
    setErr(null); setBusy(true)
    try { await api.closePosition(p.id, date, Number(px), review.trim(), q < p.quantity ? q : undefined); onDone() } catch (e) { setErr(String((e as Error).message)); setBusy(false) }
  }
  return (
    <Modal title={`Sell ${ticker(p.symbol)}`} sub={`${p.quantity.toLocaleString()} @ ${fmt(p.entry_price)}`} onClose={onClose} label={`Close ${ticker(p.symbol)}`}>
      <form className="space-y-2.5 p-3" onSubmit={e => { e.preventDefault(); if (valid) submit() }}>
        <div>
          <span className="label">How many shares</span>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
            <input className="input num !w-28" type="number" min={1} max={p.quantity} step={1} required value={qty} onChange={e => setQty(e.target.value)} aria-label="Shares to sell" />
            {[25, 50, 75, 100].map(k => <button key={k} type="button" className="btn !px-2 !py-0.5 text-[11px]" onClick={() => setQty(String(Math.max(1, Math.floor(p.quantity * k / 100))))}>{k === 100 ? 'all' : `${k}%`}</button>)}
            <span className="text-[11px] text-muted">of {p.quantity.toLocaleString()}</span>
          </div>
          {Number.isInteger(q) && q >= 1 && q < p.quantity && <p className="mt-1 text-[11px] text-muted">A part sale: {(p.quantity - q).toLocaleString()} shares stay open; the {q.toLocaleString()} sold are kept as their own closed position.</p>}
          {Number.isInteger(q) && q > p.quantity && <p className="mt-1 text-[11px] text-bear">You hold {p.quantity.toLocaleString()} shares.</p>}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="block"><span className="label">Sell price</span><input className="input num mt-0.5 w-full" type="number" step="any" min="0" required autoFocus value={px} onChange={e => setPx(e.target.value)} /></label>
          <label className="block"><span className="label">Sell date</span><input className="input mt-0.5 w-full" type="date" required max={today()} value={date} onChange={e => setDate(e.target.value)} /></label>
        </div>
        {valid && <div className="border border-line bg-panel-2 p-2"><SalePreview qty={q} price={Number(px)} entry={p.entry_price} costs={costs} /></div>}
        <label className="block"><span className="label">What went right or wrong? (optional)</span>
          <textarea className="input mt-0.5 w-full" rows={3} value={review} onChange={e => setReview(e.target.value)} placeholder="Why you bought, why you sold, what you would do differently. Saved as a note tagged “review”." />
        </label>
        {err && <p className="text-[12px] text-bear" role="alert">{err}</p>}
        <div className="flex justify-end gap-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy || !valid}>{busy ? 'Saving…' : q < p.quantity ? 'Sell part' : 'Close position'}</button></div>
      </form>
    </Modal>
  )
}

/** Correct what was recorded: dates, prices, quantity, stop, target, notes (and the sale, for a closed position). */
export function EditDialog({ p, onClose, onDone }: { p: PositionRow; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ entry_date: p.entry_date, entry_price: String(p.entry_price), quantity: String(p.quantity), stop: p.stop == null ? '' : String(p.stop),
    target: p.target == null ? '' : String(p.target), notes: p.notes ?? '', exit_date: p.exit_date ?? '', exit_price: p.exit_price == null ? '' : String(p.exit_price) })
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const hints = p.corp_since_entry ?? []
  const suggest = [...hints].reverse().find(h => h.suggest)?.suggest
  const submit = async () => {
    setErr(null)
    const ch: Record<string, unknown> = {}
    if (f.entry_date !== p.entry_date) ch.entry_date = f.entry_date
    if (Number(f.entry_price) !== p.entry_price) ch.entry_price = Number(f.entry_price)
    if (Number(f.quantity) !== p.quantity) ch.quantity = Number(f.quantity)
    if ((f.stop === '' ? null : Number(f.stop)) !== p.stop) ch.stop = f.stop === '' ? null : Number(f.stop)
    if ((f.target === '' ? null : Number(f.target)) !== p.target) ch.target = f.target === '' ? null : Number(f.target)
    if ((f.notes.trim() || null) !== (p.notes?.trim() || null)) ch.notes = f.notes.trim() || null
    if (p.closed) {
      if (f.exit_date !== (p.exit_date ?? '')) ch.exit_date = f.exit_date
      if (Number(f.exit_price) !== p.exit_price) ch.exit_price = Number(f.exit_price)
    }
    if (!Object.keys(ch).length) { onClose(); return }
    setBusy(true)
    try { await api.editPosition(p.id, ch); onDone() } catch (e) { setErr(String((e as Error).message)); setBusy(false) }
  }
  const field = (k: keyof typeof f, label: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <label className="block"><span className="label">{label}</span><input className="input num mt-0.5 w-full" value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} {...props} /></label>
  )
  return (
    <Modal title={`Edit ${ticker(p.symbol)}`} sub={p.closed ? 'closed position' : 'open position'} onClose={onClose} label={`Edit ${ticker(p.symbol)}`}>
      <form className="space-y-2.5 p-3" onSubmit={e => { e.preventDefault(); submit() }}>
        {hints.length > 0 && (
          <div className="border border-amber/50 bg-panel-2 p-2 text-[12px]" role="note" aria-label="Corporate actions since you bought">
            <b className="text-amber">Check this position.</b>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">{hints.map(h => <li key={h.id}>{h.text}</li>)}</ul>
            {suggest && <button type="button" className="btn mt-1.5 !py-0.5 text-[11px]" onClick={() => setF({ ...f, quantity: String(suggest.quantity), entry_price: String(suggest.entry_price) })}>
              Use {suggest.quantity.toLocaleString()} shares at {suggest.entry_price}</button>}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2">
          {field('entry_date', 'Bought on', { type: 'date', required: true, max: today() })}{field('entry_price', 'Buy price', { type: 'number', step: 'any', min: 0, required: true })}
          {field('quantity', 'Shares', { type: 'number', step: 1, min: 1, required: true })}{field('stop', 'Stop (empty = none)', { type: 'number', step: 'any', min: 0 })}
          {field('target', 'Target (empty = none)', { type: 'number', step: 'any', min: 0 })}
          {p.closed && field('exit_date', 'Sold on', { type: 'date', required: true, max: today() })}{p.closed && field('exit_price', 'Sell price', { type: 'number', step: 'any', min: 0, required: true })}
        </div>
        <label className="block"><span className="label">Notes</span><textarea className="input mt-0.5 w-full" rows={2} maxLength={2000} value={f.notes} onChange={e => setF({ ...f, notes: e.target.value })} /></label>
        <p className="text-[11px] text-muted">Changes recalculate your profit and loss and the evening review of this stock. They never change what the engines say about it.</p>
        {err && <p className="text-[12px] text-bear" role="alert">{err}</p>}
        <div className="flex justify-end gap-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</button></div>
      </form>
    </Modal>
  )
}
