import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { WATCH_CHANGED, api, fmt, ticker, type MarketData, type WatchItem } from '../api'
import { useLive } from '../useLive'

const KEY = 'cse:tape'
export const tapeOn = () => { try { return localStorage.getItem(KEY) !== '0' } catch { return true } }
export const setTape = (on: boolean) => { try { localStorage.setItem(KEY, on ? '1' : '0') } catch { /* private mode */ } window.dispatchEvent(new Event('cse:tape')) }

/** A ticker tape under the header: the ASPI, then every stock on your lists with its live change. Desktop only; it
 *  scrolls slowly, stops when hovered, and doesn't move at all for people who ask their system for reduced motion. */
export default function Tape() {
  const [on, setOn] = useState(tapeOn())
  const [wl, setWl] = useState<WatchItem[]>([])
  const [mktOpen, setMktOpen] = useState(false)
  const [aspi, setAspi] = useState<{ v: number; pct: number | null } | null>(null)
  useEffect(() => { const f = () => setOn(tapeOn()); window.addEventListener('cse:tape', f); return () => window.removeEventListener('cse:tape', f) }, [])
  useEffect(() => {
    if (!on) return
    const load = () => api.watchlist().then(setWl).catch(() => {})
    load()
    window.addEventListener(WATCH_CHANGED, load)
    const t = window.setInterval(load, 300000)
    return () => { window.removeEventListener(WATCH_CHANGED, load); window.clearInterval(t) }
  }, [on])
  useEffect(() => {
    if (!on) return
    const load = () => api.market().then((m: MarketData) => {
      const s = m.series.filter(x => x.aspi != null)
      const a = s.at(-1)?.aspi, b = s.at(-2)?.aspi
      if (a != null) setAspi({ v: a, pct: b ? (a / b - 1) * 100 : null })
    }).catch(() => {})
    load()
    const t = window.setInterval(load, 300000)
    return () => window.clearInterval(t)
  }, [on])
  useEffect(() => {                                   // the live stream only while the market is open: it is a long-lived connection
    if (!on) return
    const load = () => api.status().then(s => setMktOpen(s.market_open)).catch(() => {})
    load()
    const t = window.setInterval(load, 120000)
    return () => window.clearInterval(t)
  }, [on])
  const syms = useMemo(() => wl.map(w => w.symbol), [wl])
  const { quotes } = useLive(on && mktOpen ? syms : [])
  if (!on || wl.length === 0) return null

  const item = (key: string, label: string, price: string, pct: number | null, to?: string) => (
    <span key={key} className="inline-flex items-baseline gap-1.5 px-3 num">
      {to ? <Link to={to} className="font-semibold text-amber hover:underline">{label}</Link> : <b className="text-amber">{label}</b>}
      <span className="text-fg">{price}</span>
      {pct != null && <span className={pct >= 0 ? 'text-bull' : 'text-bear'}>{pct >= 0 ? '▲' : '▼'} {Math.abs(pct).toFixed(2)}%</span>}
    </span>
  )
  const items = [
    ...(aspi ? [item('aspi', 'ASPI', aspi.v.toLocaleString(undefined, { maximumFractionDigits: 2 }), aspi.pct, '/market')] : []),
    ...wl.map(w => {
      const q = quotes[w.symbol]
      return item(w.symbol, ticker(w.symbol), fmt(q?.close ?? w.price), q?.change_pct ?? w.change_pct, `/stock/${w.symbol}`)
    }),
  ]
  return (
    <div className="tape hidden border-t border-line bg-black text-[11px] md:block" role="region" aria-label="Ticker tape" tabIndex={0}>
      <div className="mx-auto max-w-[1700px] overflow-hidden whitespace-nowrap py-0.5">
        <div className="tape-track inline-flex">
          <div className="inline-flex">{items}</div>
          <div className="inline-flex" aria-hidden="true">{items}</div>
        </div>
      </div>
    </div>
  )
}
