// Run with `npm test`. Each SMC zone type must look different by shape alone, not only by colour.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { smcKindOf, smcZoneStyle } from './smcStyle.ts'

const shape = (s: ReturnType<typeof smcZoneStyle>) => s && JSON.stringify([s.fill > 0, s.hatch, s.border, s.edgeBar, s.badge, s.dash])

test('order block, weekly order block, FVG and OTE each have their own shape', () => {
  const ob = smcZoneStyle('order_block', { htf: false, fresh: true }, 'bullish')
  const wob = smcZoneStyle('order_block', { htf: true, fresh: true }, 'bullish')
  const fvg = smcZoneStyle('fvg', {}, 'bullish')
  const ote = smcZoneStyle('ote', {}, 'neutral')
  const shapes = [ob, wob, fvg, ote].map(shape)
  assert.equal(new Set(shapes).size, 4)
  assert.deepEqual([ob?.kind, wob?.kind, fvg?.kind, ote?.kind], ['ob', 'wob', 'fvg', 'ote'])
  assert.equal(ote?.color, 'accent')
})

test('bullish and bearish differ only in colour and arrow, never in shape', () => {
  for (const [role, c] of [['order_block', { htf: false }], ['order_block', { htf: true }], ['fvg', {}]] as const) {
    const up = smcZoneStyle(role, c, 'bullish'), down = smcZoneStyle(role, c, 'bearish')
    assert.equal(shape(up), shape(down))
    assert.ok(up!.tag.endsWith('↑') && down!.tag.endsWith('↓'))
  }
})

test('tested blocks and partly filled gaps are drawn fainter and say so', () => {
  assert.equal(smcZoneStyle('order_block', { fresh: true, touches: 0 })!.strength, 1)
  const t = smcZoneStyle('order_block', { fresh: false, touches: 2 })!
  assert.equal(t.strength, 0.5)
  assert.equal(t.statusText, 'tested 2×')
  assert.equal(smcZoneStyle('fvg', { partial: true })!.statusText, '½ filled')
  assert.equal(smcZoneStyle('fvg', { partial: false })!.strength, 1)
})

test('other roles keep the generic box; pattern types map to kinds', () => {
  assert.equal(smcZoneStyle('candle_box', {}), null)
  assert.equal(smcKindOf('weekly_bearish_order_block'), 'wob')
  assert.equal(smcKindOf('bullish_order_block'), 'ob')
  assert.equal(smcKindOf('bearish_fvg'), 'fvg')
  assert.equal(smcKindOf('premium_discount'), 'ote')
  assert.equal(smcKindOf('smc_structure'), null)
})
