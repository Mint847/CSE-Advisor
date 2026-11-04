// Typed client for the FastAPI backend.

export type Direction = 'bullish' | 'bearish' | 'neutral'
export type Status = 'forming' | 'confirmed' | 'failed' | 'expired'
export type Verdict = 'BUY' | 'WATCH' | 'AVOID' | 'HOLD' | 'TRIM' | 'SELL'

export interface Pt { time: string; price: number }
export interface Pattern {
  id: string; type: string; name: string; category: string; tier: number
  direction: Direction; status: Status; start: string; end: string; end_index: number
  anchors: (Pt & { label?: string })[]
  lines: { from: Pt; to: Pt; role: string; style: 'solid' | 'dashed' | 'dotted'; pane?: string; label?: string; dir?: 'up' | 'down' }[]
  zones: { t1: string; t2: string; low: number; high: number; role: string; label?: string }[]
  markers: { time: string; position: 'aboveBar' | 'belowBar' | 'inBar'; shape: 'circle' | 'square' | 'arrowUp' | 'arrowDown'; text: string }[]
  levels: Record<string, number>
  confidence: number; priority: number; priority_tier: 'high' | 'medium' | 'low'
  reliability: Record<string, unknown>; explanation: string
  context: Record<string, unknown> & { age_bars?: number }
  bars: string[]
  engine?: Engine
}
export type Engine = 'original' | 'analyst' | 'smc'
/** Which engines' drawings are shown on the chart. */
export type EngineMask = Record<Engine, boolean>
export const ALL_ENGINES: EngineMask = { original: true, analyst: true, smc: true }
export interface SmcEvent { kind: 'bos' | 'choch'; dir: 'up' | 'down'; level: number; date: string }
export interface SmcRange { dir: 'up' | 'down'; low: number; high: number; eq: number; pos: number; ote_low: number; ote_high: number; zone: 'discount' | 'premium'; in_ote: boolean }
export interface SmcContext {
  bias: string; last: SmcEvent | null; htf_bias: string; htf_last: SmcEvent | null; range: SmcRange | null; htf_range: SmcRange | null
  obs: { dir: 'up' | 'down'; low: number; high: number; status: string; touches: number; date: string; swept: boolean; fvg: boolean }[]
  fvgs: { dir: 'up' | 'down'; low: number; high: number; status: string; date: string }[]
  sweeps: { side: 'buy' | 'sell'; level: number; date: string }[]
  liquidity_above: number[]; liquidity_below: number[]
}
export interface Zone { price: number; low: number; high: number; touches: number; last_touch: string; first_touch: string; volume_share: number; strength: number; kind: 'support' | 'resistance'; flip?: { from: string; to: string; date: string; text: string } | null }
export interface Cluster { low: number; high: number; center: number; score: number; sources: string[]; kinds: string[]; side: string }
export interface Fib { direction: 'up' | 'down'; scale: string; move_pct: number; retracement_now: number; low: { time: string; price: number }; high: { time: string; price: number }; levels: { ratio: number; price: number }[]; extensions: { ratio: number; price: number }[] }
export interface CorpAction { id: number; kind: string; ex_date: string | null; ratio_new: number | null; ratio_old: number | null; price: number | null; listing_date: string | null; trading_date?: string | null; factor: number | null; terp?: number | null; cum_price: number | null; applied: boolean; reason?: string; manual: boolean; ignore: boolean }
export interface Signals {
  extension: { flag: boolean; extreme: boolean; pct_above_ema21: number | null; atr_above_ema21: number | null; rsi: number | null; rsi_max15?: number }
  momentum: { rsi: number | null; rsi_ma: number | null; cross_above_50: string | null; cross_below_50: string | null; above_rsi_ma: boolean; oversold_turn: boolean; overbought_turn: boolean }
  volume: { high_volume_streak: number; down_volume_rising: boolean; volume_trend: string; highest_since_peak: boolean; peak_date: string; block_trade_days: string[] }
  volatility: { guard: boolean; reasons: string[]; notes?: string[] }
  drawdown: { peak: number; peak_date: string; pct: number; cooling_off: boolean }
  today_vap?: { total_volume: number; top: { price: number; volume: number; share: number; trades: number }; rows: { price: number; volume: number; share: number; trades: number }[]; date?: string }
}
export interface Pool { price: number; kind: string; time: string | null; source: string }
export interface VolumeProfile { bins: { low: number; high: number; volume: number; share: number; in_value_area: boolean }[]; poc: number | null; va_low: number | null; va_high: number | null; hvn: number[]; lvn: number[]; lookback: number }
export interface Target { label: string; price: number; pct: number; net_pct: number; source: string }
export interface Plan {
  setup: string; setup_name: string; triggered: boolean; trigger: number | null
  buy_zone: { low: number; high: number }; ideal: number; ideal_note: string; max_chase: number
  stop: number; stop_steps: string[]; stop_pct: number; stop_net_pct: number
  targets: Target[]; reward_risk: number; cost_per_side_pct: number; actionable: boolean
  size: { qty: number; value: number; risk_lkr: number; qty_by_risk: number; qty_by_cap: number; qty_by_liquidity: number; binding: string; days_to_exit: number | null; capital: number; risk_pct: number }
  atr: number; close: number; time_stop_weeks: number; trailing: { method: string }
  entry_label?: string | null; why?: string | null; zone_sources?: string[] | null
}
export interface Reason { text: string; factor: string; strength: number; pattern_id?: string }
export interface PriceRow { label: string; price: string; how: string; pct?: number | null; net_pct?: number | null }
export interface Explanation {
  summary: string; reasons_for: Reason[]; reasons_against: Reason[]
  buy_prices: PriceRow[]; sell_prices: PriceRow[]; sizing: string | null
  scenarios: { name: string; text: string }[]; invalidation: string[]; watch_next: string[]; changes: string[]
  score_breakdown: { label: string; score: number | null; weight: number; contribution: number; available: boolean }[]
  ladder?: { side: 'support' | 'resistance'; low: number; high: number; score: number; sources: string[]; scenario: string }[]
  standing_note?: string
  entries?: { label: string; setup: string; zone: string; limit: number; trigger: number | null; stop: number; targets: number[]; rr: number; actionable: boolean; triggered: boolean; why: string | null; sources: string[] | null }[]
}
export interface PositionReview { action: Verdict; reasons: string[]; entry: number; quantity: number; close: number; pl_lkr: number; pl_pct: number; suggested_stop: number | null; chandelier: number; target: number | null }
export interface Corporate { phase: string | null; notes: string[]; score_adjustment: number; terp_today?: number; pre_rights_equivalent?: number; actions: CorpAction[]; events: { kind: string; date: string; label: string }[] }
/** One engine's output (Original, Analyst or SMC); all have the same shape. */
export interface EngineRec {
  engine?: Engine; label?: string
  verdict: Verdict; score: number; plan: Plan | null; checklist: { label: string; passed: boolean; value: string }[]
  explanation: Explanation; position_review: PositionReview | null; close: number; date: string
  red_flags: { category: string; title: string; date: string }[]; fundamental_flags?: string[]
  entries?: Plan[]; setups?: { kind: string; triggered: boolean; pattern_name: string }[]
  signals?: Signals; adjustments?: { points: number; reason: string }[]; corporate?: Corporate; smc?: SmcContext
}
export interface Agreement { state: 'agree' | 'mixed' | 'differ'; text: string }
export interface Recommendation {
  original: EngineRec; analyst: EngineRec | null; smc?: EngineRec | null; agreement: Agreement | null; close: number; date: string
  news?: { score: number | null; detail: string }; market?: { score: number | null; regime: string; detail: string }
  catalysts?: CatalystsCompact | null
  relative_strength?: RelativeStrength | null
}
/** The live price against the stored daily analysis (patterns and plans the session has already overtaken). */
export interface LiveStatus { state: string; text: string; level?: number | null }
export interface LiveOverlay {
  price?: number; time?: string; date?: string; open?: number; high?: number; low?: number; change_pct?: number
  patterns?: Record<string, LiveStatus>; plans?: Record<string, LiveStatus>; summary?: string[]
}
export interface RelativeStrength { vs_aspi: Record<string, number>; vs_sector: Record<string, number>; label: 'leader' | 'laggard' | 'in line' | 'n/a'; text: string }

