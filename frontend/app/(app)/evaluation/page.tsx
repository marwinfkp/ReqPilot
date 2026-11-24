"use client";

import { CheckCircle2, FlaskConical, Loader2, Play, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth";
import { BulletList, EmptyState, ErrorState, LoadingState, Notice, PageHeader, Section, StatTile, StatusPill } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { formatDate, pct } from "@/lib/format";

type Prf = { precision: number; recall: number; f1: number; tp: number; fp: number; fn: number; accuracy?: number; accuracy_note?: string };

type CaseResult = {
  case: string;
  title: string;
  ground_truth_requirements: number;
  extracted_requirements: number;
  extraction: Prf;
  completeness: number;
  ambiguity_detection: Prf;
  conflict_detection: Prf;
  duplicate_detection: Prf;
  compliance_coverage: number;
  compliance_addressed: number;
  citation_correctness: number | null;
  hallucination_rate: number | null;
  llm_references_checked: number;
  sdlc: { recommended: string | null; expected: string; acceptable: string[]; exact_match: boolean; acceptable_match: boolean; expected_rank: number | null; score: number | null };
  traceability_coverage: number;
  processing_seconds: number;
};

type Results = {
  label: string;
  mode: string;
  model: string;
  generated_at: string;
  summary: Record<string, number | null>;
  cases: CaseResult[];
  limitations: string[];
};

type Run = {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: "running" | "completed" | "failed";
  mode: string;
  model_name: string;
  results: Results | Record<string, never>;
  error: string;
  run_by: string;
};

const f = (v: number | null | undefined) => (v === null || v === undefined ? "–" : v.toFixed(2));

function SummaryTiles({ s, mode }: { s: Record<string, number | null>; mode: string }) {
  const hallucination = s.hallucination_rate === null || s.hallucination_rate === undefined
    ? (mode === "demo" ? "n/a – no LLM output in demo mode" : "n/a") : pct(s.hallucination_rate, 1);
  const tiles: [string, string, string?][] = [
    ["Extraction precision", pct(s.extraction_precision, 1)],
    ["Extraction recall", pct(s.extraction_recall, 1)],
    ["Extraction F1", pct(s.extraction_f1, 1)],
    ["Requirement completeness", pct(s.completeness, 1), "All structured fields populated"],
    ["Ambiguity detection accuracy", pct(s.ambiguity_accuracy, 1), `F1 ${pct(s.ambiguity_f1, 1)}`],
    ["Conflict detection", `F1 ${pct(s.conflict_f1, 1)}`, `P ${pct(s.conflict_precision, 1)} · R ${pct(s.conflict_recall, 1)}`],
    ["Compliance control coverage", pct(s.compliance_coverage, 1), "Expected topics with ≥1 evidence mapping"],
    ["Citation correctness", pct(s.citation_correctness, 1), "Quotes verified in source passage"],
    ["Hallucination rate", hallucination, "Invalid LLM references / references checked"],
    ["SDLC exact match", pct(s.sdlc_exact_accuracy, 0), `Acceptable set: ${pct(s.sdlc_acceptable_accuracy, 0)}`],
    ["Traceability coverage", pct(s.traceability_coverage, 1)],
    ["Avg processing time", s.avg_processing_seconds === null || s.avg_processing_seconds === undefined ? "–" : `${s.avg_processing_seconds.toFixed(2)} s`, "Per case study"],
  ];
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {tiles.map(([labelText, value, hint]) => <StatTile key={labelText} label={labelText} value={value} hint={hint} />)}
    </div>
  );
}

