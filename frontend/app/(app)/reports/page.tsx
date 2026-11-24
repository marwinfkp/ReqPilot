"use client";

import { FileDown, FileText, FileType2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { EmptyState, ErrorState, LoadingState, Notice, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorMessage } from "@/lib/api";
import { formatDate, isDone } from "@/lib/format";
import type { AnalysisBrief } from "@/lib/types";

const DESCRIPTIONS: Record<string, string> = {
  srs: "Complete Software Requirements Specification: scope, stakeholders, functional and non-functional requirements, use cases, compliance and open issues.",
  requirements: "Structured functional requirements and business rules, non-functional requirements and constraints with categories, priority and risk.",
  user_stories: "User story, Given/When/Then acceptance criteria and test recommendations for every requirement.",
  use_cases: "Use-case descriptions with actors, preconditions, main and alternate flows and covered requirements.",
  quality: "Ambiguity, incompleteness, conflicts, duplicates and missing acceptance criteria with suggested corrections and stakeholder questions.",
  compliance: "Applicable sources, requirement-to-control mappings with cited evidence, compliance gaps and unmapped requirements.",
  security: "Security coverage by area, missing controls, STRIDE threats and privacy considerations.",
  risks: "Risk register with likelihood, impact, score, severity and mitigations.",
  traceability: "Requirements Traceability Matrix from stakeholder statement to controls, tests and workflow phases, with missing links.",
  sdlc: "Ranked SDLC models, factor ratings, decision rules, justification, advantages, disadvantages and trade-offs.",
  workflow: "Project-specific SDLC workflow: phases, activities, roles, deliverables, checkpoints, entry/exit criteria and approval gates.",
  consolidated: "Everything above in one report, plus the validation report and the agent execution log.",
};

function ReportsContent() {
  const params = useSearchParams();
  const [analyses, setAnalyses] = useState<AnalysisBrief[] | null>(null);
  const [types, setTypes] = useState<{ key: string; title: string }[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([api<AnalysisBrief[]>("/api/analyses"), api<{ key: string; title: string }[]>("/api/reports/types")])
      .then(([list, reportTypes]) => {
        const done = list.filter((a) => isDone(a.status));
        setAnalyses(done);
        setTypes(reportTypes);
        const requested = params.get("analysis");
        setSelected((current) => current || (requested && done.some((a) => String(a.id) === requested) ? requested : done[0] ? String(done[0].id) : ""));
      })
      .catch((e) => setError(errorMessage(e)));
  }, [params]);
  useEffect(load, [load]);

  const current = useMemo(() => analyses?.find((a) => String(a.id) === selected), [analyses, selected]);

  if (error) return <ErrorState message={error} retry={load} />;
  if (!analyses) return <LoadingState />;
  if (analyses.length === 0) {
    return (
      <EmptyState icon={FileDown} title="No completed analyses"
        description="Reports are generated from a completed analysis."
        action={<Button asChild><Link href="/analyse">Analyse requirements</Link></Button>} />
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="w-full max-w-md space-y-1.5">
            <Label>Analysis</Label>
            <Select value={selected} onValueChange={setSelected}>
              <SelectTrigger className="w-full"><SelectValue placeholder="Choose an analysis" /></SelectTrigger>
              <SelectContent>
                {analyses.map((a) => (
                  <SelectItem key={a.id} value={String(a.id)}>#{a.id} · {a.project_name} · {formatDate(a.finished_at)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {current && (
            <p className="text-sm text-muted-foreground">
              {current.mode === "openai" ? `OpenAI · ${current.model_name}` : "Demonstration mode (rule-based agents)"} · recommended{" "}
              <span className="font-medium text-foreground">{current.recommended_sdlc || "–"}</span>
            </p>
          )}
        </CardContent>
      </Card>

      <Notice>
        Every report states the analysis mode, carries the AI-assistance disclaimer and labels illustrative and reference evidence.
        Downloads are recorded in the audit log.
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {types.map((t) => (
          <Card key={t.key} className={t.key === "consolidated" ? "border-primary/40 sm:col-span-2 xl:col-span-3" : ""}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base"><FileText className="size-4 text-primary" /> {t.title}</CardTitle>
              <CardDescription>{DESCRIPTIONS[t.key] ?? ""}</CardDescription>
            </CardHeader>
            <CardContent className="mt-auto flex gap-2">
              <Button size="sm" asChild disabled={!selected}>
                <a href={`/api/analyses/${selected}/export/${t.key}?format=pdf`}><FileDown className="size-4" /> PDF</a>
              </Button>
              <Button size="sm" variant="outline" asChild disabled={!selected}>
                <a href={`/api/analyses/${selected}/export/${t.key}?format=docx`}><FileType2 className="size-4" /> DOCX</a>
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

export default function ReportsPage() {
  return (
    <>
      <PageHeader title="Reports & Exports" description="Download formatted DOCX and PDF documents generated from a completed analysis." />
      <Suspense fallback={<LoadingState />}>
        <ReportsContent />
      </Suspense>
    </>
  );
}