/* ---- Catalysts (news, results, events) */
export type Overall = 'Supportive' | 'Neutral' | 'Cautious'
export interface ScoreRow { signal: string; reading: string; direction: -1 | 0 | 1; why: string; weight: number }
export interface CalendarRow { date: string; kind: string; title: string; detail: string; days: number }
export interface ResultsDue { period_end: string; expected: string; window: [string, string]; days: number; lag_days: number; history_lags: number[]; status: 'expected' | 'overdue'; basis: string }
export interface ResultRow { period_end: string; revenue: number | null; net_profit: number | null; eps: number | null; profit_yoy_pct: number | null; source: string; needs_review: boolean; checks?: string[] | null; url?: string | null }
export interface Reaction { session: string; r0: number; r5: number; x0?: number; x5?: number; complete: boolean }
export interface CatalystsCompact { overall: Overall; total: number; reasons: string[]; risk_lines: string[]; next: CalendarRow[]; results_due: ResultsDue | null; latest_results: ResultRow | null }
export interface Catalysts {
  symbol: string; asof: string; close: number | null
  scorecard: { overall: Overall; total: number; rows: ScoreRow[]; reasons: string[]; note: string }
  calendar: CalendarRow[]; results_due: ResultsDue | null; risk_lines: string[]; results: ResultRow[]
  dividends: { ttm_dps: number | null; ttm_yield_pct: number | null; history: { dps: number; xd: string | null; payment: string | null; fy: string | null; final: boolean }[]
    xd_drops: { xd: string; dps: number; drop: number; drop_vs_dps_pct: number; yield_pct: number }[] }
  insiders: { d90: InsiderWindow; d365: InsiderWindow; unknown_direction: number
    recent: { date: string; director: string; role: string | null; net_value: number; trades: { side: string; date: string | null; qty: number; price: number; value: number }[] }[] }
  headlines: { n30: number; tone30: number | null; items: Headline[] }
  announcements: { id: number; date: string | null; title: string; subject: string | null; summary: string | null; category: string | null; sentiment: number | null; materiality: number | null; reaction: Reaction | null }[]
  reaction_stats: { kind: string; n: number; median_day_pct: number | null; median_5d_excess_pct: number | null; pct_positive_5d: number | null; readable: boolean; note?: string }[]
  events: { kind: string; when: string; title: string; detail?: string; reaction: Reaction | null }[]
  corporate: { phase: string | null; notes: string[] }
}
export interface InsiderWindow { days: number; notices: number; buys: number; sells: number; net_value: number }
export interface Headline { source: string; title: string; link: string; published: string | null; tone: number | null; symbols?: string[] }

