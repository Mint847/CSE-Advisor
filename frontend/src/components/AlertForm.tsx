import { useEffect, useState } from 'react'
import { api, fmt, ticker, type AlertKind, type AlertRule } from '../api'
import { when } from './Inbox'

type Params = Record<string, number | string>

/** What each kind asks for: the fields, in order. */
const FIELD_UI: Record<string, { name: string; label: string; kind: 'num' | 'pick'; options?: [string, string][]; def: number | string; step?: string }[]> = {
  price_above: [{ name: 'level', label: 'Price (LKR)', kind: 'num', def: '' }],
  price_below: [{ name: 'level', label: 'Price (LKR)', kind: 'num', def: '' }],
  day_up: [{ name: 'pct', label: '% up on the day', kind: 'num', def: 5, step: '0.5' }],
  day_down: [{ name: 'pct', label: '% down on the day', kind: 'num', def: 5, step: '0.5' }],
  near_high: [{ name: 'pct', label: 'within % of the high', kind: 'num', def: 2, step: '0.5' }],
  volume_spike: [{ name: 'mult', label: '× average volume', kind: 'num', def: 3, step: '0.5' }],
  pattern: [{ name: 'direction', label: 'Direction', kind: 'pick', def: 'any', options: [['any', 'Any'], ['bullish', 'Bullish'], ['bearish', 'Bearish']] }],
  announcement: [{ name: 'min_materiality', label: 'Importance', kind: 'pick', def: 3, options: [['0', 'Any'], ['2', '2+'], ['3', '3+ (material)'], ['4', '4+'], ['5', '5 only']] }],
  verdict_change: [{ name: 'engine', label: 'Engine', kind: 'pick', def: 'any', options: [['any', 'Any engine'], ['original', 'CLS'], ['analyst', 'LVL'], ['smc', 'SMC']] }],
  rsi_cross: [{ name: 'dir', label: 'Crosses', kind: 'pick', def: 'below', options: [['below', 'below'], ['above', 'above']] }, { name: 'level', label: 'RSI level', kind: 'num', def: 30 }],
}

/** Price rules fire once and switch off (otherwise "above 100" would repeat every day the price stays there). */
const ONCE_BY_DEFAULT = new Set(['price_above', 'price_below'])

const GROUPS: [string, string[]][] = [
  ['Price (checked live, market hours)', ['price_above', 'price_below', 'day_up', 'day_down', 'near_high', 'volume_spike']],
  ['Evening (checked after the nightly analysis)', ['pattern', 'announcement', 'verdict_change', 'rsi_cross']],
]

