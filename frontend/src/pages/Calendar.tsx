import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ticker, type Calendar as CalData } from '../api'
import { ErrorBox, Spinner } from '../components/ui'

interface MyEvent { id: string; date: string; title: string; symbol?: string }
const PREF = 'calendar_events'
/** Weekdays from today up to a date (the CSE's future holidays are not known, so it can be a session or two too high). */
const weekdaysUntil = (iso: string): number | null => {
  const t = new Date(); t.setHours(0, 0, 0, 0)
  const d = new Date(`${iso}T00:00:00`)
  if (d < t) return null
  let n = 0
  for (const c = new Date(t); c < d; c.setDate(c.getDate() + 1)) if (c.getDay() !== 0 && c.getDay() !== 6) n++
  return n
}
const countdown = (n: number) => (n === 0 ? 'today' : n === 1 ? '1 session' : `${n} sessions`)
const KIND: Record<string, { label: string; cls: string }> = {
  ex_dividend: { label: 'XD', cls: 'bg-amber text-black' },
  dividend_payment: { label: 'PAY', cls: 'bg-bull text-black' },
  agm: { label: 'AGM', cls: 'bg-blue text-black' },
  results_due: { label: 'RES', cls: 'bg-cyan text-black' },
  corporate: { label: 'CORP', cls: 'bg-orange-400 text-black' },
  mine: { label: 'MINE', cls: 'bg-panel-3 text-yellow' },
}
interface Ev { date: string; kind: string; title: string; detail: string; symbol?: string; own?: MyEvent; sessions?: number | null }

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const longDate = (s: string) => new Date(s + 'T00:00:00').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })

