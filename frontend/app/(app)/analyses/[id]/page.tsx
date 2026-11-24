"use client";

import { AlertTriangle, ArrowRight, Bug, Compass, FileText, ListChecks, MessageCircleQuestion, ShieldAlert, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useLoadedAnalysis } from "@/components/analysis-context";
import { ColumnBars } from "@/components/charts";
import { BulletList, GeneratedBy, Section, StatTile, StatusPill } from "@/components/common";
import { Evidence, RetrievalSummary } from "@/components/evidence";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { label, pct } from "@/lib/format";
import type { ClarificationQuestion } from "@/lib/types";

/** Clarification provenance: what was asked before the analysis and how it influenced the results. */
function ClarificationCard() {
  const { analysis: a } = useLoadedAnalysis();
  const c = a.clarification;
  const [qs, setQs] = useState<ClarificationQuestion[] | null>(null);
  if (!c || c.total + c.stage2_questions === 0) return null;
  const load = () => api<{ questions: ClarificationQuestion[] }>(`/api/analyses/${a.id}/clarifications`).then((d) => setQs(d.questions)).catch(() => setQs([]));
  const tiles: [string, number | string][] = [
    ["Questions asked", c.total + c.stage2_questions], ["Answered", c.answered + c.stage2_answered], ["Deferred", c.deferred],
    ["Awaiting stakeholder", c.needs_stakeholder], ["Issues resolved", c.issues_resolved],
    ["Partially resolved", c.issues_partially_resolved], ["Still unresolved", c.issues_unresolved + c.open],
  ];
  return (
    <Section title="Requirements clarification"
      description={c.influenced_analysis
        ? "Clarification answers were used by the agents as supplementary information (the original requirements were not changed)."
        : "No clarification answers were given - unresolved questions are reported, nothing was assumed."}
      actions={
        <Dialog onOpenChange={(open) => open && !qs && load()}>
          <DialogTrigger asChild><Button size="sm" variant="outline"><MessageCircleQuestion className="size-4" /> Questions &amp; answers</Button></DialogTrigger>
          <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
            <DialogHeader><DialogTitle>Clarification questions and answers</DialogTitle></DialogHeader>
            {!qs ? <p className="text-sm text-muted-foreground">Loading…</p> : (
              <ul className="space-y-3">
                {qs.map((q) => (
                  <li key={q.id} className="rounded-md border p-3 text-sm">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-muted-foreground">{q.code}</span>
                      <StatusPill status={q.status} />
                      {q.resolution && <StatusPill status={q.resolution} />}
                      {q.requirement_ids.length > 0 && <span className="font-mono text-xs text-muted-foreground">{q.requirement_ids.join(", ")}</span>}
                    </div>
                    <p className="font-medium">{q.question}</p>
                    <p className={q.answer ? "mt-1" : "mt-1 text-muted-foreground"}>{q.answer || "No answer"}</p>
                  </li>
                ))}
              </ul>
            )}
          </DialogContent>
        </Dialog>
      }>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
        {tiles.map(([text, value]) => (
          <div key={text} className="rounded-lg border p-3">
            <p className="text-xs text-muted-foreground">{text}</p>
            <p className="text-xl font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Influenced the analysis: <strong className="text-foreground">{c.influenced_analysis ? "yes" : "no"}</strong> ·
        {" "}{c.requirements_with_clarifications} requirement(s) carry supplementary clarification information.
        {c.critical_unanswered > 0 && ` ${c.critical_unanswered} critical question(s) remain unanswered.`}
      </p>
    </Section>
  );
}

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
            {m.evidence_citations != null && (
              <span title="Knowledge-base citations of all agents whose quote was found in the retrieved source passage (a quotation check, not proof that the passage supports the finding; compliance mappings are counted separately)">
                KB quotes verified: {m.evidence_verified}/{m.evidence_citations}
              </span>
            )}
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
        <Section title="Validation report" description="Consistency checks run by the Validation Agent, including test coverage against the testing standard in the knowledge base.">
          <ul className="space-y-2">
            {a.validation_report.map((c) => (
              <li key={c.check} className="flex items-start gap-3 text-sm">
                <StatusPill status={c.status} text={c.status.toUpperCase()} />
                <div className="min-w-0">
                  <p className="font-medium">{c.check}</p><p className="text-xs text-muted-foreground">{c.detail}</p>
                  {c.items?.some((i) => i.test_case) && (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs">
                      {c.items.filter((i) => i.test_case).map((i, n) => (
                        <li key={n}><span className="font-mono">{i.requirement_id}</span>: {i.test_case} <span className="text-muted-foreground">- {i.reason}</span></li>
                      ))}
                    </ul>
                  )}
                  <Evidence citations={c.citations} />
                </div>
              </li>
            ))}
          </ul>
        </Section>
      </div>

      <ClarificationCard />

      <Section title="Agent pipeline log" description="Every agent run is recorded with its stage, status, mode and LLM usage. Failed agents are never replaced by fabricated output; 'skipped' in Stage 2 means the output was retained from the previous version.">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow><TableHead>#</TableHead><TableHead>Agent / operation</TableHead><TableHead>Status</TableHead><TableHead>Mode</TableHead><TableHead className="text-right">Time</TableHead><TableHead className="text-right">LLM calls</TableHead><TableHead>Knowledge base</TableHead><TableHead>Result</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {(["stage1", "stage2"] as const).map((stage) => {
                const runs = a.agent_runs.filter((r) => (r.stage ?? "stage1") === stage);
                if (!runs.length) return null;
                return [
                  <TableRow key={`${stage}-header`} className="bg-muted/50 hover:bg-muted/50">
                    <TableCell colSpan={8} className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {stage === "stage1" ? "Stage 1 - preliminary analysis, clarification and full 12-agent analysis"
                        : `Stage 2 - refinement${a.version > 1 ? ` (regeneration that created version ${a.version})` : ""}`}
                    </TableCell>
                  </TableRow>,
                  ...runs.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="text-muted-foreground">{r.order || "–"}</TableCell>
                      <TableCell className="whitespace-nowrap font-medium">{r.agent}</TableCell>
                      <TableCell><StatusPill status={r.status} text={r.status === "partial" ? "Partially completed" : undefined} /></TableCell>
                      <TableCell className="text-xs">{r.mode === "rules" ? "Rules only" : r.mode === "human" ? "Human input" : "OpenAI + rules"}</TableCell>
                      <TableCell className="text-right tabular-nums">{r.duration_ms} ms</TableCell>
                      <TableCell className="text-right tabular-nums">{r.llm_calls.length}</TableCell>
                      <TableCell className="min-w-[170px] whitespace-normal"><RetrievalSummary logs={r.retrieval} /></TableCell>
                      <TableCell className="min-w-[320px] whitespace-normal text-xs text-muted-foreground">{r.message}</TableCell>
                    </TableRow>
                  )),
                ];
              })}
            </TableBody>
          </Table>
        </div>
        {a.version > 1 && !a.agent_runs.some((r) => r.stage === "stage1") && (
          <p className="mt-2 text-xs text-muted-foreground">
            The Stage 1 log of this analysis is on <Link className="underline" href={`/analyses/${a.root_analysis_id}`}>version 1</Link>.
          </p>
        )}
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
