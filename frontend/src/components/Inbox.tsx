import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { INBOX_CHANGED, api, ticker, type Inbox, type InboxEvent } from '../api'

const NOTIFY_KEY = 'cse:notify'
const POLL_MS = 30000

export const notifyOn = () => { try { return localStorage.getItem(NOTIFY_KEY) === '1' && 'Notification' in window && Notification.permission === 'granted' } catch { return false } }
/** Ask the browser for permission, then remember the choice. Returns whether notifications are now on. */
export async function setNotify(on: boolean): Promise<boolean> {
  try {
    if (on) {
      if (!('Notification' in window)) return false
      if (Notification.permission !== 'granted' && (await Notification.requestPermission()) !== 'granted') return false
    }
    localStorage.setItem(NOTIFY_KEY, on ? '1' : '0')
  } catch { return false }
  return on
}

/** "5 min ago" / "Fri 14:32": the server stores UTC; show it in Colombo time. */
export function when(iso: string): string {
  const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  const tz = { timeZone: 'Asia/Colombo' } as const
  if (s < 86400) return d.toLocaleTimeString('en-GB', { ...tz, hour: '2-digit', minute: '2-digit' })
  return d.toLocaleString('en-GB', { ...tz, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** The inbox, refreshed every 30 s and whenever something marks entries read. New unread entries become a browser
 *  notification (only if the user switched that on and allowed it); the first load never notifies about old ones. */
export function useInbox(limit = 60) {
  const [box, setBox] = useState<Inbox | null>(null)
  const seen = useRef<number | null>(null)
  const load = useCallback(() => {
    api.inbox(limit).then(b => {
      setBox(b)
      const top = b.events.reduce((m, e) => Math.max(m, e.id), 0)
      if (seen.current != null && notifyOn()) {
        b.events.filter(e => e.id > seen.current! && !e.read).slice(0, 3).forEach(e => {
          try { new Notification(e.symbol ? `${ticker(e.symbol)} · CSE Advisor` : 'CSE Advisor', { body: e.text, tag: `cse-${e.id}` }) } catch { /* not allowed here */ }
        })
      }
      seen.current = Math.max(seen.current ?? 0, top)
    }).catch(() => {})
  }, [limit])
  useEffect(() => {
    load()
    const t = window.setInterval(load, POLL_MS)
    window.addEventListener(INBOX_CHANGED, load)
    return () => { window.clearInterval(t); window.removeEventListener(INBOX_CHANGED, load) }
  }, [load])
  return { box, reload: load }
}

export function EventLine({ e, onOpen }: { e: InboxEvent; onOpen?: () => void }) {
  return (
    <div className={`flex gap-2 border-t border-line/60 px-2.5 py-1.5 text-[12px] ${e.read ? 'text-muted' : ''}`}>
      <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${e.read ? 'bg-transparent' : 'bg-amber'}`} aria-label={e.read ? 'read' : 'unread'} />
      <div className="min-w-0 flex-1">
        <div>
          {e.symbol && <Link to={`/stock/${e.symbol}`} onClick={onOpen} className="mr-1.5 font-semibold text-amber">{ticker(e.symbol)}</Link>}
          <span className={e.read ? '' : 'text-fg'}>{e.text}</span>
        </div>
        <div className="text-[10px] text-muted">{when(e.at)}</div>
      </div>
    </div>
  )
}

/** The bell in the header: unread count, and the latest entries in a small panel. */
export function Bell() {
  const nav = useNavigate()
  const { box } = useInbox(12)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away); document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  const n = box?.unread ?? 0
  return (
    <div className="relative" ref={ref}>
      <button className="relative flex items-center px-1 text-fg hover:text-amber" onClick={() => setOpen(o => !o)} aria-label={n ? `Alerts: ${n} unread` : 'Alerts: none unread'} aria-expanded={open} title="Alerts inbox">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {n > 0 && <span className="absolute -right-1.5 -top-1.5 min-w-[15px] bg-amber px-1 text-center text-[10px] font-bold leading-[15px] text-black">{n > 99 ? '99+' : n}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-40 mt-1.5 w-[min(92vw,380px)] border border-amber/60 bg-panel shadow-lg" role="dialog" aria-label="Recent alerts">
          <div className="flex items-center gap-2 bg-panel-2 px-2.5 py-1 text-[11px]">
            <b className="uppercase tracking-wide text-amber">Alerts</b>
            <span className="text-muted">{n ? `${n} unread` : 'all read'}</span>
            {n > 0 && <button className="ml-auto text-amber hover:underline" onClick={() => api.markRead()}>Mark all read</button>}
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
            {!box ? <p className="p-3 text-[12px] text-muted">Loading…</p> : box.events.length === 0
              ? <p className="p-3 text-[12px] text-muted">Nothing yet. Your own alerts and the engines' alerts will appear here.</p>
              : box.events.map(e => <EventLine key={e.id} e={e} onOpen={() => { setOpen(false); if (!e.read) api.markRead([e.id]) }} />)}
          </div>
          <button className="w-full border-t border-line bg-panel-2 px-2.5 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-amber hover:bg-panel-3" onClick={() => { setOpen(false); nav('/alerts') }}>
            Open ALRT &lt;GO&gt; · manage alerts →
          </button>
        </div>
      )}
    </div>
  )
}
