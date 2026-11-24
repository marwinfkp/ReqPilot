"use client";

import { useState } from "react";

import { useLoadedAnalysis } from "@/components/analysis-context";
import { ColumnBars, SeverityBars } from "@/components/charts";
import { EmptyState, Section } from "@/components/common";
import { FindingCard } from "@/components/finding-card";
import { cn } from "@/lib/utils";
import type { Finding } from "@/lib/types";

const GROUPS: { key: string; title: string; match: (f: Finding) => boolean }[] = [
  { key: "ambiguity", title: "Ambiguity", match: (f) => f.kind === "quality" && ["ambiguity", "untestable"].includes(f.issue_type) },
  { key: "incompleteness", title: "Incompleteness", match: (f) => f.kind === "quality" && f.issue_type === "incompleteness" },
  { key: "conflicts", title: "Conflicts", match: (f) => ["conflict", "stakeholder_conflict"].includes(f.issue_type) },
  { key: "duplicates", title: "Duplicates", match: (f) => f.issue_type === "duplicate" },
  { key: "acceptance", title: "Missing acceptance criteria", match: (f) => f.issue_type === "missing_acceptance_criteria" },
  { key: "infeasible", title: "Infeasibility", match: (f) => f.issue_type === "infeasibility" },
  { key: "sources", title: "Missing sources & undefined terms", match: (f) => ["missing_source", "undefined_term"].includes(f.issue_type) },
  { key: "security", title: "Missing security requirements", match: (f) => f.kind === "security_gap" },
  { key: "compliance", title: "Missing compliance requirements", match: (f) => f.kind === "compliance_gap" },
];

export default function QualityPage() {
  const { analysis: a } = useLoadedAnalysis();
  const [active, setActive] = useState(GROUPS[0].key);
  const relevant = a.findings.filter((f) => GROUPS.some((g) => g.match(f)));
  const counts = GROUPS.map((g) => ({ ...g, count: a.findings.filter(g.match).length }));
  const group = GROUPS.find((g) => g.key === active)!;
  const shown = a.findings.filter(group.match);
  const severity = relevant.reduce<Record<string, number>>((acc, f) => ({ ...acc, [f.severity]: (acc[f.severity] ?? 0) + 1 }), {});

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Findings by type" description="Quality, security and compliance gaps found across the full requirement set.">
          <ColumnBars data={counts.map((c) => ({ name: c.title.replace("Missing ", "No "), value: c.count }))} height={260} />
        </Section>
        <Section title="Findings by severity" description="Conflicting requirements are reported, never silently rewritten or removed.">
          <SeverityBars counts={severity} height={260} />
        </Section>
      </div>
      <Section title="Requirement quality analysis">
        <div className="mb-4 flex flex-wrap gap-2">
          {counts.map((c) => (
            <button key={c.key} onClick={() => setActive(c.key)}
              className={cn("rounded-full border px-3 py-1 text-sm transition-colors", active === c.key ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
              {c.title} <span className="ml-1 tabular-nums opacity-70">{c.count}</span>
            </button>
          ))}
        </div>
        {shown.length ? <ul className="grid gap-3 xl:grid-cols-2">{shown.map((f) => <FindingCard key={f.id} f={f} />)}</ul>
          : <EmptyState title={`No ${group.title.toLowerCase()} found`} description="The agents found no issues of this type." />}
      </Section>
    </div>
  );
}
