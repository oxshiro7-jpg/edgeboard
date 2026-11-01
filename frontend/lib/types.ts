// Mirrors the backend engine output (app/engine/pipeline.py). Keep in sync.

export type RiskCategory = "Very Low" | "Low" | "Moderate" | "High" | "Very High";
export type MarketStatus = "recommended" | "avoid" | "no_price";
export type LabelTag =
  | "Highest Confidence" | "Strong Pick" | "Conservative Pick" | "Value Pick"
  | "Higher Risk" | "Optional" | "Avoid" | "Insufficient Edge" | "No Price";

export interface Settlement { win: number; half_win: number; push: number; half_loss: number; loss: number }

export interface Market {
  key: string;
  label: string;
  family: string;
  model_probability: number;
  settlement: Settlement;
  fair_odds: number | null;
  bookmaker_odds: number | null;
  implied_probability: number | null;
  no_vig_probability: number | null;
  edge_pp: number | null;
  ev: number | null;
  confidence: number;
  confidence_level: string;
  confidence_factors: Record<string, number>;
  risk_score: number;
  risk: RiskCategory;
  risk_factors: Record<string, number>;
  model_disagreement: number;
  warnings: string[];
  status: MarketStatus;
  label_tag: LabelTag;
  pick_score: number | null;
  pick_score_parts?: Record<string, number>;
  reasons_rejected: string[];
  why?: string[];
  risks?: string[];
}

export interface RankedPick extends Market {
  fixture_id: string;
  match: string;
  competition: string;
  kickoff: string;
  home: string;
  away: string;
}

export interface WindowStats {
  matches: number; points: number; points_per_game: number; weighted_points_per_game: number;
  goals_for: number; goals_against: number; xg_for: number | null; xg_against: number | null; xgd: number | null;
  npxg_for: number | null; adj_xg_for: number | null; adj_xg_against: number | null; expected_points: number | null;
  goals_minus_xg: number | null; shots: number | null; shots_on_target: number | null; big_chances: number | null;
  big_chances_conceded: number | null; form_string: string;
}

export interface TeamBlock {
  id: string; name: string; short_name: string; badge_url: string | null; elo: number; league_position: number | null;
  last5: WindowStats | null; last10: WindowStats | null; venue: WindowStats | null;
  style: Record<string, number | string | null>;
  absences: { player: string; position: string; importance: number; reason: string; status: string }[];
  absence_impact: { attack_multiplier: number; defence_multiplier: number; uncertainty: number; lines: string[] };
  lineup: { status: "confirmed" | "predicted" | "unknown"; formation: string | null; starters: string[] };
  ratings: Record<"attack" | "defence" | "form" | "venue_strength" | "squad_availability" | "model_rating", number | null>;
}

export interface Component { name: string; weight: number; home: number; draw: number; away: number; lambdas: number[] | null; note: string }

export interface Movement {
  market_key: string; opening: number; current: number; high: number; low: number;
  change_pct: number; implied_shift_pp: number; flags: string[]; history: { t: string; odds: number }[];
}

export type VerificationStatus = "VERIFIED" | "UNVERIFIED" | "ODDS UNAVAILABLE" | "DATA INCOMPLETE" | "POSTPONED" | "CANCELLED";

export interface Provenance { provider: string; fixture_id: string; verified_at: string; home_team_id: string; away_team_id: string; competition_id: string }

export interface Analysis {
  fixture_id: string;
  provenance: Provenance;
  verification: { status: VerificationStatus; messages: string[] };
  competition: { id: string; name: string; type: string };
  kickoff: string;
  status: string;
  data_updated_at: string | null;
  model: { version: string; generated_at: string; input_hash: string };
  home: TeamBlock;
  away: TeamBlock;
  prediction: {
    probabilities: { home: number; draw: number; away: number };
    expected_goals: { home: number; away: number; total: number };
    expected_score_range: string;
    score_distribution: { score: string; p: number }[];
    score_matrix: number[][];
    components: Component[];
    model_disagreement: number;
    goals_disagreement: number;
    cards_mean: number | null;
    corners_mean: number | null;
  };
  data_quality: { score: number; factors: Record<string, number>; gaps: string[] };
  context: { notes: string[]; uncertainty: number };
  flags: { team: string; kind: string; text: string }[];
  tactics: { favours: string; text: string }[];
  h2h: { matches: { date: string; venue: string; score: string; xg: string | null }[]; weight_in_model: number; note: string };
  markets: Market[];
  ranking: Record<"best_overall" | "best_conservative" | "best_value" | "best_higher_odds" | "best_goals" | "best_result", Market | null>
    & { optional: Market[]; avoided: Market[] };
  summary: {
    status: "Strong opportunity" | "Moderate opportunity" | "No edge" | "Avoid";
    recommended_count: number; best_market: string | null; best_pick_score: number | null;
    value_score: number; confidence: number | null; risk: RiskCategory | null; max_edge_pp: number | null;
  };
  no_bet: { no_bet: boolean; reasons: string[] };
  movement: Movement[];
}

type TeamHead = { id: string; name: string | null; short_name?: string; badge_url?: string | null };

/** One fixture from the provider's slate. Analysis fields exist only when `analysed` is true. */
export type MatchCard = Partial<Analysis["summary"]> & {
  fixture_id: string; verification_status: VerificationStatus; verification_messages: string[];
  provider: string | null; analysed: boolean; kickoff: string | null;
  competition: { id: string; name: string } | null;
  home: TeamHead | null; away: TeamHead | null;
  match_status?: string;
  odds?: { H: number | null; D: number | null; A: number | null };
  probabilities?: Analysis["prediction"]["probabilities"];
  market_probabilities?: Analysis["prediction"]["probabilities"] | null;
  expected_goals?: Analysis["prediction"]["expected_goals"];
  expected_score_range?: string;
  data_quality?: number;
  data_updated_at?: string | null;
  no_bet?: Analysis["no_bet"];
};

/** /api/matches/{fixture_id}: verification first; analysis is null unless verified. */
export interface MatchEntry {
  fixture_id: string; status: VerificationStatus; messages: string[]; analysis: Analysis | null;
  verification: { fixture: unknown; listed: { home: TeamHead; away: TeamHead; kickoff: string | null } | null };
}

export interface Board {
  date: string;
  model_version: string;
  verification: { fixtures_from_provider: number; by_status: Partial<Record<VerificationStatus, number>> };
  hero: {
    todays_games: number; verified_games: number; recommended_picks: number; average_confidence: number | null; average_ev_pct: number | null;
    active_multiple: null; current_streak: string | null; monthly_roi: number | null; record_source?: string;
  };
  summary: {
    matches_analysed: number; passed_screening: number; with_measurable_edge: number; matches_with_recommendations: number;
    recommendations: number; high_confidence: number; value_opportunities: number; no_bet_matches: number;
    rejected_selections: number;
    best_edge: { match: string; selection: string; model: number; market: number; edge_pp: number } | null;
  };
  board: Record<"best_picks" | "highest_confidence" | "best_value" | "conservative" | "high_risk_value" | "avoid", RankedPick[]>;
}
