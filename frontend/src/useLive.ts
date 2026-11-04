import { useEffect, useState } from 'react'
import type { Quote } from './api'

/** Subscribes to the backend's SSE quote stream (it only polls CSE while market is open). */
export function useLive(symbols: string[]) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({})
  const [open, setOpen] = useState(false)
  const [updated, setUpdated] = useState<string | null>(null)
  const key = symbols.join(',')
  useEffect(() => {
    if (!key) return
    const es = new EventSource(`/api/live?symbols=${encodeURIComponent(key)}`)
    es.onmessage = ev => {
      try {
        const d = JSON.parse(ev.data)
        setQuotes(q => ({ ...q, ...d.quotes }))
        setOpen(Boolean(d.open))
        setUpdated(d.updated)
      } catch { /* ignore */ }
    }
    return () => es.close()
  }, [key])
  return { quotes, open, updated }
}
