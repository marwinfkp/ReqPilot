"use client";

import { Link2, Link2Off, Network } from "lucide-react";
import { useState } from "react";

import { useLoadedAnalysis } from "@/components/analysis-context";
import { Meter } from "@/components/charts";
import { Section, StatTile } from "@/components/common";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { pct } from "@/lib/format";

function Cell({ items, empty = "missing" }: { items: string[]; empty?: string }) {
  if (!items.length) return <span className="text-xs text-red-700">{empty}</span>;
  return <ul className="space-y-0.5 text-xs">{items.slice(0, 3).map((i) => <li key={i}>{i}</li>)}{items.length > 3 && <li className="text-muted-foreground">+{items.length - 3} more</li>}</ul>;
}

export default function TraceabilityPage() {
  const { analysis: a } = useLoadedAnalysis();
  const [onlyMissing, setOnlyMissing] = useState(false);
  const records = a.traceability.filter((t) => !onlyMissing || t.missing_links.length);
  const withMissing = a.traceability.filter((t) => t.missing_links.length).length;
  const linkCounts = [
    ["Regulatory controls", a.traceability.filter((t) => t.regulatory_controls.length).length],
    ["Security controls", a.traceability.filter((t) => t.security_controls.length).length],
    ["User stories", a.traceability.filter((t) => t.user_story).length],
    ["Acceptance criteria", a.traceability.filter((t) => t.acceptance_criteria.length).length],
    ["Test recommendations", a.traceability.filter((t) => t.tests.length).length],
  ] as const;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile label="Traceability coverage" value={pct(a.metrics.traceability_coverage)} hint="expected links present, averaged over requirements" icon={Network} />
        <StatTile label="Fully traced requirements" value={a.traceability.length - withMissing} hint={`of ${a.traceability.length}`} icon={Link2} tone="good" />
        <StatTile label="Requirements with missing links" value={withMissing} icon={Link2Off} tone={withMissing ? "warning" : "good"} />
      </div>
      <Section title="Link coverage by artefact" description="Regulatory/security control links are only expected for requirements in those categories.">
        <div className="grid gap-3 md:grid-cols-5">
          {linkCounts.map(([name, count]) => (
            <div key={name} className="rounded-lg border p-3">
              <p className="text-xs text-muted-foreground">{name}</p>
              <p className="text-lg font-semibold tabular-nums">{count}<span className="text-sm font-normal text-muted-foreground"> / {a.traceability.length}</span></p>
            </div>
          ))}
        </div>
      </Section>
      <Section title="Requirements Traceability Matrix"
        description="Stakeholder statement → requirement → categories → controls → user story → acceptance criteria → tests → workflow phase."
        actions={<div className="flex items-center gap-2"><Checkbox id="missing" checked={onlyMissing} onCheckedChange={(v) => setOnlyMissing(!!v)} /><Label htmlFor="missing" className="text-sm font-normal">Only missing links</Label></div>}>
        <div className="overflow-x-auto">
          <Table className="text-xs">
            <TableHeader>
              <TableRow>
                <TableHead>Requirement</TableHead><TableHead>Stakeholder statement</TableHead><TableHead>Regulatory controls</TableHead>
                <TableHead>Security controls</TableHead><TableHead>User story</TableHead><TableHead>Acceptance criteria</TableHead>
                <TableHead>Tests</TableHead><TableHead>Workflow phase</TableHead><TableHead>Missing links</TableHead><TableHead>Coverage</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map((t) => {
                const expectsReg = t.categories.some((c) => c === "regulatory" || c === "privacy");
                const expectsSec = t.categories.includes("security");
                return (
                  <TableRow key={t.id} className="align-top">
                    <TableCell className="whitespace-nowrap font-mono">{t.requirement_id}</TableCell>
                    <TableCell className="min-w-[240px] max-w-[320px] whitespace-normal">{t.source_statement}<p className="mt-1 text-muted-foreground">— {t.source}</p></TableCell>
                    <TableCell className="min-w-[180px] whitespace-normal"><Cell items={t.regulatory_controls} empty={expectsReg ? "missing" : "not required"} /></TableCell>
                    <TableCell className="min-w-[180px] whitespace-normal"><Cell items={t.security_controls} empty={expectsSec ? "missing" : "not required"} /></TableCell>
                    <TableCell className="min-w-[220px] max-w-[280px] whitespace-normal">{t.user_story || <span className="text-red-700">missing</span>}</TableCell>
                    <TableCell className="min-w-[220px] whitespace-normal"><Cell items={t.acceptance_criteria} /></TableCell>
                    <TableCell className="min-w-[180px] whitespace-normal"><Cell items={t.tests} /></TableCell>
                    <TableCell className="min-w-[140px] whitespace-normal"><Cell items={t.workflow_phases} /></TableCell>
                    <TableCell className="min-w-[120px] whitespace-normal">{t.missing_links.length ? <span className="text-red-700">{t.missing_links.join(", ")}</span> : <span className="text-emerald-700">none</span>}</TableCell>
                    <TableCell className="w-28"><Meter value={t.coverage} /></TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </Section>
    </div>
  );
}
