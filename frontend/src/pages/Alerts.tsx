import { useCallback, useEffect, useState } from 'react'
import { api, type AlertKind, type AlertRule } from '../api'
import { AlertForm, RuleRow } from '../components/AlertForm'
import { EventLine, notifyOn, setNotify, useInbox } from '../components/Inbox'
import { ErrorBox, Spinner } from '../components/ui'

export default function Alerts() {
  const { box, reload } = useInbox(150)
  const [rules, setRules] = useState<AlertRule[] | null>(null)
  const [kinds, setKinds] = useState<Record<string, AlertKind>>({})
  const [syms, setSyms] = useState<string[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [onlyUnread, setOnlyUnread] = useState(false)
  const [notify, setNotifyState] = useState(notifyOn())
  const [notifyMsg, setNotifyMsg] = useState<string | null>(null)

  const loadRules = useCallback(() => { api.alerts().then(d => { setRules(d.rules); setKinds(d.kinds) }).catch(e => setErr(String(e.message))) }, [])
  useEffect(() => {
    loadRules()
    Promise.all([api.watchlist(), api.positions()]).then(([w, p]) => {
      setSyms([...new Set([...w.map(x => x.symbol), ...p.filter(x => !x.closed).map(x => x.symbol)])].sort())
    }).catch(() => {})
  }, [loadRules])

  const toggleNotify = async () => {
    setNotifyMsg(null)
    const on = await setNotify(!notify)
    setNotifyState(on)
    if (!notify && !on) setNotifyMsg('The browser did not allow notifications. Allow them for this site in the address bar, then try again.')
  }
  const events = (box?.events ?? []).filter(e => !onlyUnread || !e.read)
  const active = rules?.filter(r => r.active) ?? []
  const off = rules?.filter(r => !r.active) ?? []

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-3">
      <h1 className="mb-2 text-[14px] font-semibold uppercase tracking-wider text-amber">ALRT · Alerts</h1>
      {err && <ErrorBox msg={err} />}
      <div className="grid gap-3 lg:grid-cols-2 [&>*]:min-w-0">
        <section className="card" aria-label="Inbox">
          <div className="panel-title">
            <h2 className="flex-1">Inbox {box && box.unread > 0 && <span className="chip ml-1 bg-amber text-black">{box.unread} new</span>}</h2>
            <label className="flex items-center gap-1 text-[11px] font-normal normal-case"><input type="checkbox" checked={onlyUnread} onChange={e => setOnlyUnread(e.target.checked)} /> unread only</label>
            <button className="btn !py-0 text-[11px]" disabled={!box?.unread} onClick={() => api.markRead()}>Mark all read</button>
            <button className="btn !py-0 text-[11px]" onClick={async () => { await api.clearInbox(); reload() }} title="Remove the entries you have read">Clear read</button>
          </div>
          {!box ? <Spinner /> : events.length === 0 ? (
            <p className="p-4 text-[12px] text-muted">{onlyUnread ? 'Nothing unread.' : 'Nothing yet. Alerts you set on the right, and the engines’ own alerts (stops, entry triggers, signal changes, new patterns, announcements), land here. Entries are kept for 90 days.'}</p>
          ) : (
            <div className="max-h-[70vh] overflow-y-auto">{events.map(e => <EventLine key={e.id} e={e} onOpen={() => { if (!e.read) api.markRead([e.id]) }} />)}</div>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t border-line bg-panel-2 px-2.5 py-1.5 text-[11px] text-muted">
            <button className="btn !py-0 text-[11px]" aria-pressed={notify} onClick={toggleNotify}>{notify ? 'Browser notifications: on' : 'Turn on browser notifications'}</button>
            <span>pop-ups while this tab is open; Telegram covers the rest</span>
            {notifyMsg && <span className="w-full text-warn">{notifyMsg}</span>}
          </div>
        </section>

        <div className="space-y-3">
          <section className="card" aria-label="New alert">
            <div className="panel-title"><h2 className="flex-1">New alert</h2></div>
            <div className="p-3">
              {syms.length === 0 && rules ? <p className="text-[12px] text-muted">Add a stock to a watchlist first. Alerts are for stocks you follow.</p>
                : <AlertForm symbols={syms} kinds={kinds} onCreated={loadRules} />}
              <p className="mt-2 text-[11px] text-muted">Price, day-move, volume and 52-week-high alerts are checked on every live quote while the market is open (the app must be running). Pattern, announcement, verdict and RSI alerts are checked each evening after the analysis. Each alert fires at most once a day.</p>
            </div>
          </section>
          <section className="card" aria-label="My alerts">
            <div className="panel-title"><h2 className="flex-1">My alerts <span className="font-normal normal-case text-muted">{active.length} on{off.length ? ` · ${off.length} off` : ''}</span></h2></div>
            {!rules ? <Spinner /> : rules.length === 0 ? <p className="p-4 text-[12px] text-muted">None yet. You can also press <b className="text-yellow">+ Alert</b> in any stock’s header.</p>
              : [...active, ...off].map(r => <RuleRow key={r.id} r={r} onChange={loadRules} />)}
          </section>
        </div>
      </div>
    </div>
  )
}
