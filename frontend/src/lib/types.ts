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

export interface ChatResponse {
  answer: string;
  sources: Record<string, unknown>[];
  batch_id: number | null;
  session_id: string;
  turn_id: number;
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
