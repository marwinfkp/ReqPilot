// TypeScript shapes of the backend responses (see backend/app/serializers.py).

export type User = {
  id: number;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  permissions: string[];
};

export type Project = {
  id: number;
  name: string;
  domain: string;
  domain_label: string;
  description: string;
  jurisdiction: string;
  scale: string;
  budget: string;
  schedule: string;
  stakeholder_availability: string;
  owner_email: string;
  created_at: string;
  analysis_count?: number;
  latest_analysis?: AnalysisBrief | null;
  analyses?: AnalysisBrief[];
};

export type AnalysisBrief = {
  id: number;
  project_id: number;
  project_name: string;
  domain: string;
  status: "queued" | "running" | "completed" | "completed_with_warnings" | "failed";
  mode: "openai" | "demo";
  model_name: string;
  progress: number;
  current_stage: string;
  error: string;
  recommended_sdlc: string;
  started_at: string | null;
  finished_at: string | null;
  created_by: string;
};

export type Requirement = {
  id: number;
  req_id: string;
  statement: string;
  original_text: string;
  source: string;
  kind: "functional" | "non_functional" | "business_rule" | "constraint";
  categories: string[];
  priority: "High" | "Medium" | "Low";
  risk_level: "High" | "Medium" | "Low";
  confidence: number;
  dependencies: string[];
  user_story: string;
  acceptance_criteria: string[];
  test_recommendations: string[];
  generated_by: string;
  status: "pending_review" | "accepted" | "rejected" | "modified";
};

export type Finding = {
  id: number;
  kind: string;
  issue_type: string;
  severity: "Low" | "Medium" | "High" | "Critical";
  title: string;
  explanation: string;
  evidence: string;
  suggestion: string;
  question: string;
  requirement_ids: string[];
  generated_by: string;
};

export type Mapping = {
  id: number;
  requirement_id: string;
  document_id: number | null;
  chunk_id: number | null;
  document_name: string;
  doc_type: string;
  authenticity: "official" | "illustrative" | "reference";
  control_ref: string;
  control_title: string;
  jurisdiction: string;
  version: string;
  evidence_text: string;
  citation_verified: boolean;
  rationale: string;
  confidence: number;
  needs_verification: boolean;
  generated_by: string;
  status: "pending" | "approved" | "flagged";
};

export type Risk = {
  id: number;
  risk_id: string;
  description: string;
  category: string;
  likelihood: number;
  impact: number;
  score: number;
  severity: "Low" | "Medium" | "High" | "Critical";
  mitigation: string;
  requirement_ids: string[];
  generated_by: string;
};

export type Factor = {
  id: string;
  name: string;
  low_meaning: string;
  high_meaning: string;
  rule_value: number;
  value: number;
  adjustment: number;
  explanation: string;
  assumption: boolean;
  llm_reason: string;
};

export type BreakdownItem = {
  factor: string;
  name: string;
  value: number;
  affinity: number;
  weight: number;
  fit: number;
  points: number;
  max_points: number;
};

export type RankedModel = {
  model: string;
  score: number;
  base_score: number;
  penalties: { rule: string; description: string; points: number }[];
  is_hybrid: boolean;
  breakdown: BreakdownItem[];
  strengths: string[];
  weaknesses: string[];
  description: string;
  advantages: string[];
  disadvantages: string[];
  components: string[];
  rank: number;
  suitable: boolean;
  llm_note?: string;
};

export type Sdlc = {
  id: number;
  recommended_model: string;
  is_hybrid: boolean;
  factors: Factor[];
  ranking: RankedModel[];
  rules_applied: { rule: string; description: string; penalties: Record<string, number> }[];
  justification: string;
  trade_offs: string[];
  hybrid_explanation: string;
  generated_by: string;
  status: "pending_approval" | "approved" | "overridden";
  approved_model: string;
};

export type Phase = {
  order: number;
  name: string;
  stage: string;
  description: string;
  activities: string[];
  roles: string[];
  deliverables: string[];
  security: string[];
  testing: string[];
  compliance: string[];
  entry_criteria: string[];
  exit_criteria: string[];
  approval_gates: { name: string; approvers: string[] }[];
  traceability: string[];
  key_requirements: string[];
  verifies: string;
  customised: string[];
  generated_by: string;
};

export type Workflow = {
  id: number;
  sdlc_model: string;
  phases: Phase[];
  customisations: { reason: string; phase: string; field: string; item: string }[];
  generated_by: string;
  status: "draft" | "approved" | "changes_requested";
};

export type TraceRecord = {
  id: number;
  requirement_id: string;
  source_statement: string;
  source: string;
  categories: string[];
  regulatory_controls: string[];
  security_controls: string[];
  user_story: string;
  acceptance_criteria: string[];
  tests: string[];
  workflow_phases: string[];
  missing_links: string[];
  coverage: number;
};

export type Approval = {
  id: number;
  analysis_id: number;
  target_type: string;
  target_id: string;
  action: string;
  reviewer_email: string;
  reviewer_role: string;
  comment: string;
  previous_value: Record<string, unknown>;
  new_value: Record<string, unknown>;
  timestamp: string;
};

export type AgentRun = {
  id: number;
  order: number;
  agent: string;
  status: "completed" | "partial" | "failed" | "skipped";
  mode: string;
  duration_ms: number;
  message: string;
  llm_calls: { task: string; model: string; input_tokens: number | null; output_tokens: number | null; duration_ms: number; ok: boolean }[];
};

export type SecurityArea = {
  area: string;
  label: string;
  status: "covered" | "partial" | "missing";
  requirement_ids: string[];
  safeguard: string;
};

export type UseCase = {
  id: string;
  name: string;
  actor: string;
  preconditions: string[];
  main_flow: string[];
  alternate_flows: string[];
  postconditions: string[];
  requirement_ids: string[];
  generated_by: string;
};

export type Metrics = Record<string, number | string | null> & {
  submitted: number;
  extracted: number;
  functional: number;
  non_functional: number;
  quality_issues: number;
  conflicts: number;
  ambiguities: number;
  compliance_mappings: number;
  compliance_gaps: number;
  high_risks: number;
  high_risk_findings: number;
  recommended_sdlc: string;
  sdlc_score: number | null;
  traceability_coverage: number;
  duration_seconds: number;
  retrieval_method: string;
  llm_calls: number;
};

export type AnalysisPayload = AnalysisBrief & {
  project: Project;
  input_text: string;
  input_sources: { name: string; chars: number }[];
  submitted_count: number;
  summary: string;
  key_findings: string[];
  metrics: Metrics;
  security_coverage: SecurityArea[];
  applicable_sources: { document_id: number; name: string; doc_type: string; authenticity: string; jurisdiction: string; version: string; mapped_requirements: string[] }[];
  use_cases: UseCase[];
  validation_report: { check: string; status: "pass" | "warn" | "fail" | "info"; detail: string }[];
  baseline_status: "draft" | "approved";
  agent_runs: AgentRun[];
  requirements: Requirement[];
  findings: Finding[];
  mappings: Mapping[];
  risks: Risk[];
  traceability: TraceRecord[];
  approvals: Approval[];
  sdlc: Sdlc | null;
  workflow: Workflow | null;
};
