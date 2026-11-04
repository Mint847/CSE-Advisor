import type { ReactNode } from 'react'
import type { Analysis, Corporate, EngineRec, Reason, Signals, SmcContext } from '../api'
import { engines, fmt } from '../api'
import { AgreeBadge, ENGINE_TEXT, EngineBadge, ScoreBar, Section, VerdictBadge } from './ui'

export type RecView = 'original' | 'analyst' | 'smc' | 'compare'
type Eng = 'original' | 'analyst' | 'smc'
const ENGINE_NAME: Record<Eng, string> = { original: 'Original engine', analyst: 'Analyst engine', smc: 'SMC engine' }

function SmcBox({ c, close }: { c: SmcContext; close: number }) {
  const ev = (e: SmcContext['last']) => e ? `${e.kind.toUpperCase()} ${e.dir === 'up' ? '↑' : '↓'} ${fmt(e.level)} · ${e.date}` : 'no break yet'
  const tone = (b: string) => b === 'bullish' ? 'text-bull' : b === 'bearish' ? 'text-bear' : 'text-fg'
  const r = c.range
  return (
    <Section title="Smart-money context" className="lg:col-span-2">
      <div className="grid gap-3 md:grid-cols-2 [&>*]:min-w-0">
        <table className="bb-table [&_td]:whitespace-normal"><tbody>
          <tr><td className="text-muted">Weekly bias (HTF)</td><td className={`text-right font-semibold uppercase ${tone(c.htf_bias)}`}>{c.htf_bias}</td></tr>
          <tr><td className="text-muted">Last weekly break</td><td className="text-right num">{ev(c.htf_last)}</td></tr>
          <tr><td className="text-muted">Daily structure</td><td className={`text-right font-semibold uppercase ${tone(c.bias)}`}>{c.bias}</td></tr>
          <tr><td className="text-muted">Last daily break</td><td className="text-right num">{ev(c.last)}</td></tr>
          {r && <tr><td className="text-muted">Dealing range ({r.dir === 'up' ? 'up' : 'down'} leg)</td><td className="text-right num">{fmt(r.low)} → {fmt(r.high)}</td></tr>}
          {r && <tr><td className="text-muted">Equilibrium (0.5)</td><td className="text-right font-semibold text-amber num">{fmt(r.eq)}</td></tr>}
          {r && <tr><td className="text-muted">Price {fmt(close)} is in</td><td className={`text-right font-semibold uppercase num ${r.zone === 'discount' ? 'text-bull' : 'text-bear'}`}>{r.zone} · {(r.pos * 100).toFixed(0)}%{r.in_ote ? ' · OTE' : ''}</td></tr>}
          {r && <tr><td className="text-muted">OTE band (0.62–0.79)</td><td className="text-right num">{fmt(r.ote_low)}–{fmt(r.ote_high)}</td></tr>}
          <tr><td className="text-muted">Buy-side liquidity above</td><td className="text-right num">{c.liquidity_above.map(x => fmt(x)).join(' · ') || '–'}</td></tr>
          <tr><td className="text-muted">Sell-side liquidity below</td><td className="text-right num">{c.liquidity_below.map(x => fmt(x)).join(' · ') || '–'}</td></tr>
        </tbody></table>
        <div className="space-y-2">
          <table className="bb-table [&_td]:whitespace-normal">
            <thead><tr><th>Block / gap</th><th className="text-right">Range</th><th>State</th></tr></thead>
            <tbody>
              {[...c.obs.map(o => ({ k: `OB ${o.date}`, name: `${o.dir === 'up' ? '▲ Bullish' : '▼ Bearish'} OB`, dir: o.dir, low: o.low, high: o.high,
                  st: (o.status === 'fresh' ? 'fresh' : `tested ${o.touches}×`) + (o.swept ? ' · swept liq.' : '') + (o.fvg ? ' · FVG' : '') })),
                ...c.fvgs.map(f => ({ k: `FVG ${f.date}`, name: `${f.dir === 'up' ? '▲ Bullish' : '▼ Bearish'} FVG`, dir: f.dir, low: f.low, high: f.high, st: f.status }))]
                .sort((x, y) => y.high - x.high).map(x => (
                  <tr key={x.k}><td className={x.dir === 'up' ? 'text-bull' : 'text-bear'}>{x.name}</td><td className="text-right num">{fmt(x.low)}–{fmt(x.high)}</td><td className="text-muted">{x.st}</td></tr>
                ))}
              {c.obs.length + c.fvgs.length === 0 && <tr><td colSpan={3} className="text-muted">No open blocks or gaps near price.</td></tr>}
            </tbody>
          </table>
          {c.sweeps.length > 0 && <p className="text-[11px] text-muted">Recent sweeps: {c.sweeps.map(s => `${s.side === 'sell' ? 'sell-side' : 'buy-side'} ${fmt(s.level)} (${s.date})`).join(' · ')}</p>}
        </div>
      </div>
    </Section>
  )
}
interface Props { a: Analysis; view: RecView; onView: (v: RecView) => void; onPattern: (id: string) => void }

