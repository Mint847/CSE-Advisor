import { useEffect, useRef, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api } from './api'
import { afterSignIn } from './auth/rules'
import { AccountMenu, VerifyBanner } from './components/Account'
import { DataBanner, HealthStrip, useHealth } from './components/DataHealth'
import { Bell } from './components/Inbox'
import Tape from './components/Tape'
import CommandLine, { FUNCTIONS } from './components/CommandLine'
import Account from './pages/Account'
import Alerts from './pages/Alerts'
import Calendar from './pages/Calendar'
import Global from './pages/Global'
import Heatmap from './pages/Heatmap'
import Journal from './pages/Journal'
import Market from './pages/Market'
import News from './pages/News'
import Notes from './pages/Notes'
import Positions from './pages/Positions'
import Quote from './pages/Quote'
import Screener from './pages/Screener'
import Settings from './pages/Settings'
import { AFTER_KEY } from './pages/SignIn'
import StockDetail from './pages/StockDetail'
import Home from './pages/Home'

/** Function keys; entries with no key are menu-only (the command line reaches them too). */
const FKEYS: { key: string; label: string; to?: string; help?: boolean }[] = [
  { key: 'F1', label: 'HELP', help: true },
  { key: 'F2', label: 'WL', to: '/' },
  { key: 'F3', label: 'PORT', to: '/positions' },
  { key: 'F4', label: 'MKT', to: '/market' },
  { key: 'F5', label: 'SCR', to: '/screener' },
  { key: 'F6', label: 'JRN', to: '/journal' },
  { key: 'F8', label: 'SET', to: '/settings' },
  { key: 'F9', label: 'ALRT', to: '/alerts' },
  { key: 'F10', label: 'NOTE', to: '/notes' },
  { key: '', label: 'GLB', to: '/global' },
  { key: '', label: 'HMAP', to: '/heatmap' },
  { key: '', label: 'NEWS', to: '/news' },
  { key: '', label: 'CAL', to: '/calendar' },
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
              {FKEYS.filter(k => k.key).map(k => <tr key={k.key}><td className="text-amber">{k.key}</td><td>{k.label}</td></tr>)}
              <tr><td className="text-amber">GLB HMAP NEWS CAL</td><td>world stocks, heatmap, news feed, calendar (menu or command line)</td></tr>
              <tr><td className="text-amber">P</td><td>stock page: patterns & layers</td></tr>
              <tr><td className="text-amber">1 – 7</td><td>stock page: timeframe 1M, 5M, 15M, 1H, 4H, 1D, 1W</td></tr>
              <tr><td className="text-amber">C · L</td><td>stock page: candles / line · log scale on / off</td></tr>
              <tr><td className="text-amber">[ ]</td><td>stock page: previous / next stock in your list</td></tr>
              <tr><td className="text-amber">H T B N</td><td>draw a horizontal line, trendline, box, note (drag a drawing to move it)</td></tr>
              <tr><td className="text-amber">DEL · CTRL+Z</td><td>delete the selected drawing · undo</td></tr>
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
  const health = useHealth()

  // after signing in, open the page that was asked for while signed out
  useEffect(() => {
    let to: string | null = null
    try { to = sessionStorage.getItem(AFTER_KEY); sessionStorage.removeItem(AFTER_KEY) } catch { /* */ }
    if (to && afterSignIn(to) !== '/') nav(afterSignIn(to), { replace: true })
  }, [nav])

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
            <Bell />
            <AccountMenu />
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
                {k.key && <span className="bg-menu px-1 text-white">{k.key}</span>}{({ WL: 'WATCHLIST', PORT: 'POSITIONS', MKT: 'MARKET', SCR: 'CANDIDATES', JRN: 'JOURNAL', ALRT: 'ALERTS', NOTE: 'NOTES', GLB: 'WORLD', HMAP: 'HEATMAP', NEWS: 'NEWS', CAL: 'CALENDAR' } as Record<string, string>)[k.label] ?? 'SETTINGS'}
              </NavLink>
            ))}
            <span className="ml-auto hidden shrink-0 pl-3 text-[11px] text-muted md:inline">type <b className="text-yellow">/</b> for the command line · <b className="text-yellow">HELP &lt;GO&gt;</b></span>
          </div>
        </nav>
        <Tape />
      </header>
      <VerifyBanner />
      <DataBanner h={health} />
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/stock/:symbol" element={<StockDetail />} />
          <Route path="/positions" element={<Positions />} />
          <Route path="/market" element={<Market />} />
          <Route path="/screener" element={<Screener />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/heatmap" element={<Heatmap />} />
          <Route path="/global" element={<Global />} />
          <Route path="/global/:symbol" element={<StockDetail world />} />
          <Route path="/news" element={<News />} />
          <Route path="/calendar" element={<Calendar />} />
          <Route path="/quote/:symbol" element={<Quote />} />
          <Route path="/notes" element={<Notes />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/account" element={<Account />} />
          {['/login', '/signup', '/forgot'].map(p => <Route key={p} path={p} element={<Navigate to="/" replace />} />)}
        </Routes>
      </main>
      <footer className="border-t border-line bg-panel">
        <div className="mx-auto flex max-w-[1700px] flex-wrap gap-x-4 gap-y-0.5 px-3 py-1 text-[11px] text-muted">
          <span>DATA <span className="text-fg">cse.lk (unofficial endpoints)</span></span>
          <HealthStrip h={health} />
          <span>TELEGRAM <span className={status?.telegram ? 'text-bull' : 'text-fg'}>{status?.telegram ? 'ON' : 'OFF'}</span></span>
          <span className="ml-auto">Decision support only, not financial advice.</span>
        </div>
      </footer>
      {help && <HelpPanel onClose={() => setHelp(false)} />}
    </div>
  )
}
