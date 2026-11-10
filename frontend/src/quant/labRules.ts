// The Quant Lab builder shows a rule as a flat list of conditions joined by ALL / ANY, each optionally negated.
// The backend accepts deeper trees; those are shown read-only (toFlat returns null).
import type { LabLeaf, LabRule } from '../api'

export interface Item { leaf: LabLeaf; not: boolean }
export interface Flat { op: 'all' | 'any'; items: Item[] }

const isLeaf = (r: LabRule): r is LabLeaf => 'type' in r
const asItem = (r: LabRule): Item | null => isLeaf(r) ? { leaf: r, not: false } : r.op === 'not' && isLeaf(r.rule) ? { leaf: r.rule, not: true } : null

/** The rule as a flat list, or null when it is nested more deeply than this form can show. */
export function toFlat(r: LabRule | null | undefined): Flat | null {
  if (!r) return { op: 'all', items: [] }
  if (isLeaf(r)) return { op: 'all', items: [{ leaf: r, not: false }] }
  if (r.op === 'not') { const one = asItem(r); return one ? { op: 'all', items: [one] } : null }
  const items = r.rules.map(asItem)
  return items.every(Boolean) ? { op: r.op, items: items as Item[] } : null
}

export function fromFlat(f: Flat): LabRule | null {
  const rules = f.items.map(i => (i.not ? { op: 'not' as const, rule: i.leaf } : i.leaf))
  return rules.length === 0 ? null : rules.length === 1 ? rules[0] : { op: f.op, rules }
}

