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

export interface RuleConfig {
  rule_version: string;
  config: Record<string, unknown>;
}

export interface ChatResponse {
  answer: string;
  sources: Record<string, unknown>[];
  batch_id: number | null;
}
