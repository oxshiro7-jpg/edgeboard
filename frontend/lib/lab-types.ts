// Shapes returned by the phase 4-8 endpoints (backend app/engine/multiples.py, tracking.py, backtest.py).
export interface Leg {
  fixture_id: string; key: string; odds: number; label: string; match: string; model_probability: number;
  ev: number | null; confidence: number; risk: string; risk_score: number; pick_score: number | null;
  data_quality: number; status: string; family: string;
}
export interface Correlation { a: string; b: string; a_label: string; b_label: string; joint: number; independent: number; lift: number; kind: "positive" | "negative" | "conflicting"; text: string }
export interface Multiple {
  legs: Leg[]; combined_odds: number; probability: number; naive_probability: number; ev: number; fair_odds: number | null;
  correlations: Correlation[]; confidence: number; risk_score: number; risk: string; data_quality: number; health: number; notes: string[];
}
export interface BuildResult { mode: string; requested_legs: number; built: boolean; multiple?: Multiple; reason?: string; name?: string }
export interface OptimiseResult {
  original: Multiple; optimised: Multiple; removed: { leg: Leg; reasons: string[]; effect: string }[];
  diagnostics: Record<"weakest_leg" | "lowest_ev" | "highest_risk" | "lowest_confidence" | "most_correlated", string | null>;
}
export interface SlipResult {
  slip: Multiple; health: number; main_problem: string; legs: (Leg & { fair_odds: number | null; problems: string[] })[];
  fix: OptimiseResult | null; replacements: Leg[];
}
export interface Summary {
  picks: number; wins?: number; losses?: number; pushes?: number; win_rate?: number | null; average_odds?: number;
  staked_units?: number; profit_units?: number; roi?: number | null; yield?: number | null; expected_profit_units?: number;
  average_ev?: number; clv?: number | null; beat_closing_pct?: number | null; longest_win_streak?: number; longest_loss_streak?: number;
}
export type BreakdownRow = Summary & { group: string };
export interface CalRow { bucket: string; n: number; predicted: number | null; actual: number | null; gap: number | null; flag: string | null; ci_low: number | null; ci_high: number | null }
export interface CalTable { rows: CalRow[]; n: number; ece: number | null; brier: number | null; log_loss: number | null; degraded: boolean; message: string }
export interface Challenge {
  id: string; name: string; kind: string; target: number; progress: number; done: number; status: string; bar: string;
  games_analysed: number | null; picks_placed: number; wins: number; losses: number; current_streak: string; longest_streak: number;
  average_odds: number | null; wagered_units: number; profit_units: number; roi: number | null; pending: number; source: string;
}
