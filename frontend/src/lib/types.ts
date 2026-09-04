export type Role = "engineer" | "senior" | "planner" | "admin" | "it" | "auditor" | "viewer";

export type Status = "auto_cleared" | "pending_review" | "awaiting_senior" | "reviewed";

export interface Batch {
  batch_id: number;
  label: string;
  source_filename: string | null;
  uploaded_by: string | null;
  uploaded_at: string;
  module_filter: string | null;
  row_count: number;
  quarantined_count: number;
  status: string;
  scored_rule_version: string | null;
  scored_at: string | null;
}

export interface UploadSummary {
  batch_id: number;
  label: string;
  module_filter: string;
  rows_loaded: number;
  rows_quarantined: number;
  quarantine_reasons: Record<string, number>;
}

export interface RunSummary {
  batch_id: number;
  rows_scored: number;
  review_required_Y: number;
  review_required_N: number;
  actions: Record<string, number>;
  risk_levels: Record<string, number>;
  top_reason_codes: Record<string, number>;
  rule_version: string;
  config_hash: string;
}

export interface Recommendation {
  batch_id: number;
  item_id: string;
  stockroom_id: string;
  new_max: number;
  new_rop: number;
  new_min: number;
  review_required: "Y" | "N";
  action: "Increase" | "Maintain" | "Decrease";
  reason_code: string;
  risk_level: "Low" | "Medium" | "High";
  confidence: number;
  explanation: string;
  exposure_usd: number;
  model_version: string;
  rule_version: string;
  scored_at: string;
  status: Status;
  route: string;
  consumable: string;
  agreement: "match" | "diverge" | "none" | "";
  /** Which benchmark `agreement` was measured against. "factory" = the
   *  engineer's own number in this upload; "prior_review" = their last decision
   *  on this part; "" = no benchmark existed. */
  agreement_source: "factory" | "prior_review" | "";
  /* Queue-display fields. Attached per page by /recommendations (_enrich) and
   * absent from the single-item detail payload, hence optional. */
  item_desc?: string;
  part_category?: string;
  current_max?: number | null;
  current_rop?: number | null;
  bench_max?: number | null;
  bench_rop?: number | null;
}

export interface BatchSummary {
  batch: {
    batch_id: number; label: string; status: string; row_count: number;
    quarantined_count: number; scored_rule_version: string | null;
  };
  scored: number;
  statuses: Record<string, number>;
  risk_levels: Record<string, number>;
  actions: Record<string, number>;
  consumables: Record<string, number>;
  routes: Record<string, number>;
  agreements: Record<string, number>;
  reason_codes: Record<string, number>;
  exposure_total_usd: number;
  exposure_pending_usd: number;
  bulk_acceptable: number;
  pareto: { items_for_80pct: number; top100_coverage_pct: number };
  export_ready_rows: number;
}

export interface BulkReviewResult {
  batch_id: number;
  decision: "accept" | "reject";
  selected: number;
  reviewed: number;
  awaiting_senior: number;
  skipped: number;
  failed: { item_id: string; error: string }[];
}

export interface RecommendationPage {
  total: number;
  limit: number;
  offset: number;
  items: Recommendation[];
}

export interface Review {
  review_id: number;
  batch_id: number;
  item_id: string;
  reviewer: string;
  role: string;
  decision: "accept" | "override" | "reject";
  current_max: number; current_rop: number; current_min: number;
  engine_max: number; engine_rop: number; engine_min: number;
  final_max: number; final_rop: number; final_min: number;
  comment: string | null;
  justification: string | null;
  requires_senior_approval: number;
  senior_approved_by: string | null;
  senior_approved_at: string | null;
  rule_version: string;
  reviewed_at: string;
}

export interface ItemDetail {
  recommendation: Recommendation;
  status: Status;
  latest_review: Review | null;
  context: Record<string, string | number | null>;
}

export interface JustificationTemplate {
  justification: string;
  definition: string;
}

export interface JustificationTemplatePage {
  templates: JustificationTemplate[];
}

export interface RuleConfig {
  rule_version: string;
  config: Record<string, unknown>;
}

/**
 * Advisory peer evidence. Never applied to Min/ROP/Max and never lowers a risk
 * level — the layer can only add evidence and raise review priority.
 */
