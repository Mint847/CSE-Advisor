import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { TF_LABEL, TIMEFRAMES, api, engines, fmt, isIntraday, ticker, type Analysis, type Candles, type Coverage, type EngineRec, type LiveOverlay, type LiveStatus, type Timeframe } from '../api'
import PriceChart, { engineOf, levelPatterns, smcPatterns } from '../chart/PriceChart'
import { AlertDialog } from '../components/AlertForm'
import { StockStats } from '../components/KeyStats'
import CorporatePanel from '../components/CorporatePanel'
import NotesPanel from '../components/NotesPanel'
import FundamentalsTab from '../components/FundamentalsTab'
import LiquidityTab from '../components/LiquidityTab'
import CatalystsTab, { OverallBadge } from '../components/CatalystsTab'
import PatternDrawer, { analystGroups, smcGroups, type LevelItem } from '../components/PatternDrawer'
import { SmcKey, SmcSwatch } from '../components/SmcSwatch'
import RecommendationTab, { type RecView } from '../components/RecommendationTab'
import { AgreeBadge, ENGINE_TEXT, EngineBadge, ErrorBox, Spinner, VerdictBadge } from '../components/ui'
import { filterPatterns, useCatalog, useStockPrefs } from '../prefs'
import { useHScroll } from '../useHScroll'
import { useLive } from '../useLive'

const TABS = [
  { id: 'rec', code: 'REC', label: 'Recommendation' },
  { id: 'des', code: 'DES', label: 'Fundamentals' },
  { id: 'liq', code: 'LIQ', label: 'Liquidity & levels' },
  { id: 'cn', code: 'CN', label: 'Catalysts & news' },
  { id: 'cacs', code: 'CACS', label: 'Corporate actions & data' },
  { id: 'note', code: 'NOTE', label: 'Notes' },
] as const
type TabId = (typeof TABS)[number]['id']

const PANEL_TITLE = { original: 'CLASSIC · patterns + 5-factor score', analyst: 'LEVELS · Fibonacci, zones, level entries',
  smc: 'SMC · order blocks, premium/discount, liquidity' }

function CoverageLine({ c }: { c: Coverage }) {
  return (
    <span className="num">
      {c.bars.toLocaleString()} bars
      {c.tv && <> · TradingView <span className="text-fg">{c.tv.from} → {c.tv.to}</span></>}
      {c.cse && <> · cse.lk trades <span className="text-fg">{c.cse.from}{c.cse.to !== c.cse.from ? ` → ${c.cse.to}` : ''}</span></>}
    </span>
  )
}

const LIVE_WARN = new Set(['stop_level_hit', 't1_reached', 'above_max_chase', 'invalidated', 'target'])

