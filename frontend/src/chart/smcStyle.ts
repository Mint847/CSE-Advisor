// How each SMC zone is drawn, so an order block, a weekly order block, a fair value gap and the OTE band can be told
// apart at a glance. Colour still means direction (green bullish, red bearish; amber for OTE); the TYPE is carried
// by fill texture, border shape and label badge, which also survive colour-blind viewing and grayscale printing.
// Pure data: the canvas overlay (overlay.ts) and the CSS swatches (SmcSwatch.tsx) both read it.

export type SmcKind = 'ob' | 'wob' | 'fvg' | 'ote'

export interface SmcZoneStyle {
  kind: SmcKind
  /** solid fill opacity before the status factor (0 = no fill) */
  fill: number
  /** diagonal hatch ('/' lines) instead of / on top of the fill */
  hatch: boolean
  /** solid box, double box (weekly), or top and bottom edges only */
  border: 'solid' | 'double' | 'edges'
  dash: number[]
  /** a heavy bar on the left edge: the candle the block came from */
  edgeBar: boolean
  badge: 'filled' | 'outline' | 'hatch' | 'dotted'
  /** 1 = fresh / open; 0.5 = already tested or partly filled (drawn fainter) */
  strength: 1 | 0.5
  tag: string
  statusText: string
  /** 'direction' = green / red by the pattern's direction; 'accent' = amber */
  color: 'direction' | 'accent'
}

/** The pattern's context: htf (weekly), fresh, touches (order blocks), partial (FVGs). */
export type SmcContext = Record<string, unknown>


const arrow = (direction?: string) => direction === 'bullish' ? ' ↑' : direction === 'bearish' ? ' ↓' : ''

/** The style for one zone, or null when the role is not an SMC zone (the generic box is drawn instead). */
export function smcZoneStyle(role: string, ctx: SmcContext = {}, direction?: string): SmcZoneStyle | null {
  if (role === 'order_block') {
    const touches = Number(ctx.touches ?? 0) || 0
    const tested = ctx.fresh === false || touches > 0
    const status = tested ? (touches > 0 ? `tested ${touches}×` : 'tested') : 'fresh'
    if (ctx.htf) {
      return { kind: 'wob', fill: 0.08, hatch: false, border: 'double', dash: [8, 3], edgeBar: false, badge: 'outline',
               strength: tested ? 0.5 : 1, tag: `W-OB${arrow(direction)}`, statusText: status, color: 'direction' }
    }
    return { kind: 'ob', fill: 0.16, hatch: false, border: 'solid', dash: [], edgeBar: true, badge: 'filled',
             strength: tested ? 0.5 : 1, tag: `OB${arrow(direction)}`, statusText: status, color: 'direction' }
  }
  if (role === 'fvg') {
    const partial = !!ctx.partial
    return { kind: 'fvg', fill: 0, hatch: true, border: 'edges', dash: [3, 3], edgeBar: false, badge: 'hatch',
             strength: partial ? 0.5 : 1, tag: `FVG${arrow(direction)}`, statusText: partial ? '½ filled' : 'open', color: 'direction' }
  }
  if (role === 'ote') {
    return { kind: 'ote', fill: 0, hatch: false, border: 'edges', dash: [1, 3], edgeBar: false, badge: 'dotted',
             strength: 1, tag: 'OTE', statusText: '', color: 'accent' }
  }
  return null
}

/** Paint order: the biggest, slowest zones first so smaller ones stay visible on top. */
export const SMC_ORDER: Record<SmcKind, number> = { wob: 0, ob: 1, fvg: 2, ote: 3 }

/** The chart key under the price chart. */
export const SMC_KEY: { kind: SmcKind; text: string }[] = [
  { kind: 'ob', text: 'Order block' }, { kind: 'wob', text: 'Weekly order block' },
  { kind: 'fvg', text: 'Fair value gap' }, { kind: 'ote', text: 'OTE (optimal entry)' },
]

/** The SMC kind a pattern draws, from its catalog type (for the chips and the drawer). */
export function smcKindOf(type: string): SmcKind | null {
  if (type.startsWith('weekly_') && type.endsWith('order_block')) return 'wob'
  if (type.endsWith('order_block')) return 'ob'
  if (type.endsWith('_fvg')) return 'fvg'
  if (type === 'premium_discount') return 'ote'
  return null
}
