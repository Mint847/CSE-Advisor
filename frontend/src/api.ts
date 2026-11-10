// Typed client for the FastAPI backend. Every call carries the signed-in user's ID token (when sign-in is on).
import { authHeader } from './auth/session'

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
export interface Headline {
  source: string; title: string; link: string; published: string | null; tone: number | null; symbols?: string[]
  kind?: 'local' | 'company' | 'macro' | 'global'; read_by?: 'Claude' | 'keywords'; category?: string | null; theme?: string | null
  sl_market_tone?: number | null; reason?: string | null; counted?: boolean
}

/* ---- Portfolio */
export interface PortfolioFigures { cost: number; market_value?: number; unrealised_net?: number; unrealised_net_pct?: number; realised_net?: number; realised_net_pct?: number; stop?: number; pl_at_stop?: number; drop_to_stop_pct?: number }
export interface Portfolio {
  open_positions: number; invested: number; market_value: number; unrealised_net: number; unrealised_net_pct: number | null; realised_net: number
  risk_to_stops: number; positions_without_stop: string[]; largest: { symbol: string; value: number; pct: number | null } | null
  by_stock: { symbol: string; value: number; pct: number | null }[]; by_sector: { sector: string; value: number; pct: number | null }[]
  fee_pct_per_side: number; fees_paid?: number; fees_to_sell_open?: number; rows: Record<string, PortfolioFigures>
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
/** A split, bonus or rights issue recorded after the buy date; for a split/bonus, the quantity and price in today's shares. */
export interface CorpHint { id: number; kind: string; ex_date: string; ratio: string | null; share_multiplier: number | null; text: string; suggest?: { quantity: number; entry_price: number } }
export interface CostInfo { per_side_pct: number; slippage_pct: number; parts: { label: string; pct: number }[] }
export interface PositionRow { id: number; symbol: string; entry_date: string; entry_price: number; quantity: number; stop: number | null; target: number | null; closed: boolean; exit_date: string | null; exit_price: number | null; notes: string | null; price: number | null; review: PositionReview | null; verdict: Verdict | null; review_analyst?: PositionReview | null; verdict_analyst?: Verdict | null; review_smc?: PositionReview | null; verdict_smc?: Verdict | null; warnings?: ExitWarning[]; corp_since_entry?: CorpHint[] }
export interface ExitWarning { kind: 'pattern' | 'trend'; id: string | null; title: string; text: string; crossed_today?: boolean }
/** One year of daily prices: volatility, Sharpe, beta to the ASPI and the worst fall (same maths as the QNT tab). */
export interface QuantCols { vol: number | null; sharpe: number | null; beta: number | null; mdd: number | null }
export interface ScreenerRow {
  symbol: string; name: string; close: number | null; chg1d: number | null; ret20: number | null; ret60: number | null; ret120: number | null
  rs120: number | null; dist52: number | null; ext20: number | null; trendup: boolean; above50: boolean; turn: number | null; rs_rank: number | null
  qualifies: boolean; hits: number; missing: string[]; reason: string; on_watchlist: boolean; quant?: QuantCols | null
}
export interface ScreenerData {
  run: { date: string; created_at: string; rows: ScreenerRow[]; meta: { rule: string; tested: string; checked: number; failed: string[]; liquid: number; qualifying: number
    min_median_turnover_lkr: number; aspi_ret120: number | null; breadth_above50: number | null; last_bar: string; full_recheck: boolean; seconds: number } } | null
  progress: { running: boolean; done: number; total: number; phase: string }
  enabled: boolean
}
export interface JournalStats { group: string; calls: number; entered: number; n20: number; mean20: number | null; win20: number | null; n60: number; mean60: number | null; win60: number | null
  plan_n: number; target_first: number | null; stop_first: number | null; plan_mean_net: number | null; reference: { n: number; plan_target_first: number; plan_stop_first: number; mean_net: number } | null }
export interface JournalRow { id: number; symbol: string; date: string; engine: 'original' | 'analyst' | 'smc'; verdict: Verdict; setup: string | null; triggered: boolean; close: number
  plan: { zl: number; zh: number; ideal: number; stop: number; t1: number | null }; rule_ok: boolean | null
  outcome: { entered: boolean; entry_px: number | null; r5?: number | null; r20?: number | null; r40?: number | null; r60?: number | null; plan?: string; plan_net?: number } | null }
export interface JournalData { total: number; scored: number; since: string | null; groups: JournalStats[]; recent: JournalRow[] }
export interface WatchList { id: number; name: string; count: number }
export interface ListsInfo { lists: WatchList[]; portfolio: number; max_lists: number }
export interface AllStock {
  symbol: string; name: string; price: number | null; change_pct: number | null; volume: number | null; turnover: number | null
  trades: number | null; market_cap: number | null; traded: string | null; sector?: string | null; industry?: string | null
  lists: number[]; held: boolean; candidate: { qualifies: boolean; hits: number; rs_rank: number | null } | null; quant?: QuantCols | null
}
export interface HeatStock { symbol: string; name: string; price: number; change_pct: number; turnover: number; market_cap: number; sector: string; industry: string | null; mine: boolean }
export interface Heatmap { stocks: HeatStock[]; meta_age_days: number | null; indices: { code: string; name: string; change_pct: number | null; value: number | null }[]; indices_date: string | null }
export interface FeedItem { type: 'announcement' | 'headline'; id: string; at: string | null; title: string; source: string; symbols: string[]; category: string | null; tone: number | null; importance: number | null; link: string | null; detail: string | null }
export interface Feed { count: number; items: FeedItem[]; names: Record<string, string> }
export interface CalEvent { symbol: string; date: string; kind: string; title: string; detail: string; days: number; sessions?: number | null; mine: boolean }
export interface Calendar { today: string; events: CalEvent[]; names: Record<string, string> }
export interface KeyStats {
  price: number | null; prev_close: number | null; day_low: number | null; day_high: number | null; hi_52w: number | null; lo_52w: number | null; pos_52w: number | null
  pct_from_high: number | null; hi_ytd: number | null; lo_ytd: number | null; hi_all: number | null; lo_all: number | null; volume_ytd: number | null; volume_12m: number | null
  turnover_ytd: number | null; market_cap: number | null; market_cap_pct: number | null; foreign_pct: number | null; shares_issued: number | null; par: number | null
  sector: string | null; industry: string | null; isin: string | null
}
export interface QuoteCard { quote: AllStock; stats: KeyStats; lists: number[]; held: boolean; tracked: boolean }
export interface GlobalQuote {
  close: number | null; change_pct: number | null; change?: number | null; open?: number | null; high?: number | null; low?: number | null
  volume?: number | null; avg_volume_30d?: number | null; market_cap?: number | null; currency?: string | null; sector?: string | null; industry?: string | null
  hi_52w?: number | null; lo_52w?: number | null; perf_w?: number | null; perf_1m?: number | null; perf_3m?: number | null; perf_ytd?: number | null; perf_1y?: number | null
  pe?: number | null; div_yield?: number | null; delay_min?: number | null; name?: string | null; stored?: boolean
}
export interface GlobalHeadline { title: string; link: string; outlet: string | null; published: string | null; tone: number | null; market: boolean }
export interface GlobalRow {
  symbol: string; ticker: string; exchange: string; name: string; group: string; currency: string | null; quote: GlobalQuote | null; spark: number[]
  analysis: { date: string; verdict: Verdict; score: number; verdict_analyst: Verdict | null; score_analyst: number | null; verdict_smc: Verdict | null; score_smc: number | null } | null
  move: { date: string; pct: number; headline: GlobalHeadline | null } | null
  headline: GlobalHeadline | null
}
export interface GlobalList { rows: GlobalRow[]; groups: string[]; move_pct: number; run: { at: string; ok: boolean } | null; running: boolean }
export interface GlobalMoveRow { date: string; pct: number; checked: boolean; headlines: GlobalHeadline[] }
export interface GlobalNews { symbol: string; name: string; aliases: string[]; move_pct: number; moves: GlobalMoveRow[]; recent: GlobalHeadline[]; searching: boolean }
export interface GlobalQuoteCard { symbol: string; name: string; group: string; aliases: string[]; quote: GlobalQuote | null }
export interface QPoint { time: string; value: number }
type QFigs = Record<string, unknown> & { missing?: Record<string, string> }
export interface PortfolioQuant {
  curve: { available: boolean; reason?: string; start?: string; money_in?: number; money_out?: number; dividends?: number; skipped?: string[]
    entry_estimated?: string[]; fee_pct_per_side?: number; figures?: QFigs; relative?: QFigs; aspi_figures?: QFigs
    charts?: { portfolio: QPoint[]; aspi: QPoint[]; drawdown: QPoint[]; pnl: QPoint[]; value: QPoint[]; monthly: QuantReport['charts']['monthly'] } }
  risk: { available: boolean; reason?: string; total_value?: number; sessions?: number; from?: string; to?: string; volatility?: number; beta?: number | null
    diversification_ratio?: number | null; var95_lkr?: number; cvar95_lkr?: number; var95_pct?: number; cvar95_pct?: number; any_thin?: boolean
    rows?: { symbol: string; label: string; value: number; weight: number; volatility: number; risk_share: number; thin: boolean; share_no_trade: number | null }[]
    correlation?: { labels: string[]; matrix: number[][] }
    stress?: { horizon: string; sessions: number; from: string; to: string; aspi: number; portfolio_pct: number; portfolio_lkr: number; beta_pct: number; beta_lkr: number; estimated: string[] }[] }
  rate: QuantReport['meta']['rate']; sessions_per_year: number; gs_quant: string
}
export interface ListBasket {
  id: number; name: string; available: boolean; reason?: string; symbols?: string[]; missing?: string[]; too_new?: string[]; start?: string; end?: string
  limited_by?: string | null; rebalance_day_range?: [number, number]; figures?: QFigs; hold_figures?: QFigs; aspi_figures?: QFigs; relative?: QFigs
  charts?: { basket: QPoint[]; hold: QPoint[]; aspi: QPoint[] }
}
export interface LabParam { key: string; label: string; default: number; min: number; max: number; step: number }
export interface LabRuleDef { type: string; label: string; help: string; params: LabParam[] }
export type LabLeaf = { type: string } & Record<string, number | string>
export type LabRule = LabLeaf | { op: 'all' | 'any'; rules: LabRule[] } | { op: 'not'; rule: LabRule }
export interface LabExit { hold_days?: number | null; stop_pct?: number | null; target_pct?: number | null; trail_pct?: number | null; rule?: LabRule | null }
export interface LabSpec { name?: string; symbols?: string[]; years: number | 'all'; capital: number; max_positions: number; basis: 'total' | 'price'; slippage_pct: number; entry: LabRule; exit: LabExit }
export interface LabCatalog {
  rules: LabRuleDef[]; presets: { id: string; name: string; blurb: string; spec: Partial<LabSpec> }[]
  defaults: { capital: number; years: number; basis: 'total' | 'price'; max_positions: number; slippage_pct: number; brokerage_pct: number }
  stocks: { symbol: string; label: string; name: string | null; sessions: number }[]; max_symbols: number
}
type LabFigs = Record<string, number | string | null | Record<string, string>> & { missing?: Record<string, string> }
export interface LabResult {
  spec: LabSpec & { name: string; symbols: string[] }
  meta: { universe: string[]; start: string; end: string; sessions: number; sessions_per_year: number; rate: QuantReport['meta']['rate']; hold_stocks: number; of_stocks: number
    brokerage_pct: number; slippage_pct: number; entry_text: string; exit_text: string; seconds: number; gs_quant: string }
  parity: { ok: boolean; max_rel_diff: number; sessions_compared: number; engine_trades: number; own_trades: number; fills_differing: number }
  charts: { strategy: QPoint[]; hold: QPoint[]; aspi: QPoint[]; cash: QPoint[]; drawdown: QPoint[]; invested: QPoint[] }
  figures: LabFigs; hold_figures: LabFigs; aspi_figures: LabFigs; relative: LabFigs
  stats: { trades: number; open_at_end: number; wins: number; win_rate: number | null; avg_win: number | null; avg_loss: number | null; profit_factor: number | null
    avg_sessions: number | null; best: number | null; worst: number | null; exposure: number | null; turnover_per_year: number | null
    brokerage: number; slippage: number; interest: number; min_cash: number; overdrawn_days: number }
  trades: { rows: { stock: string; in: string; out: string | null; sessions: number | null; qty: number; ret: number; pnl: number; costs: number; why: string }[]; total: number; shown: number }
  halves: { name: string; from: string; to: string; total_return: number; sharpe: number | null; hold_total_return: number | null; hold_sharpe: number | null; trades: number }[]
  luck: { runs: number; total: { median: number; p5: number; p95: number; beat: number | null }; sharpe?: { median: number; p5: number; p95: number; beat: number; runs: number }
    bins: { from: number; to: number; count: number }[] } | null
  dividends: { left_out?: Record<string, number> }
  summary: string[]
}
export interface LabRun { id: string; status: 'running' | 'done' | 'error'; progress: number; stage: string; cached: boolean; elapsed: number; error?: string; result?: LabResult }
export interface LabSaved { name: string; spec: LabSpec; saved_at: string }
type LabFigs2 = Record<string, number | string | null | Record<string, string>> & { missing?: Record<string, string> }
export interface EventCatalog {
  company_kinds: { id: string; label: string; count: number }[]; shock_sources: { id: string; label: string }[]; world_stocks: { id: string; label: string }[]
  stocks: { id: string; label: string }[]; before: number; after: number; min_n: number
}
export type EventSpec = { kind: 'company'; event: string; symbols: string[]; adjust: 'market' | 'none' }
  | { kind: 'shock'; source: string; direction: 'down' | 'up' | 'abs'; threshold_pct: number; response: string; adjust: 'market' | 'none' }
export interface EventStudy {
  available: boolean; reason?: string; notes?: string[]
  meta: { title: string; response: string; adjusted: boolean; n_events?: number; n_dates?: number; before?: number; after?: number; min_n?: number; start?: string; end?: string; reacts?: string }
  path?: { day: number; mean: number | null; se: number | null; n: number }[]
  horizons?: { sessions: number; mean: number | null; median: number | null; positive: number | null; t: number | null; n: number }[]
  pre?: { mean: number | null; t: number | null; n: number }
  table?: { stock: string; date: string; day0: number | null; car5: number | null; car20: number | null }[]
  summary?: string[]
}
export interface CorrOptions { indices: { id: string; label: string }[]; sectors: { id: string; label: string }[]; cse: { id: string; label: string }[]; world: { id: string; label: string }[]; max: number }
export interface CorrResult {
  ids: string[]; labels: string[]; thin: boolean[]; cse: boolean[]; matrix: (number | null)[][]; n: number[][]; significant: boolean[][]
  pairs: { a: string; b: string; r: number; n: number; t: number | null; significant: boolean }[]; missing: string[]; window: string; freq: string; start: string; end: string; notes: string[]
  rolling?: { a: string; b: string; window: number; points: QPoint[] }
}
export interface BasketResult {
  available: boolean; reason?: string; name: string; symbols?: string[]; missing?: string[]; too_new?: string[]; start?: string; end?: string; limited_by?: string | null
  weights?: { symbol: string; weight: number }[]; weights_note?: string | null; rebalance?: string; rebalance_day_range?: [number, number] | null
  figures?: LabFigs2; hold_figures?: LabFigs2; aspi_figures?: LabFigs2; relative?: LabFigs2; charts?: { basket: QPoint[]; hold: QPoint[]; aspi: QPoint[] }
}
export interface CoverageEntry { module: string; status: 'used' | 'recreated' | 'not_possible' | 'not_applicable' | 'plumbing'; what: string; where: string | null; stage: string | null; why: string | null }
export interface MarketRisk {
  available: boolean; reason?: string; as_of?: string; sessions_per_year?: number; summary?: string[]
  volatility?: { now: number; percentile: number; regime: 'calm' | 'normal' | 'stormy'; median: number; window: number; recent: number | null; points: QPoint[] }
  drawdown?: { now: number; max_1y: number | null; points: QPoint[] }
  year?: Record<string, number | null>
  correlation?: Record<string, { label: string; now: number; all: number; points: QPoint[] }>
}
export interface EqsField { id: string; label: string; kind: 'num' | 'text' | 'bool'; unit: string; group: string; help: string; ops: string[] }
export type EqsValue = number | string | boolean | (number | string)[]
export interface EqsFilter { field: string; op: string; value: EqsValue }
export interface EqsSpec { name?: string; filters: EqsFilter[]; sort: { field: string; dir: 'asc' | 'desc' }; limit: number }
export interface EqsCatalog { fields: EqsField[]; presets: { id: string; name: string; blurb: string; spec: Partial<EqsSpec> }[]; max_filters: number; max_rows: number }
export interface EqsResult {
  spec: EqsSpec; total: number; matched: number; shown: number; columns: string[]; notes: string[]
  rows: { symbol: string; name: string | null; sector: string | null; mine: boolean; held: boolean; values: Record<string, number | string | boolean | null> }[]
}
export interface QuantReport {
  meta: {
    symbol: string; label: string; kind: 'cse' | 'world' | 'index'; currency: string; window: string; basis: 'total' | 'price'
    bench: { id: string; label: string }; benchmarks: { id: string; label: string }[]; sessions_per_year: number
    rate: { rate_pct: number; as_of: string | null; source: string; stale: boolean }
    dividends: { count: number; from: string | null; applies: boolean; left_out?: number; approx?: number }
    thin: { share_no_trade: number | null; thin: boolean; limit?: number }; start: string | null; end: string | null; gs_quant: string
  }
  figures: Record<string, number | string | null | { month: string; return: number } | Record<string, string>> & { missing: Record<string, string> }
  relative: Record<string, number | null | Record<string, string>> & { missing: Record<string, string> }
  charts: {
    drawdown: QPoint[]; rolling_vol?: QPoint[]; rolling_beta?: QPoint[]; rolling_corr?: QPoint[]; zscore?: QPoint[]
    histogram: { bins: { from: number; to: number; count: number }[]; var95?: number; mean?: number; n?: number }
    monthly: { years: { year: number; months: Record<string, number>; year_return: number | null; partial: boolean }[]
      seasonal: { month: number; average: number | null; years: number; positive: number | null; t: number | null }[] }
    decomposition: { available: boolean; reason?: string; trend?: QPoint[]; adjusted?: QPoint[]; price?: QPoint[] }
    regression?: { available: boolean; reason?: string; frequency?: string; observations?: number; r_squared?: number; rows?: { factor: string; coefficient: number; t: number }[] }
  }
  summary: string[]
}
export interface AllStocksData { lists: WatchList[]; count: number; rows: AllStock[] }
export interface HealthSource { key: string; label: string; as_of: string | null; stale: boolean; kind: 'session' | 'days' | 'time' }
export interface StepResult { ok: boolean; s?: number; error?: string; n?: number; failed?: Record<string, string>; failed_feeds?: string[] }
export interface Health {
  status: 'ok' | 'partial' | 'stale' | 'failed'; session: string; expected: string; running: boolean; stale: string[]; sources: HealthSource[]
  run: { run_date: string | null; ok: boolean; at: string | null; late: boolean; attempts: number; failed_steps: string[]; analysed?: number | null
    steps?: Record<string, StepResult>; errors?: Record<string, string> | null; late_note?: string | null; message?: string | null }
  spikes?: { count: number; items: { symbol: string; date: string; close: number; neighbours: number[] }[] }
}
export interface Overnight { notes: { series: string; text: string }[]; note: string }
export interface Regime { state: 'risk_on' | 'risk_off' | 'mixed' | 'unknown'; label?: string; text: string; facts: string[]; note?: string; breadth?: number | null; overnight?: Overnight | null }
export interface WorldSeries {
  code: string; name: string; unit: string; last: number; date: string; chg1d: number | null; chg1w: number | null; chg1m: number | null
  spark?: number[]; link: string | null; verdict: 'held' | 'one_half' | 'weak' | 'none' | null
}
export interface FeedState { checked: string; ok: boolean; items: number; last_ok: string | null; error: string | null }
export interface World {
  series: WorldSeries[]; overnight: Overnight | null; news: Headline[]
  study: { date: string | null; period: string | null; events: Record<string, Record<string, number | string | null>> | null }
  feeds: { feeds: Record<string, FeedState>; not_used: { name: string; note: string }[] }
  reader: { enabled: boolean; model: string; usage: { date: string; headlines: number; input_tokens: number; output_tokens: number; calls: number; errors: number; total_input_tokens?: number; total_output_tokens?: number } | null }
  news_tone: { n: number; read: number; tone: number | null; themes: Record<string, number> }
}
export interface CatalogEntry { name: string; tier: number; enabled: boolean; base_reliability: number; category: string; type: string; engine?: Engine }

// writes still on their way to the server (preferences are saved in the background): signing out asks first
let writesInFlight = 0
export const hasUnsyncedChanges = () => writesInFlight > 0

export interface AlertRule { id: number; symbol: string; kind: string; params: Record<string, number | string>; text: string; active: boolean; once: boolean; created_at: string; last_fired: string | null; fired_count: number }
export interface AlertKind { label: string; when: 'live' | 'eod'; fields: Record<string, string> }
export interface InboxEvent { id: number; symbol: string | null; kind: string; text: string; read: boolean; alert_id: number | null; at: string }
export interface Inbox { unread: number; events: InboxEvent[] }
export interface NoteRow { id: number; symbol: string | null; position_id: number | null; body: string; tags: string[]; pinned: boolean; created_at: string; updated_at: string }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const write = !!init?.method && init.method !== 'GET'
  if (write) writesInFlight++
  try {
    const r = await fetch(path, { ...init, headers: { 'Content-Type': 'application/json', ...(await authHeader()), ...(init?.headers ?? {}) } })
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`
      try { const j = await r.json(); msg = j.detail ?? msg } catch { /* not json */ }
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    }
    return r.json() as Promise<T>
  } finally {
    if (write) writesInFlight--
  }
}

/** Fired after a stock is added or removed, so anything holding the tracked-stock list can refresh it. */
export const WATCH_CHANGED = 'cse:watchlist-changed'
export const INBOX_CHANGED = 'cse:inbox-changed'
const inboxChanged = <T,>(p: Promise<T>) => p.then(r => { window.dispatchEvent(new Event(INBOX_CHANGED)); return r })
const changed = <T,>(p: Promise<T>) => p.then(r => { window.dispatchEvent(new Event(WATCH_CHANGED)); return r })

export const api = {
  /** Every stock tracked; or one list (its id), or 'portfolio' (stocks with an open position). */
  watchlist: (list?: number | 'portfolio') => req<WatchItem[]>(`/api/watchlist${list != null ? `?list=${list}` : ''}`),
  addStock: (query: string, listId?: number) => changed(req<{ symbol: string }>('/api/watchlist', { method: 'POST', body: JSON.stringify({ query, list_id: listId ?? null }) })),
  /** Off one list (it stops being tracked when that was its last list), or off every list. */
  removeStock: (s: string, listId?: number) => changed(req<{ removed: string; untracked: boolean }>(`/api/watchlist/${s}${listId != null ? `?list=${listId}` : ''}`, { method: 'DELETE' })),
  lists: () => req<ListsInfo>('/api/lists'),
  createList: (name: string) => req<{ id: number; name: string }>('/api/lists', { method: 'POST', body: JSON.stringify({ name }) }),
  renameList: (id: number, name: string) => req<{ id: number; name: string }>(`/api/lists/${id}`, { method: 'PUT', body: JSON.stringify({ name }) }),
  deleteList: (id: number) => changed(req<{ ok: boolean; untracked: string[] }>(`/api/lists/${id}`, { method: 'DELETE' })),
  allStocks: () => req<AllStocksData>('/api/stocks/all'),
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
  screener: () => req<ScreenerData>('/api/screener'),
  runScreener: (full = false) => req<{ started: boolean; message?: string }>(`/api/screener/run${full ? '?full=true' : ''}`, { method: 'POST' }),
  journal: () => req<JournalData>('/api/journal'),
  regime: () => req<Regime>('/api/regime'),
  world: () => req<World>('/api/world'),
  health: () => req<Health>('/api/health'),
  addPosition: (p: Partial<PositionRow>) => req('/api/positions', { method: 'POST', body: JSON.stringify(p) }),
  closePosition: (id: number, exit_date: string, exit_price: number, review?: string, quantity?: number) => req<{ ok: boolean; sold: number; remaining: number }>(`/api/positions/${id}/close`, { method: 'POST', body: JSON.stringify({ exit_date, exit_price, review: review || null, quantity: quantity ?? null }) }),
  editPosition: (id: number, changes: Record<string, unknown>) => req<{ ok: boolean }>(`/api/positions/${id}`, { method: 'PUT', body: JSON.stringify(changes) }),
  costs: () => req<CostInfo>('/api/costs'),
  dismissCorp: (id: number) => req<{ ok: boolean; dismissed: number }>(`/api/positions/${id}/corp-ack`, { method: 'POST' }),
  eqsCatalog: () => req<EqsCatalog>('/api/eqs/catalog'),
  eqsRun: (spec: EqsSpec) => req<EqsResult>('/api/eqs/run', { method: 'POST', body: JSON.stringify(spec) }),
  globalList: () => req<GlobalList>('/api/global'),
  worldAdd: (query: string, group: string) => req<{ symbol: string; name: string }>('/api/global', { method: 'POST', body: JSON.stringify({ query, group }) }),
  worldEdit: (s: string, e: { group?: string; aliases?: string[]; name?: string }) => req(`/api/global/${encodeURIComponent(s)}`, { method: 'PUT', body: JSON.stringify(e) }),
  worldDelete: (s: string) => req(`/api/global/${encodeURIComponent(s)}`, { method: 'DELETE' }),
  worldRun: () => req<{ started: boolean }>('/api/global/run', { method: 'POST' }),
  worldAnalysis: (s: string, tf: string = '1D') => req<Analysis>(`/api/global/${encodeURIComponent(s)}/analysis?tf=${tf}`),
  worldCandles: (s: string, tf: string = '1D') => req<Candles>(`/api/global/${encodeURIComponent(s)}/candles?tf=${tf}`),
  worldQuote: (s: string) => req<GlobalQuoteCard>(`/api/global/${encodeURIComponent(s)}/quote`),
  worldNews: (s: string) => req<GlobalNews>(`/api/global/${encodeURIComponent(s)}/news`),
  compare: (ids: string[]) => req<{ series: Record<string, { label: string; points: { time: string; value: number }[]; error?: string }> }>(`/api/compare?symbols=${encodeURIComponent(ids.join(','))}`),
  compareOptions: () => req<{ indices: { id: string; label: string }[]; cse: { id: string; label: string; name: string }[]; world: { id: string; label: string; name: string }[] }>('/api/compare/options'),
  labCatalog: () => req<LabCatalog>('/api/lab/catalog'),
  labRun: (spec: LabSpec) => req<LabRun>('/api/lab/run', { method: 'POST', body: JSON.stringify(spec) }),
  labStatus: (id: string) => req<LabRun>(`/api/lab/run/${id}`),
  labSaved: () => req<LabSaved[]>('/api/lab/saved'),
  labSave: (name: string, spec: LabSpec) => req<LabSaved[]>('/api/lab/saved', { method: 'PUT', body: JSON.stringify({ name, spec }) }),
  labDelete: (name: string) => req<LabSaved[]>(`/api/lab/saved?name=${encodeURIComponent(name)}`, { method: 'DELETE' }),
  labEventCatalog: () => req<EventCatalog>('/api/lab/events/catalog'),
  labEvents: (spec: EventSpec) => req<EventStudy>('/api/lab/events', { method: 'POST', body: JSON.stringify(spec) }),
  labCorrOptions: () => req<CorrOptions>('/api/lab/corr/options'),
  labCorr: (body: { ids: string[]; window: string; freq: string; pair?: [number, number] }) => req<CorrResult>('/api/lab/corr', { method: 'POST', body: JSON.stringify(body) }),
  labBasket: (body: { symbols: string[]; weights: 'equal' | 'inverse_vol' | Record<string, number>; rebalance: string; window: string }) => req<BasketResult>('/api/lab/basket', { method: 'POST', body: JSON.stringify(body) }),
  labCoverage: () => req<{ version: string; marquee: string; entries: CoverageEntry[] }>('/api/lab/coverage'),
  marketRisk: () => req<MarketRisk>('/api/market/risk'),
  portfolioQuant: () => req<PortfolioQuant>('/api/portfolio/quant'),
  portfolioBaskets: (window: string) => req<ListBasket[]>(`/api/portfolio/baskets?window=${window}`),
  quant: (s: string, world: boolean, window: string, basis: string, bench?: string) => {
    const sp = new URLSearchParams({ window }); if (!world) sp.set('basis', basis); if (bench) sp.set('bench', bench)
    return req<QuantReport>(`/api/${world ? 'global' : 'stocks'}/${encodeURIComponent(s)}/quant?${sp}`)
  },
  heatmap: () => req<Heatmap>('/api/heatmap'),
  feed: (f: { scope: string; kind: string; tone: string; q: string; days: number; symbol?: string }) => {
    const sp = new URLSearchParams({ scope: f.scope, kind: f.kind, tone: f.tone, q: f.q, days: String(f.days) }); if (f.symbol) sp.set('symbol', f.symbol)
    return req<Feed>(`/api/feed?${sp}`)
  },
  calendar: (scope: string, days = 90) => req<Calendar>(`/api/calendar?scope=${scope}&days=${days}`),
  stats: (s: string, price?: number | null) => req<KeyStats>(`/api/stocks/${s}/stats${price != null ? `?price=${price}` : ''}`),
  quoteCard: (s: string) => req<QuoteCard>(`/api/quote/${encodeURIComponent(s)}`),
  listed: () => req<{ symbol: string; name: string }[]>('/api/listed'),
  alerts: () => req<{ rules: AlertRule[]; kinds: Record<string, AlertKind> }>('/api/alerts'),
  addAlert: (a: { symbol: string; kind: string; params: Record<string, number | string>; once: boolean }) => req<AlertRule>('/api/alerts', { method: 'POST', body: JSON.stringify(a) }),
  editAlert: (id: number, a: { active?: boolean; once?: boolean; params?: Record<string, number | string> }) => req<AlertRule>(`/api/alerts/${id}`, { method: 'PUT', body: JSON.stringify(a) }),
  deleteAlert: (id: number) => req(`/api/alerts/${id}`, { method: 'DELETE' }),
  inbox: (limit = 60) => req<Inbox>(`/api/alerts/inbox?limit=${limit}`),
  markRead: (ids?: number[]) => inboxChanged(req('/api/alerts/read', { method: 'POST', body: JSON.stringify({ ids: ids ?? null }) })),
  clearInbox: () => inboxChanged(req<{ removed: number }>('/api/alerts/clear', { method: 'POST' })),
  notes: (f: { symbol?: string; position_id?: number; tag?: string; q?: string; limit?: number } = {}) => {
    const sp = new URLSearchParams(); Object.entries(f).forEach(([k, v]) => { if (v != null && v !== '') sp.set(k, String(v)) })
    return req<NoteRow[]>(`/api/notes${sp.size ? `?${sp}` : ''}`)
  },
  addNote: (n: { body: string; symbol?: string | null; position_id?: number | null; tags?: string[]; pinned?: boolean }) => req<NoteRow>('/api/notes', { method: 'POST', body: JSON.stringify(n) }),
  editNote: (id: number, n: { body?: string; tags?: string[]; pinned?: boolean }) => req<NoteRow>(`/api/notes/${id}`, { method: 'PUT', body: JSON.stringify(n) }),
  deleteNote: (id: number) => req(`/api/notes/${id}`, { method: 'DELETE' }),
  deletePosition: (id: number) => req(`/api/positions/${id}`, { method: 'DELETE' }),
  catalog: () => req<{ patterns: Record<string, CatalogEntry>; default_display: string }>('/api/catalog'),
  getPref: <T,>(k: string) => req<T | null>(`/api/prefs/${encodeURIComponent(k)}`),
  putPref: (k: string, v: unknown) => req(`/api/prefs/${encodeURIComponent(k)}`, { method: 'PUT', body: JSON.stringify(v) }),
  runEod: () => req('/api/jobs/eod', { method: 'POST' }),
  status: () => req<{ market_open: boolean; telegram: boolean; admin: boolean; auth: boolean; live_updated: string | null; jobs: { job: string; date: string; ok: boolean; at: string; message: string | null }[]; settings: { capital: number; risk_pct: number; fees_pct: number; weights: Record<string, number> } }>('/api/status'),
  claimLocal: () => req<{ ok: boolean; moved: Record<string, number> }>('/api/me/claim-local', { method: 'POST' }),
  telegramOwner: () => req<{ ok: boolean }>('/api/me/telegram-owner', { method: 'POST' }),
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
