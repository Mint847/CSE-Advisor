import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { engines, fmt, fmtBig, type Analysis } from '../api'
import { Section, Stat } from './ui'

const REGIME_CLS: Record<string, string> = {
  dormant: 'bg-neutral/30', awakening: 'bg-bull/70', active: 'bg-bull/40', frenzy: 'bg-warn', drying_up: 'bg-bear/50', normal: 'bg-panel-2',
}

export default function LiquidityTab({ a }: { a: Analysis }) {
  const l = a.liquidity
  const lv = a.levels
  const [qty, setQty] = useState<string>(String(engines(a).orig.plan?.size.qty ?? engines(a).anly?.plan?.size.qty ?? 1000))
  const dte = l.adv_volume_20 > 0 ? Number(qty) / (0.2 * l.adv_volume_20) : null
  const series = l.turnover_series.slice(-120)
  const tl = l.timeline
  const t0 = tl.length ? new Date(tl[0].start).getTime() : 0
  const t1 = tl.length ? new Date(tl[tl.length - 1].end).getTime() : 1
  const span = Math.max(1, t1 - t0)

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title={`Tradability: grade ${l.grade}`}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="Median turnover 60d" value={`LKR ${fmtBig(l.median_turnover_60)}`} />
          <Stat label="Avg volume 20d" value={fmtBig(l.adv_volume_20)} sub="shares / day" />
          <Stat label="Days traded" value={`${l.days_traded_pct.toFixed(0)}%`} sub="of last 60 sessions" />
          <Stat label="Spread (est.)" value={l.spread_est_pct != null ? `${l.spread_est_pct.toFixed(2)}%` : '–'} sub="Corwin–Schultz" />
          <Stat label="Amihud" value={l.amihud != null ? l.amihud.toFixed(3) : '–'} sub="price impact per LKR 1M" />
          <Stat label="Regime" value={l.regime.replace('_', ' ')} />
        </div>
        <ul className="mt-3 list-disc pl-5 text-sm text-muted">{l.grade_notes.map(n => <li key={n}>{n}</li>)}</ul>
        <div className="mt-3 bg-panel-2 p-3 text-sm">
          <label className="flex flex-wrap items-center gap-2">Days to exit
            <input className="input w-28 num" inputMode="numeric" value={qty} onChange={e => setQty(e.target.value.replace(/\D/g, ''))} aria-label="Quantity" /> shares
            <b className="num">≈ {dte != null ? dte.toFixed(1) : '–'} sessions</b>
          </label>
          <div className="mt-1 text-xs text-muted">Assumes you sell no more than 20% of the average daily volume each session.</div>
        </div>
      </Section>

      <Section title={`Money flow: ${l.flow.label} (${l.flow.score.toFixed(0)}/100)`}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="Up / down volume" value={`${l.flow.up_down_volume_ratio.toFixed(2)}×`} sub="last 20 sessions" tone={l.flow.up_down_volume_ratio >= 1.2 ? 'bull' : l.flow.up_down_volume_ratio <= 0.8 ? 'bear' : undefined} />
          <Stat label="Turnover 5d / 20d" value={`${l.flow.turnover_5_vs_20.toFixed(2)}×`} />
          <Stat label="Turnover 20d / 60d" value={`${l.flow.turnover_20_vs_60.toFixed(2)}×`} />
          <Stat label="OBV trend" value={l.flow.obv_slope_norm > 0 ? 'rising' : 'falling'} tone={l.flow.obv_slope_norm > 0 ? 'bull' : 'bear'} />
          <Stat label="Price 5d" value={`${l.flow.price_change_5d_pct.toFixed(1)}%`} />
          <Stat label="Price 20d" value={`${l.flow.price_change_20d_pct.toFixed(1)}%`} />
        </div>
        {l.flow.avg_trade_size && <p className="mt-2 text-sm text-muted">Average trade size LKR {fmtBig(l.flow.avg_trade_size.last5)} (5d) vs LKR {fmtBig(l.flow.avg_trade_size.last20)} (20d). Bigger trades suggest institutional buying; many tiny trades suggest a retail rush.</p>}
        <p className="mt-2 text-sm">{l.regime_text}</p>
      </Section>

      <Section title="Liquidity regime timeline">
        <div className="flex h-6 w-full overflow-hidden" role="img" aria-label="Liquidity regime over time">
          {tl.map((seg, i) => {
            const w = ((new Date(seg.end).getTime() - new Date(seg.start).getTime() + 86400000) / (span + 86400000)) * 100
            return <div key={i} title={`${seg.state.replace('_', ' ')}: ${seg.start} → ${seg.end}`} className={`${REGIME_CLS[seg.state] ?? 'bg-panel-2'} border-r-2 border-panel`} style={{ width: `${w}%` }} />
          })}
        </div>
        <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted">
          {Object.keys(REGIME_CLS).map(k => <span key={k} className="inline-flex items-center gap-1"><span className={`inline-block h-2.5 w-2.5 ${REGIME_CLS[k]}`} />{k.replace('_', ' ')}</span>)}
        </div>
      </Section>

      <Section title="Daily turnover (LKR), last 120 sessions">
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={series} margin={{ top: 4, right: 4, left: 0, bottom: 0 }} barCategoryGap={1}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="time" tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={40} />
              <YAxis tickFormatter={v => fmtBig(v)} tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} width={44} />
              <Tooltip formatter={v => [`LKR ${fmtBig(Number(v))}`, 'Turnover']} cursor={{ fill: 'var(--panel-2)' }}
                       contentStyle={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 0, color: 'var(--text)', fontSize: 12 }} />
              <Bar dataKey="value" fill="var(--series-1)" radius={0} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Section>

      <Section title="Support / resistance zones">
        <table className="w-full text-sm num">
          <thead className="text-xs text-muted"><tr className="text-left"><th className="py-1">Zone</th><th>Kind</th><th>Touches</th><th>Last touch</th><th>Volume share</th></tr></thead>
          <tbody>
            {[...lv.zones].reverse().map(z => (
              <tr key={z.price} className="border-t border-line/60">
                <td className="py-1">{fmt(z.low)} – {fmt(z.high)}</td>
                <td className={z.kind === 'support' ? 'text-support' : 'text-resistance'}>{z.kind}</td>
                <td>{z.touches}</td><td>{z.last_touch}</td><td>{(z.volume_share * 100).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Volume profile & liquidity pools">
        <div className="grid grid-cols-3 gap-2">
          <Stat label="POC" value={fmt(lv.volume_profile.poc)} sub={`last ${lv.volume_profile.lookback} bars`} />
          <Stat label="Value area low" value={fmt(lv.volume_profile.va_low)} />
          <Stat label="Value area high" value={fmt(lv.volume_profile.va_high)} />
        </div>
        <p className="mt-2 text-sm text-muted">High-volume nodes (price magnets / support-resistance): {lv.volume_profile.hvn.map(x => fmt(x)).join(', ') || '–'}. Low-volume nodes (price tends to move fast through them): {lv.volume_profile.lvn.map(x => fmt(x)).join(', ') || '–'}.</p>
        <ul className="mt-2 space-y-0.5 text-sm">
          {lv.pools.map((p, i) => (
            <li key={i}><b className="num">{fmt(p.price)}</b> <span className="text-muted">{p.kind === 'buy_stops_above' ? 'untaken swing high: buy stops sit above' : p.kind === 'sell_stops_below' ? 'untaken swing low: sell stops sit below' : 'round number'}{p.time ? ` (${p.time})` : ''}</span></li>
          ))}
        </ul>
      </Section>
    </div>
  )
}
