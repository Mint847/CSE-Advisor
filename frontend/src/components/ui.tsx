import type { ReactNode } from 'react'
import type { Agreement, Verdict } from '../api'

const VERDICT_CLS: Record<string, string> = {
  BUY: 'bg-bull text-black', HOLD: 'bg-blue text-black', WATCH: 'bg-amber text-black',
  TRIM: 'bg-orange-400 text-black', SELL: 'bg-bear text-black', AVOID: 'bg-bear text-black',
}

export function VerdictBadge({ v, big = false }: { v: Verdict | null | undefined; big?: boolean }) {
  if (!v) return <span className="chip bg-panel-3 text-muted">N/A</span>
  return <span className={`chip ${VERDICT_CLS[v]} ${big ? '!px-2.5 !text-sm !leading-6' : ''}`}>{v}</span>
}

/** CLS / LVL / SMC tag so every number says which engine produced it. */
export function EngineBadge({ e }: { e: 'original' | 'analyst' | 'smc' }) {
  return e === 'original'
    ? <span className="chip border border-plan/60 text-plan" title="Classic engine: chart & candlestick patterns + 5-factor score">CLS</span>
    : e === 'analyst'
    ? <span className="chip border border-plan-anly text-plan-anly" title="Levels engine: Fibonacci, confluence zones, level entries">LVL</span>
    : <span className="chip border border-plan-smc text-plan-smc" title="SMC engine: order blocks, premium/discount, fair value gaps, liquidity sweeps">SMC</span>
}

export const ENGINE_TEXT: Record<'original' | 'analyst' | 'smc', string> = {
  original: 'text-plan', analyst: 'text-plan-anly', smc: 'text-plan-smc',
}

const AGREE: Record<string, { icon: string; cls: string; word: string }> = {
  agree: { icon: '●', cls: 'text-bull', word: 'AGREE' },
  mixed: { icon: '◐', cls: 'text-amber', word: 'MIXED' },
  differ: { icon: '○', cls: 'text-bear', word: 'DIFFER' },
}
export function AgreeBadge({ a, compact = false }: { a: Agreement | null | undefined; compact?: boolean }) {
  if (!a) return <span className="text-muted">–</span>
  const x = AGREE[a.state]
  return <span className={`chip ${x.cls}`} title={a.text}>{x.icon}{compact ? '' : ` ${x.word}`}</span>
}

export function ScoreBar({ score, label }: { score: number | null; label?: string }) {
  const s = score ?? 0
  const col = s >= 70 ? 'var(--bull)' : s >= 55 ? 'var(--amber)' : s >= 40 ? 'var(--neutral)' : 'var(--bear)'
  return (
    <div className="flex items-center gap-2" aria-label={label ? `${label} ${s.toFixed(0)} of 100` : undefined}>
      <div className="h-1.5 flex-1 overflow-hidden bg-panel-3">
        <div className="h-full" style={{ width: `${score == null ? 0 : Math.max(2, s)}%`, background: col }} />
      </div>
      <span className="w-8 text-right text-xs font-semibold num">{score == null ? '–' : s.toFixed(0)}</span>
    </div>
  )
}

export function Sparkline({ data, w = 120, h = 32 }: { data: number[]; w?: number; h?: number }) {
  if (data.length < 2) return <svg width={w} height={h} />
  const min = Math.min(...data), max = Math.max(...data), rng = max - min || 1
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - 2 - ((v - min) / rng) * (h - 4)}`).join(' ')
  const up = data[data.length - 1] >= data[0]
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={up ? 'var(--bull)' : 'var(--bear)'} strokeWidth="1.25" strokeLinejoin="round" />
    </svg>
  )
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'bull' | 'bear' | 'warn' }) {
  return (
    <div className="border border-line bg-panel px-2.5 py-1.5">
      <div className="label">{label}</div>
      <div className={`text-[15px] font-semibold num ${tone === 'bull' ? 'text-bull' : tone === 'bear' ? 'text-bear' : tone === 'warn' ? 'text-amber' : 'text-fg'}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  )
}

export function Section({ title, children, right, className = '' }: { title: ReactNode; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      <div className="panel-title"><h3 className="flex-1">{title}</h3>{right}</div>
      <div className="p-3">{children}</div>
    </section>
  )
}

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return <div className="flex items-center gap-2 p-6 text-sm text-amber"><span className="h-3 w-3 animate-pulse bg-amber" />{label.toUpperCase()}</div>
}

export function ErrorBox({ msg }: { msg: string }) {
  return <div role="alert" className="border border-bear/60 bg-bear/10 p-2.5 text-sm text-bear">{msg}</div>
}

/** On/off switch (button with role=switch), larger hit target than a checkbox. */
export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={on} aria-label={label} className="switch" onClick={e => { e.stopPropagation(); onChange(!on) }} />
}
