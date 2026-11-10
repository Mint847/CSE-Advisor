// The quant figure tables with their plain-English explanations (ⓘ), shared by the QNT tab and PORT.
import { Fragment, useState, type ReactNode } from 'react'
import { GLOSSARY } from '../quant/glossary'
import { pctf } from './QuantCharts'

export type Kind = 'pct' | 'share' | 'ratio' | 'count' | 'pctile' | 'lkr'
export interface Group { title: string; src: 'f' | 'r'; rows: [string, Kind][]; optional?: boolean }

/** The figures, grouped, with how each is formatted. */
export const GROUPS: Group[] = [
  { title: 'Return', src: 'f', rows: [['total_return', 'pct'], ['cagr', 'pct'], ['excess_return', 'pct'], ['positive_months', 'share']] },
  { title: 'Risk', src: 'f', rows: [['volatility', 'pct'], ['ewma_volatility', 'pct'], ['max_drawdown', 'pct'], ['current_drawdown', 'pct'], ['var95', 'pct'],
    ['cvar95', 'pct'], ['downside_deviation', 'pct'], ['skew', 'ratio'], ['kurtosis', 'ratio']] },
  { title: 'Return for the risk', src: 'f', rows: [['sharpe', 'ratio'], ['sortino', 'ratio'], ['calmar', 'ratio']] },
  { title: 'Against the benchmark', src: 'r', rows: [['beta', 'ratio'], ['beta_weekly', 'ratio'], ['correlation', 'ratio'], ['r_squared', 'share'], ['jensen_alpha', 'pct'],
    ['information_ratio', 'ratio'], ['tracking_error', 'pct'], ['treynor', 'pct'], ['m2', 'pct'], ['up_capture', 'share'], ['down_capture', 'share']] },
  { title: 'In up and down markets', src: 'r', optional: true, rows: [['jensen_alpha_bull', 'pct'], ['jensen_alpha_bear', 'pct'], ['information_ratio_bull', 'ratio'],
    ['information_ratio_bear', 'ratio'], ['tracking_error_bull', 'pct'], ['tracking_error_bear', 'pct']] },
  { title: 'Habits and today', src: 'f', rows: [['positive_days', 'share'], ['longest_up', 'count'], ['longest_down', 'count'], ['zscore_now', 'ratio'], ['today_move_percentile', 'pctile']] },
]

export const lkr = (v: number) => `LKR ${Math.round(v).toLocaleString()}`

export function show(v: unknown, k: Kind): string {
  if (v == null || typeof v !== 'number' || !Number.isFinite(v)) return '–'
  return k === 'pct' ? pctf(v, 1) : k === 'share' ? pctf(v, 0, false) : k === 'count' ? String(v) : k === 'pctile' ? `${Math.round(v)}th`
    : k === 'lkr' ? lkr(v) : v.toFixed(2)
}

/** A term with its ⓘ button; the explanation opens below (as a table row when `asRow`). */
export function Explain({ k, children }: { k: string; children?: ReactNode }) {
  const [open, setOpen] = useState(false)
  const g = GLOSSARY[k]
  return (
    <span>
      <span className="text-fg">{children ?? g?.name ?? k}</span>
      {g && <button className="ml-1 text-[10px] text-muted hover:text-amber" aria-expanded={open} aria-label={`What is ${g.name}?`} onClick={() => setOpen(o => !o)}>ⓘ</button>}
      {open && g && <Gloss k={k} />}
    </span>
  )
}

function Gloss({ k }: { k: string }) {
  const g = GLOSSARY[k]
  return (
    <span className="mt-1 block bg-panel-2 px-2 py-1.5 text-[11px] font-normal normal-case leading-snug tracking-normal">
      <span className="block"><b className="text-amber">What:</b> {g.what}</span><span className="block"><b className="text-amber">How to read it:</b> {g.read}</span>
      {g.caveat && <span className="block text-warn"><b>Careful:</b> {g.caveat}</span>}
    </span>
  )
}

export function Row({ k, kind, v, missing, extra }: { k: string; kind: Kind; v: unknown; missing?: string; extra?: string }) {
  const [open, setOpen] = useState(false)
  const g = GLOSSARY[k]
  return (
    <>
      <tr className="border-t border-line/60">
        <td className="py-1 pr-2">
          <span className="text-fg">{g?.name ?? k}</span>
          {g && <button className="ml-1 text-[10px] text-muted hover:text-amber" aria-expanded={open} aria-label={`What is ${g.name}?`} onClick={() => setOpen(o => !o)}>ⓘ</button>}
          {extra && <span className="block text-[10px] text-muted">{extra}</span>}
        </td>
        <td className="py-1 text-right num" title={missing}>{v == null && missing ? <span className="text-[10px] text-muted">{missing.startsWith('not meaningful') ? 'not meaningful here' : 'not enough history'}</span> : <b className="text-fg">{show(v, kind)}</b>}</td>
      </tr>
      {open && g && (
        <tr><td colSpan={2} className="bg-panel-2 px-2 py-1.5 text-[11px] leading-snug">
          <p><b className="text-amber">What:</b> {g.what}</p><p><b className="text-amber">How to read it:</b> {g.read}</p>
          {g.caveat && <p className="text-warn"><b>Careful:</b> {g.caveat}</p>}
        </td></tr>
      )}
    </>
  )
}

type Figs = Record<string, unknown> & { missing?: Record<string, string> }

/** The grouped figure cards. `skip` drops whole groups (e.g. 'Habits and today' on PORT). */
export function FigureGroups({ f, r, benchLabel, skip = [] }: { f: Figs; r: Figs; benchLabel: string; skip?: string[] }) {
  const missing = { ...(f.missing ?? {}), ...(r.missing ?? {}) }
  const ddExtra = typeof f.peak === 'string' ? `${f.peak} → ${f.trough}${f.recovered ? `, back by ${f.recovered}` : ', not yet recovered'}` : undefined
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 [&>*]:min-w-0">
      {GROUPS.filter(g => !skip.includes(g.title)).map(g => (
        <section key={g.title} className="card">
          <div className="panel-title"><h3>{g.title}{g.src === 'r' ? <span className="font-normal normal-case text-muted"> · vs {benchLabel}</span> : null}</h3></div>
          <table className="w-full px-2 text-[12px]"><tbody className="[&_td:first-child]:pl-2.5 [&_td:last-child]:pr-2.5">
            {g.rows.map(([k, kind]) => {
              const v = (g.src === 'f' ? f : r)[k] as number | null | undefined
              if (v === undefined && !missing[k] && g.optional) return null
              return <Fragment key={k}><Row k={k} kind={kind} v={v ?? null} missing={missing[k]} extra={k === 'max_drawdown' ? ddExtra : undefined} /></Fragment>
            })}
          </tbody></table>
        </section>
      ))}
    </div>
  )
}