/* ---- Portfolio */
export interface PortfolioFigures { cost: number; market_value?: number; unrealised_net?: number; unrealised_net_pct?: number; realised_net?: number; realised_net_pct?: number; stop?: number; pl_at_stop?: number; drop_to_stop_pct?: number }
export interface Portfolio {
  open_positions: number; invested: number; market_value: number; unrealised_net: number; unrealised_net_pct: number | null; realised_net: number
  risk_to_stops: number; positions_without_stop: string[]; largest: { symbol: string; value: number; pct: number | null } | null
  by_stock: { symbol: string; value: number; pct: number | null }[]; by_sector: { sector: string; value: number; pct: number | null }[]
  fee_pct_per_side: number; rows: Record<string, PortfolioFigures>
  capital?: number; invested_pct_of_capital?: number; risk_to_stops_pct_of_capital?: number; cash_estimate?: number
}
export interface Liquidity {
  grade: string; grade_notes: string[]; median_turnover_60: number; avg_turnover_20: number; adv_volume_20: number
  days_traded_pct: number; amihud: number | null; spread_est_pct: number | null
  flow: { score: number; label: string; up_down_volume_ratio: number; obv_slope_norm: number; ad_slope_norm: number; turnover_5_vs_20: number; turnover_20_vs_60: number; price_change_5d_pct: number; price_change_20d_pct: number; avg_trade_size: { last5: number; last20: number } | null }
  regime: string; regime_text: string; timeline: { state: string; start: string; end: string }[]
  turnover_series: { time: string; value: number }[]
}
export interface Trend { state: string; direction: number; score: number; quality: number; weekly: number; votes: { method: string; vote: number; detail: string }[]; atr: number; rsi: number | null; regression: { slope_pct_per_bar: number; r2: number } }
/** Chart timeframes. 1D/1W use the stored nightly analysis; the intraday ones run the engines on demand. */
export type Timeframe = '1m' | '5m' | '15m' | '1h' | '4h' | '1D' | '1W'
export const TIMEFRAMES: Timeframe[] = ['1m', '5m', '15m', '1h', '4h', '1D', '1W']
export const TF_LABEL: Record<Timeframe, string> = { '1m': '1M', '5m': '5M', '15m': '15M', '1h': '1H', '4h': '4H', '1D': '1D', '1W': '1W' }
export const isIntraday = (tf: string) => tf === '1m' || tf === '5m' || tf === '15m' || tf === '1h' || tf === '4h'
export type ChartStyle = 'candles' | 'line'
export interface Coverage { bars: number; tf: string; tv?: { from: string; to: string; bars: number }; cse?: { from: string; to: string; bars: number } }
export interface SectorToday { code: string; name: string; date: string | null; value: number | null; change_pct: number | null }
export interface Analysis {
  symbol: string; name: string; date: string; created_at: string | null; verdict: Verdict; score: number; logo: string | null
  tf?: Timeframe; time?: string; coverage?: Coverage | null; error?: string; elapsed_ms?: number
  daily?: { date: string | null; verdicts: { original: Verdict | null; analyst: Verdict | null; smc: Verdict | null } | null }
  sector?: SectorToday | null
  verdict_analyst?: Verdict | null; score_analyst?: number | null
  verdict_smc?: Verdict | null; score_smc?: number | null
  patterns: Pattern[]; levels: { zones: Zone[]; volume_profile: VolumeProfile; pools: Pool[]; pivots: { time: string; price: number; kind: string }[]
    fib?: Fib | null; anchored_profile?: VolumeProfile & { anchor?: string }; confluence?: { zone1: Cluster | null; zone2: Cluster | null; sell_zones: Cluster[] }
    events?: { kind: string; date: string; label: string }[]; ma?: Record<string, unknown> }
  liquidity: Liquidity; trend: Trend; recommendation: Recommendation
}

