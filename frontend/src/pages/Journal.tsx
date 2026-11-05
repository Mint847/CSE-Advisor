import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, ticker, type JournalData, type JournalRow } from '../api'
import { EngineBadge, ErrorBox, Spinner, VerdictBadge } from '../components/ui'

const num = (x: number | null | undefined, nd = 1, sign = true) => x == null ? '–' : `${sign && x > 0 ? '+' : ''}${x.toFixed(nd)}`
const tone = (x: number | null | undefined) => x == null ? 'text-muted' : x >= 0 ? 'text-bull' : 'text-bear'

function outcomeText(r: JournalRow) {
  const o = r.outcome
  if (!o) return <span className="text-muted">pending</span>
  if (!o.entered) return <span className="text-muted">not entered (price opened outside the buy zone)</span>
  const plan = o.plan === 'target' ? <span className="text-bull">hit target</span> : o.plan === 'stop' ? <span className="text-bear">hit stop</span>
    : o.plan === 'timeout' ? <span className="text-muted">30 sessions, no exit</span> : <span className="text-muted">open</span>
  return <>{plan}{o.plan_net != null && o.plan !== 'open' ? <span className={`num ${tone(o.plan_net)}`}> ({num(o.plan_net)}%)</span> : null}</>
}

export default function Journal() {
  const [d, setD] = useState<JournalData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.journal().then(setD).catch(e => setErr(String(e.message ?? e))) }, [])

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-3">
      <section className="card">
        <div className="panel-title"><h1 className="flex-1">JRN · Forward-test journal <span className="font-normal normal-case text-muted">calls written down after the close, scored later</span></h1></div>
        {err && <div className="p-2"><ErrorBox msg={err} /></div>}
        <div className="grid gap-3 p-3 text-[12px] lg:grid-cols-2">
          <div className="border border-line bg-panel p-2.5">
            <div className="label mb-1">What this is</div>
            <p>Every evening each engine's BUY and WATCH call (with its buy zone, stop and first target) is recorded once and never edited. Later sessions score it with the same fees as the back-tests: buy at the next open if it is inside the zone, then see where price was 5, 20, 40 and 60 sessions later and whether the plan hit its stop or its first target first. Because the calls are made before the outcome exists, this is the honest check on the back-tests.</p>
          </div>
          <div className="border border-amber/50 bg-amber/5 p-2.5">
            <div className="label mb-1 text-amber">How to read it</div>
            <p>Small samples mislead. Treat a group as meaningful only after roughly 100 scored calls, and compare it with the back-test reference (14 years: the average WATCH trade lost about 2.3% after costs). “Rule met” marks calls on stocks that also met the SCR selection rule that day, so you can see whether that rule adds anything going forward.</p>
          </div>
        </div>
        {!d ? <Spinner /> : d.total === 0 ? (
          <p className="p-6 text-center text-muted">Nothing recorded yet. The first entries appear after the next end-of-day run (15:30 Colombo time, weekdays).</p>
        ) : (
          <>
            <div className="border-y border-line px-3 py-1.5 text-[12px]">
              <b className="num text-fg">{d.total}</b> calls recorded since <b className="num text-fg">{d.since}</b> · <b className="num text-fg">{d.scored}</b> fully scored
            </div>
            <div className="scroll-thin overflow-x-auto">
              <table className="bb-table">
                <thead>
                  <tr>
                    <th>Group</th><th className="text-right">Calls</th><th className="text-right">Entered</th>
                    <th className="text-right">20D scored</th><th className="text-right">20D net %</th><th className="text-right">20D win %</th>
                    <th className="hidden text-right md:table-cell">60D scored</th><th className="hidden text-right md:table-cell">60D net %</th>
                    <th className="text-right">Plan: target first %</th><th className="text-right">Plan: stop first %</th><th className="text-right">Plan net %</th>
                    <th>Back-test reference (14 years)</th>
                  </tr>
                </thead>
                <tbody>
                  {d.groups.map(g => (
                    <tr key={g.group}>
                      <td className="font-semibold text-amber">{g.group}</td>
                      <td className="text-right num">{g.calls}</td><td className="text-right num">{g.entered}</td>
                      <td className="text-right num">{g.n20}</td>
                      <td className={`text-right num ${tone(g.mean20)}`}>{num(g.mean20, 2)}</td><td className="text-right num">{num(g.win20, 0, false)}</td>
                      <td className="hidden text-right num md:table-cell">{g.n60}</td>
                      <td className={`hidden text-right num md:table-cell ${tone(g.mean60)}`}>{num(g.mean60, 2)}</td>
                      <td className="text-right num">{num(g.target_first, 0, false)}</td><td className="text-right num">{num(g.stop_first, 0, false)}</td>
                      <td className={`text-right num ${tone(g.plan_mean_net)}`}>{num(g.plan_mean_net, 2)}</td>
                      <td className="whitespace-normal text-muted">{g.reference ? `${g.reference.plan_target_first}% target first, ${g.reference.plan_stop_first}% stop first, avg ${g.reference.mean_net}% net (n=${g.reference.n})` : '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="panel-title mt-2"><h2 className="flex-1">Recent calls</h2></div>
            <div className="scroll-thin overflow-x-auto">
              <table className="bb-table">
                <thead>
                  <tr><th>Date</th><th>Ticker</th><th>Engine</th><th>Call</th><th className="hidden md:table-cell">Setup</th><th className="text-right">Close</th>
                    <th className="hidden text-right md:table-cell">Buy zone</th><th className="hidden text-right md:table-cell">Stop</th><th className="hidden text-right md:table-cell">Target 1</th>
                    <th>Rule met</th><th className="text-right">5D</th><th className="text-right">20D</th><th className="hidden text-right md:table-cell">60D</th><th>Plan outcome</th></tr>
                </thead>
                <tbody>
                  {d.recent.map(r => (
                    <tr key={r.id}>
                      <td className="num">{r.date}</td>
                      <td><Link className="font-semibold text-amber hover:underline" to={`/stock/${r.symbol}`}>{ticker(r.symbol)}</Link></td>
                      <td><EngineBadge e={r.engine} /></td>
                      <td><VerdictBadge v={r.verdict} /></td>
                      <td className="hidden text-muted md:table-cell">{r.setup?.replace(/_/g, ' ') ?? '–'}</td>
                      <td className="text-right num">{fmt(r.close)}</td>
                      <td className="hidden text-right num md:table-cell">{fmt(r.plan.zl)}–{fmt(r.plan.zh)}</td>
                      <td className="hidden text-right num md:table-cell">{fmt(r.plan.stop)}</td>
                      <td className="hidden text-right num md:table-cell">{fmt(r.plan.t1)}</td>
                      <td>{r.rule_ok == null ? <span className="text-muted">–</span> : r.rule_ok ? <span className="text-bull">yes</span> : <span className="text-muted">no</span>}</td>
                      <td className={`text-right num ${tone(r.outcome?.r5)}`}>{num(r.outcome?.r5)}</td>
                      <td className={`text-right num ${tone(r.outcome?.r20)}`}>{num(r.outcome?.r20)}</td>
                      <td className={`hidden text-right num md:table-cell ${tone(r.outcome?.r60)}`}>{num(r.outcome?.r60)}</td>
                      <td className="whitespace-normal">{outcomeText(r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="border-t border-line px-3 py-2 text-[11px] text-muted">Returns are net of fees on both sides. A call that opens outside its buy zone is not counted as a trade. Decision support only, not financial advice.</p>
          </>
        )}
      </section>
    </div>
  )
}
