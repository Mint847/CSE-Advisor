// MKT: world markets beside the CSE (oil, Nasdaq, S&P 500, USD/LKR, gold, Nifty) with the link each one has had to the
// ASPI, world / economy headlines, and the state of every news source. Description only: no engine uses any of it.
import { useEffect, useState } from 'react'
import { api, fmt, type World } from '../api'
import { Section, Sparkline } from './ui'

const pct = (x: number | null | undefined) => x == null ? '–' : `${x >= 0 ? '+' : ''}${x.toFixed(2)}%`
const tone = (x: number | null | undefined) => x == null ? 'text-muted' : x > 0 ? 'text-bull' : x < 0 ? 'text-bear' : 'text-fg'
const VERDICT: Record<string, string> = { held: 'text-bull', one_half: 'text-amber', weak: 'text-muted', none: 'text-muted' }
const THEME: Record<string, string> = {
  oil_energy: 'oil & energy', rupee_fx: 'rupee', rates_inflation: 'rates & inflation', tax_policy: 'tax & budget', imf_debt: 'IMF & debt',
  tourism: 'tourism', global_markets: 'world markets', none: 'other',
}
// Claude Haiku 5.5 list prices per million tokens (input / output)
const COST = (i: number, o: number) => (i * 0.10 + o * 0.50) / 1e6