export interface SimilarityNeighbour {
  neighbour_rank: number;
  neighbour_item_id: string;
  /** From the peer's own frozen BOM row; null if that row is gone. */
  neighbour_item_desc: string | null;
  neighbour_batch_id: number;
  distance: number;
  similarity_reasons: string;
  neighbour_decision: string | null;
  neighbour_final_max: number | null;
  neighbour_final_rop: number | null;
  neighbour_final_min: number | null;
  neighbour_risk_level: string | null;
  neighbour_reason_code: string | null;
  neighbour_justification: string | null;
  neighbour_comment: string | null;
}

export interface SimilarityResult {
  batch_id: number;
  item_id: string;
  stockroom_id: string;
  similarity_model_version: string;
  neighbour_count: number;
  pool_size: number;
  nearest_distance: number | null;
  /** INTEGER on both dialects, like Review.requires_senior_approval. */
  outlier_score: number;
  is_outlier: number;
  historical_override_rate: number | null;
  historical_upward_override_rate: number | null;
  historical_high_risk_rate: number | null;
  analogue_max_median: number | null;
  analogue_max_p25: number | null;
  analogue_max_p75: number | null;
  analogue_rop_median: number | null;
  analogue_min_median: number | null;
  /**
   * What KIND of part this is, from the engineer-owned lexicon. A constraint,
   * not a weighted feature: peers of a different known category are excluded
   * outright. "" means no rule matched, and nothing was restricted.
   */
  part_category: string;
  /** Comma-joined, like reason_code — renders through <ReasonCodes />. */
  advisory_codes: string;
  confidence: number;
  generated_at: string;
  neighbours: SimilarityNeighbour[];
}

export interface SimilarityRunSummary {
  batch_id: number;
  candidates: number;
  scored: number;
  neighbour_pool: number;
  outliers: number;
  diverging: number;
  no_analogue: number;
  categorised: number;
  uncategorised: number;
  broken_category_rules: string[];
  similarity_model_version: string;
}

/** One lexicon rule. Only confirmed rules affect retrieval. */
export interface PartCategoryRule {
  pattern: string;
  category: string;
  priority: number;
  set_by: string | null;
  confirmed: number;
  confirmed_by: string | null;
  updated_at: string;
}

export interface PartCategoryPage {
  rules: PartCategoryRule[];
  confirmed: number;
  pending: number;
}

export type AssistVerdict =
  | "flag_for_review" | "bulk_accept_candidate" | "needs_context";

export interface AssistResult {
  batch_id: number;
  item_id: string;
  stockroom_id: string;
  verdict: AssistVerdict;
  reasons: string[];
  narrative: string | null;
  /** Written by assist/rules.suggest, never by the model. Both numbers come
   *  from the same source, or both are null. */
  suggested_max: number | null;
  suggested_rop: number | null;
  suggestion_basis: "engine" | "prior_accepted" | "";
  model_version: string;
  assisted_at: string;
}

export interface AssistPage {
  batch_id: number;
  items: AssistResult[];
  counts: Record<AssistVerdict, number>;
  model_version: string;
}

export interface AssistRunSummary {
  batch_id: number;
  rows_assisted: number;
  counts: Record<AssistVerdict, number>;
  model_version: string;
  /** Null when the batch already had peer matches and none were rebuilt. */
  similarity: SimilarityRunSummary | null;
}

export type DormantPolicy = "hold_current" | "fixed_qty" | "zero";

export interface DormantRule {
  rule_id: number;
  scope: "item" | "category" | "default";
  match_key: string;
  policy: DormantPolicy;
  fixed_qty: number | null;
  set_by: string | null;
  confirmed: number;
  confirmed_by: string | null;
  updated_at: string;
}

export interface DormantRulePage {
  rules: DormantRule[];
  confirmed: number;
  pending: number;
}

export interface DormantRuleCoverage {
  batch_id: number;
  dormant_rows: number;
  matched: number;
  pct: number;
  engine_book_usd: number;
  proposed_book_usd: number;
  delta_usd: number;
  confirmed_rules: number;
}

/** Which branch of the agent graph answered. Read-only detail for the trace
 *  panel — the backend enforces what each branch may reach. */