export function AlertForm({ symbols, symbol, price, kinds, onCreated }: {
  symbols?: string[]; symbol?: string; price?: number | null; kinds: Record<string, AlertKind>; onCreated: (r: AlertRule) => void
}) {
  const [sym, setSym] = useState(symbol ?? symbols?.[0] ?? '')
  const [kind, setKind] = useState('price_above')
  const [vals, setVals] = useState<Params>({})
  const [once, setOnce] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (symbol) setSym(symbol); else if (!sym && symbols?.length) setSym(symbols[0]) }, [symbol, symbols, sym])
  const ui = FIELD_UI[kind] ?? []
  const pick = (k: string) => { setKind(k); setVals({}); setOnce(ONCE_BY_DEFAULT.has(k)); setErr(null) }
  const val = (n: string, def: number | string) => vals[n] ?? (n === 'level' && ['price_above', 'price_below'].includes(kind) && price ? Number(price.toFixed(2)) : def)

  const submit = async () => {
    setErr(null); setBusy(true)
    try {
      const params: Params = {}
      for (const f of ui) { const v = val(f.name, f.def); params[f.name] = f.kind === 'num' ? Number(v) : (f.name === 'min_materiality' ? Number(v) : String(v)) }
      onCreated(await api.addAlert({ symbol: sym, kind, params, once }))
      setVals({})
    } catch (e) { setErr(String((e as Error).message)) } finally { setBusy(false) }
  }

  return (
    <form className="space-y-2" onSubmit={e => { e.preventDefault(); submit() }}>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {!symbol && (
          <label className="block"><span className="label">Stock</span>
            <select className="input mt-0.5 w-full" value={sym} onChange={e => setSym(e.target.value)} required>
              <option value="">Choose…</option>{symbols?.map(s => <option key={s} value={s}>{ticker(s)}</option>)}
            </select>
          </label>
        )}
        <label className={`block ${symbol ? 'sm:col-span-2' : ''}`}><span className="label">Alert me when</span>
          <select className="input mt-0.5 w-full" value={kind} onChange={e => pick(e.target.value)}>
            {GROUPS.map(([g, ks]) => <optgroup key={g} label={g}>{ks.filter(k => kinds[k]).map(k => <option key={k} value={k}>{kinds[k].label}</option>)}</optgroup>)}
          </select>
        </label>
        {ui.map(f => (
          <label key={f.name} className="block"><span className="label">{f.label}</span>
            {f.kind === 'num'
              ? <input className="input num mt-0.5 w-full" type="number" inputMode="decimal" step={f.step ?? 'any'} min="0" required value={String(val(f.name, f.def))} onChange={e => setVals({ ...vals, [f.name]: e.target.value })} />
              : <select className="input mt-0.5 w-full" value={String(val(f.name, f.def))} onChange={e => setVals({ ...vals, [f.name]: e.target.value })}>{f.options!.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5 text-[12px]"><input type="checkbox" checked={once} onChange={e => setOnce(e.target.checked)} /> only once, then switch off</label>
        <button className="btn btn-primary ml-auto" disabled={busy || !sym}>{busy ? 'Adding…' : 'Add alert'}</button>
      </div>
      {price != null && ['price_above', 'price_below'].includes(kind) && <p className="text-[11px] text-muted">Last price {fmt(price)}. The alert fires when a live quote reaches the level, during market hours.</p>}
      {err && <p className="text-[12px] text-bear" role="alert">{err}</p>}
    </form>
  )
}

export function RuleRow({ r, onChange, showSymbol = true }: { r: AlertRule; onChange: () => void; showSymbol?: boolean }) {
  return (
    <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line/60 px-2.5 py-1.5 text-[12px] ${r.active ? '' : 'text-muted'}`}>
      {showSymbol && <b className="w-16 shrink-0 text-amber">{ticker(r.symbol)}</b>}
      <span className="min-w-0 flex-1">{r.text}{r.once && <span className="chip ml-1.5 bg-panel-3 text-muted">once</span>}
        <span className="block text-[10px] text-muted">{r.fired_count ? `fired ${r.fired_count}× · last ${r.last_fired ? when(r.last_fired) : ''}` : 'not fired yet'}{!r.active && r.fired_count ? ' · switched off' : ''}</span>
      </span>
      <button className="btn !py-0 text-[11px]" onClick={async () => { await api.editAlert(r.id, { active: !r.active }); onChange() }}>{r.active ? 'Switch off' : 'Switch on'}</button>
      <button className="text-[11px] text-muted hover:text-bear" onClick={async () => { await api.deleteAlert(r.id); onChange() }} aria-label={`Delete alert: ${r.text}`}>delete</button>
    </div>
  )
}

/** "+ Alert" on the stock page: one-click ±3% / ±5% price alerts, the full form, and this stock's existing rules. */
export function AlertDialog({ symbol, price, onClose, onChanged }: { symbol: string; price: number | null; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<{ rules: AlertRule[]; kinds: Record<string, AlertKind> } | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const load = () => api.alerts().then(d => { setData(d); onChanged() }).catch(e => setMsg(String(e.message)))
  useEffect(() => { load() }, [])    // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const mine = data?.rules.filter(r => r.symbol === symbol) ?? []
  const preset = async (pct: number) => {
    if (!price) return
    setMsg(null)
    try {
      const level = Math.round(price * (1 + pct / 100) * 100) / 100
      await api.addAlert({ symbol, kind: pct > 0 ? 'price_above' : 'price_below', params: { level }, once: true })
      await load()
    } catch (e) { setMsg(String((e as Error).message)) }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-3 sm:pt-16" role="dialog" aria-modal="true" aria-label={`Alerts for ${ticker(symbol)}`} onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="w-full max-w-[520px] border border-amber/60 bg-panel">
        <div className="flex items-center gap-2 bg-panel-2 px-3 py-1.5">
          <b className="text-[12px] uppercase tracking-wide text-amber">Alerts · {ticker(symbol)}</b>
          <span className="num text-[12px] text-muted">{price != null ? fmt(price) : ''}</span>
          <button className="ml-auto text-muted hover:text-fg" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="space-y-3 p-3">
          {price != null && (
            <div>
              <div className="label mb-1">Quick price alerts (from {fmt(price)})</div>
              <div className="flex flex-wrap gap-1.5">
                {[-5, -3, 3, 5].map(p => <button key={p} className="btn num" onClick={() => preset(p)}>{p > 0 ? '+' : '−'}{Math.abs(p)}% · {fmt(price * (1 + p / 100))}</button>)}
              </div>
            </div>
          )}
          {data ? <AlertForm symbol={symbol} price={price} kinds={data.kinds} onCreated={() => load()} /> : <p className="text-[12px] text-muted">Loading…</p>}
          {msg && <p className="text-[12px] text-bear" role="alert">{msg}</p>}
          <div>
            <div className="label mb-0.5">Your alerts on {ticker(symbol)} ({mine.length})</div>
            {mine.length === 0 ? <p className="text-[12px] text-muted">None yet.</p> : mine.map(r => <RuleRow key={r.id} r={r} showSymbol={false} onChange={load} />)}
          </div>
          <p className="text-[11px] text-muted">Alerts go to the inbox (the bell) and, for the Telegram owner, to Telegram. Price alerts need the app running while the market is open.</p>
        </div>
      </div>
    </div>
  )
}