/** The three engines' results; older analyses lack the later engines. */
export function engines(a: Analysis): { orig: EngineRec; anly: EngineRec | null; smc: EngineRec | null; agreement: Agreement | null } {
  const r = a.recommendation as Recommendation & Partial<EngineRec>
  if (r && 'original' in r && r.original) return { orig: r.original, anly: r.analyst ?? null, smc: r.smc ?? null, agreement: r.agreement ?? null }
  return { orig: r as unknown as EngineRec, anly: null, smc: null, agreement: null }
}
export interface Bar { time: string; open: number; high: number; low: number; close: number; volume: number; est: boolean }
export interface LinePoint { time: string; value: number }
export interface Candles { symbol: string; timeframe: string; bars: Bar[]; indicators: Record<string, LinePoint[]>; adjusted?: boolean; events?: { time: string; kind: string; applied: boolean; factor: number | null; text: string }[]; coverage?: Coverage | null }
export interface SectorRow { code: string; name: string; index_name: string | null; value: number | null; change: number | null; change_pct: number | null; turnover: number | null; trades: number | null; watchlist: string[] }
export interface Mover { symbol: string; price?: number | null; change_pct?: number | null; trades?: number | null; volume?: number | null; turnover?: number | null }
export interface MarketData {
  regime: { score: number | null; regime: string; detail: string; factors: string[] }; open: boolean
  series: { time: string; aspi: number | null; spsl20: number | null; turnover: number | null; advancers: number | null; decliners: number | null
    foreign_buy?: number | null; foreign_sell?: number | null; foreign_trades?: number | null; per?: number | null; pbv?: number | null; dy?: number | null; market_cap?: number | null }[]
  sectors?: { date: string | null; rows: SectorRow[] }
  movers?: { gainers?: Mover[]; losers?: Mover[]; active?: Mover[]; updated?: string }
}
export interface WatchItem {
  symbol: string; name: string; logo: string | null; price: number | null; change_pct: number | null; sparkline: number[]; sector?: string | null
  verdict: Verdict | null; score: number | null; analysis_date: string | null; trend: string | null
  liquidity_grade: string | null; liquidity_regime: string | null
  top_pattern: { name: string; direction: Direction; status: Status; priority: number } | null
  plan: PlanSummary | null
  summary: string | null
  verdict_analyst?: Verdict | null; score_analyst?: number | null; plan_analyst?: PlanSummary | null
  zone1?: Cluster | null; summary_analyst?: string | null; agreement?: Agreement | null
  verdict_smc?: Verdict | null; score_smc?: number | null; plan_smc?: PlanSummary | null; summary_smc?: string | null
}
export interface PlanSummary { ideal: number; stop: number; actionable: boolean; zone: { low: number; high: number }; trigger: number | null; t1: number | null; label?: string | null; rr?: number | null }
export interface Quote { symbol: string; time: string; last_trade: string; open: number; high: number; low: number; close: number; prev_close: number | null; change: number | null; change_pct: number | null; volume: number | null; turnover: number | null; trades: number | null }
export interface FundRow { id?: number; period_end: string; period_type: 'Q' | 'FY'; source?: string; needs_review?: boolean; detail?: FundDetail | null; revenue?: number | null; gross_profit?: number | null; operating_profit?: number | null; net_profit?: number | null; eps?: number | null; total_assets?: number | null; total_liabilities?: number | null; total_equity?: number | null; current_assets?: number | null; current_liabilities?: number | null; total_debt?: number | null; finance_cost?: number | null; operating_cash_flow?: number | null; dividend_per_share?: number | null; shares_outstanding?: number | null; one_off_income?: number | null }
export interface Fundamentals {
  name: string; has_data: boolean; detail?: string; basis: string | null; score: number | null
  market: { price: number; market_cap: number | null; shares: number | null; high_52w: number | null; low_52w: number | null; ytd_high: number | null; ytd_low: number | null; all_time_high: number | null; ytd_turnover: number | null; foreign_pct: number | null }
  ratios: Record<string, number | null>; components?: { name: string; score: number; reason: string }[]; flags: string[]
  periods: FundRow[]; valuation_band?: { low: number; mid: number; high: number; method: string }
  relative?: { pe_vs_market_pct?: number; pbv_vs_market_pct?: number; market_pe: number; market_pbv: number }
  reports: { id: number; kind: string; title: string; url: string; period: string | null; filed: string | null }[]
  next_reports?: { quarterly: ResultsDue | null; annual: (ResultsDue & { year_ended: boolean }) | null }
  beta: { aspi: number | null; spsl20: number | null }
  review?: FundRow[]
  auto?: Record<string, { source: string; detail: FundDetail | null }>
  market_valuation?: { per: number | null; pbv: number | null; date: string } | null
}
export interface FundDetail { report_id?: number; title?: string; url?: string; column?: string | null; evidence?: Record<string, string>; checks?: string[]; comparative?: boolean; confirmed?: boolean; nav_per_share?: number | null }
export interface AnnouncementRow { id: number; date: string | null; title: string; body: string | null; category: string | null; sentiment: number | null; materiality: number | null }
export interface PositionRow { id: number; symbol: string; entry_date: string; entry_price: number; quantity: number; stop: number | null; target: number | null; closed: boolean; exit_date: string | null; exit_price: number | null; notes: string | null; price: number | null; review: PositionReview | null; verdict: Verdict | null; review_analyst?: PositionReview | null; verdict_analyst?: Verdict | null; review_smc?: PositionReview | null; verdict_smc?: Verdict | null }
export interface CatalogEntry { name: string; tier: number; enabled: boolean; base_reliability: number; category: string; type: string; engine?: Engine }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try { const j = await r.json(); msg = j.detail ?? msg } catch { /* not json */ }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return r.json() as Promise<T>
}