function SignalsBox({ s, corp }: { s: Signals; corp: Corporate | undefined }) {
  const row = (label: string, value: string, tone: 'bull' | 'bear' | 'muted' = 'muted') => (
    <tr><td className="text-muted">{label}</td><td className={`text-right font-semibold num ${tone === 'bull' ? 'text-bull' : tone === 'bear' ? 'text-bear' : 'text-fg'}`}>{value}</td></tr>
  )
  const e = s.extension, m = s.momentum, v = s.volume
  return (
    <Section title="Momentum, volume & risk checks">
      <table className="bb-table"><tbody>
        {row('Stretch vs 21-day EMA', `${(e.pct_above_ema21 ?? 0) >= 0 ? '+' : ''}${(e.pct_above_ema21 ?? 0).toFixed(1)}%${e.flag ? ' · over-extended' : ''}`, e.flag ? 'bear' : 'muted')}
        {row('RSI (14) / its average', `${m.rsi?.toFixed(0) ?? '–'} / ${m.rsi_ma?.toFixed(0) ?? '–'}${m.cross_above_50 ? ` · crossed 50 on ${m.cross_above_50}` : ''}`, m.above_rsi_ma ? 'bull' : 'muted')}
        {m.oversold_turn && row('Oversold turn', 'RSI turning up from < 32', 'bull')}
        {row('Volume trend (20d)', v.volume_trend + (v.high_volume_streak >= 2 ? ` · ${v.high_volume_streak} heavy days in a row` : ''), v.volume_trend === 'rising' ? 'bull' : v.volume_trend === 'declining' ? 'bear' : 'muted')}
        {v.down_volume_rising && row('Selling volume', 'rising (distribution)', 'bear')}
        {row('Drawdown from peak', `${s.drawdown.pct.toFixed(0)}% (peak ${s.drawdown.peak.toFixed(2)} on ${s.drawdown.peak_date})`, s.drawdown.cooling_off ? 'bull' : 'muted')}
        {row('Volatility', s.volatility.guard ? 'extreme: wait for direction' : 'normal', s.volatility.guard ? 'bear' : 'muted')}
        {s.today_vap && row(`Today's busiest price`, `${s.today_vap.top.price.toFixed(2)} (${(s.today_vap.top.share * 100).toFixed(0)}% of volume)`)}
        {v.block_trade_days.length > 0 && row('Likely crossings (ignored)', v.block_trade_days.slice(-3).join(', '))}
        {corp?.phase && row('Rights issue', corp.phase === 'pre_xr' ? `XR ahead · TERP ${corp.terp_today?.toFixed(2) ?? '?'}` : 'supply overhang', 'bear')}
        {corp?.pre_rights_equivalent != null && row('Pre-rights equivalent price', corp.pre_rights_equivalent.toFixed(2))}
      </tbody></table>
    </Section>
  )
}

