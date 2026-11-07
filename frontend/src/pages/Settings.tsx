import { useEffect, useState } from 'react'
import { api, ticker, type SectorRow, type WatchItem } from '../api'
import { HealthPanel } from '../components/DataHealth'
import { setTape, tapeOn } from '../components/Tape'
import { Section } from '../components/ui'
import { CATEGORY_LABEL, useCatalog } from '../prefs'

type St = Awaited<ReturnType<typeof api.status>>

/** One line for a past run: new runs store JSON (analysed, late, steps); older ones stored plain text. */
function jobNote(message: string | null): string {
  if (!message) return ''
  try {
    const m = JSON.parse(message) as { analysed?: number; late?: string; message?: string; steps?: Record<string, { ok: boolean }>; errors?: Record<string, string> }
    const bad = Object.entries(m.steps ?? {}).filter(([, v]) => !v.ok).map(([k]) => k)
    return [m.message, m.analysed != null && `${m.analysed} analysed`, m.late, bad.length ? `did not update: ${bad.join(', ')}` : '',
            Object.keys(m.errors ?? {}).length ? 'analysis errors' : ''].filter(Boolean).join(' · ')
  } catch { return message.slice(0, 140) }
}

export default function Settings() {
  const [tape, setTapeState] = useState(tapeOn())
  const { catalog, enabled, setEnabled } = useCatalog()
  const [st, setSt] = useState<St | null>(null)
  const [running, setRunning] = useState(false)
  const [tg, setTg] = useState<string | null>(null)
  useEffect(() => { api.status().then(setSt).catch(() => {}) }, [])
  const [wl, setWl] = useState<WatchItem[]>([])
  const [sectors, setSectors] = useState<SectorRow[]>([])
  const [saved, setSaved] = useState<string | null>(null)
  useEffect(() => {
    api.watchlist().then(setWl).catch(() => {})
    api.market().then(m => setSectors([...(m.sectors?.rows ?? [])].sort((a, b) => a.name.localeCompare(b.name)))).catch(() => {})
  }, [])
  const setSector = async (sym: string, code: string) => {
    await api.setSector(sym, code || null)
    setWl(w => w.map(x => x.symbol === sym ? { ...x, sector: code || null } : x))
    setSaved(`${ticker(sym)}: ${code || 'no sector'}`)
  }
  const groups = Object.values(catalog).reduce<Record<string, typeof catalog[string][]>>((g, c) => { (g[c.category] ??= []).push(c); return g }, {})

  return (
    <div className="mx-auto max-w-[1700px] space-y-2 px-3 py-3">
      <h1 className="text-[14px] font-semibold uppercase tracking-wider text-amber">SET · Settings</h1>
      <HealthPanel />
      {st && !st.admin && (
        <Section title="Jobs">
          <p className="text-sm text-muted">The analysis runs every weekday after the close (15:30 Colombo time) for every stock on anyone's watchlist.
            Jobs, Telegram and shared market data (sectors, statements, corporate actions) are managed by the app's administrator.</p>
        </Section>
      )}
      {st?.admin && <Section title="Jobs & integrations">
        <div className="space-y-2 text-sm">
          <p className="flex flex-wrap items-center gap-2">Telegram: {st?.telegram ? <b className="text-bull">connected</b> : <span className="text-warn">not set up, so alerts and the evening digest go nowhere. Add TELEGRAM_TOKEN and TELEGRAM_CHAT_ID to backend/.env (README, "Telegram"), then restart.</span>}
            <button className="btn !py-0.5 text-xs" disabled={!st?.telegram} onClick={async () => { try { await api.telegramTest(); setTg('Test message sent: check Telegram.') } catch (e) { setTg(String((e as Error).message)) } }}>Send test message</button>
            {tg && <span className="text-xs text-muted">{tg}</span>}</p>
          <p>Capital LKR {st?.settings.capital.toLocaleString()} · risk {st?.settings.risk_pct}% per trade · brokerage {st?.settings.fees_pct}% per side. Edit <code>backend/config/settings.yaml</code> and restart.</p>
          <button className="btn btn-primary" disabled={running} onClick={async () => { setRunning(true); await api.runEod(); setTimeout(() => { setRunning(false); api.status().then(setSt) }, 4000) }}>
            {running ? 'Started…' : 'Run end-of-day analysis now'}
          </button>
          <ul className="mt-2 text-xs text-muted">
            {st?.jobs.map((j, i) => <li key={i}>{new Date(j.at + 'Z').toLocaleString()}: {j.job} for {j.date} {j.ok ? '✓' : '✗'} <span className="opacity-70">{jobNote(j.message)}</span></li>)}
          </ul>
        </div>
      </Section>}
      {st?.admin && <Section title="Sectors (shown on the stock page and the market page; not scored)">
        <p className="mb-2 text-[12px] text-muted">CSE's data doesn't say which S&amp;P/CSE sector index a stock belongs to, so pick it here (used for its relative-strength line). The heatmap, All stocks and the sector breakdown use TradingView's sector instead, filled in automatically.{saved && <span className="ml-2 text-bull">Saved {saved}</span>}</p>
        <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-3 [&>*]:min-w-0">
          {wl.map(w => (
            <label key={w.symbol} className="flex items-center gap-2 text-[12px]">
              <span className="w-14 text-amber">{ticker(w.symbol)}</span>
              <select className="input w-0 min-w-0 flex-1 !py-0.5" value={w.sector ?? ''} onChange={e => setSector(w.symbol, e.target.value)} aria-label={`Sector for ${w.symbol}`}>
                <option value="">(none)</option>
                {sectors.map(x => <option key={x.code} value={x.code}>{x.code} · {x.name}</option>)}
              </select>
            </label>
          ))}
        </div>
      </Section>}
      <Section title="Display">
        <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={tape} onChange={e => { setTapeState(e.target.checked); setTape(e.target.checked) }} /> Ticker tape under the menu (desktop only, this browser): the ASPI and your stocks scroll past; it stops when you point at it.</label>
      </Section>
      <Section title="Pattern catalog">
        <p className="mb-3 text-sm text-muted">Turn pattern types off to hide them everywhere. Tier 1 = chart & liquidity patterns (most useful for swing trading), tier 2 = multi-candle, tier 3 = single-candle. The measured track record of each pattern (14-year study) is shown next to it on the stock page.</p>
        <div className="grid gap-4 sm:grid-cols-2">
          {Object.entries(groups).map(([cat, items]) => (
            <div key={cat}>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{CATEGORY_LABEL[cat] ?? cat}</h4>
              <ul className="space-y-0.5 text-sm">
                {items.sort((a, b) => a.tier - b.tier || b.base_reliability - a.base_reliability).map(c => (
                  <li key={c.type}>
                    <label className="flex cursor-pointer items-center gap-2">
                      <input type="checkbox" checked={enabled(c.type)} onChange={e => setEnabled(c.type, e.target.checked)} />
                      <span className="flex-1">{c.name}</span>
                      <span className="chip bg-panel-2 text-muted">T{c.tier}</span>
                      <span className="w-8 text-right text-xs text-muted num">{(c.base_reliability * 100).toFixed(0)}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Section>
    </div>
  )
}
