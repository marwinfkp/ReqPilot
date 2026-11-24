"use client";

import { useLoadedAnalysis } from "@/components/analysis-context";
import { AuthenticityBadge, EmptyState, GeneratedBy, Notice, ReqIds, Section, SeverityBadge } from "@/components/common";
import { FindingCard } from "@/components/finding-card";
import { label } from "@/lib/format";
import type { Finding } from "@/lib/types";

function FindingList({ findings, empty }: { findings: Finding[]; empty: string }) {
  return findings.length ? <ul className="grid gap-3 xl:grid-cols-2">{findings.map((f) => <FindingCard key={f.id} f={f} />)}</ul>
    : <EmptyState title={empty} />;
}

export default function IssuesPage() {
  const { analysis: a } = useLoadedAnalysis();
  const ambiguous = a.findings.filter((f) => f.issue_type === "ambiguity" || f.issue_type === "untestable");
  const missing = a.findings.filter((f) => ["incompleteness", "missing_acceptance_criteria", "missing_source", "undefined_term"].includes(f.issue_type));
  const conflicts = a.findings.filter((f) => f.issue_type === "conflict" || f.issue_type === "stakeholder_conflict");
  const assumptions = a.findings.filter((f) => f.kind === "assumption");
  const questions = a.findings.filter((f) => f.question);
  const expert = a.findings.filter((f) => f.kind === "expert_review" || f.kind === "input_warning");
  const verify = a.mappings.filter((m) => m.needs_verification);

  return (
    <div className="space-y-6">
      <Notice>
        These open items did <strong>not</strong> block the analysis: ReqPilot documented assumptions and produced the best possible
        recommendation from the available information. Stakeholders should resolve them before the requirement baseline is approved.
      </Notice>

      <Section title={`Questions stakeholders should answer (${questions.length})`} description="Generated from the quality, compliance and security findings.">
        {questions.length ? (
          <ol className="space-y-2">
            {questions.map((f, i) => (
              <li key={f.id} className="flex gap-3 rounded-md border p-3 text-sm">
                <span className="font-semibold text-muted-foreground">{i + 1}.</span>
                <div className="flex-1"><p>{f.question}</p><div className="mt-1 flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">{label(f.issue_type)}</span><ReqIds ids={f.requirement_ids} max={4} /></div></div>
                <SeverityBadge severity={f.severity} />
              </li>
            ))}
          </ol>
        ) : <EmptyState title="No open questions" />}
      </Section>

      <Section title={`Unresolved conflicts (${conflicts.length})`}><FindingList findings={conflicts} empty="No conflicts detected" /></Section>
      <Section title={`Ambiguous requirements (${ambiguous.length})`}><FindingList findings={ambiguous} empty="No ambiguous requirements" /></Section>
      <Section title={`Missing information (${missing.length})`}><FindingList findings={missing} empty="No missing information" /></Section>

      <Section title={`Assumptions made during analysis (${assumptions.length})`}>
        {assumptions.length ? (
          <ul className="space-y-2">{assumptions.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm"><span className="flex-1">{f.title}</span><ReqIds ids={f.requirement_ids} max={4} /><GeneratedBy value={f.generated_by} /></li>
          ))}</ul>
        ) : <EmptyState title="No assumptions recorded" />}
      </Section>

      <Section title={`Areas requiring expert review (${expert.length + verify.length})`} description="Input warnings, knowledge-base limitations and compliance interpretations that need verification by a compliance officer.">
        {expert.length > 0 && <ul className="mb-4 grid gap-3 xl:grid-cols-2">{expert.map((f) => <FindingCard key={f.id} f={f} />)}</ul>}
        {verify.length ? (
          <ul className="space-y-2">{verify.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm">
              <span className="font-mono text-xs">{m.requirement_id}</span><span>→ {m.document_name} {m.control_ref}</span>
              <AuthenticityBadge value={m.authenticity} /><span className="text-xs text-muted-foreground">confidence {Math.round(m.confidence * 100)}%{!m.citation_verified && " · citation not verified"}</span>
            </li>
          ))}</ul>
        ) : expert.length === 0 && <EmptyState title="Nothing requires expert review" />}
      </Section>
    </div>
  );
}
