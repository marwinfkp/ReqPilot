"use client";

import { AlertTriangle, ArrowRight, Bug, Compass, FileText, ListChecks, ShieldAlert, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { useLoadedAnalysis } from "@/components/analysis-context";
import { ColumnBars } from "@/components/charts";
import { BulletList, GeneratedBy, Section, StatTile, StatusPill } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { label, pct } from "@/lib/format";

export default function OverviewPage() {
  const { analysis: a } = useLoadedAnalysis();
  const m = a.metrics;
  const top = a.sdlc?.ranking.slice(0, 3) ?? [];
  const categories = Object.entries(
    a.requirements.flatMap((r) => r.categories).reduce<Record<string, number>>((acc, c) => ({ ...acc, [c]: (acc[c] ?? 0) + 1 }), {}),
  ).sort((x, y) => y[1] - x[1]).map(([name, value]) => ({ name: label(name), value }));
  const summaryRun = a.agent_runs.find((r) => r.agent === "Summary Writer");
  const llmSummary = a.mode === "openai" && summaryRun?.status === "completed";

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Submitted statements" value={m.submitted} hint={`${a.input_sources.length} source(s)`} icon={FileText} />
        <StatTile label="Extracted requirements" value={m.extracted} hint={`${m.functional} functional · ${m.non_functional} non-functional`} icon={ListChecks} />
        <StatTile label="Quality issues" value={m.quality_issues} hint={`${m.conflicts} conflicts · ${m.ambiguities} ambiguities`} icon={Bug} tone={m.quality_issues ? "warning" : "good"} />
        <StatTile label="Compliance mappings" value={m.compliance_mappings} hint={`${m.compliance_gaps} gaps · ${m.retrieval_method} retrieval`} icon={ShieldCheck} />
        <StatTile label="High-risk findings" value={m.high_risk_findings} hint={`${m.high_risks} high/critical risks`} icon={ShieldAlert} tone={m.high_risk_findings ? "danger" : "good"} />
        <StatTile label="Recommended SDLC" value={<span className="text-lg">{m.recommended_sdlc || "–"}</span>} hint={m.sdlc_score !== null ? `Suitability ${m.sdlc_score}/100` : undefined} icon={Compass} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Section title="Executive summary"
          actions={<GeneratedBy value={llmSummary ? `openai:${a.model_name}` : "rules"} />}>
          <p className="text-sm leading-relaxed">{a.summary}</p>
          <h3 className="mb-2 mt-5 text-sm font-medium">Key findings</h3>
          <BulletList items={a.key_findings} />
          <div className="mt-5 flex flex-wrap gap-4 border-t pt-4 text-xs text-muted-foreground">
            <span>Overall status: <StatusPill status={a.status} /></span>
            <span>Requirement baseline: <StatusPill status={a.baseline_status} /></span>
            <span>Processing time: {m.duration_seconds}s</span>
            <span>LLM calls: {m.llm_calls}</span>
            <span>Traceability coverage: {pct(m.traceability_coverage)}</span>
          </div>
        </Section>

        <Card className="border-primary/30 bg-gradient-to-br from-primary/5 to-transparent">
          <CardContent className="space-y-4">
            <div className="flex items-center gap-2 text-sm font-medium text-primary"><Compass className="size-4" /> Recommended SDLC model</div>
            {a.sdlc ? (
              <>
                <div>
                  <p className="text-2xl font-semibold tracking-tight">{a.sdlc.recommended_model}</p>
                  <p className="text-sm text-muted-foreground">Suitability score {a.sdlc.ranking[0].score}/100 · <StatusPill status={a.sdlc.status} /></p>
                </div>
                <ul className="space-y-2">
                  {top.map((r) => (
                    <li key={r.model} className="flex items-center gap-3 text-sm">
                      <span className="w-5 text-muted-foreground">{r.rank}.</span>
                      <span className="flex-1 truncate">{r.model}</span>
                      <div className="h-1.5 w-24 rounded-full bg-muted"><div className="h-1.5 rounded-full bg-[var(--series-1)]" style={{ width: `${r.score}%` }} /></div>
                      <span className="w-10 text-right tabular-nums">{r.score}</span>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-muted-foreground">Scores are explainable suitability scores, not probabilities.</p>
                <div className="flex flex-wrap gap-2">
                  <Button asChild size="sm"><Link href={`/analyses/${a.id}/sdlc`}>Why this model <ArrowRight className="size-4" /></Link></Button>
                  <Button asChild size="sm" variant="outline"><Link href={`/analyses/${a.id}/workflow`}>View workflow</Link></Button>
                </div>
              </>
            ) : (
              <p className="flex items-center gap-2 text-sm text-red-700"><AlertTriangle className="size-4" /> No recommendation - the SDLC agent failed. See the agent log.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Requirements by category" description="Multi-label: one requirement can belong to several categories.">
          <ColumnBars data={categories} />
        </Section>
        <Section title="Validation report" description="Deterministic consistency checks run by the Validation Agent.">
          <ul className="space-y-2">
            {a.validation_report.map((c) => (
              <li key={c.check} className="flex items-start gap-3 text-sm">
                <StatusPill status={c.status} text={c.status.toUpperCase()} />
                <div><p className="font-medium">{c.check}</p><p className="text-xs text-muted-foreground">{c.detail}</p></div>
              </li>
            ))}
          </ul>
        </Section>
      </div>

      <Section title="Agent pipeline log" description="Every agent run is recorded with its status, mode and LLM usage. Failed agents are never replaced by fabricated output.">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow><TableHead>#</TableHead><TableHead>Agent</TableHead><TableHead>Status</TableHead><TableHead>Mode</TableHead><TableHead className="text-right">Time</TableHead><TableHead className="text-right">LLM calls</TableHead><TableHead>Result</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {a.agent_runs.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="text-muted-foreground">{r.order}</TableCell>
                  <TableCell className="whitespace-nowrap font-medium">{r.agent}</TableCell>
                  <TableCell><StatusPill status={r.status} /></TableCell>
                  <TableCell className="text-xs">{r.mode === "rules" ? "Rules only" : "OpenAI + rules"}</TableCell>
                  <TableCell className="text-right tabular-nums">{r.duration_ms} ms</TableCell>
                  <TableCell className="text-right tabular-nums">{r.llm_calls.length}</TableCell>
                  <TableCell className="min-w-[320px] whitespace-normal text-xs text-muted-foreground">{r.message}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>

      <Section title="Submitted input" description="The original submission is stored unchanged.">
        <ul className="mb-3 flex flex-wrap gap-2 text-sm">
          {a.input_sources.map((s) => <li key={s.name} className="rounded-md border px-2.5 py-1">{s.name} · {s.chars.toLocaleString()} chars</li>)}
        </ul>
        {a.input_text && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs">{a.input_text}</pre>}
      </Section>
    </div>
  );
}
