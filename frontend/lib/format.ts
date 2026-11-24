// Display helpers shared by all pages.

export const label = (value: string) =>
  value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\bSdlc\b/g, "SDLC");

export const pct = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? "–" : `${(value * 100).toFixed(digits)}%`;

export function formatDate(value: string | null | undefined) {
  if (!value) return "–";
  return new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export const ROLE_LABELS: Record<string, string> = {
  admin: "Administrator",
  business_analyst: "Business Analyst",
  compliance_officer: "Compliance Officer",
  security_officer: "Security Officer",
  project_manager: "Project Manager",
  auditor: "Auditor",
};

export const SEVERITY_ORDER = ["Critical", "High", "Medium", "Low"] as const;

// Status colours are reserved for state and always rendered next to a text label.
export const SEVERITY_COLOR: Record<string, string> = {
  Critical: "var(--status-critical)",
  High: "var(--status-serious)",
  Medium: "var(--status-warning)",
  Low: "var(--status-good)",
};

export const ISSUE_TYPE_LABELS: Record<string, string> = {
  ambiguity: "Ambiguity",
  incompleteness: "Incompleteness",
  conflict: "Conflict",
  stakeholder_conflict: "Stakeholder conflict",
  duplicate: "Duplicate",
  infeasibility: "Infeasibility",
  untestable: "Not testable",
  missing_acceptance_criteria: "Missing acceptance criteria",
  missing_source: "Missing source",
  undefined_term: "Undefined term",
};

export const isDone = (status: string) => status === "completed" || status === "completed_with_warnings";

export function generatedByLabel(value: string) {
  if (!value || value === "rules") return "Rule-based";
  if (value.startsWith("openai:")) return `OpenAI · ${value.slice(7)}`;
  return value.replace("openai:", "OpenAI · ");
}
