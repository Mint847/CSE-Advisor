// Bloomberg-style command line: "<TICKER> <FUNCTION> <GO>" or a global function ("WL <GO>").
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, ticker, WATCH_CHANGED } from '../api'

export const FUNCTIONS: { code: string; text: string; global?: boolean }[] = [
  { code: 'GP', text: 'Price chart & recommendation' },
  { code: 'CMP', text: 'Compare the Classic, Levels and SMC engines' },
  { code: 'SMC', text: 'Smart-money view: order blocks, premium/discount, liquidity' },
  { code: 'DES', text: 'Description: fundamentals & value checks' },
  { code: 'LIQ', text: 'Liquidity & levels' },
  { code: 'CN', text: 'Catalysts: results, dividends, director dealings, calendar, headlines' },
  { code: 'CAT', text: 'Same as CN (catalysts)' },
  { code: 'CACS', text: 'Corporate actions & data import' },
  { code: 'NOTE', text: 'Notes on this stock' },
  { code: 'PAT', text: 'Open the pattern & layer chooser' },
  { code: '1M', text: 'Chart & engines on 1-minute bars' },
  { code: '5M', text: 'Chart & engines on 5-minute bars' },
  { code: '15M', text: 'Chart & engines on 15-minute bars' },
  { code: '1H', text: 'Chart & engines on 1-hour bars' },
  { code: '4H', text: 'Chart & engines on 4-hour bars' },
  { code: '1D', text: 'Daily chart (the nightly analysis)' },
  { code: '1W', text: 'Weekly chart (daily analysis drawn on weekly bars)' },
  { code: 'WL', text: 'Home: portfolio and watchlists', global: true },
  { code: 'ALL', text: 'All listed stocks (add any to a list)', global: true },
  { code: 'PORT', text: 'Positions (portfolio)', global: true },
  { code: 'MKT', text: 'Market (ASPI, breadth); also WEI', global: true },
  { code: 'SCR', text: 'Candidates: ranking of all liquid CSE stocks', global: true },
  { code: 'JRN', text: 'Forward-test journal: calls scored by what happened', global: true },
  { code: 'GLB', text: 'World stocks: NVIDIA, SK Hynix, big tech... with the news around their big moves', global: true },
  { code: 'HMAP', text: 'Market heatmap by sector (size = market cap, colour = day change)', global: true },
  { code: 'NEWS', text: 'News & announcements feed (my stocks / all tracked)', global: true },
  { code: 'CAL', text: 'Calendar: results due, ex-dividend, payments, AGMs', global: true },
  { code: 'ALRT', text: 'Alerts: your own price and pattern alerts, and the inbox', global: true },
  { code: 'NOTES', text: 'Notes on all your stocks (search, tags)', global: true },
  { code: 'SET', text: 'Settings & jobs', global: true },
  { code: 'ACCT', text: 'Account: profile, password, sign out', global: true },
  { code: 'ADD', text: 'Add a stock: ADD JKH', global: true },
  { code: 'HELP', text: 'Commands and keys', global: true },
]
const TAB_OF: Record<string, string> = { GP: 'rec', CMP: 'cmp', SMC: 'smc', DES: 'des', LIQ: 'liq', CN: 'cn', CAT: 'cn', CACS: 'cacs', NOTE: 'note', PAT: 'rec' }
const TF_OF: Record<string, string> = { '1M': '1m', '5M': '5m', '15M': '15m', '1H': '1h', '4H': '4h', '1D': '1D', '1W': '1W' }
const GLOBAL_ROUTE: Record<string, string> = { WL: '/', ALL: '/?view=all', PORT: '/positions', MKT: '/market', WEI: '/market', SCR: '/screener', JRN: '/journal', GLB: '/global', WORLD: '/global', HMAP: '/heatmap', NEWS: '/news', CAL: '/calendar', ALRT: '/alerts', ALERTS: '/alerts', NOTES: '/notes', SET: '/settings', ACCT: '/account' }

interface Props { onHelp: () => void; inputRef: React.RefObject<HTMLInputElement | null> }

