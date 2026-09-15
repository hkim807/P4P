export type DebugSource = {
  id: string;
  name: string;
  robot_type: string;
  status: "online" | "stale" | "offline" | string;
  frame_count: number;
  human_count: number;
  age_s: number;
  clock_domain: string;
  debug_snapshot_revision: number;
  debug_decision_count: number;
  latest_debug_request_id: string | null;
  latest_debug_snapshot_status: "COMPLETED" | "FAILED" | null;
  latest_debug_state_timestamp_us: number | null;
};

export type DebugPromptMessage = {
  role: string;
  content: string;
};

export type DebugRobotInput = {
  source: string;
  latest_value: string | boolean | number | null;
  interpretation: string;
};

export type DebugEvidence = {
  type: "OBSERVATION" | "INTERPRETATION";
  description: string;
  source_fields: string[];
};

export type DebugActionScore = {
  score: number;
  reason: string;
};

export type DebugPolicyResponse = {
  social_summary: string;
  robot_inputs: DebugRobotInput[];
  evidence: DebugEvidence[];
  recommended_action: string;
  target_human_id: string | null;
  preferences: Record<string, unknown>;
  valid_for_ms: number;
  reason_codes: string[];
  decision_confidence: number;
  decision_rationale: string;
  action_scores: Record<string, DebugActionScore>;
  uncertainties: string[];
};

export type DebugInferenceMetadata = {
  provider: string;
  requested_model: string;
  returned_model: string | null;
  endpoint: string;
  decision_mode: "DEBUG";
  prompt_version: string;
  requested_at: string;
  responded_at: string;
  latency_ms: number;
  response_id: string | null;
  created: number | null;
  finish_reason: string | null;
  usage: Record<string, number | null> | null;
};

export type DebugDecisionSnapshot = {
  request_id: string;
  status: "COMPLETED" | "FAILED";
  source_id: string;
  observation_id: string;
  social_state_id: string;
  state_timestamp_us: number;
  clock_domain: string;
  raw_social_state: Record<string, unknown>;
  rendered_messages: DebugPromptMessage[];
  request_parameters: Record<string, unknown>;
  raw_response: string | null;
  validated_response: DebugPolicyResponse | null;
  behavior_intent: Record<string, unknown> | null;
  metadata: DebugInferenceMetadata;
  error: { code: string; message: string } | null;
};

export type DebugSnapshotEnvelope = {
  source_id: string;
  revision: number;
  snapshot: DebugDecisionSnapshot | null;
};

export type DebugDecisionSummary = {
  history_id: number;
  request_id: string;
  source_id: string;
  status: "COMPLETED" | "FAILED";
  observation_id: string | null;
  social_state_id: string | null;
  state_timestamp_us: number | null;
  clock_domain: string | null;
  requested_at: string | null;
  responded_at: string | null;
  latency_ms: number | null;
  provider: string | null;
  requested_model: string | null;
  returned_model: string | null;
  recommended_action: string | null;
  decision_rationale: string | null;
  decision_confidence: number | null;
  error_code: string | null;
  error_message: string | null;
  snapshot_bytes: number;
  recorded_at: string;
};

export type DebugDecisionHistoryPage = {
  source_id: string;
  items: DebugDecisionSummary[];
  next_cursor: number | null;
};

export type DebugDecisionDetail = {
  history_id: number;
  recorded_at: string;
  snapshot: DebugDecisionSnapshot;
};

export type MonitorBootstrap = {
  sources: DebugSource[];
  health: {
    status: string;
    mode: string;
    actuation_enabled: boolean;
  };
};