function ReasonList({ items, tone, onPattern }: { items: Reason[]; tone: 'bull' | 'bear'; onPattern: (id: string) => void }) {
  return (
    <ul className="space-y-1.5">
      {items.length === 0 && <li className="text-muted">None.</li>}
      {items.map((r, i) => (
        <li key={i} className="flex gap-2 leading-relaxed">
          <span className={`shrink-0 ${tone === 'bull' ? 'text-bull' : 'text-bear'}`}>{tone === 'bull' ? '+' : '−'}</span>
          <span>
            {r.text}
            {r.pattern_id && <button className="ml-1 text-[11px] text-blue underline" onClick={() => onPattern(r.pattern_id!)}>show on chart</button>}
            <span className="ml-2 text-[10px] uppercase text-muted">{r.factor.replace('_', ' & ')}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

const List = ({ items }: { items: string[] }) => <ul className="space-y-1">{items.map((t, i) => <li key={i} className="flex gap-2"><span className="text-amber">›</span><span>{t}</span></li>)}</ul>

function EngineView({ r, engine, a, onPattern }: { r: EngineRec; engine: Eng; a: Analysis; onPattern: (id: string) => void }) {
  const ex = r.explanation
  const plan = r.plan
  const rv = r.position_review
  const tone = ENGINE_TEXT[engine]
  return (
    <div className="grid gap-2 lg:grid-cols-2 [&>*]:min-w-0">
      <section className="card lg:col-span-2">
        <div className="panel-title"><EngineBadge e={engine} /><span className="flex-1">{ENGINE_NAME[engine]} · summary</span>
          <span className="font-normal normal-case text-muted">{r.date} close · generated {new Date(a.created_at + 'Z').toLocaleString()}</span></div>
        <div className="p-3">
          <div className="mb-2 flex items-center gap-3"><VerdictBadge v={r.verdict} big /><span className="text-[18px] font-semibold num">{r.score.toFixed(0)}<span className="text-[12px] text-muted">/100</span></span></div>
          <p className="text-[13px] leading-relaxed">{ex.summary}</p>
          {rv && (
            <div className="mt-2 border border-line bg-panel-2 p-2.5">
              <span className="label">Your position</span> {rv.quantity.toLocaleString()} @ {fmt(rv.entry)} → {fmt(rv.close)} (<span className={rv.pl_pct >= 0 ? 'text-bull' : 'text-bear'}>{rv.pl_pct >= 0 ? '+' : ''}{rv.pl_pct.toFixed(2)}%, LKR {rv.pl_lkr.toLocaleString()}</span>).
              {' '}<b>{rv.action}</b>: {rv.reasons.join('; ')}. Suggested stop {fmt(rv.suggested_stop)}{rv.target ? `, target ${fmt(rv.target)}` : ''}.
            </div>
          )}
        </div>
      </section>

      {engine === 'smc' && r.smc && <SmcBox c={r.smc} close={r.close} />}

      <Section title={engine === 'original' ? 'Score breakdown (weighted factors)' : engine === 'smc' ? 'SMC score (starts at 50)' : 'Level score (starts at 50)'}>
        {engine === 'original' ? (
          <div className="space-y-2">
            {ex.score_breakdown.map(c => (
              <div key={c.label}>
                <div className="mb-0.5 flex justify-between gap-2 text-[12px]">
                  <span>{c.label}</span>
                  <span className="shrink-0 text-muted">{c.available ? (c.score == null ? `${c.contribution >= 0 ? '+' : ''}${c.contribution.toFixed(1)} pts` : `weight ${(c.weight * 100).toFixed(0)}% · ${c.contribution >= 0 ? '+' : ''}${c.contribution.toFixed(1)} pts`) : 'no data (excluded)'}</span>
                </div>
                {c.score != null && <ScoreBar score={c.score} label={c.label} />}
              </div>
            ))}
          </div>
        ) : (
          <table className="bb-table"><tbody>
            {ex.score_breakdown.map((c, i) => (
              <tr key={i}><td className="whitespace-normal">{c.label}</td>
                <td className={`text-right font-semibold num ${i === 0 ? 'text-fg' : c.contribution > 0 ? 'text-bull' : 'text-bear'}`}>{i === 0 ? c.contribution : `${c.contribution > 0 ? '+' : ''}${c.contribution}`}</td></tr>
            ))}
            <tr><td className="text-amber">{engine === 'smc' ? 'SMC score' : 'Level score'}</td><td className="text-right font-semibold text-amber num">{r.score.toFixed(0)}</td></tr>
          </tbody></table>
        )}
      </Section>

      <Section title={`Checklist for a BUY (${r.checklist.filter(c => c.passed).length}/${r.checklist.length})`}>
        <table className="bb-table"><tbody>
          {r.checklist.map(c => (
            <tr key={c.label}>
              <td className={c.passed ? 'text-bull' : 'text-bear'} aria-label={c.passed ? 'passed' : 'failed'}>{c.passed ? '✓' : '✗'}</td>
              <td className="w-full whitespace-normal">{c.label}</td>
              <td className="min-w-36 whitespace-normal text-right text-muted num">{c.value}</td>
            </tr>
          ))}
        </tbody></table>
      </Section>

      {(ex.entries?.length ?? 0) > 0 && (
        <Section title={engine === 'smc' ? 'Entry plan: order block / fair value gap / break & retest / weekly OB' : 'Entry plan: Entry 1 / Entry 2 / Zone 2'} className="lg:col-span-2">
          <div className="scroll-thin overflow-x-auto">
            <table className="bb-table min-w-[760px]">
              <thead><tr><th>Entry</th><th>Buy zone</th><th>Limit</th><th>Trigger</th><th>Stop</th><th>Take profit</th><th>R:R</th><th>Why</th></tr></thead>
              <tbody>
                {ex.entries!.map(e => (
                  <tr key={e.label} className={`align-top ${e.actionable ? '' : 'opacity-75'}`}>
                    <td className="font-semibold text-amber">{e.label}<div className="text-[11px] font-normal text-muted">{e.setup}{e.triggered ? ' · triggered' : ''}</div></td>
                    <td className="font-semibold text-bull num">{e.zone}</td>
                    <td className="num">{fmt(e.limit)}</td>
                    <td className="text-amber num">{e.trigger ? `close > ${fmt(e.trigger)}` : '–'}</td>
                    <td className="text-bear num">{fmt(e.stop)}</td>
                    <td className={`${tone} num`}>{e.targets.map(t => fmt(t)).join(' / ') || '–'}</td>
                    <td className={`num ${e.rr >= 2 ? 'text-bull' : 'text-muted'}`}>{e.rr.toFixed(2)}</td>
                    <td className="min-w-[260px] whitespace-normal text-[11px] text-muted">{e.why}{e.label === 'Zone 2' ? ' Only if Zone 1 breaks.' : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-muted">
            {engine === 'smc'
              ? 'Limits sit at the top of an order block (the proximal line) or the middle of a fair value gap; the stop goes under the block. Targets are the buy-side liquidity above (untaken swing highs, equal highs) and bearish blocks / gaps overhead. '
              : 'Each entry is a separate trade with its own stop; Zone 2 is never an add-on to a losing position. '}
            R:R is after brokerage and slippage on both sides; the engine only says BUY at R:R ≥ 2.</p>
        </Section>
      )}

      {(ex.ladder?.length ?? 0) > 0 && (
        <Section title="Level ladder: what happens at each level">
          <ul className="space-y-1">
            {ex.ladder!.map((l, i) => {
              const isNow = i > 0 && ex.ladder![i - 1].side === 'resistance' && l.side === 'support'
              return (
                <li key={i}>
                  {isNow && <div className="my-1 border-t border-dashed border-amber text-[11px] text-amber">PRICE {fmt(r.close)}</div>}
                  <div className="flex gap-2">
                    <b className={`w-28 shrink-0 num ${l.side === 'support' ? 'text-bull' : 'text-bear'}`}>{fmt(l.low)}–{fmt(l.high)}</b>
                    <span><span className="text-muted">{l.sources.slice(0, 3).join(' + ')}.</span> {l.scenario}</span>
                  </div>
                </li>
              )
            })}
          </ul>
          {ex.standing_note && <p className="mt-2 text-[11px] text-muted">{ex.standing_note}</p>}
        </Section>
      )}
      {engine === 'smc' && ex.standing_note && <Section title="Kill zones"><p className="text-[12px] text-muted">{ex.standing_note}</p></Section>}
      {engine === 'analyst' && r.signals && <SignalsBox s={r.signals} corp={r.corporate} />}

      <Section title="Case FOR"><ReasonList items={ex.reasons_for} tone="bull" onPattern={onPattern} /></Section>
      <Section title="Case AGAINST / risks"><ReasonList items={ex.reasons_against} tone="bear" onPattern={onPattern} /></Section>

      {plan ? (
        <>
          <Section title={`Buy prices: how they were calculated${plan.triggered ? '' : ' (not triggered yet)'}`}>
            <table className="bb-table"><tbody>
              {ex.buy_prices.map(x => (
                <tr key={x.label} className="align-top">
                  <td className="text-amber">{x.label}</td>
                  <td className="font-semibold text-bull num">{x.price}</td>
                  <td className="whitespace-normal text-muted">{x.how}</td>
                </tr>
              ))}
            </tbody></table>
          </Section>
          <Section title="Sell prices: stop-loss & targets">
            <table className="bb-table"><tbody>
              {ex.sell_prices.map(x => (
                <tr key={x.label} className="align-top">
                  <td className="text-amber">{x.label}</td>
                  <td className={`font-semibold num ${x.label === 'Stop-loss' ? 'text-bear' : tone}`}>
                    {x.price}{x.net_pct != null && <div className="text-[11px] font-normal text-muted">{x.net_pct >= 0 ? '+' : ''}{x.net_pct.toFixed(1)}% net</div>}
                  </td>
                  <td className="whitespace-normal text-muted">{x.how}</td>
                </tr>
              ))}
            </tbody></table>
            <p className="mt-2 text-[11px] text-muted">After T1: sell part, move the stop to breakeven, then trail the rest ({plan.trailing.method}). Costs: {plan.cost_per_side_pct}% per side (settings.yaml).</p>
          </Section>
          {ex.sizing && <Section title="Position size & liquidity check"><p className="leading-relaxed">{ex.sizing}</p></Section>}
          {ex.scenarios.length > 0 && (
            <Section title="Scenarios">
              <ul className="space-y-1.5">{ex.scenarios.map(s => <li key={s.name}><b className={s.name === 'Bull' ? 'text-bull' : s.name === 'Bear' ? 'text-bear' : 'text-amber'}>{s.name.toUpperCase()}:</b> {s.text}</li>)}</ul>
            </Section>
          )}
        </>
      ) : (
        <Section title="Trade plan"><p className="text-muted">No setup right now, so there are no buy/sell levels from this engine.</p></Section>
      )}

      {ex.invalidation.length > 0 && <Section title="What would change this view"><List items={ex.invalidation} /></Section>}
      <Section title="What to watch next session"><List items={ex.watch_next} /></Section>
      <Section title="Changes since the last report"><List items={ex.changes} /></Section>
    </div>
  )
}

function CompareView({ o, n, s, a }: { o: EngineRec; n: EngineRec | null; s: EngineRec | null; a: Analysis }) {
  const ag = engines(a).agreement
  const cell = (r: EngineRec | null, f: (r: EngineRec) => ReactNode) => <td className="whitespace-normal break-words align-top">{r ? f(r) : <span className="text-muted">–</span>}</td>
  const planVal = (r: EngineRec, k: (p: NonNullable<EngineRec['plan']>) => ReactNode) => r.plan ? k(r.plan) : <span className="text-muted">no setup</span>
  const rows: [string, (r: EngineRec) => ReactNode][] = [
    ['Verdict', r => <span className="inline-flex items-center gap-2"><VerdictBadge v={r.verdict} big /><b className="num">{r.score.toFixed(0)}</b><span className="text-muted">/100</span></span>],
    ['Setup', r => planVal(r, p => <>{p.entry_label ? <b className="text-amber">{p.entry_label}: </b> : null}{p.setup_name}{p.triggered ? ' (triggered)' : ' (waiting)'}</>)],
    ['Buy zone', r => planVal(r, p => <b className="text-bull num">{fmt(p.buy_zone.low)}–{fmt(p.buy_zone.high)}</b>)],
    ['Entry', r => planVal(r, p => <span className="num">{p.trigger ? <>after close &gt; <b className="text-amber">{fmt(p.trigger)}</b>, </> : null}limit <b>{fmt(p.ideal)}</b>, max {fmt(p.max_chase)}</span>)],
    ['Stop', r => planVal(r, p => <b className="text-bear num">{fmt(p.stop)} <span className="font-normal text-muted">({p.stop_net_pct.toFixed(1)}% net)</span></b>)],
    ['T1 / T2', r => planVal(r, p => <span className="num">{p.targets.slice(0, 2).map(t => `${fmt(t.price)} (${t.net_pct >= 0 ? '+' : ''}${t.net_pct.toFixed(1)}%)`).join(' / ') || '–'}</span>)],
    ['Reward:risk', r => planVal(r, p => <b className={`num ${p.reward_risk >= 2 ? 'text-bull' : 'text-muted'}`}>{p.reward_risk.toFixed(2)}{p.actionable ? '' : ' · not actionable'}</b>)],
    ['Checklist', r => <span>{r.checklist.filter(c => c.passed).length}/{r.checklist.length} passed{r.checklist.some(c => !c.passed) ? <span className="text-muted">: missing {r.checklist.filter(c => !c.passed).map(c => c.label).slice(0, 3).join('; ')}</span> : null}</span>],
    ['Top reasons for', r => <ul className="space-y-0.5">{r.explanation.reasons_for.slice(0, 3).map((x, i) => <li key={i}><span className="text-bull">+</span> {x.text.split('. ')[0]}</li>)}</ul>],
    ['Top reasons against', r => <ul className="space-y-0.5">{r.explanation.reasons_against.slice(0, 3).map((x, i) => <li key={i}><span className="text-bear">−</span> {x.text.split('. ')[0]}</li>)}</ul>],
    ['Summary', r => <span className="text-muted">{r.explanation.summary}</span>],
  ]
  return (
    <section className="card">
      <div className="panel-title"><span className="flex-1">CMP · Original vs Analyst vs SMC</span><AgreeBadge a={ag} /></div>
      {ag && <p className="border-b border-line px-3 py-1.5 text-[12px]">{ag.text} {ag.state !== 'agree' ? 'When they disagree, read each case below before acting; no engine has shown a reliable edge in the walk-forward tests.' : ''}</p>}
      <div className="scroll-thin overflow-x-auto">
        <table className="bb-table min-w-[960px] table-fixed">
          <colgroup><col className="w-32" /><col /><col /><col /></colgroup>
          <thead><tr><th></th><th><EngineBadge e="original" /> Original</th><th><EngineBadge e="analyst" /> Analyst</th><th><EngineBadge e="smc" /> SMC</th></tr></thead>
          <tbody>
            {rows.map(([label, f]) => <tr key={label}><td className="align-top text-amber">{label}</td>{cell(o, f)}{cell(n, f)}{cell(s, f)}</tr>)}
            {/* shared by all three: evidence beside the engines, not part of their scores */}
            {a.recommendation.catalysts && (
              <tr><td className="align-top text-amber">Catalysts</td>
                <td colSpan={3} className="whitespace-normal">
                  <span className={`chip mr-2 ${a.recommendation.catalysts.overall === 'Supportive' ? 'bg-bull text-black' : a.recommendation.catalysts.overall === 'Cautious' ? 'bg-bear text-black' : 'bg-amber text-black'}`}>
                    {a.recommendation.catalysts.overall.toUpperCase()}</span>
                  {a.recommendation.catalysts.reasons.join(' · ') || <span className="text-muted">no strong news, results or event signal</span>}
                  {a.recommendation.catalysts.risk_lines.map(x => <div key={x} className="text-[11px] text-warn">! {x}</div>)}
                  <div className="text-[11px] text-muted">News, results and events (CN tab). Shown beside the engines, not part of their scores.</div>
                </td></tr>
            )}
            {a.recommendation.relative_strength && a.recommendation.relative_strength.label !== 'n/a' && (
              <tr><td className="align-top text-amber">Relative strength</td>
                <td colSpan={3} className="whitespace-normal">{a.recommendation.relative_strength.text}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default function RecommendationTab({ a, view, onView, onPattern }: Props) {
  const { orig, anly, smc } = engines(a)
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1" role="radiogroup" aria-label="Engine view">
        {([['compare', 'CMP · Compare'], ['original', 'ORIG · Original'], ['analyst', 'ANLY · Analyst'], ['smc', 'SMC · Smart money']] as [RecView, string][]).map(([v, label]) => (
          <button key={v} role="radio" aria-checked={view === v} className="btn" onClick={() => onView(v)}
                  disabled={(v === 'analyst' && !anly) || (v === 'smc' && !smc)}>{label}</button>
        ))}
      </div>
      {view === 'compare' ? <CompareView o={orig} n={anly} s={smc} a={a} />
        : view === 'analyst' && anly ? <EngineView r={anly} engine="analyst" a={a} onPattern={onPattern} />
        : view === 'smc' && smc ? <EngineView r={smc} engine="smc" a={a} onPattern={onPattern} />
        : <EngineView r={orig} engine="original" a={a} onPattern={onPattern} />}
      <p className="mt-3 text-[11px] text-muted">Decision support for personal use, not financial advice. Verify with your broker and paper-trade before using real money.</p>
    </div>
  )
}