export default function CommandLine({ onHelp, inputRef }: Props) {
  const nav = useNavigate()
  const loc = useLocation()
  const [q, setQ] = useState('')
  const [syms, setSyms] = useState<{ symbol: string; name: string }[]>([])
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const [picked, setPicked] = useState(false)   // true once the user moves onto a suggestion
  const [msg, setMsg] = useState<string | null>(null)
  const listId = 'cmd-suggestions'
  const blurTimer = useRef<number | undefined>(undefined)
  const listed = useRef<{ symbol: string; name: string }[] | null>(null)
  const worldList = useRef<{ symbol: string; ticker: string }[] | null>(null)   // the GLB stocks, fetched the first time one is needed    // every CSE-listed stock, fetched the first time one is needed

  // the tracked stocks, fetched once and again only when the list changes (add / remove), not on every click
  useEffect(() => {
    const load = () => api.watchlist().then(w => setSyms(w.map(x => ({ symbol: x.symbol, name: x.name })))).catch(() => {})
    load()
    window.addEventListener(WATCH_CHANGED, load)
    return () => window.removeEventListener(WATCH_CHANGED, load)
  }, [])
  useEffect(() => { if (!msg) return; const t = window.setTimeout(() => setMsg(null), 4000); return () => window.clearTimeout(t) }, [msg])

  const currentSym = loc.pathname.startsWith('/stock/') ? decodeURIComponent(loc.pathname.split('/')[2]) : null
  const resolve = (tok: string) => {
    const t = tok.toUpperCase()
    return syms.find(s => s.symbol.toUpperCase() === t || ticker(s.symbol).toUpperCase() === t)?.symbol ?? null
  }

  const suggestions = useMemo(() => {
    const parts = q.trim().toUpperCase().split(/\s+/).filter(Boolean)
    if (!parts.length) return []
    if (parts.length === 1) {
      const p = parts[0]
      const fx = FUNCTIONS.filter(f => f.global && f.code.startsWith(p)).map(f => ({ value: f.code, label: f.code, hint: f.text }))
      const st = syms.filter(s => ticker(s.symbol).toUpperCase().startsWith(p) || s.name.toUpperCase().includes(p))
        .slice(0, 8).map(s => ({ value: ticker(s.symbol), label: ticker(s.symbol), hint: s.name }))
      const local = currentSym ? FUNCTIONS.filter(f => !f.global && f.code.startsWith(p)).map(f => ({ value: f.code, label: f.code, hint: `${ticker(currentSym)}: ${f.text}` })) : []
      return [...st, ...fx, ...local].slice(0, 10)
    }
    if (parts.length === 2 && resolve(parts[0])) {
      return FUNCTIONS.filter(f => !f.global && f.code.startsWith(parts[1])).map(f => ({ value: `${parts[0]} ${f.code}`, label: `${parts[0]} ${f.code}`, hint: f.text }))
    }
    return []
  }, [q, syms, currentSym])

  const go = async (raw: string) => {
    const parts = raw.trim().toUpperCase().replace(/<?GO>?$/, '').trim().split(/\s+/).filter(Boolean)
    if (!parts.length) return
    setOpen(false)
    const [a] = parts
    if (a === 'HELP' || a === 'H') { onHelp(); setQ(''); return }
    if (GLOBAL_ROUTE[a]) { nav(GLOBAL_ROUTE[a]); setQ(''); return }
    if (a === 'ADD' && parts.length > 1) {
      setMsg(`Adding ${parts.slice(1).join(' ')}: fetching a year of history…`)
      try {
        const r = await api.addStock(parts.slice(1).join(' '))
        setSyms(s => [...s, { symbol: r.symbol, name: r.symbol }]); nav(`/stock/${r.symbol}`); setQ(''); setMsg(null)
      } catch (e) { setMsg(String((e as Error).message)) }
      return
    }
    // a timeframe on the stock currently open (e.g. "15M <GO>"): keep the tab, change the chart
    if (currentSym && TF_OF[a] && !resolve(a)) {
      const sp = new URLSearchParams(loc.search)
      if (TF_OF[a] === '1D') sp.delete('tf'); else sp.set('tf', TF_OF[a])
      nav(`/stock/${currentSym}?${sp.toString()}`); setQ(''); return
    }
    // a function on the stock currently open (e.g. "DES <GO>")
    if (currentSym && TAB_OF[a] && !resolve(a)) {
      nav(`/stock/${currentSym}?tab=${TAB_OF[a]}${a === 'PAT' ? '&pat=1' : ''}`); setQ(''); return
    }
    if (a === 'NOTE' && !currentSym) { nav('/notes'); setQ(''); return }          // NOTE on a stock page = that stock's notes; elsewhere = all notes
    const sym = resolve(a)
    if (!sym) {
      // a world stock on the GLB page (NVDA, MU, 000660...)
      try { worldList.current ??= (await api.globalList()).rows.map(r => ({ symbol: r.symbol, ticker: r.ticker })) } catch { /* offline */ }
      const w = (worldList.current ?? []).find(r => r.ticker.toUpperCase() === a || r.symbol.toUpperCase() === a)
      if (w) { nav(`/global/${encodeURIComponent(w.symbol)}`); setQ(''); return }
      // not followed: if the CSE lists it, show its quote card (read-only, with an Add button)
      try { listed.current ??= await api.listed() } catch { /* offline: fall through to the message */ }
      const hit = (listed.current ?? []).filter(s => s.symbol.toUpperCase() === a || ticker(s.symbol).toUpperCase() === a)
        .sort((x, y) => Number(y.symbol.endsWith('.N0000')) - Number(x.symbol.endsWith('.N0000')))[0]
      if (hit) { nav(`/quote/${hit.symbol}`); setQ(''); return }
      setMsg(`${a}: not a listed stock or function. Type HELP <GO> for the commands.`); return
    }
    // "JKH 15M", "JKH GP 1H", "JKH SMC 4H": a function and / or a timeframe, in any order
    const fn = parts.slice(1).find(x => TAB_OF[x]) ?? 'GP'
    const tf = parts.slice(1).map(x => TF_OF[x]).find(Boolean)
    nav(`/stock/${sym}?tab=${TAB_OF[fn]}${fn === 'PAT' ? '&pat=1' : ''}${tf && tf !== '1D' ? `&tf=${tf}` : ''}`)
    setQ('')
  }

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' && suggestions.length) { e.preventDefault(); setOpen(true); setHi(h => (picked ? h + 1 : 0) % suggestions.length); setPicked(true) }
    else if (e.key === 'ArrowUp' && suggestions.length) { e.preventDefault(); setHi(h => (h - 1 + suggestions.length) % suggestions.length); setPicked(true) }
    else if (e.key === 'Tab' && open && suggestions[hi]) { e.preventDefault(); setQ(suggestions[hi].value + ' '); setPicked(false) }
    // Enter runs what was typed, unless the user moved onto a suggestion with the arrow keys
    else if (e.key === 'Enter') { e.preventDefault(); go(open && picked && suggestions[hi] ? suggestions[hi].value : q) }
    else if (e.key === 'Escape') { setQ(''); setOpen(false); inputRef.current?.blur() }
  }

  return (
    <div className="relative min-w-0 flex-1">
      <form className="flex min-w-0 items-stretch border border-amber/70 bg-black" onSubmit={e => { e.preventDefault(); go(q) }}>
        <span className="hidden select-none items-center bg-amber px-2 text-[11px] font-semibold text-black sm:flex">CMD</span>
        <input ref={inputRef} value={q} spellCheck={false} autoComplete="off"
               onChange={e => { setQ(e.target.value); setOpen(true); setHi(0); setPicked(false) }}
               onFocus={() => setOpen(true)} onBlur={() => { blurTimer.current = window.setTimeout(() => setOpen(false), 150) }}
               onKeyDown={onKey}
               role="combobox" aria-expanded={open && suggestions.length > 0} aria-controls={listId} aria-autocomplete="list"
               aria-label="Command line: type a ticker and a function, e.g. JKH GP, then press Enter"
               placeholder="JKH GP <GO>  ·  WL <GO>  ·  HELP <GO>"
               className="min-w-0 flex-1 bg-transparent px-2 py-1 text-[13px] uppercase text-yellow outline-none placeholder:normal-case placeholder:text-[#6b6b6b]" />
        <button className="bg-[#1a1a1a] px-3 text-[11px] font-semibold text-amber hover:bg-amber hover:text-black" aria-label="Go">&lt;GO&gt;</button>
      </form>
      {open && suggestions.length > 0 && (
        <ul id={listId} role="listbox" className="absolute left-0 right-0 top-full z-50 max-h-80 overflow-y-auto border border-amber/60 bg-black shadow-2xl">
          {suggestions.map((s, i) => (
            <li key={s.value} role="option" aria-selected={i === hi}
                onMouseDown={e => { e.preventDefault(); window.clearTimeout(blurTimer.current); go(s.value) }}
                onMouseEnter={() => { setHi(i); setPicked(true) }}
                className={`flex cursor-pointer gap-3 px-2 py-1 text-[12px] ${i === hi && picked ? 'bg-amber text-black' : 'text-fg'}`}>
              <b className={`w-28 shrink-0 ${i === hi && picked ? '' : 'text-amber'}`}>{s.label}</b>
              <span className={`truncate ${i === hi && picked ? '' : 'text-muted'}`}>{s.hint}</span>
            </li>
          ))}
        </ul>
      )}
      {msg && <div role="status" className="absolute left-0 right-0 top-full z-40 mt-px border border-amber/60 bg-black px-2 py-1 text-[12px] text-yellow">{msg}</div>}
    </div>
  )
}
