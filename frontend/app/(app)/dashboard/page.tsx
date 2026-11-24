"use client";

import { Activity, ArrowRight, CheckCircle2, FilePlus2, FolderKanban, ListChecks } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { HorizontalBars, SeverityBars } from "@/components/charts";
import { EmptyState, ErrorState, LoadingState, PageHeader, Section, StatTile, StatusPill } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { formatDate, label } from "@/lib/format";
import type { AnalysisBrief } from "@/lib/types";

type Dashboard = {
  projects: number;
  analyses: number;
  completed_analyses: number;
  running_analyses: number;
  requirements_analysed: number;
  pending: { requirements: number; mappings: number; sdlc: number; workflows: number };
  sdlc_distribution: { model: string; count: number }[];
  risk_severity: { severity: string; count: number }[];
  domains: { domain: string; count: number }[];
  recent_analyses: AnalysisBrief[];
  mode: string;
};

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<Dashboard>("/api/dashboard").then(setData).catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  const cta = (
    <Button asChild>
      <Link href="/analyse"><FilePlus2 className="size-4" /> Analyse Requirements</Link>
    </Button>
  );

  if (error) return <><PageHeader title="Dashboard" actions={cta} /><ErrorState message={error} retry={load} /></>;
  if (!data) return <><PageHeader title="Dashboard" actions={cta} /><LoadingState /></>;

  const pendingTotal = data.pending.requirements + data.pending.mappings + data.pending.sdlc + data.pending.workflows;
  const severityCounts = Object.fromEntries(data.risk_severity.map((r) => [r.severity, r.count]));

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Portfolio view of every financial project analysed by ReqPilot's agents, and what still needs a human decision."
        actions={cta}
      />

      {data.analyses === 0 ? (
        <EmptyState
          icon={FilePlus2}
          title="No analyses yet"
          description="Submit a set of financial application requirements and ReqPilot will extract, classify, check and map them, then recommend an SDLC model and generate a workflow - in one click."
          action={cta}
        />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label="Projects" value={data.projects} icon={FolderKanban} hint={`${data.domains.length} financial domain(s)`} />
            <StatTile label="Analyses" value={data.analyses} icon={Activity}
              hint={`${data.completed_analyses} completed${data.running_analyses ? ` · ${data.running_analyses} running` : ""}`} />
            <StatTile label="Requirements analysed" value={data.requirements_analysed.toLocaleString()} icon={ListChecks} />
            <StatTile label="Pending human approvals" value={pendingTotal} icon={CheckCircle2} tone={pendingTotal ? "warning" : "good"}
              hint="Across requirements, mappings, SDLC and workflows" />
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <Section title="Awaiting approval" description="AI output never approves itself."
              actions={<Button variant="outline" size="sm" asChild><Link href="/approvals">Open <ArrowRight className="size-4" /></Link></Button>}>
              <ul className="divide-y text-sm">
                {[
                  ["Requirements pending review", data.pending.requirements, "Business Analyst / PM"],
                  ["Compliance mappings", data.pending.mappings, "Compliance Officer"],
                  ["SDLC recommendations", data.pending.sdlc, "Project Manager"],
                  ["Workflows", data.pending.workflows, "Project Manager"],
                ].map(([name, count, role]) => (
                  <li key={name as string} className="flex items-center justify-between py-2.5">
                    <div>
                      <p className="font-medium">{name}</p>
                      <p className="text-xs text-muted-foreground">{role}</p>
                    </div>
                    <span className="text-lg font-semibold tabular-nums">{count}</span>
                  </li>
                ))}
              </ul>
            </Section>

            <Section title="Recommended SDLC models" description="Number of analyses per recommended model.">
              {data.sdlc_distribution.length ? (
                <HorizontalBars data={data.sdlc_distribution.map((d) => ({ name: d.model, value: d.count }))} />
              ) : <p className="text-sm text-muted-foreground">No recommendations yet.</p>}
            </Section>

            <Section title="Risk register by severity" description="All registered risks across analyses (likelihood × impact).">
              <SeverityBars counts={severityCounts} />
            </Section>
          </div>

          <Section title="Recent analyses"
            actions={<Button variant="outline" size="sm" asChild><Link href="/projects">All projects</Link></Button>}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Project</TableHead>
                  <TableHead>Domain</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Recommended SDLC</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.recent_analyses.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell className="font-mono text-xs">{a.id}</TableCell>
                    <TableCell className="font-medium">{a.project_name}</TableCell>
                    <TableCell>{label(a.domain)}</TableCell>
                    <TableCell><StatusPill status={a.status} /></TableCell>
                    <TableCell className="text-xs">{a.mode === "openai" ? "OpenAI" : "Demo (rules)"}</TableCell>
                    <TableCell>{a.recommended_sdlc || "–"}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDate(a.started_at)}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" asChild><Link href={`/analyses/${a.id}`}>View <ArrowRight className="size-4" /></Link></Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Section>
        </div>
      )}
    </>
  );
}