export default function Calendar() {
  const [scope, setScope] = useState('mine')
  const [data, setData] = useState<CalData | null>(null)
  const [mine, setMine] = useState<MyEvent[]>([])
  const [syms, setSyms] = useState<string[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [cursor, setCursor] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1) })
  const [sel, setSel] = useState<string | null>(null)
  const [f, setF] = useState({ date: iso(new Date()), title: '', symbol: '' })
  useEffect(() => { api.calendar(scope, 120).then(d => { setData(d); setErr(null) }).catch(e => setErr(String(e.message ?? e))) }, [scope])
  useEffect(() => { api.watchlist().then(w => setSyms(w.map(x => x.symbol).sort())).catch(() => {}) }, [])
  useEffect(() => { api.getPref<MyEvent[]>(PREF).then(v => setMine(Array.isArray(v) ? v : [])).catch(() => {}) }, [])

  const saveMine = (next: MyEvent[]) => { setMine(next); api.putPref(PREF, next).catch(e => setErr(String(e.message ?? e))) }
  const events: Ev[] = useMemo(() => [
    ...(data?.events ?? []).map(e => ({ date: e.date, kind: e.kind, title: e.title, detail: e.detail, symbol: e.symbol, sessions: e.sessions })),
    ...mine.map(m => ({ date: m.date, kind: 'mine', title: m.title, detail: '', symbol: m.symbol, own: m })),
  ].sort((a, b) => a.date.localeCompare(b.date)), [data, mine])
  const byDay = useMemo(() => { const m = new Map<string, Ev[]>(); events.forEach(e => m.set(e.date, [...(m.get(e.date) ?? []), e])); return m }, [events])

  const today = iso(new Date())
  const first = cursor, offset = (first.getDay() + 6) % 7                      // Monday first
  const cells = Array.from({ length: 42 }, (_, i) => new Date(first.getFullYear(), first.getMonth(), 1 - offset + i))
  const shown = sel ? (byDay.get(sel) ?? []) : events.filter(e => e.date >= today).slice(0, 40)
  const names = data?.names ?? {}

  return (
    <div className="mx-auto max-w-[1300px] px-3 py-3">
      <section className="card">
        <div className="panel-title flex-wrap">
          <h1 className="flex-1">CAL · Calendar <span className="font-normal normal-case text-muted">results due, ex-dividend dates, payments, AGMs, corporate actions, and your own dates</span></h1>
          <span className="flex gap-1" role="radiogroup" aria-label="Whose stocks">
            {([['mine', 'My stocks'], ['all', 'All tracked']] as [string, string][]).map(([k, l]) => <button key={k} role="radio" aria-checked={scope === k} aria-pressed={scope === k} className="btn !py-0.5" onClick={() => setScope(k)}>{l}</button>)}
          </span>
        </div>
        {err && <div className="p-2"><ErrorBox msg={err} /></div>}
        {!data ? (err ? null : <Spinner />) : (
          <div className="grid gap-3 p-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] [&>*]:min-w-0">
            <div>
              <div className="mb-1.5 flex items-center gap-2">
                <button className="btn !py-0" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))} aria-label="Previous month">◀</button>
                <b className="w-40 text-center text-[13px] uppercase tracking-wide text-amber">{cursor.toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })}</b>
                <button className="btn !py-0" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))} aria-label="Next month">▶</button>
                <button className="btn !py-0 text-[11px]" onClick={() => { const d = new Date(); setCursor(new Date(d.getFullYear(), d.getMonth(), 1)); setSel(null) }}>Today</button>
              </div>
              <div className="grid grid-cols-7 gap-px border border-line bg-line text-[11px]" role="grid" aria-label="Month">
                {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(d => <div key={d} className="bg-panel-2 px-1 py-0.5 text-center text-[10px] uppercase text-muted">{d}</div>)}
                {cells.map(d => {
                  const k = iso(d), list = byDay.get(k) ?? [], out = d.getMonth() !== first.getMonth()
                  return (
                    <button key={k} role="gridcell" aria-selected={sel === k} onClick={() => setSel(sel === k ? null : k)} aria-label={`${longDate(k)}: ${list.length} events`}
                            className={`min-h-[52px] bg-panel p-0.5 text-left align-top hover:bg-panel-3 sm:min-h-[70px] ${out ? 'opacity-40' : ''} ${sel === k ? 'outline outline-1 -outline-offset-1 outline-amber' : ''} ${d.getDay() % 6 === 0 ? 'bg-black' : ''}`}>
                      <span className={`block text-[10px] num ${k === today ? 'inline-block bg-amber px-1 font-bold text-black' : 'text-muted'}`}>{d.getDate()}</span>
                      <span className="mt-0.5 hidden flex-col gap-px sm:flex">
                        {list.slice(0, 3).map((e, i) => <span key={i} className={`truncate px-0.5 text-[9px] font-semibold ${KIND[e.kind]?.cls ?? 'bg-panel-3'}`}>{e.symbol ? ticker(e.symbol) : ''} {KIND[e.kind]?.label}</span>)}
                        {list.length > 3 && <span className="text-[9px] text-muted">+{list.length - 3} more</span>}
                      </span>
                      {list.length > 0 && <span className="mt-0.5 block text-[10px] font-bold text-amber sm:hidden">{list.length}</span>}
                    </button>
                  )
                })}
              </div>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted">
                {Object.entries(KIND).map(([k, v]) => <span key={k}><span className={`chip !leading-[14px] ${v.cls}`}>{v.label}</span> {({ ex_dividend: 'ex-dividend', dividend_payment: 'dividend paid', agm: 'AGM', results_due: 'results (estimated)', corporate: 'corporate action', mine: 'your own' } as Record<string, string>)[k]}</span>)}
              </div>
            </div>

            <div className="space-y-3">
              <div>
                <div className="label mb-1">{sel ? longDate(sel) : 'Coming up'} {sel && <button className="ml-2 text-[11px] normal-case text-muted hover:text-fg" onClick={() => setSel(null)}>show all upcoming</button>}</div>
                {shown.length === 0 ? <p className="text-[12px] text-muted">{sel ? 'Nothing on this day.' : 'Nothing dated in the next 120 days for these stocks.'}</p> : (
                  <ul className="border border-line">
                    {shown.map((e, i) => (
                      <li key={i} className="flex gap-2 border-t border-line/60 px-2 py-1.5 text-[12px] first:border-t-0">
                        <span className="w-[64px] shrink-0 text-[10px] text-muted num">{e.date.slice(5)}<br />{new Date(e.date + 'T00:00:00').toLocaleDateString('en-GB', { weekday: 'short' })}
                          {(e.sessions ?? weekdaysUntil(e.date)) != null && <span className="block normal-case" title="Weekdays until then: the CSE’s future holidays are not known, so this can be a session or two too high">≈ {countdown(e.sessions ?? weekdaysUntil(e.date)!)}</span>}</span>
                        <span className="min-w-0 flex-1">
                          <span className={`chip mr-1 !leading-[14px] ${KIND[e.kind]?.cls ?? 'bg-panel-3'}`}>{KIND[e.kind]?.label ?? e.kind}</span>
                          {e.symbol && <Link to={`/stock/${e.symbol}?tab=cn`} title={names[e.symbol]} className="mr-1 font-semibold text-amber">{ticker(e.symbol)}</Link>}
                          <span className="text-fg">{e.title}</span>
                          {e.detail && <span className="block text-[11px] text-muted">{e.detail}</span>}
                        </span>
                        {e.own && <button className="text-[11px] text-muted hover:text-bear" onClick={() => saveMine(mine.filter(m => m.id !== e.own!.id))} aria-label={`Delete ${e.title}`}>delete</button>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <form className="space-y-1.5 border border-line p-2.5" onSubmit={ev => {
                ev.preventDefault()
                if (!f.title.trim() || !f.date) return
                saveMine([...mine, { id: String(Date.now()), date: f.date, title: f.title.trim().slice(0, 120), symbol: f.symbol || undefined }]); setF({ ...f, title: '' })
              }}>
                <div className="label">Add your own date</div>
                <div className="flex flex-wrap gap-2">
                  <input className="input" type="date" required value={f.date} onChange={e => setF({ ...f, date: e.target.value })} aria-label="Date" />
                  <input className="input min-w-[8rem] flex-1" required maxLength={120} placeholder="e.g. Decide on adding to COMB" value={f.title} onChange={e => setF({ ...f, title: e.target.value })} aria-label="What" />
                  <select className="input" value={f.symbol} onChange={e => setF({ ...f, symbol: e.target.value })} aria-label="Stock (optional)">
                    <option value="">No stock</option>{syms.map(s => <option key={s} value={s}>{ticker(s)}</option>)}
                  </select>
                  <button className="btn btn-primary">Add</button>
                </div>
              </form>
              <p className="text-[11px] text-muted">Dates come from the CSE’s announcements for the stocks you follow. “Results” dates are estimates from when each company reported in earlier years, not announced dates. The CSE’s market holidays aren’t included.</p>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