function EnginePanel({ r, engine, onOpen, tfTag, live }: { r: EngineRec | null; engine: 'original' | 'analyst' | 'smc'; onOpen: () => void; tfTag?: string; live?: LiveStatus }) {
  const title = PANEL_TITLE[engine]
  if (!r) return (
    <div className="card flex-1"><div className="panel-title"><EngineBadge e={engine} /><span>{title}</span></div>
      <p className="p-2.5 text-muted">Not computed yet. Press 2) RE-ANALYSE.</p></div>
  )
  const p = r.plan
  const rv = r.position_review
  return (
    <button className="card flex-1 text-left hover:border-amber/60" onClick={onOpen} aria-label={`${engine} engine details`}>
      <div className="panel-title"><EngineBadge e={engine} />{tfTag && <span className="chip bg-yellow text-black">{tfTag}</span>}<span className="flex-1 truncate">{title}</span>
        <span className="flex items-center gap-1.5"><VerdictBadge v={r.verdict} /><b className="text-fg num">{r.score.toFixed(0)}</b><span className="font-normal text-muted">/100</span></span>
      </div>
      <div className="px-2.5 py-2 text-[12px]">
        {rv ? (
          <div className="num">POSITION <b className={rv.pl_pct >= 0 ? 'text-bull' : 'text-bear'}>{rv.pl_pct >= 0 ? '+' : ''}{rv.pl_pct.toFixed(1)}%</b> · {rv.action} · stop <b className="text-bear">{fmt(rv.suggested_stop)}</b> <span className="text-muted">· {rv.reasons[0]}</span></div>
        ) : p ? (
          <div className={`grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 num sm:grid-cols-[auto_1fr_auto_1fr] ${p.actionable ? '' : 'opacity-70'}`}>
            <span className="label">Setup</span><span className="truncate">{p.entry_label ? `${p.entry_label}: ` : ''}{p.setup_name}{p.triggered ? '' : ' (waiting)'}</span>
            <span className="label">Zone</span><b className="text-bull">{fmt(p.buy_zone.low)}–{fmt(p.buy_zone.high)}</b>
            {p.trigger ? <><span className="label">Trigger</span><b className="text-amber">close &gt; {fmt(p.trigger)}</b></> : <><span className="label">Limit</span><b>{fmt(p.ideal)}</b></>}
            <span className="label">Stop</span><b className="text-bear">{fmt(p.stop)} <span className="font-normal text-muted">{p.stop_net_pct.toFixed(1)}%</span></b>
            <span className="label">T1 / T2</span><b className={ENGINE_TEXT[engine]}>{p.targets.slice(0, 2).map(t => fmt(t.price)).join(' / ') || '–'}</b>
            <span className="label">R:R</span><b className={p.reward_risk >= 2 ? 'text-bull' : 'text-muted'}>{p.reward_risk.toFixed(2)}{p.actionable ? '' : ' · not actionable'}</b>
          </div>
        ) : <p className="line-clamp-2 text-muted">{r.explanation.summary}</p>}
        {live && live.text && (
          <p className={`mt-1.5 border-t border-line pt-1 text-[11px] ${LIVE_WARN.has(live.state) ? 'text-warn' : 'text-muted'}`}>
            <b className="mr-1 text-yellow">LIVE</b>{live.text}
          </p>
        )}
      </div>
    </button>
  )
}