function CaseTable({ cases }: { cases: CaseResult[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Case study</TableHead>
          <TableHead className="text-right">GT / extracted</TableHead>
          <TableHead className="text-right">Extraction P / R / F1</TableHead>
          <TableHead className="text-right">Ambiguity acc. / F1</TableHead>
          <TableHead className="text-right">Conflicts P / R / F1</TableHead>
          <TableHead className="text-right">Duplicates R</TableHead>
          <TableHead className="text-right">Compliance cov.</TableHead>
          <TableHead>SDLC recommended vs expected</TableHead>
          <TableHead className="text-right">Trace cov.</TableHead>
          <TableHead className="text-right">Time</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {cases.map((c) => (
          <TableRow key={c.case}>
            <TableCell className="font-medium">{c.title}</TableCell>
            <TableCell className="text-right tabular-nums">{c.ground_truth_requirements} / {c.extracted_requirements}</TableCell>
            <TableCell className="text-right tabular-nums">{f(c.extraction.precision)} / {f(c.extraction.recall)} / {f(c.extraction.f1)}</TableCell>
            <TableCell className="text-right tabular-nums">{f(c.ambiguity_detection.accuracy)} / {f(c.ambiguity_detection.f1)}</TableCell>
            <TableCell className="text-right tabular-nums" title={c.conflict_detection.accuracy_note}>
              {f(c.conflict_detection.precision)} / {f(c.conflict_detection.recall)} / {f(c.conflict_detection.f1)}
            </TableCell>
            <TableCell className="text-right tabular-nums" title={c.duplicate_detection.accuracy_note}>{f(c.duplicate_detection.recall)}</TableCell>
            <TableCell className="text-right tabular-nums">{pct(c.compliance_coverage)}</TableCell>
            <TableCell className="text-xs">
              <div className="flex items-center gap-1.5">
                {c.sdlc.acceptable_match ? <CheckCircle2 className="size-4 shrink-0 text-emerald-600" /> : <XCircle className="size-4 shrink-0 text-red-600" />}
                <span><span className="font-medium">{c.sdlc.recommended ?? "–"}</span> ({c.sdlc.score ?? "–"})</span>
              </div>
              <p className="text-muted-foreground">
                expected {c.sdlc.expected}{c.sdlc.expected_rank ? ` (ranked #${c.sdlc.expected_rank})` : ""} ·{" "}
                {c.sdlc.exact_match ? "exact match" : c.sdlc.acceptable_match ? "acceptable alternative" : "not in acceptable set"}
              </p>
            </TableCell>
            <TableCell className="text-right tabular-nums">{pct(c.traceability_coverage)}</TableCell>
            <TableCell className="text-right tabular-nums">{c.processing_seconds.toFixed(2)} s</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export default function EvaluationPage() {
  const { can } = useAuth();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(() => {
    api<Run[]>("/api/evaluation/runs").then((r) => { setRuns(r); setError(null); }).catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  const running = runs?.some((r) => r.status === "running") ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [running, load]);

  const start = async () => {
    setStarting(true);
    try {
      await api("/api/evaluation/run", { method: "POST" });
      toast.success("Evaluation started - the full agent pipeline runs on all four case studies");
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setStarting(false);
    }
  };

  const latest = runs?.find((r) => r.status === "completed" && "summary" in r.results) as (Run & { results: Results }) | undefined;

  return (
    <>
      <PageHeader
        title="Evaluation"
        description="Measures ReqPilot against a synthetic financial-sector benchmark of four case studies: digital banking, loan processing, payment systems and fraud detection."
        actions={
          <Button onClick={start} disabled={!can("evaluation:run") || running || starting}>
            {running ? <><Loader2 className="size-4 animate-spin" /> Running…</> : <><Play className="size-4" /> Run evaluation</>}
          </Button>
        }
      />
      <div className="mb-6">
        <Notice tone="warning">
          <strong>SYNTHETIC BENCHMARK.</strong> The case studies and their ground truth (expected requirements, ambiguities, conflicts,
          duplicates, compliance topics and SDLC labels) were written by the developer for this academic project. They have not been
          validated by domain experts or real financial institutions, and the results must not be read as real-world accuracy. All numbers
          below are calculated by <code>backend/app/evaluation.py</code> from an actual pipeline run.
        </Notice>
      </div>

      {error ? <ErrorState message={error} retry={load} /> : !runs ? <LoadingState /> : (
        <div className="space-y-6">
          {running && (
            <Notice>The evaluation is running: each case study goes through the full 12-stage agent pipeline. This page refreshes automatically.</Notice>
          )}
          {!latest ? (
            <EmptyState icon={FlaskConical} title="No completed evaluation yet"
              description={can("evaluation:run") ? "Run the evaluation to compute the metrics." : "Ask a Business Analyst, Project Manager or Administrator to run the evaluation."} />
          ) : (
            <>
              <Section title={`Latest results · run #${latest.id}`}
                description={`${latest.results.mode === "openai" ? `OpenAI mode · ${latest.results.model}` : "Demonstration mode · rule-based agents only"} · ${formatDate(latest.results.generated_at)} · run by ${latest.run_by}`}>
                <SummaryTiles s={latest.results.summary} mode={latest.results.mode} />
              </Section>
              <Section title="Per case study" description="P = precision, R = recall. Hover conflict/duplicate cells for found/expected pair counts.">
                <CaseTable cases={latest.results.cases} />
              </Section>
              <Section title="Limitations of this evaluation">
                <BulletList items={latest.results.limitations} />
              </Section>
            </>
          )}

          <Section title="Run history">
            {runs.length === 0 ? <p className="text-sm text-muted-foreground">No runs yet.</p> : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>#</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Finished</TableHead>
                    <TableHead>Run by</TableHead>
                    <TableHead className="text-right">Extraction F1</TableHead>
                    <TableHead className="text-right">SDLC acceptable</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((r) => {
                    const summary = "summary" in r.results ? (r.results as Results).summary : null;
                    return (
                      <TableRow key={r.id}>
                        <TableCell className="font-mono text-xs">{r.id}</TableCell>
                        <TableCell>
                          <StatusPill status={r.status} />
                          {r.error && <p className="mt-1 max-w-xs text-xs text-red-700">{r.error}</p>}
                        </TableCell>
                        <TableCell className="text-xs">{r.mode === "openai" ? `OpenAI · ${r.model_name}` : "Demo (rules)"}</TableCell>
                        <TableCell className="whitespace-nowrap text-xs">{formatDate(r.started_at)}</TableCell>
                        <TableCell className="whitespace-nowrap text-xs">{formatDate(r.finished_at)}</TableCell>
                        <TableCell className="text-xs">{r.run_by}</TableCell>
                        <TableCell className="text-right tabular-nums">{summary ? pct(summary.extraction_f1, 1) : "–"}</TableCell>
                        <TableCell className="text-right tabular-nums">{summary ? pct(summary.sdlc_acceptable_accuracy, 0) : "–"}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </Section>
        </div>
      )}
    </>
  );
}
