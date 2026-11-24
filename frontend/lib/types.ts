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
  status: "preliminary" | "awaiting_clarification" | "queued" | "running" | "completed" | "completed_with_warnings" | "failed";
  mode: "openai" | "demo";
  model_name: string;
  progress: number;
  current_stage: string;
  error: string;
  recommended_sdlc: string;
  started_at: string | null;
  finished_at: string | null;
  created_by: string;
  // Two-stage analysis
  stage1_status: "preliminary" | "awaiting_clarification" | "analysing" | "completed" | "failed";
  stage2_status: "not_started" | "in_progress" | "regenerating" | "regenerated" | "failed";
  version: number;
  parent_analysis_id: number | null;
  root_analysis_id: number;
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
  status: "pending_review" | "accepted" | "rejected" | "modified" | "obsolete";
  supplementary: Supplementary[];
};

/** Information added after submission, kept apart from the original requirement text. */
export type Supplementary = { kind: "clarification" | "stakeholder" | "acceptance_criterion" | "change"; ref: string; question?: string; text: string };

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
  resolution: "" | "resolved" | "partially_resolved" | "unresolved" | "deferred";
  clarification_refs: string[];
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
  status: "open" | "accepted" | "rejected";
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
  clarifications: string[];
};

export type Approval = {
  id: number;
  analysis_id?: number;
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
  llm_calls: { task: string; model: string; input_tokens: number | null; output_tokens: number | null; duration_ms: number; ok: boolean; error?: string }[];
  stage: "stage1" | "stage2";
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
  clarification: ClarificationSummary;
  versions: VersionInfo[];
};

// ---------------------------------------------------------------------------
// Two-stage analysis
// ---------------------------------------------------------------------------
export type AnswerFormat = "single_choice" | "multiple_choice" | "numeric" | "short_text" | "long_text" | "yes_no";

export type ClarificationQuestion = {
  id: number;
  code: string;
  stage: 1 | 2;
  batch: number;
  order: number;
  question: string;
  category: string;
  issue_type: string;
  priority: "Critical" | "High" | "Medium" | "Low";
  reason: string;
  impact: string;
  answer_format: AnswerFormat;
  options: string[];
  stakeholder_role: string;
  related_requirements: { ref: string; line: number | null; text: string }[];
  requirement_ids: string[];
  topic: string;
  status: "open" | "answered" | "deferred" | "needs_stakeholder";
  resolution: "" | "resolved" | "partially_resolved" | "unresolved" | "deferred";
  parent_question_id: number | null;
  generated_by: string;
  applied_in_version: number | null;
  answer: string;
  response_status: string;
  responded_by: string;
  responder_role: string;
  responded_at: string | null;
  answered_in_stage: number | null;
  affects_requirements: boolean;
};

export type QuestionCounts = {
  total: number; critical: number; high: number; answered: number; deferred: number;
  needs_stakeholder: number; open: number; critical_unanswered: number;
};

export type ClarificationSummary = QuestionCounts & {
  stage2_questions: number;
  stage2_answered: number;
  issues_resolved: number;
  issues_partially_resolved: number;
  issues_unresolved: number;
  requirements_with_clarifications: number;
  influenced_analysis: boolean;
  note: string;
};

export type VersionInfo = {
  id: number; version: number; status: string; stage2_status: string; baseline_status: string;
  parent_analysis_id: number | null; recommended_sdlc: string; created_by: string;
  started_at: string | null; finished_at: string | null;
};

export type RequirementChange = {
  id: number; analysis_id: number; code: string; requirement_id: string;
  change_type: "edit" | "acceptance_criteria" | "new" | "resolve_conflict" | "business_rule" | "constraint" | "obsolete";
  new_kind: string; original_text: string; proposed_text: string; approved_text: string; reason: string; source: string;
  proposed_by: string; proposer_role: string;
  status: "draft" | "pending_review" | "approved" | "rejected" | "changes_requested";
  reviewed_by: string; review_comment: string; reviewed_at: string | null; applied_in_version: number | null; created_at: string;
};

export type StakeholderInteraction = {
  id: number; analysis_id: number; stakeholder_role: string; interviewee: string; topic: string;
  requirement_ids: string[]; entries: { question: string; answer: string }[];
  recorded_by: string; recorder_role: string; status: "pending_review" | "accepted" | "rejected" | "changes_requested";
  reviewed_by: string; review_comment: string; applied_in_version: number | null; created_at: string; updated_at: string;
};

export type RegenerationRunInfo = {
  id: number; source_analysis_id: number; new_analysis_id: number | null; previous_version: number; new_version: number;
  changed_requirements: string[]; inputs: Record<string, number[]>; affected_agents: { agent: string; reasons: string[] }[];
  agents_rerun: string[]; outputs_retained: string[]; outputs_updated: string[];
  status: "running" | "completed" | "failed"; error: string; created_by: string; created_at: string; finished_at: string | null;
};

export type PlanEntry = { key: string; agent: string; reasons: string[]; output: string };
export type RegenerationPlan = {
  source_analysis_id: number; source_version: number; is_latest: boolean; has_inputs: boolean;
  inputs: { changes: { id: number; code: string; type: string; requirement_id: string }[]; questions: { id: number; code: string }[]; interviews: { id: number; role: string }[] };
  changed_requirements: string[]; notes: string[];
  will_rerun: PlanEntry[]; may_rerun: PlanEntry[]; always_rerun: PlanEntry[]; retained: PlanEntry[];
};
