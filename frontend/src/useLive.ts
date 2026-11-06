import { useEffect, useState } from 'react'
import type { Quote } from './api'
import { authHeader } from './auth/session'

/** Subscribes to the backend's quote stream (server-sent events; it only polls CSE while the market is open).
 *  Read with fetch rather than EventSource so the request can carry the sign-in token; reconnects on drops. */
export function useLive(symbols: string[]) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [open, setOpen] = useState(false)
  const [updated, setUpdated] = useState<string | null>(null)
  const key = symbols.join(',')
  useEffect(() => {
    if (!key) return
    const ctrl = new AbortController()
    let retry: number | undefined
    const connect = async (delay: number) => {
      try {
        const r = await fetch(`/api/live?symbols=${encodeURIComponent(key)}`, { headers: await authHeader(), signal: ctrl.signal })
        if (!r.ok || !r.body) throw new Error(String(r.status))
        const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
        let buf = ''
        delay = 2000
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += value
          let i
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const ev = buf.slice(0, i)
            buf = buf.slice(i + 2)
            const data = ev.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).join('\n')
            if (!data) continue
            try {
              const d = JSON.parse(data)
              setQuotes(q => ({ ...q, ...d.quotes }))
              setOpen(Boolean(d.open))
              setUpdated(d.updated)
            } catch { /* ignore */ }
          }
        }
      } catch { /* dropped, refused or aborted */ }
      if (!ctrl.signal.aborted) retry = window.setTimeout(() => void connect(Math.min(delay * 2, 60000)), delay)
    }
    void connect(2000)
    return () => { ctrl.abort(); window.clearTimeout(retry) }
  }, [key])
  return { quotes, open, updated }
}
