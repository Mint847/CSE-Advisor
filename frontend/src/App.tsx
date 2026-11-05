import { useEffect, useRef, useState } from 'react'
import { NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api } from './api'
import CommandLine, { FUNCTIONS } from './components/CommandLine'
import Journal from './pages/Journal'
import Market from './pages/Market'
import Positions from './pages/Positions'
import Screener from './pages/Screener'
import Settings from './pages/Settings'
import StockDetail from './pages/StockDetail'
import Watchlist from './pages/Watchlist'

const FKEYS: { key: string; label: string; to?: string; help?: boolean }[] = [
  { key: 'F1', label: 'HELP', help: true },
  { key: 'F2', label: 'WL', to: '/' },
  { key: 'F3', label: 'PORT', to: '/positions' },
  { key: 'F4', label: 'MKT', to: '/market' },
  { key: 'F5', label: 'SCR', to: '/screener' },
  { key: 'F6', label: 'JRN', to: '/journal' },
  { key: 'F8', label: 'SET', to: '/settings' },
]

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { const t = window.setInterval(() => setNow(new Date()), 15000); return () => window.clearInterval(t) }, [])
  return now.toLocaleTimeString('en-GB', { timeZone: 'Asia/Colombo', hour: '2-digit', minute: '2-digit' })
}

function HelpPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/70 p-4 pt-16" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Commands and keys" className="card w-full max-w-2xl" onClick={e => e.stopPropagation()}>
        <div className="panel-title"><span className="flex-1">HELP: COMMANDS & KEYS</span><button className="btn !py-0" onClick={onClose}>ESC ✕</button></div>
        <div className="grid gap-4 p-3 text-[12px] sm:grid-cols-2">
          <div>
            <div className="label mb-1">Stock functions</div>
            <p className="mb-2 text-muted">Type a ticker, then a function, then Enter (= &lt;GO&gt;). Example: <b className="text-yellow">JKH DES &lt;GO&gt;</b>. On a stock page, a function on its own works too.</p>
            <table className="bb-table [&_td]:whitespace-normal"><tbody>
              {FUNCTIONS.filter(f => !f.global).map(f => <tr key={f.code}><td className="text-amber">{f.code}</td><td className="whitespace-normal">{f.text}</td></tr>)}
            </tbody></table>
          </div>
          <div>
            <div className="label mb-1">Global functions</div>
            <table className="bb-table mb-3 [&_td]:whitespace-normal"><tbody>
              {FUNCTIONS.filter(f => f.global).map(f => <tr key={f.code}><td className="text-amber">{f.code}</td><td className="whitespace-normal">{f.text}</td></tr>)}
            </tbody></table>
            <div className="label mb-1">Keys</div>
            <table className="bb-table [&_td]:whitespace-normal"><tbody>
              <tr><td className="text-amber">/</td><td>focus the command line</td></tr>
              {FKEYS.map(k => <tr key={k.key}><td className="text-amber">{k.key}</td><td>{k.label}</td></tr>)}
              <tr><td className="text-amber">P</td><td>stock page: patterns & layers</td></tr>
              <tr><td className="text-amber">↑ ↓ ENTER</td><td>watchlist: move and open</td></tr>
              <tr><td className="text-amber">ESC</td><td>close a panel / clear the command line</td></tr>
            </tbody></table>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function App() {
  const nav = useNavigate()
  const cmdRef = useRef<HTMLInputElement>(null)
  const [help, setHelp] = useState(false)
  const [status, setStatus] = useState<{ open: boolean; eod: string | null; telegram: boolean } | null>(null)
  const clock = useClock()

  useEffect(() => {
    const load = () => api.status().then(s => {
      const eod = s.jobs.find(j => j.job === 'eod' && j.ok)
      setStatus({ open: s.market_open, eod: eod ? eod.date : null, telegram: s.telegram })
    }).catch(() => {})
    load()
    const t = window.setInterval(load, 60000)
    return () => window.clearInterval(t)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const typing = e.target instanceof HTMLElement && (e.target.closest('input, textarea, select, [contenteditable="true"]') != null)
      const fk = FKEYS.find(k => k.key === e.key)
      if (fk) {
        e.preventDefault()
        if (fk.help) setHelp(h => !h)
        else if (fk.to) nav(fk.to)
        return
      }
      if (e.key === 'Escape' && help) { setHelp(false); return }
      if (!typing && e.key === '/') { e.preventDefault(); cmdRef.current?.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nav, help])

  const link = ({ isActive }: { isActive: boolean }) =>
    `flex shrink-0 items-center gap-1 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${isActive ? 'bg-amber text-black' : 'text-fg hover:bg-panel-3'}`

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <header className="sticky top-0 z-30 border-b border-line bg-black">
        <div className="mx-auto flex max-w-[1700px] flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-1.5">
          <NavLink to="/" className="flex shrink-0 items-center gap-2" aria-label="CSE Advisor home">
            <img src="/favicon.svg" alt="" className="h-5 w-5" />
            <span className="text-[13px] font-semibold tracking-wider text-amber">CSE ADVISOR</span>
          </NavLink>
          <div className="order-last w-full sm:order-none sm:w-auto sm:flex-1"><CommandLine onHelp={() => setHelp(true)} inputRef={cmdRef} /></div>
          <div className="ml-auto flex shrink-0 items-center gap-3 text-[11px] num sm:ml-0">
            <span className={status?.open ? 'text-bull' : 'text-muted'}>{status?.open ? '● LIVE' : '○ CLOSED'}</span>
            <span className="text-fg">{clock} <span className="text-muted">COLOMBO</span></span>
          </div>
        </div>
        <nav className="border-t border-line bg-panel" aria-label="Functions">
          <div className="scroll-thin mx-auto flex max-w-[1700px] items-center gap-1 overflow-x-auto px-3 py-1">
            {FKEYS.map(k => k.help ? (
              <button key={k.key} className="flex shrink-0 items-center gap-1 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-fg hover:bg-panel-3" onClick={() => setHelp(true)}>
                <span className="bg-menu px-1 text-white">{k.key}</span>{k.label}
              </button>
            ) : (
              <NavLink key={k.key} to={k.to!} end={k.to === '/'} className={link}>
                <span className="bg-menu px-1 text-white">{k.key}</span>{({ WL: 'WATCHLIST', PORT: 'POSITIONS', MKT: 'MARKET', SCR: 'CANDIDATES', JRN: 'JOURNAL' } as Record<string, string>)[k.label] ?? 'SETTINGS'}
              </NavLink>
            ))}
            <span className="ml-auto hidden shrink-0 pl-3 text-[11px] text-muted md:inline">type <b className="text-yellow">/</b> for the command line · <b className="text-yellow">HELP &lt;GO&gt;</b></span>
          </div>
        </nav>
      </header>
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Watchlist />} />
          <Route path="/stock/:symbol" element={<StockDetail />} />
          <Route path="/positions" element={<Positions />} />
          <Route path="/market" element={<Market />} />
          <Route path="/screener" element={<Screener />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
      <footer className="border-t border-line bg-panel">
        <div className="mx-auto flex max-w-[1700px] flex-wrap gap-x-4 gap-y-0.5 px-3 py-1 text-[11px] text-muted">
          <span>DATA <span className="text-fg">cse.lk (unofficial endpoints)</span></span>
          <span>LAST EOD RUN <span className="text-fg num">{status?.eod ?? '–'}</span></span>
          <span>TELEGRAM <span className={status?.telegram ? 'text-bull' : 'text-fg'}>{status?.telegram ? 'ON' : 'OFF'}</span></span>
          <span className="ml-auto">Decision support only, not financial advice.</span>
        </div>
      </footer>
      {help && <HelpPanel onClose={() => setHelp(false)} />}
    </div>
  )
}