export default function WorldPanel() {
  const [w, setW] = useState<World | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.world().then(setW).catch(e => setErr(String(e.message ?? e))) }, [])
  if (err) return <Section title="World markets"><p className="text-[12px] text-bear">{err}</p></Section>
  if (!w) return null
  const u = w.reader.usage
  const ev = w.study.events?.us_down_2 as Record<string, number> | undefined
  return (
    <>
      <Section title={`World markets${w.study.period ? ` · link to the ASPI measured ${w.study.period}` : ''}`}>
        {w.overnight?.notes.map(n => (
          <p key={n.series} className="mb-2 border border-amber/50 bg-[#1d1405] px-2 py-1 text-[12px]"><b className="text-amber">OVERNIGHT </b>{n.text}</p>
        ))}
        {w.series.length === 0 ? <p className="text-[12px] text-muted">World series load the first time the app starts (TradingView, about 15 seconds).</p> : (
          <div className="scroll-thin overflow-x-auto">
            <table className="bb-table [&_td]:whitespace-normal">
              <thead><tr><th>Series</th><th className="text-right">Last</th><th className="text-right">1D</th><th className="text-right">1W</th><th className="text-right">1M</th>
                <th className="hidden md:table-cell">60D</th><th>Link to the ASPI</th></tr></thead>
              <tbody>{w.series.map(s => (
                <tr key={s.code}>
                  <td className="whitespace-nowrap"><span className="text-amber">{s.name}</span> <span className="text-[11px] text-muted">{s.date}</span></td>
                  <td className="text-right num">{fmt(s.last, s.last > 1000 ? 0 : 2)}</td>
                  <td className={`text-right num ${tone(s.chg1d)}`}>{pct(s.chg1d)}</td>
                  <td className={`text-right num ${tone(s.chg1w)}`}>{pct(s.chg1w)}</td>
                  <td className={`text-right num ${tone(s.chg1m)}`}>{pct(s.chg1m)}</td>
                  <td className="hidden py-0 md:table-cell">{s.spark && <Sparkline data={s.spark} w={90} h={20} />}</td>
                  <td className={`min-w-[14rem] text-[11px] ${VERDICT[s.verdict ?? 'none']}`}>{s.link ?? 'not measured yet (macro study runs at first start)'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <p className="mt-1 text-[11px] text-muted">
          "Moves with" = weekly or monthly changes in the same period; "ahead of it" = this period's move against the ASPI's next one. A link counts as
          real only if it held in both 2013-19 and 2020-26.{ev?.n ? ` After a US fall of 2% or more, the next CSE session averaged ${pct(ev.next_mean)} (${ev.n} days, 2013-19 ${pct(ev.mean_2013_19)}, 2020-26 ${pct(ev.mean_2020_26)}): a short dip, small next to the 2.6% round-trip cost.` : ''}
          {' '}Shown and recorded in the journal; no engine score uses world markets. Rerun: <code>python -m cse_advisor.macro_study</code>.
        </p>
      </Section>

      <Section title={`World & economy headlines${w.news_tone.tone != null ? ` · Claude's tone for Sri Lankan shares (3 days) ${w.news_tone.tone >= 0 ? '+' : ''}${w.news_tone.tone.toFixed(2)}` : ''}`}>
        {w.news.length === 0 ? <p className="text-[12px] text-muted">Fetched with the evening job (Google News searches on oil, the Sri Lankan economy and world markets, plus CBSL).</p> : (
          <ul className="max-h-[22rem] divide-y divide-line/60 overflow-y-auto scroll-thin">{w.news.map(h => (
            <li key={h.link} className="flex flex-wrap items-start gap-x-2 py-1.5 text-[12px] sm:flex-nowrap">
              <span className="w-28 shrink-0 text-[11px] text-muted num">{h.published?.slice(0, 16).replace('T', ' ')}<div className="truncate">{h.source}</div></span>
              <span className="min-w-0 flex-1">
                <a className="text-fg hover:text-amber hover:underline" href={h.link} target="_blank" rel="noreferrer">{h.title}</a>
                {h.reason && <span className="block text-[11px] text-muted">{h.reason}</span>}
              </span>
              <span className="chip shrink-0 border border-line text-muted">{THEME[h.theme ?? 'none'] ?? h.theme}</span>
              {h.sl_market_tone != null && <span className={`w-10 shrink-0 text-right num ${tone(h.sl_market_tone)}`} title="Claude: what it means for Sri Lankan shares">{h.sl_market_tone > 0 ? '+' : ''}{h.sl_market_tone.toFixed(1)}</span>}
            </li>
          ))}</ul>
        )}
        <p className="mt-1 text-[11px] text-muted">
          Headline reader: {w.reader.enabled ? <b className="text-fg">Claude ({w.reader.model})</b> : <b className="text-fg">keyword rules</b>}
          {w.reader.enabled && u ? ` · today ${u.headlines} headlines, ${u.calls} calls, about $${COST(u.input_tokens, u.output_tokens).toFixed(3)}; so far about $${COST(u.total_input_tokens ?? 0, u.total_output_tokens ?? 0).toFixed(2)}${u.errors ? ` · ${u.errors} failed (keywords used)` : ''}` : ''}
          {!w.reader.enabled && ' (to let Claude read headlines: add ANTHROPIC_API_KEY to backend/.env and set llm.enabled: true in settings.yaml).'}
        </p>
      </Section>

      <Section title="News sources">
        <div className="scroll-thin overflow-x-auto">
          <table className="bb-table [&_td]:whitespace-normal">
            <thead><tr><th>Source</th><th>State</th><th className="text-right">Items</th><th>Last read</th></tr></thead>
            <tbody>
              {Object.entries(w.feeds.feeds).sort(([a], [b]) => a.localeCompare(b)).map(([name, f]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className={f.ok ? 'text-bull' : 'text-bear'}>{f.ok ? 'ok' : f.error ?? 'failed'}</td>
                  <td className="text-right num">{f.items}</td>
                  <td className="text-[11px] text-muted num">{f.last_ok?.replace('T', ' ') ?? 'never'}</td>
                </tr>
              ))}
              {w.feeds.not_used.map(n => <tr key={n.name}><td>{n.name}</td><td colSpan={3} className="text-muted">not used: {n.note}</td></tr>)}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-[11px] text-muted">A site that refuses automated readers is left out, never worked around. Titles and links only; read the article on the source site.</p>
      </Section>
    </>
  )
}
