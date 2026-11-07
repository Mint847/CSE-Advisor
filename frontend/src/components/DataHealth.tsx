// Is the data current? The footer strip, the banner shown when it is not, and the Health panel (Settings).
// The numbers come from /api/health: each stored source against the last finished session, and the last evening run's steps.
import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api, type Health } from '../api'
import { Section } from './ui'

/** Polls /api/health once a minute; every component that calls it shares nothing, so keep callers few. */
export function useHealth(): Health | null {
  const [h, setH] = useState<Health | null>(null)
  useEffect(() => {
    const load = () => api.health().then(setH).catch(() => {})
    load()
    const t = window.setInterval(load, 60000)
    return () => window.clearInterval(t)
  }, [])
  return h
}

const TONE: Record<string, string> = { ok: 'text-bull', partial: 'text-amber', stale: 'text-amber', failed: 'text-bear' }

/** "5 Nov" for a session date, "12:16" for a time (an ISO UTC timestamp, shown in Colombo time). */
export function asOf(src: Health['sources'][number]): string {
  if (!src.as_of) return '–'
  if (src.kind === 'time') return new Date(src.as_of).toLocaleTimeString('en-GB', { timeZone: 'Asia/Colombo', hour: '2-digit', minute: '2-digit' })
  const [y, m, d] = src.as_of.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-GB', { timeZone: 'UTC', day: 'numeric', month: 'short' })
}

export function HealthStrip({ h }: { h: Health | null }) {
  if (!h) return <span>DATA <span className="text-fg">checking…</span></span>
  const show = h.sources.filter(s => ['prices', 'analysis', 'headlines', 'world'].includes(s.key))
  return (
    <span className="flex flex-wrap items-center gap-x-3" aria-label="Data freshness">
      {show.map(s => (
        <span key={s.key} title={`${s.label}: as of ${s.as_of ?? 'never'}${s.stale ? ' (behind)' : ''}`}>
          {s.label.split(' ')[0].toUpperCase()} <span className={`num ${s.stale ? 'text-amber' : 'text-fg'}`}>{asOf(s)}</span>
        </span>
      ))}
      <Link to="/settings#health" className={`font-semibold ${TONE[h.status] ?? 'text-fg'}`} title="Open the data health panel">
        RUN {h.running ? '… running' : h.status === 'ok' ? '✓' : h.status === 'partial' ? '✓ partial' : h.status === 'failed' ? '✗ failed' : '! behind'}
      </Link>
    </span>
  )
}

/** Shown above the page when the stored data is behind the last finished session, or the last run was partial. */
export function DataBanner({ h }: { h: Health | null }) {
  const [hidden, setHidden] = useState(false)
  if (!h || hidden || h.status === 'ok' || h.running) return null
  const behind = h.stale.length > 0
  const text = h.status === 'failed'
    ? `The evening run for ${h.session} failed. The data below is from before it; it retries by itself (up to 3 times).`
    : behind
      ? `The data is behind: ${h.stale.join(', ')} stop before the last session (${h.expected}). The evening run will catch up by itself; the PC may have been asleep or off.`
      : `The last evening run finished but ${h.run.failed_steps.join(', ')} did not update.`
  return (
    <div role="status" className="border-b border-amber/40 bg-[#1d1405]">
      <div className="mx-auto flex max-w-[1700px] items-center gap-3 px-3 py-1.5 text-[12px]">
        <span className={`font-semibold uppercase ${TONE[h.status]}`}>{h.status === 'partial' ? 'Partial update' : 'Data behind'}</span>
        <span className="min-w-0 flex-1 text-fg">{text}</span>
        <Link to="/settings#health" className="shrink-0 text-blue hover:underline">Details</Link>
        <button className="shrink-0 text-muted hover:text-fg" aria-label="Dismiss" onClick={() => setHidden(true)}>✕</button>
      </div>
    </div>
  )
}

export function HealthPanel() {
  const h = useHealth()
  const { hash } = useLocation()
  const loaded = h != null
  useEffect(() => { if (loaded && hash === '#health') document.getElementById('health')?.scrollIntoView({ block: 'start' }) }, [loaded, hash])
  if (!h) return null
  const r = h.run
  return (
    <div id="health">
      <Section title="Data health" right={<span className={`text-[12px] font-semibold uppercase ${TONE[h.status]}`}>{h.running ? 'running' : h.status}</span>}>
        <div className="scroll-thin overflow-x-auto">
          <table className="bb-table">
            <thead><tr><th>Source</th><th>As of</th><th>State</th></tr></thead>
            <tbody>{h.sources.map(s => (
              <tr key={s.key}>
                <td>{s.label}</td><td className="num">{asOf(s)}{s.kind === 'session' && s.as_of ? ` · ${s.as_of}` : ''}</td>
                <td className={s.stale ? 'text-amber' : 'text-bull'}>{s.stale ? 'behind' : 'current'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <p className="mt-2 text-[12px] text-muted">
          Last finished session <b className="text-fg">{h.session}</b>{h.expected !== h.session && <> (the market was closed; data expected to {h.expected})</>}.{' '}
          Last evening run: {r.run_date ? <><b className="text-fg">{r.run_date}</b> {r.ok ? '✓' : '✗'}{r.late && ' · recovered late after the PC was off or asleep'}{r.attempts > 1 && ` · ${r.attempts} attempts`}</> : 'none yet'}.
          {r.failed_steps.length > 0 && <span className="text-amber"> Did not update: {r.failed_steps.join(', ')}.</span>}
          {' '}The app checks every 15 minutes and runs the evening job by itself when a session is missing.
        </p>
        {r.steps && (
          <div className="scroll-thin mt-2 overflow-x-auto">
            <table className="bb-table [&_td]:whitespace-normal">
              <thead><tr><th>Step</th><th>Result</th><th className="text-right">Seconds</th></tr></thead>
              <tbody>{Object.entries(r.steps).map(([name, v]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className={v.ok ? 'text-bull' : 'text-bear'}>{v.ok ? 'ok' : v.error ?? 'failed'}
                    {v.failed && Object.keys(v.failed).length > 0 && <span className="text-muted"> · {Object.entries(v.failed).map(([k, e]) => `${k.split('.')[0]}: ${e}`).join('; ')}</span>}
                    {v.failed_feeds && v.failed_feeds.length > 0 && <span className="text-muted"> · refused: {v.failed_feeds.join(', ')}</span>}</td>
                  <td className="text-right num">{v.s ?? ''}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}