export default function StockDetail() {
  const { symbol = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const [raw, setRaw] = useState(false)
  const [candles, setCandles] = useState<Candles | null>(null)
  const [daily, setDaily] = useState<Analysis | null>(null)
  const [ia, setIa] = useState<Analysis | null>(null)        // intraday analysis (on demand)
  const [iLoading, setILoading] = useState(false)
  const [backfilling, setBackfilling] = useState(false)
  const [liveCheck, setLiveCheck] = useState<LiveOverlay | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [focus, setFocus] = useState<{ id: string; nonce: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const { prefs, update, applyPreset, saveAsDefault, loadMyDefault } = useStockPrefs(symbol)
  const { enabled } = useCatalog()
  const { quotes, open } = useLive([symbol])
  const quote = quotes[symbol] ?? null
  const chipStrip = useHScroll<HTMLDivElement>()
  const [alertDlg, setAlertDlg] = useState(false)
  const [counts, setCounts] = useState({ alerts: 0, notes: 0 })
  const loadCounts = useCallback(() => {
    if (!symbol) return
    Promise.all([api.alerts().then(d => d.rules.filter(r => r.symbol === symbol && r.active).length), api.notes({ symbol }).then(n => n.length)])
      .then(([alerts, notes]) => setCounts({ alerts, notes })).catch(() => {})
  }, [symbol])
  useEffect(() => { loadCounts() }, [loadCounts])

  const tabParam = params.get('tab') ?? 'rec'
  const tab: TabId = (tabParam === 'cmp' || tabParam === 'smc' ? 'rec' : TABS.some(t => t.id === tabParam) ? tabParam : 'rec') as TabId
  const view: RecView = tabParam === 'cmp' ? 'compare' : tabParam === 'smc' ? 'smc' : (params.get('view') as RecView) || 'compare'
  const drawer = params.get('pat') === '1'
  const tfParam = params.get('tf') as Timeframe | null
  const tf: Timeframe = tfParam && TIMEFRAMES.includes(tfParam) ? tfParam : '1D'
  const intra = isIntraday(tf)
  const setQuery = (patch: Record<string, string | null>) => setParams(prev => {
    const n = new URLSearchParams(prev)
    for (const [k, v] of Object.entries(patch)) { if (v == null) n.delete(k); else n.set(k, v) }
    return n
  }, { replace: true })
  const setDrawer = (on: boolean) => setQuery({ pat: on ? '1' : null })
  const setTf = (t: Timeframe) => setQuery({ tf: t === '1D' ? null : t })

  const loadAnalysis = useCallback(() => api.analysis(symbol).then(setDaily).catch(e => setErr(String(e.message ?? e))), [symbol])
  useEffect(() => { setDaily(null); setErr(null); setSelected(null); loadAnalysis() }, [loadAnalysis])
  // the engines on intraday bars, computed on demand by the backend (cached per completed bar)
  const loadIntraday = useCallback((quiet = false) => {
    if (!intra) return Promise.resolve()
    if (!quiet) setILoading(true)
    return api.analysis(symbol, tf).then(setIa)
      .catch(e => setIa({ symbol, tf, error: String(e.message ?? e) } as unknown as Analysis))
      .finally(() => setILoading(false))
  }, [symbol, tf, intra])
  useEffect(() => { setIa(null); setSelected(null); loadIntraday() }, [loadIntraday])
  const loadCandles = useCallback(() => api.candles(symbol, tf, raw).then(setCandles).catch(e => setErr(String(e.message ?? e))), [symbol, tf, raw])
  useEffect(() => { loadCandles() }, [loadCandles])
  // the active analysis: the chart timeframe's own on intraday charts, the nightly one on 1D / 1W
  const a: Analysis | null = intra ? (ia && !ia.error ? ia : null) : daily
  // during the session, intraday charts re-read today's trades and re-run the engines once a minute
  useEffect(() => {
    if (!intra || !open) return
    const id = window.setInterval(() => { loadCandles(); loadIntraday(true) }, 60_000)
    return () => window.clearInterval(id)
  }, [intra, open, loadCandles, loadIntraday])
  const backfill = async () => {
    setBackfilling(true)
    try { await api.backfillIntraday(symbol); await Promise.all([loadCandles(), loadIntraday()]) }
    catch (e) { setIa({ symbol, tf, error: String((e as Error).message ?? e) } as unknown as Analysis) }
    finally { setBackfilling(false) }
  }

  // P opens the pattern drawer (when not typing)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const typing = e.target instanceof HTMLElement && e.target.closest('input, textarea, select') != null
      if (!typing && (e.key === 'p' || e.key === 'P')) { e.preventDefault(); setDrawer(!drawer) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const f = useMemo(() => filterPatterns(a?.patterns ?? [], prefs, enabled), [a, prefs, enabled])
  // a selected pattern is always drawn, even if filtered out of the visible set
  const mask = prefs.engines
  const drawn = useMemo(() => {
    const shown = f.visible.filter(p => mask[engineOf(p)])
    if (!selected || shown.some(p => p.id === selected)) return shown
    const p = a?.patterns.find(x => x.id === selected)
    return p ? [...shown, p] : shown
  }, [f.visible, selected, a, mask])
  const levelOn = useMemo(() => mask.analyst ? levelPatterns(a?.patterns ?? [], prefs.layers, prefs.levelOverrides) : [], [a, prefs.layers, prefs.levelOverrides, mask])
  const groups = useMemo(() => a ? analystGroups(a, prefs, f.analystPatterns, f.isAnalystVisible) : [], [a, prefs, f.analystPatterns, f.isAnalystVisible])
  const sGroups = useMemo(() => a ? smcGroups(a, prefs) : [], [a, prefs])
  const smcOn = useMemo(() => mask.smc ? smcPatterns(a?.patterns ?? [], prefs.layers, prefs.levelOverrides) : [], [a, prefs.layers, prefs.levelOverrides, mask])
  const drawnCount = f.visible.filter(p => mask[engineOf(p)]).length + levelOn.length + smcOn.length
    + (mask.analyst ? groups.filter(g => !['fib', 'tl', 'swing', 'apat'].includes(g.layer)).flatMap(g => g.items).filter(i => i.on).length : 0)

  const selectPattern = (id: string | null) => {
    setSelected(id)
    if (id) setFocus({ id, nonce: Date.now() })
  }
  const eng = a ? engines(a) : null
  const sel = a?.patterns.find(p => p.id === selected) ?? null
  const dEng = daily ? engines(daily) : null
  const price = quote?.close ?? dEng?.orig.close ?? eng?.orig.close
  const chg = quote?.change_pct ?? null
  const tfTag = intra ? TF_LABEL[tf] : undefined
  // the live price against the stored analysis (daily and weekly charts): which signals the session has already overtaken
  useEffect(() => {
    if (intra || !daily || !quote) { setLiveCheck(null); return }
    let alive = true
    api.live(symbol).then(o => { if (alive) setLiveCheck(o && o.price != null ? o : null) }).catch(() => { if (alive) setLiveCheck(null) })
    return () => { alive = false }
  }, [symbol, intra, daily, quote?.close, quote?.time]) // eslint-disable-line react-hooks/exhaustive-deps
  const cat = daily?.recommendation.catalysts ?? null
  const rs = daily?.recommendation.relative_strength ?? null
  const cov = (intra ? ia?.coverage : null) ?? candles?.coverage ?? null

  if (err && !daily) return <div className="p-3"><ErrorBox msg={err} /><Link to="/" className="mt-3 inline-block text-amber">← WL &lt;GO&gt;</Link></div>

  // active-drawings strip: everything currently drawn, removable with one click
  const allChips: { key: string; label: string; cls: string; off: () => void; id?: string; swatch?: LevelItem['swatch'] }[] = [
    ...(mask.original ? f.listed.filter(f.isVisible) : []).map(p => ({ key: p.id, id: p.id, label: `${p.direction === 'bullish' ? '▲' : p.direction === 'bearish' ? '▼' : '◆'} ${p.name}${liveCheck?.patterns?.[p.id]?.state === 'invalidated' ? ' ⚠ live' : liveCheck?.patterns?.[p.id]?.state === 'target' ? ' ✓ live' : ''}`,
      cls: p.direction === 'bullish' ? 'text-bull' : p.direction === 'bearish' ? 'text-bear' : 'text-neutral', off: () => update({ overrides: { ...prefs.overrides, [p.id]: false }, preset: null }) })),
    ...(mask.analyst ? groups : []).flatMap(g => g.items.filter(i => i.on).map(i => ({ key: i.key, id: i.patternId, label: i.label, cls: 'text-plan-anly',
      off: () => update({ levelOverrides: { ...prefs.levelOverrides, [i.key]: false }, preset: null }) }))),
    ...(mask.smc ? sGroups : []).flatMap(g => g.items.filter(i => i.on).map(i => ({ key: i.key, id: i.patternId, label: i.label, cls: 'text-plan-smc', swatch: i.swatch,
      off: () => update({ levelOverrides: { ...prefs.levelOverrides, [i.key]: false }, preset: null }) }))),
  ]
  const chips = [...new Map(allChips.map(c => [c.key, c])).values()]

  return (
    <div className="mx-auto max-w-[1700px] px-3 py-2">
      {/* security header */}
      <header className="card mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1.5">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <h1 className="text-[15px] font-semibold tracking-wide text-amber">{ticker(symbol)}</h1>
          <span className="truncate text-[12px] text-muted">{daily?.name}</span>
          {daily?.sector && <span className="chip bg-panel-3 text-muted" title={`${daily.sector.name} sector index, ${daily.sector.date ?? ''}`}>
            {daily.sector.code}{daily.sector.change_pct != null && <b className={`ml-1 ${daily.sector.change_pct >= 0 ? 'text-bull' : 'text-bear'}`}>{daily.sector.change_pct >= 0 ? '+' : ''}{daily.sector.change_pct.toFixed(2)}%</b>}
          </span>}
          {cat && <button onClick={() => setQuery({ tab: 'cn' })} title={`Catalysts: ${cat.reasons.join(' · ') || 'no strong signal'}`}><OverallBadge o={cat.overall} small /></button>}
          {rs && rs.label !== 'n/a' && <span className={`chip bg-panel-3 ${rs.label === 'leader' ? 'text-bull' : rs.label === 'laggard' ? 'text-bear' : 'text-muted'}`} title={rs.text}>
            {rs.label.toUpperCase()} {rs.vs_aspi['60'] != null ? `${rs.vs_aspi['60'] >= 0 ? '+' : ''}${rs.vs_aspi['60']} vs ASPI 60d` : ''}</span>}
        </div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 num">
          <b className="text-[20px] text-fg">{fmt(price)}</b>
          {chg != null && <span className={`text-[13px] ${chg >= 0 ? 'text-bull' : 'text-bear'}`}>{chg >= 0 ? '▲ +' : '▼ '}{chg.toFixed(2)}%</span>}
          {quote && <span className="text-[11px] text-muted">VOL <span className="text-fg">{quote.volume?.toLocaleString()}</span> · LAST {quote.last_trade}</span>}
          <span className={`chip ${open ? 'bg-bull text-black' : 'bg-panel-3 text-muted'}`}>{open ? 'LIVE' : 'CLOSED'}</span>
          <button className="chip border border-amber/60 text-amber hover:bg-amber hover:text-black" onClick={() => setAlertDlg(true)} title="Set a price, volume, pattern or news alert on this stock">
            + Alert{counts.alerts > 0 && <b className="ml-0.5">{counts.alerts}</b>}
          </button>
          <button className="chip border border-line text-fg hover:bg-panel-3" onClick={() => setQuery({ tab: 'note' })} title="Your notes on this stock">
            Notes{counts.notes > 0 && <b className="ml-0.5 text-amber">{counts.notes}</b>}
          </button>
        </div>
        {eng && <div className="ml-auto flex items-center gap-2 text-[11px]"><AgreeBadge a={eng.agreement} /><span className="hidden text-muted md:inline">{intra ? `${TF_LABEL[tf]} bar ${a?.time ?? ''}` : `${a?.date} close`}</span></div>}
      </header>

      <div className="card mb-2"><StockStats symbol={symbol} price={price} /></div>

      {/* intraday: data coverage and what this verdict is */}
      {intra && (
        <div className="card mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 px-2.5 py-1.5 text-[11px] text-muted">
          <span className="chip bg-yellow text-black">{TF_LABEL[tf]}</span>
          {cov ? <CoverageLine c={cov} /> : <span>loading bars…</span>}
          <span className="lg:hidden">· intraday verdicts; the watchlist &amp; alerts use daily</span>
          <span className="hidden lg:inline">· intraday verdicts, run on demand; the watchlist, alerts and Telegram use daily
            {ia?.daily?.verdicts && <> (daily: CLS <b className="text-fg">{ia.daily.verdicts.original ?? '–'}</b> · LVL <b className="text-fg">{ia.daily.verdicts.analyst ?? '–'}</b> · SMC <b className="text-fg">{ia.daily.verdicts.smc ?? '–'}</b>)</>}</span>
          <button className="btn ml-auto !py-0" disabled={backfilling} onClick={backfill} title="Re-load intraday history from TradingView (unofficial; may fail)">
            {backfilling ? 'Backfilling…' : 'Backfill history'}
          </button>
        </div>
      )}
      {intra && !a && (
        <div className="card mb-2 px-2.5 py-2 text-[12px]">
          {iLoading || !ia ? <Spinner label={`Running the engines on ${TF_LABEL[tf]} bars…`} />
            : <p className="text-warn">{ia.error ?? 'No intraday analysis'}. The chart still shows whatever {TF_LABEL[tf]} bars exist; press Backfill history to load more.</p>}
        </div>
      )}

      {/* the session so far vs the stored (end-of-day) signals */}
      {liveCheck && (liveCheck.summary?.length ?? 0) > 0 && (
        <div className="card mb-2 border-warn/60 px-2.5 py-2 text-[12px]">
          <div className="label pb-1 text-warn">Live vs the stored analysis · {liveCheck.date} {liveCheck.time} · {fmt(liveCheck.price)}{liveCheck.change_pct != null ? ` (${liveCheck.change_pct >= 0 ? '+' : ''}${liveCheck.change_pct.toFixed(1)}% on the last close)` : ''}</div>
          <ul className="list-disc space-y-0.5 pl-5">{liveCheck.summary!.map(x => <li key={x}>{x}</li>)}</ul>
          <p className="mt-1 text-[11px] text-muted">The stored patterns and plans were computed after the last close and don't change during the session. A pattern fails or confirms only on a close.</p>
        </div>
      )}

      {/* engines side by side */}
      {eng && (
        <div className="mb-2 grid gap-2 lg:grid-cols-3 [&>*]:min-w-0">
          <EnginePanel r={eng.orig} engine="original" tfTag={tfTag} live={liveCheck?.plans?.original} onOpen={() => setQuery({ tab: 'rec', view: 'original' })} />
          <EnginePanel r={eng.anly} engine="analyst" tfTag={tfTag} live={liveCheck?.plans?.analyst} onOpen={() => setQuery({ tab: 'rec', view: 'analyst' })} />
          <EnginePanel r={eng.smc} engine="smc" tfTag={tfTag} live={liveCheck?.plans?.smc} onOpen={() => setQuery({ tab: 'rec', view: 'smc' })} />
        </div>
      )}

      {/* toolbar: directly above the chart, so the timeframe and layer buttons are never a scroll away from it */}
      <div className="mb-2 flex flex-wrap items-center gap-1">
        <button className="btn btn-menu" onClick={() => setDrawer(!drawer)} aria-expanded={drawer} title="Patterns & layers (P)">
          <span className="key">1)</span>Patterns & layers <span className="bg-black/30 px-1 num">{drawnCount}/{(a?.patterns.length ?? 0)}</span>
        </button>
        <button className="btn btn-menu" disabled={busy} onClick={async () => { setBusy(true); try { await api.reanalyze(symbol); await Promise.all([loadAnalysis(), loadIntraday()]) } finally { setBusy(false) } }}>
          <span className="key">2)</span>{busy ? 'Analysing…' : 'Re-analyse'}
        </button>
        <div className="flex" role="radiogroup" aria-label="Timeframe">
          <span className="btn btn-menu !pr-1.5"><span className="key">3)</span></span>
          {TIMEFRAMES.map(t => <button key={t} role="radio" aria-checked={tf === t} className="btn !border-l-0 !px-2" onClick={() => setTf(t)}>{TF_LABEL[t]}</button>)}
        </div>
        <div className="flex" role="radiogroup" aria-label="Chart style">
          <span className="btn btn-menu !pr-1.5"><span className="key">4)</span></span>
          {(['candles', 'line'] as const).map(c => <button key={c} role="radio" aria-checked={prefs.chartStyle === c} className="btn !border-l-0 !px-2" title={c === 'line' ? 'Close-price line: easier to see the trend' : 'Candlesticks'}
                                                  onClick={() => update({ chartStyle: c })}>{c === 'line' ? 'Line' : 'Candles'}</button>)}
          <button className="btn !border-l-0 !px-2" aria-pressed={prefs.layers.zigzag} title="Zig-zag swings: a line through the swing highs and lows (a move of 2 × ATR turns it)"
                  onClick={() => update({ layers: { ...prefs.layers, zigzag: !prefs.layers.zigzag }, preset: null })}>Zig-zag {prefs.layers.zigzag ? 'ON' : 'OFF'}</button>
        </div>
        <div className="flex" role="group" aria-label="Engines shown on the chart"
             title="Choose whose drawings are on the chart. Click an engine to switch it on or off; double-click to show only that engine.">
          <span className="btn btn-menu !pr-1.5"><span className="key">5)</span>Show</span>
          {([['original', 'CLS'], ['analyst', 'LVL'], ['smc', 'SMC']] as const).map(([e, label]) => (
            <button key={e} className="btn !border-l-0 !px-2" aria-pressed={mask[e]}
                    onClick={() => update({ engines: { ...mask, [e]: !mask[e] }, preset: null })}
                    onDoubleClick={() => update({ engines: { original: e === 'original', analyst: e === 'analyst', smc: e === 'smc' }, preset: null })}>{label}</button>
          ))}
        </div>
        {(candles?.events?.length ?? 0) > 0 && (
          <button className="btn btn-menu" aria-pressed={raw} onClick={() => setRaw(r => !r)} title="Prices exactly as traded, without the rights/split adjustment (drawings hidden)">
            <span className="key">6)</span>Raw prices {raw ? 'ON' : 'OFF'}
          </button>
        )}
        <span className="ml-auto hidden text-[11px] text-muted lg:inline">P = patterns · type <b className="text-yellow">CMP &lt;GO&gt;</b>, <b className="text-yellow">SMC &lt;GO&gt;</b>, <b className="text-yellow">DES &lt;GO&gt;</b></span>
      </div>

      {/* chart */}
      <section className="card">
        <div ref={chipStrip} className="scroll-thin flex cursor-grab items-center gap-1 overflow-x-auto border-b border-line px-2 py-1" title="Scroll with the mouse wheel or drag sideways">
          <span className="label shrink-0 pr-1">GP · On chart</span>
          {chips.length === 0 && <span className="text-[11px] text-muted">nothing drawn; open 1) Patterns & layers</span>}
          {chips.map(c => (
            <span key={c.key} className={`chip shrink-0 border border-line bg-panel-2 !normal-case !font-medium ${c.cls}`}>
              {c.swatch && <SmcSwatch kind={c.swatch.kind} dir={c.swatch.dir} faded={c.swatch.faded} />}
              <button className="max-w-[180px] truncate" onClick={() => c.id && selectPattern(c.id === selected ? null : c.id)} title={c.label}>{c.label}</button>
              <button className="text-muted hover:text-bear" onClick={c.off} aria-label={`Hide ${c.label}`}>✕</button>
            </span>
          ))}
          <button className="chip shrink-0 border border-amber/60 text-amber hover:bg-amber hover:text-black" onClick={() => setDrawer(true)}>+ Patterns</button>
        </div>
        <div className="h-[64vh] min-h-[420px]">
          {candles ? <PriceChart candles={candles} analysis={a} visible={drawn} layers={prefs.layers} highlightId={selected}
                                 onSelect={selectPattern} quote={tf === '1D' || intra ? quote : null} focus={focus}
                                 planEngine={prefs.planEngine} levelOverrides={prefs.levelOverrides} chartStyle={prefs.chartStyle} engineMask={mask} /> : <Spinner label="Loading chart…" />}
        </div>
        {chips.some(c => c.swatch) && <SmcKey />}
      </section>

      {sel && (
        <div className="card mt-2 p-2.5 text-[12px]">
          <div className="flex flex-wrap items-center gap-2">
            <EngineBadge e={sel.engine === 'smc' ? 'smc' : sel.engine === 'analyst' || sel.category === 'level' ? 'analyst' : 'original'} />
            <b className={sel.direction === 'bullish' ? 'text-bull' : sel.direction === 'bearish' ? 'text-bear' : 'text-fg'}>{sel.name}</b>
            <span className="chip bg-panel-3 text-muted">{sel.status}</span>
            <span className="chip bg-panel-3 text-muted">priority {sel.priority.toFixed(0)} ({sel.priority_tier})</span>
            <span className="chip bg-panel-3 text-muted">confidence {(sel.confidence * 100).toFixed(0)}%</span>
            <span className="text-[11px] text-muted num">{sel.start} → {sel.end}</span>
            <button className="btn ml-auto !py-0" onClick={() => setSelected(null)}>Clear ✕</button>
          </div>
          <p className="mt-1.5 leading-relaxed">{sel.explanation}</p>
          {liveCheck?.patterns?.[sel.id]?.text && <p className="mt-1 text-[12px] text-warn"><b className="mr-1 text-yellow">LIVE</b>{liveCheck.patterns[sel.id].text}</p>}
          {sel.engine !== 'smc' && <p className="mt-1 text-[11px] text-muted">
            Why this priority: base reliability {(Number(sel.reliability.base ?? 0) * 100).toFixed(0)} ({String(sel.reliability.source ?? '')})
            {sel.context.at_key_level ? ' · at a key S/R level ×1.25' : ''}{sel.context.volume_confirmed ? ' · volume-confirmed ×1.15' : ''}
            {sel.context.trend_aligned ? ' · with the trend ×1.1' : ''}{sel.context.against_trend ? ' · against the trend ×0.75' : ''}
            {sel.context.estimated_open ? ' · uses estimated opens (×0.6)' : ''} · {sel.context.age_bars ?? 0} bars old.
          </p>}
        </div>
      )}

      <nav className="mt-3 flex gap-px overflow-x-auto border-b border-line" role="tablist">
        {TABS.map(t => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className="tab" onClick={() => setQuery({ tab: t.id })}>
            <span className="text-amber">{t.code}</span> <span className="hidden sm:inline">{t.label}</span>
          </button>
        ))}
      </nav>
      <div className="py-3">
        {!daily ? <Spinner /> : tab === 'rec' ? (a ? <RecommendationTab a={a} view={view} onView={v => setQuery({ tab: 'rec', view: v })}
                                                             onPattern={id => { selectPattern(id); window.scrollTo({ top: 0, behavior: 'smooth' }) }} />
                                                  : <p className="text-[12px] text-muted">No {TF_LABEL[tf]} analysis yet (see above). Switch to 1D for the nightly analysis.</p>)
          : tab === 'des' ? <FundamentalsTab symbol={symbol} onSaved={loadAnalysis} />
          : tab === 'liq' ? <LiquidityTab a={daily} />
          : tab === 'note' ? <div className="card"><NotesPanel symbol={symbol} onLoaded={r => setCounts(c => c.notes === r.length ? c : { ...c, notes: r.length })} /></div>
          : tab === 'cacs' ? <CorporatePanel symbol={symbol} a={daily} onChanged={() => { loadAnalysis(); loadIntraday(); loadCandles() }} />
          : <CatalystsTab symbol={symbol} />}
      </div>

      {alertDlg && <AlertDialog symbol={symbol} price={price ?? null} onClose={() => setAlertDlg(false)} onChanged={loadCounts} />}

      {drawer && a && (
        <PatternDrawer a={a} prefs={prefs} update={update} applyPreset={applyPreset} saveAsDefault={saveAsDefault} loadMyDefault={loadMyDefault}
                       listed={f.listed} analystPatterns={f.analystPatterns} isVisible={f.isVisible} isAnalystVisible={f.isAnalystVisible}
                       drawnCount={drawnCount} selected={selected} onSelect={id => selectPattern(id === selected ? null : id)} onClose={() => setDrawer(false)} />
      )}
    </div>
  )
}