export type ChatIntent =
  "lookup" | "assist" | "advisory" | "propose" | "action" | "unknown";

/**
 * A review-queue action the agent staged for a human to press. It is not a
 * decision and it is not executed: pressing confirm calls the same review
 * endpoint the console tray calls, under the engineer's own role. `executed`
 * is typed as the literal `false` so a card claiming otherwise cannot compile.
 */
export interface StagedAction {
  kind: "confirm_pending" | "discard_pending";
  item_id: string;
  batch_id: number | null;
  stockroom_id: string | null;
  pending_id: number | null;
  proposed_max: number | null;
  proposed_rop: number | null;
  proposed_min: number | null;
  executed: false;
}

export interface ChatResponse {
  answer: string;
  sources: Record<string, unknown>[];
  batch_id: number | null;
  session_id: string;
  turn_id: number;
  intent: ChatIntent;
  staged_action: StagedAction | null;
  next_steps?: NextStepPrediction | null;
}

export interface NextStepSuggestion {
  label: string;
  prompt: string;
}

export interface NextStepPrediction {
  intent: string;
  suggestions: NextStepSuggestion[];
}

export interface ChatSessionSummary {
  session_id: string;
  title: string;
  updated_at: string;
  turn_count: number;
}

export interface ChatHistoryTurn {
  turn_id: number;
  session_id: string;
  batch_id: number | null;
  question: string;
  answer: string | null;
  tool_calls: Array<{
    name: string;
    args: Record<string, unknown>;
    ok: boolean;
  }>;
  provider: string | null;
  model: string | null;
  ts: string;
}

export interface ChatSession {
  session_id: string;
  turns: ChatHistoryTurn[];
}

export interface ChatSessionPage {
  sessions: ChatSessionSummary[];
}

export interface ChatStreamComplete extends ChatResponse {
  type: "complete";
  provider: string;
  model: string;
  tool_calls: Array<{
    name: string;
    args: Record<string, unknown>;
    ok: boolean;
  }>;
}

export type ChatStreamEvent =
  | { type: "request"; query: string; batch_id: number | null;
      provider: string; model: string }
  | { type: "classify"; intent: ChatIntent; provider: string; model: string }
  | { type: "intent_downgraded"; from: ChatIntent; reason: string }
  | { type: "model_start"; attempt: number; phase: string;
      provider: string; model: string }
  | { type: "model_complete"; attempt: number; summary: string }
  | { type: "tool_start"; sequence: number; name: string;
      args: Record<string, unknown> }
  | { type: "tool_result"; sequence: number; name: string;
      status: "ok" | "empty" | "error"; summary: string }
  | { type: "fallback"; reason: string }
  | { type: "agent_complete"; source_count: number; tool_count: number;
      fallback: boolean }
  | { type: "answer_start" }
  | { type: "answer_delta"; delta: string }
  | { type: "prediction_start"; provider: string; model: string }
  | ({ type: "prediction_complete" } & NextStepPrediction)
  | { type: "prediction_error"; message: string }
  | ChatStreamComplete
  | { type: "error"; stage: string; status: number;
      error_type: string; message: string };

/**
 * A change the agent parsed out of chat and STAGED. It is not a decision:
 * nothing here is visible to the WINGS export until a human confirms it, at
 * which point it goes through the same review path as a console decision.
 */
export interface PendingChange {
  pending_id: number;
  batch_id: number | null;
  item_id: string;
  stockroom_id: string;
  proposed_max: number | null;
  proposed_rop: number | null;
  proposed_min: number | null;
  rationale: string | null;
  /** The engineer's own words, stored unmodified. */
  source_utterance: string;
  /** provider:model that parsed it — provenance for the audit trail. */
  parsed_by: string | null;
  status: "pending" | "confirmed" | "discarded" | "superseded";
  created_by: string | null;
  created_at: string;
  confirmed_review_id: number | null;
}

export interface PendingChangePage {
  pending: PendingChange[];
  count: number;
}

export interface ConfirmPendingResult {
  review_id: number;
  item_id: string;
  pending_id: number;
  decision: string;
  final_max: number;
  final_rop: number;
  final_min: number;
  requires_senior_approval: boolean;
  status: Status;
}