export const api = {
  watchlist: () => req<WatchItem[]>('/api/watchlist'),
  addStock: (query: string) => req<{ symbol: string }>('/api/watchlist', { method: 'POST', body: JSON.stringify({ query }) }),
  removeStock: (s: string) => req(`/api/watchlist/${s}`, { method: 'DELETE' }),
  candles: (s: string, tf: string, raw = false) => req<Candles>(`/api/stocks/${s}/candles?tf=${tf}${raw ? '&raw=true' : ''}`),
  corporateActions: (s: string) => req<CorpAction[]>(`/api/stocks/${s}/corporate-actions`),
  saveCorporateAction: (s: string, a: Partial<CorpAction>, id?: number) => req(`/api/stocks/${s}/corporate-actions${id ? '/' + id : ''}`, { method: id ? 'PUT' : 'POST', body: JSON.stringify(a) }),
  deleteCorporateAction: (s: string, id: number) => req(`/api/stocks/${s}/corporate-actions/${id}`, { method: 'DELETE' }),
  importHistory: (s: string, csv: string, already_adjusted: boolean) => req<{ added: number; rows: number; first: string | null }>(`/api/stocks/${s}/import`, { method: 'POST', body: JSON.stringify({ csv, already_adjusted }) }),
  analysis: (s: string, tf: string = '1D') => req<Analysis>(`/api/stocks/${s}/analysis${isIntraday(tf) ? `?tf=${tf}` : ''}`),
  backfillIntraday: (s: string) => req<{ ok: boolean; bars: Record<string, number> }>(`/api/stocks/${s}/intraday/backfill`, { method: 'POST' }),
  setSector: (s: string, sector: string | null) => req(`/api/stocks/${s}/sector`, { method: 'PUT', body: JSON.stringify({ sector }) }),
  reanalyze: (s: string) => req(`/api/stocks/${s}/analyze`, { method: 'POST' }),
  fundamentals: (s: string) => req<Fundamentals>(`/api/stocks/${s}/fundamentals`),
  saveFundamental: (s: string, row: FundRow) => req(`/api/stocks/${s}/fundamentals`, { method: 'POST', body: JSON.stringify(row) }),
  deleteFundamental: (s: string, id: number) => req(`/api/stocks/${s}/fundamentals/${id}`, { method: 'DELETE' }),
  announcements: (s: string) => req<AnnouncementRow[]>(`/api/stocks/${s}/announcements`),
  catalysts: (s: string) => req<Catalysts>(`/api/stocks/${s}/catalysts`),
  headlines: () => req<Headline[]>('/api/market/headlines'),
  confirmFundamental: (s: string, id: number) => req(`/api/stocks/${s}/fundamentals/${id}/confirm`, { method: 'POST' }),
  extractFundamentals: (s: string) => req<{ parsed: string[]; review: string[]; unreadable: string[] }>(`/api/stocks/${s}/fundamentals/extract`, { method: 'POST' }),
  portfolio: () => req<Portfolio>('/api/portfolio'),
  live: (s: string) => req<LiveOverlay>(`/api/stocks/${s}/live`),
  telegramTest: () => req<{ ok: boolean }>('/api/telegram/test', { method: 'POST' }),
  market: () => req<MarketData>('/api/market'),
  positions: () => req<PositionRow[]>('/api/positions'),
  addPosition: (p: Partial<PositionRow>) => req('/api/positions', { method: 'POST', body: JSON.stringify(p) }),
  closePosition: (id: number, exit_date: string, exit_price: number) => req(`/api/positions/${id}/close`, { method: 'POST', body: JSON.stringify({ exit_date, exit_price }) }),
  deletePosition: (id: number) => req(`/api/positions/${id}`, { method: 'DELETE' }),
  catalog: () => req<{ patterns: Record<string, CatalogEntry>; default_display: string }>('/api/catalog'),
  getPref: <T,>(k: string) => req<T | null>(`/api/prefs/${encodeURIComponent(k)}`),
  putPref: (k: string, v: unknown) => req(`/api/prefs/${encodeURIComponent(k)}`, { method: 'PUT', body: JSON.stringify(v) }),
  runEod: () => req('/api/jobs/eod', { method: 'POST' }),
  status: () => req<{ market_open: boolean; telegram: boolean; live_updated: string | null; jobs: { job: string; date: string; ok: boolean; at: string; message: string }[]; settings: { capital: number; risk_pct: number; fees_pct: number; weights: Record<string, number> } }>('/api/status'),
}

export const fmt = (x: number | null | undefined, nd = 2) =>
  x == null || !Number.isFinite(x) ? '–' : x.toLocaleString('en-US', { minimumFractionDigits: nd, maximumFractionDigits: nd })
export const fmtBig = (x: number | null | undefined) => {
  if (x == null || !Number.isFinite(x)) return '–'
  const a = Math.abs(x)
  if (a >= 1e9) return (x / 1e9).toFixed(2) + 'B'
  if (a >= 1e6) return (x / 1e6).toFixed(2) + 'M'
  if (a >= 1e3) return (x / 1e3).toFixed(1) + 'K'
  return x.toFixed(0)
}
export const ticker = (s: string) => s.split('.')[0]
