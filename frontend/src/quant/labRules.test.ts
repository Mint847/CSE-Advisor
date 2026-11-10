import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fromFlat, toFlat } from './labRules.ts'

const a = { type: 'ma_above', n: 50 }
const b = { type: 'near_high', pct: 10, n: 250 }

test('a single condition is a one-item list and back', () => {
  assert.deepEqual(toFlat(a), { op: 'all', items: [{ leaf: a, not: false }] })
  assert.deepEqual(fromFlat(toFlat(a)!), a)
})

test('ALL / ANY lists keep their operator and order', () => {
  const r = { op: 'any' as const, rules: [a, b] }
  const f = toFlat(r)!
  assert.equal(f.op, 'any')
  assert.deepEqual(f.items.map(i => i.leaf.type), ['ma_above', 'near_high'])
  assert.deepEqual(fromFlat(f), r)
})

test('a negated condition is shown ticked NOT and rebuilt as a "not"', () => {
  const r = { op: 'not' as const, rule: a }
  assert.deepEqual(toFlat(r), { op: 'all', items: [{ leaf: a, not: true }] })
  assert.deepEqual(fromFlat(toFlat(r)!), r)
  const mixed = { op: 'all' as const, rules: [a, { op: 'not' as const, rule: b }] }
  assert.deepEqual(fromFlat(toFlat(mixed)!), mixed)
})

test('nothing, and trees deeper than the form can show', () => {
  assert.deepEqual(toFlat(null), { op: 'all', items: [] })
  assert.equal(fromFlat({ op: 'all', items: [] }), null)
  assert.equal(toFlat({ op: 'all', rules: [a, { op: 'any', rules: [a, b] }] }), null)
  assert.equal(toFlat({ op: 'not', rule: { op: 'all', rules: [a, b] } }), null)
})
